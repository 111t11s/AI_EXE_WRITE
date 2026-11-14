import asyncio
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from openai import APIStatusError, OpenAI
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlalchemy.exc import SQLAlchemyError
from fastapi import HTTPException
from fastapi.testclient import TestClient
from agents import writing_agents as a
from agents.mcp.server import create_server
from api.novel_crud_api import get_database
from api.novel_writing_store import save_outline
from api.writing_schemas import OutlineDraft, ChapterPlanDraft, ChapterDraft, ReviewReport
from main import app

def create_test_db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    schema = Path(__file__).with_name("db").joinpath("schema.sql").read_text(encoding="utf-8")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        # Preserve unique and foreign key constraints while translating MySQL syntax.
        for name, body in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\) ENGINE=", schema, re.S):
            lines = []
            for line in body.splitlines():
                line = line.strip().rstrip(",")
                if not line or line.startswith(("KEY ", "PRIMARY KEY")):
                    continue
                line = re.sub(r" COMMENT '.*?'", "", line)
                if "AUTO_INCREMENT" in line:
                    line = "id INTEGER PRIMARY KEY AUTOINCREMENT"
                if name == "novel_design_session" and line.startswith("id "):
                    line += " PRIMARY KEY"
                if name == "app_auth_session" and line.startswith("token_hash "):
                    line += " PRIMARY KEY"
                line = re.sub(r"ENUM\([^)]*\)", "TEXT", line)
                line = re.sub(r"UNIQUE KEY \w+", "UNIQUE", line)
                lines.append(line.replace(" UNSIGNED", "").replace(" ON UPDATE CURRENT_TIMESTAMP", ""))
            connection.exec_driver_sql(f"CREATE TABLE {name} (" + ",\n".join(lines) + ")")
    return engine

def outline():
    return OutlineDraft(summary='Summary', central_conflict='Conflict',themes='Memory',
      volumes=[dict(title='The ticket',summary='Find a passenger',protagonist_goal='Find her',main_conflict='Bad records',
       climax='The bell',ending_hook='A name gone',beats=[dict(title='Empty ship',summary='Find the ticket')])])

def fake(role,ctx):
    assert ctx['project']['id']>0 and ctx['world']['id']>0
    if role==a.OUTLINE:
        return outline()
    if role==a.PLAN:
        return ChapterPlanDraft(chapters=[dict(title='Ticket',summary='Find it'),dict(title='Bell',summary='Hear it')])
    if role==a.DRAFT:
        return ChapterDraft(content='The apprentice walked toward the lighthouse. '*10)
    return ReviewReport(verdict='ready',summary='Matches the world rules.',issues=[])

async def run(engine, token):
    server = create_server("http://aitxt.test/api/v1", transport=httpx.ASGITransport(app=app), auth_token=token)

    @asynccontextmanager
    async def session():
        async with create_connected_server_and_client_session(server) as client:
            yield client

    with patch('api.novel_writing_api.novel_session',session),patch('agents.writing_agents.generate',side_effect=fake):
        async with session() as c:
            tools=(await c.list_tools()).tools
            assert len(tools)==79 and all(t.outputSchema for t in tools)
            async def call(name,code=None,**args):
                result=await c.call_tool(name,args)
                if code:
                    assert result.isError and str(code) in result.content[0].text,result
                else:
                    assert not result.isError,result
                    return result.structuredContent
            pid=(await call('create_novel_project',payload=dict(genre='Fantasy',setting='Harbor',tone='Hopeful',target_length='Short',protagonist_goal='Find her')))['id']
            await call('generate_novel_outline',409,project_id=pid,payload={'volume_count':1})
            await call('create_novel_world',payload={'project_id':pid,'summary':'Memories shape tides','era':'Lost moon'})
            one=await call('generate_novel_outline',project_id=pid,payload={'volume_count':1})
            two=await call('generate_novel_outline',project_id=pid,payload={'volume_count':1})
            assert one['version_no']==1 and two['version_no']==2 and one['plot_beat_count']==1
            vid=one['volume_ids'][0]
            plan=await call('plan_novel_chapters',volume_id=vid,payload={'chapter_count':2})
            assert [x['chapter_number'] for x in plan['chapters']]==[1,2]
            await call('plan_novel_chapters',409,volume_id=vid,payload={'chapter_count':2})
            cid=plan['chapters'][0]['chapter_id']
            await call('review_novel_chapter',409,chapter_id=cid)
            draft=await call('draft_novel_chapter',chapter_id=cid)
            assert draft['status']=='draft' and draft['word_count']>0
            saved=await call('read_novel_chapter',record_id=cid)
            assert saved['content'].startswith('The apprentice')
            await call('draft_novel_chapter',409,chapter_id=cid)
            assert (await call('review_novel_chapter',chapter_id=cid))['verdict']=='ready'
            assert (await call('read_novel_chapter',record_id=cid))==saved
            await call('generate_novel_outline',502,project_id=pid)
            await call('generate_novel_outline',404,project_id=pid+999)
            await call('plan_novel_chapters',422,volume_id=vid,payload={'start_chapter':2**32-1,'chapter_count':2})
            invalid=outline()
            invalid.volumes[0].title=None
            try:
                save_outline(engine,pid,invalid)
            except (SQLAlchemyError, HTTPException):
                pass
            else:
                raise AssertionError('write should have failed')
            with engine.connect() as conn:
                assert conn.scalar(text('SELECT COUNT(*) FROM novel_outline'))==2
                assert conn.scalar(text('SELECT COUNT(*) FROM novel_chapter'))==2
def test_writing_workflow():
    engine = create_test_db()
    try:
        with patch.dict(app.dependency_overrides, {get_database: lambda: engine}):
            with TestClient(app) as client:
                response = client.post('/api/v1/auth/register', json={'username': 'writer', 'password': 'test-password'})
                assert response.status_code == 201
                token = response.json()['access_token']
            asyncio.run(run(engine, token))
    finally:
        engine.dispose()


@pytest.mark.parametrize("status", [500, 401])
def test_gateway_tool_failure(status, monkeypatch):
    from core.config import Settings
    monkeypatch.setattr(a, "get_settings", lambda: Settings(_env_file=None, MODEL_NATIVE_TOOLS=True, API_KEY="test-key"))
    upstream = httpx.Response(status, request=httpx.Request("POST", "https://gateway.test"))
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": outline().model_dump_json()}}],
        })

    monkeypatch.setattr(a, "create_agent", lambda role, llm: None)
    monkeypatch.setattr(a, "OpenAI", lambda **kwargs: OpenAI(
        **{**kwargs, "api_key": "test-key", "base_url": "https://gateway.test"},
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    ))
    context = {"world": {"summary": "Memories shape tides"}, "request": {"volume_count": 1}}
    with patch("crewai.Crew") as crew, patch("crewai.LLM"):
        crew.return_value.kickoff.side_effect = APIStatusError("upstream failure", response=upstream, body=None)
        if status == 401:
            with pytest.raises(APIStatusError):
                a.generate(a.OUTLINE, context)
            assert not requests
            return
        assert a.generate(a.OUTLINE, context) == outline()

    assert len(requests) == 1
    assert "tools" not in requests[0]
    assert "Memories shape tides" in requests[0]["messages"][1]["content"]
