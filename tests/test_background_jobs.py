"""Background submission, account ownership, and resumable progress."""

import asyncio
import json
import threading
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.concurrency import run_in_threadpool

from agents.llm_streaming import emit_generation_event
from api import generation_jobs as jobs, novel_assistant_api as assistant, novel_writing_api as writing
from api.account_api import Account
from api.database import get_database
from core.identity import AUTH_TOKEN
from main import app
from test_writing_workflow import create_test_db


def decode(frame):
    name, payload = frame.strip().split('\n', 1)
    return name.removeprefix('event: '), json.loads(payload.removeprefix('data: '))


@pytest.fixture
def background(monkeypatch):
    engine = create_test_db()
    monkeypatch.setattr(jobs, 'store', jobs.JobStore())
    monkeypatch.setattr(assistant, '_sessions', {})
    monkeypatch.setitem(app.dependency_overrides, get_database, lambda: engine)
    with TestClient(app) as client:
        users = {}
        for name in ('writer', 'other'):
            user = client.post('/api/v1/auth/register', json={'username': name, 'password': 'test-password'}).json()
            users[name] = {'Authorization': 'Bearer ' + user['access_token']}

        def create(path, **data):
            response = client.post('/api/v1/' + path, json=data, headers=users['writer'])
            assert response.status_code == 201, response.text
            return response.json()['id']

        project = create('projects', title='后台小说', genre='仙侠', setting='山海', tone='热血',
                         target_length='短篇', protagonist_goal='守护')
        outline = create('outlines', project_id=project, summary='全书规划', central_conflict='灵气衰退')
        volume = create('outline-volumes', outline_id=outline, volume_number=1, title='启程', summary='离乡')
        chapter = create('chapters', project_id=project, volume_id=volume, chapter_number=1, title='出发')
        yield client, users, project, volume, chapter
    engine.dispose()


@pytest.mark.parametrize('kind,method,table,suffix', [
    ('outline', 'write_outline', 'projects', 'outline'), ('plan', 'write_chapter_plan', 'volumes', 'chapter-plan'),
    ('draft', 'write_draft', 'chapters', 'draft'), ('review', 'review_chapter', 'chapters', 'review'),
    ('outline', 'rewrite_outline', 'outlines', 'rewrite'), ('draft', 'rewrite_draft', 'chapters', 'rewrite'),
])
def test_submit_returns_before_generation_and_deduplicates_owned_target(background, monkeypatch, kind, method, table, suffix):
    client, users, project, volume, chapter = background
    record_id = project if table == 'projects' else volume if table == 'volumes' else chapter
    if table == 'outlines':
        record_id = client.get('/api/v1/outlines', headers=users['writer']).json()['items'][0]['id']
    path = f'/api/v1/writing/{table}/{record_id}/{suffix}/tasks'
    entered, release = threading.Event(), threading.Event()
    calls = []

    async def operation(*args):
        assert AUTH_TOKEN.get() == users['writer']['Authorization'].split()[1]
        emit_generation_event('thinking', text='核对资料')
        emit_generation_event('delta', text='第一段')
        entered.set()
        assert await run_in_threadpool(release.wait, 10)
        emit_generation_event('delta', text='第二段')
        calls.append('saved')
        return {'project_id': project, 'saved': True}

    monkeypatch.setattr(writing, method, operation)
    payload = {'volume_count': 1} if kind == 'outline' else {}
    try:
        response = client.post(path, json=payload, headers=users['writer'])
        assert response.status_code == 202
        job_id = response.json()['id']
        assert entered.wait(2) and calls == []
        assert client.post(path, json=payload, headers=users['writer']).json()['id'] == job_id
        page = client.get('/api/v1/generation-jobs', headers=users['writer']).json()['items']
        assert len(page) == 1 and page[0]['status'] == 'running'
        assert 'text' not in page[0] and 'thinking' not in page[0]
        detail = client.get(f'/api/v1/generation-jobs/{job_id}', headers=users['writer']).json()
        assert detail['text'] == '第一段' and detail['thinking'] == '核对资料'
        assert detail['project_id'] == project
        assert client.get('/api/v1/generation-jobs', headers=users['other']).json()['items'] == []
        for suffix in ('', '/events'):
            assert client.get(f'/api/v1/generation-jobs/{job_id}{suffix}', headers=users['other']).status_code == 404
        assert client.post(path, json=payload, headers=users['other']).status_code == 404
        assert client.post(path, json=payload).status_code == 401
        release.set()
        replay = client.get(f'/api/v1/generation-jobs/{job_id}/events', headers=users['writer'])
        assert replay.status_code == 200 and '第二段' in replay.text and 'event: result' in replay.text
        assert calls == ['saved']
    finally:
        release.set()


def test_disconnect_and_reopen_replays_text_without_restarting(monkeypatch):
    async def run():
        store = jobs.JobStore()
        monkeypatch.setattr(jobs, 'store', store)
        ready, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def operation():
            emit_generation_event('thinking', text='先思考')
            emit_generation_event('delta', text='前半段')
            ready.set()
            await release.wait()
            emit_generation_event('delta', text='后半段')
            calls.append('saved')
            return {'saved': True}

        job = store.start(account_id=1, key='test', kind='draft', title='测试', operation=operation)
        user = Account(id=1, username='writer', display_name='作者')
        response = await jobs.watch_job(job.id, user)
        stream = response.body_iterator
        first = decode(await anext(stream))
        assert first[0] == 'snapshot' and first[1]['status'] == 'running'
        await ready.wait()
        assert decode(await anext(stream)) == ('thinking', {'text': '先思考'})
        assert decode(await anext(stream)) == ('delta', {'text': '前半段'})
        await stream.aclose()
        assert not job.listeners
        release.set()
        await asyncio.gather(*store.running)
        response = await jobs.watch_job(job.id, user)
        frames = [decode(frame) async for frame in response.body_iterator]
        assert frames[0][1]['text'] == '前半段后半段'
        assert frames[0][1]['thinking'] == '先思考'
        assert frames[-1] == ('result', {'saved': True})
        assert calls == ['saved']

    asyncio.run(run())


def test_error_retains_partial_text_and_never_reports_success(monkeypatch):
    async def run():
        store = jobs.JobStore()
        monkeypatch.setattr(jobs, 'store', store)

        async def operation():
            emit_generation_event('delta', text='已生成的内容')
            raise HTTPException(504, '模型响应超时')

        job = store.start(account_id=1, key='test', kind='draft', title='测试', operation=operation)
        await asyncio.gather(*store.running)
        response = await jobs.watch_job(job.id, Account(id=1, username='writer', display_name='作者'))
        frames = [decode(frame) async for frame in response.body_iterator]
        assert frames[0][1]['text'] == '已生成的内容' and frames[0][1]['status'] == 'failed'
        assert frames[-1] == ('error', {'message': '模型响应超时', 'status': 504})
        assert all(event != 'result' for event, _ in frames)

    asyncio.run(run())


def test_slow_progress_reader_gets_snapshot_after_event_history_rolls_over(monkeypatch):
    async def run():
        store = jobs.JobStore()
        monkeypatch.setattr(jobs, 'store', store)
        release = asyncio.Event()

        async def operation():
            await release.wait()
            for _ in range(1005):
                emit_generation_event('delta', text='字')
            return {'saved': True}

        job = store.start(account_id=1, key='test', kind='draft', title='测试', operation=operation)
        response = await jobs.watch_job(job.id, Account(id=1, username='writer', display_name='作者'))
        stream = response.body_iterator
        await anext(stream)
        release.set()
        await asyncio.gather(*store.running)
        frames = [decode(frame) async for frame in stream]
        assert frames[0][0] == 'snapshot' and frames[0][1]['text'] == '字' * 1005
        assert frames[-1][0] == 'result'

    asyncio.run(run())


def test_active_limit_is_account_scoped_and_duplicates_do_not_consume_slots():
    async def run():
        store, release = jobs.JobStore(), asyncio.Event()

        async def operation():
            await release.wait()
            return {}

        try:
            first = store.start(account_id=1, key='one', kind='draft', title='任务', operation=operation)
            assert store.start(account_id=1, key='one', kind='draft', title='任务', operation=operation) is first
            for key in ('two', 'three'):
                store.start(account_id=1, key=key, kind='draft', title='任务', operation=operation)
            with pytest.raises(HTTPException) as exc:
                store.start(account_id=1, key='four', kind='draft', title='任务', operation=operation)
            assert exc.value.status_code == 429
            store.start(account_id=2, key='four', kind='draft', title='任务', operation=operation)
        finally:
            release.set()
            await asyncio.gather(*store.running)

    asyncio.run(run())


def test_worldbuilding_submission_checks_ownership_and_runs_once(background, monkeypatch):
    client, users, *_ = background
    from agents.worldbuilding_agent import ConversationState
    from test_worldbuilding_generation import VALUES
    assistant._sessions['ready'] = ConversationState(account_id=1, values=VALUES.copy())
    operation = AsyncMock(return_value={'persistence': {'project_id': 7, 'world_id': 8}, 'done': True})
    monkeypatch.setattr(assistant, 'send_message', operation)
    path = '/api/v1/novel-assistant/sessions/ready/messages/tasks'
    assert client.post(path, json={'content': '确认生成'}, headers=users['other']).status_code == 404
    response = client.post(path, json={'content': '确认生成'}, headers=users['writer'])
    assert response.status_code == 202
    replay = client.get(f"/api/v1/generation-jobs/{response.json()['id']}/events", headers=users['writer'])
    assert 'event: result' in replay.text and operation.await_count == 1
    detail = client.get(f"/api/v1/generation-jobs/{response.json()['id']}", headers=users['writer']).json()
    assert detail['project_id'] == 7
