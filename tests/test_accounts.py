"""Exercise real account authentication, resource isolation and MCP identity."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp.shared.memory import create_connected_server_and_client_session
from sqlalchemy import create_engine, insert, inspect, select, update

from agents import worldbuilding_agent
from agents.mcp.server import create_server
from agents.mcp.worldbuilding import server_parameters
from api import novel_assistant_api as assistant, novel_writing_api as writing
from api import account_api
from api.database import get_database
from api.novel_contracts import CRUD_RESOURCES
from api.novel_tables import tables
from core.identity import AUTH_TOKEN
from db.accounts import account, auth_session, token_hash, utcnow, verify_password
from db.migrate_accounts import bind_legacy, create_account, migrate_schema
from db.reset_password import reset_password
from main import app
from test_writing_workflow import create_test_db


PROJECT = dict(title="测试小说", genre="仙侠", setting="灵气大陆", tone="热血", target_length="短篇", protagonist_goal="守护家园")


@pytest.fixture
def setup_accounts(monkeypatch):
    engine = create_test_db()
    monkeypatch.setitem(app.dependency_overrides, get_database, lambda: engine)
    monkeypatch.setattr(assistant, "_sessions", {})
    with TestClient(app) as client:
        users = {}
        for name in ("admin", "writer"):
            response = client.post("/api/v1/auth/register", json={"username": name, "password": "test-password"})
            assert response.status_code == 201
            users[name] = response.json()
        yield engine, client, users
    engine.dispose()


def headers(user):
    return {"Authorization": "Bearer " + user["access_token"]}


def graph(client, user):
    rows = {}

    def create(path, **payload):
        response = client.post("/api/v1/" + path, json=payload, headers=headers(user))
        assert response.status_code == 201, (path, response.text)
        rows[path] = response.json()
        return rows[path]["id"]

    project = create("projects", **PROJECT)
    session = create("design-sessions", project_id=project)
    create("design-messages", session_id=session, sequence_no=1, speaker="user", content="玄幻")
    world = create("worlds", project_id=project, summary="灵潮复苏", era="古代")
    create("regions", world_id=world, name="云泽", description="群山")
    power = create("power-systems", world_id=world, name="修行", source="灵气")
    create("power-ranks", power_system_id=power, rank_order=1, name="炼气")
    create("cheat-systems", world_id=world, name="古印", cheat_type="传承")
    faction = create("factions", world_id=world, name="云宗", summary="守护灵脉")
    source = create("characters", project_id=project, faction_id=faction, name="林舟", role="主角", summary="少年")
    target_response = client.post("/api/v1/characters", headers=headers(user), json={
        "project_id": project, "name": "云宁", "role": "同伴", "summary": "药师"})
    assert target_response.status_code == 201
    create("character-relationships", source_character_id=source, target_character_id=target_response.json()["id"],
           relationship_type="同行", description="寻找灵脉")
    outline = create("outlines", project_id=project, summary="寻找灵脉", central_conflict="灵气衰退")
    volume = create("outline-volumes", outline_id=outline, volume_number=1, title="启程", summary="离乡")
    create("plot-beats", volume_id=volume, sort_order=1, title="遇险", summary="山中遭遇")
    create("chapters", project_id=project, volume_id=volume, chapter_number=1, title="初行")
    return rows


def test_login_logout_expiry_disabled_account_and_password_storage(setup_accounts):
    engine, client, users = setup_accounts
    user = users["admin"]
    assert client.get("/api/v1/auth/me", headers=headers(user)).json() == user["account"]
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong-password"}).status_code == 401
    assert client.post("/api/v1/auth/register", json={"username": "ADMIN", "password": "test-password"}).status_code == 409
    login = client.post("/api/v1/auth/login", json={"username": " ADMIN ", "password": "test-password"})
    assert login.status_code == 200 and login.headers["cache-control"] == "no-store"
    with engine.begin() as connection:
        stored = connection.execute(select(account).where(account.c.id == user["account"]["id"])).mappings().one()
        assert stored["password_hash"].startswith("scrypt$") and "test-password" not in stored["password_hash"]
        assert connection.scalar(select(auth_session.c.token_hash).where(
            auth_session.c.token_hash == user["access_token"])) is None
    assert client.post("/api/v1/auth/logout", headers=headers(user)).status_code == 204
    assert client.get("/api/v1/auth/me", headers=headers(user)).status_code == 401
    renewed = login.json()
    with engine.begin() as connection:
        connection.execute(update(auth_session).where(auth_session.c.token_hash == token_hash(renewed["access_token"])).values(
            expires_at=utcnow() - timedelta(seconds=1)))
    assert client.get("/api/v1/projects", headers=headers(renewed)).status_code == 401
    with engine.begin() as connection:
        connection.execute(update(account).where(account.c.username == "writer").values(is_active=False))
    assert client.get("/api/v1/auth/me", headers=headers(users["writer"])).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "writer", "password": "test-password"}).status_code == 401


def test_unauthenticated_endpoints_reject_requests_before_database_lookup(setup_accounts):
    _, client, _ = setup_accounts
    for path in ["/projects", "/worlds", "/chapters", "/auth/me", "/access"]:
        assert client.get("/api/v1" + path).status_code == 401
    assert client.post("/api/v1/novel-assistant/sessions").status_code == 401
    assert client.post("/api/v1/writing/chapters/1/draft/stream", json={}).status_code == 401
    assert client.get("/").status_code == 200 and client.get("/healthz").status_code == 200


def test_registration_can_be_closed_without_disabling_existing_accounts(setup_accounts, monkeypatch):
    from types import SimpleNamespace
    _, client, _ = setup_accounts
    monkeypatch.setattr(account_api, 'get_settings', lambda: SimpleNamespace(
        AUTH_REGISTRATION_ENABLED=False, AUTH_SESSION_HOURS=168))
    assert client.post('/api/v1/auth/register', json={'username': 'newuser', 'password': 'test-password'}).status_code == 403
    assert client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'test-password'}).status_code == 200


def test_change_password_revokes_all_own_sessions_and_preserves_novels(setup_accounts):
    engine, client, users = setup_accounts
    user = users['admin']
    project = client.post('/api/v1/projects', headers=headers(user), json=PROJECT).json()
    second = client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'test-password'}).json()
    response = client.post('/api/v1/auth/password', headers=headers(user), json={
        'current_password': 'test-password', 'new_password': 'updated-password'})
    assert response.status_code == 204 and response.headers['cache-control'] == 'no-store'
    for session in (user, second):
        assert client.get('/api/v1/auth/me', headers=headers(session)).status_code == 401
    assert client.get('/api/v1/auth/me', headers=headers(users['writer'])).status_code == 200
    assert client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'test-password'}).status_code == 401
    login = client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'updated-password'})
    assert login.status_code == 200
    assert client.get(f"/api/v1/projects/{project['id']}", headers=headers(login.json())).json() == project
    with engine.connect() as connection:
        stored = connection.scalar(select(account.c.password_hash).where(account.c.id == user['account']['id']))
        assert stored != 'updated-password' and verify_password('updated-password', stored)


def test_password_change_rejects_wrong_password_weak_password_and_foreign_target(setup_accounts):
    _, client, users = setup_accounts
    path = '/api/v1/auth/password'
    data = {'current_password': 'test-password', 'new_password': 'updated-password'}
    assert client.post(path, json=data).status_code == 401
    assert client.post(path, headers=headers(users['admin']), json={**data,
        'current_password': 'wrong-password'}).status_code == 400
    assert client.post(path, headers=headers(users['admin']), json={**data,
        'new_password': 'short'}).status_code == 422
    assert client.post(path, headers=headers(users['admin']), json={**data,
        'username': 'writer'}).status_code == 422
    for name in ('admin', 'writer'):
        assert client.get('/api/v1/auth/me', headers=headers(users[name])).status_code == 200
        assert client.post('/api/v1/auth/login', json={'username': name, 'password': 'test-password'}).status_code == 200


def test_admin_password_reset_targets_only_existing_account(setup_accounts):
    engine, client, users = setup_accounts
    second = client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'test-password'}).json()
    assert reset_password(engine, ' ADMIN ', 'recovered-password') == 2
    for session in (users['admin'], second):
        assert client.get('/api/v1/auth/me', headers=headers(session)).status_code == 401
    assert client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'test-password'}).status_code == 401
    assert client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'recovered-password'}).status_code == 200
    assert client.get('/api/v1/auth/me', headers=headers(users['writer'])).status_code == 200
    with pytest.raises(ValueError, match='账号不存在'):
        reset_password(engine, 'missing', 'recovered-password')
    with pytest.raises(ValueError, match='8—128'):
        reset_password(engine, 'writer', 'short')
    with engine.begin() as connection:
        assert connection.scalar(select(account.c.id).where(account.c.username == 'missing')) is None
        connection.execute(update(account).where(account.c.username == 'writer').values(is_active=False))
    reset_password(engine, 'writer', 'disabled-password')
    assert client.post('/api/v1/auth/login', json={'username': 'writer', 'password': 'disabled-password'}).status_code == 401


def test_admin_reset_confirmation_failure_does_not_touch_database(monkeypatch, capsys):
    from db import reset_password as command
    answers = iter(['recovered-password', 'different-password'])
    monkeypatch.setattr('sys.argv', ['reset_password', 'admin'])
    monkeypatch.setattr(command, 'getpass', lambda _: next(answers))
    monkeypatch.setattr(command, 'get_engine', lambda: pytest.fail('must not open a database'))
    with pytest.raises(SystemExit) as exc:
        command.main()
    assert exc.value.code == 2
    output = capsys.readouterr()
    assert '两次密码不一致' in output.err
    assert 'recovered-password' not in output.out + output.err


def test_all_fifteen_resources_are_isolated_for_lists_reads_updates_and_deletes(setup_accounts):
    _, client, users = setup_accounts
    own = graph(client, users["admin"])
    other = graph(client, users["writer"])
    for resource in CRUD_RESOURCES:
        path = resource.path.lstrip("/")
        mine, theirs = own[path], other[path]
        page = client.get("/api/v1/" + path, headers=headers(users["admin"])).json()
        assert mine["id"] in [row["id"] for row in page["items"]]
        assert theirs["id"] not in [row["id"] for row in page["items"]]
        item_url = "/api/v1/" + path + "/" + str(theirs["id"])
        change = next((key, theirs[key]) for key in resource.create_schema.model_fields if key != resource.owner)
        for method in ("get", "patch", "delete"):
            kwargs = {"json": dict([change])} if method == "patch" else {}
            assert getattr(client, method)(item_url, headers=headers(users["admin"]), **kwargs).status_code == 404, path
        mine_url = "/api/v1/" + path + "/" + str(mine["id"])
        assert client.get(mine_url, headers=headers(users["admin"])).status_code == 200
    assert own["projects"]["account_id"] == users["admin"]["account"]["id"]
    assert own["design-sessions"]["account_id"] == users["admin"]["account"]["id"]
    assert client.patch('/api/v1/projects/' + str(own['projects']['id']), headers=headers(users['admin']),
                        json={'account_id': users['writer']['account']['id']}).status_code == 422
    assert client.post('/api/v1/projects', headers=headers(users['admin']),
                       json={**PROJECT, 'account_id': users['writer']['account']['id']}).status_code == 422


def test_foreign_links_cannot_attach_resources_from_another_account(setup_accounts):
    _, client, users = setup_accounts
    own = graph(client, users["admin"])
    other = graph(client, users["writer"])
    cases = [
        ('worlds', {'project_id': other['projects']['id'], 'summary': 'x', 'era': 'x'}),
        ('characters', {'project_id': own['projects']['id'], 'faction_id': other['factions']['id'],
                        'name': '陌生人', 'role': '主角', 'summary': 'x'}),
        ('chapters', {'project_id': own['projects']['id'], 'volume_id': other['outline-volumes']['id'], 'chapter_number': 2}),
        ('character-relationships', {'source_character_id': own['characters']['id'],
                                    'target_character_id': other['characters']['id'], 'relationship_type': '敌对', 'description': 'x'}),
        ('design-sessions', {'project_id': other['projects']['id']}),
        ('power-ranks', {'power_system_id': other['power-systems']['id'], 'rank_order': 2, 'name': '筑基'}),
    ]
    for path, payload in cases:
        assert client.post('/api/v1/' + path, headers=headers(users['admin']), json=payload).status_code == 404
    for path, field, value in [('characters', 'faction_id', other['factions']['id']),
                               ('chapters', 'volume_id', other['outline-volumes']['id']),
                               ('design-sessions', 'project_id', other['projects']['id'])]:
        assert client.patch('/api/v1/' + path + '/' + str(own[path]['id']), headers=headers(users['admin']),
                            json={field: value}).status_code == 404


def test_writing_rejects_foreign_ids_before_model_or_mcp_work(setup_accounts, monkeypatch):
    _, client, users = setup_accounts
    other = graph(client, users['writer'])
    def forbidden(*args):
        pytest.fail('Unowned resource reached MCP/model generation')
    monkeypatch.setattr(writing, 'load_context', forbidden)
    for path in [f"projects/{other['projects']['id']}/outline", f"volumes/{other['outline-volumes']['id']}/chapter-plan",
                 f"chapters/{other['chapters']['id']}/draft", f"chapters/{other['chapters']['id']}/review"]:
        for suffix in ['', '/stream']:
            assert client.post('/api/v1/writing/' + path + suffix, json={}, headers=headers(users['admin'])).status_code == 404


def test_questionnaire_sessions_belong_to_their_creator(setup_accounts):
    _, client, users = setup_accounts
    created = client.post('/api/v1/novel-assistant/sessions', headers=headers(users['admin'])).json()
    path = '/api/v1/novel-assistant/sessions/' + created['session_id']
    assert client.get(path, headers=headers(users['writer'])).status_code == 404
    for suffix in ['/messages', '/messages/stream']:
        assert client.post(path + suffix, json={'content': '确认生成'}, headers=headers(users['writer'])).status_code == 404
    assert client.post(path + '/messages', json={'content': '仙侠'}, headers=headers(users['admin'])).json()['values'] == {'genre': '仙侠'}


def test_idempotent_migration_assigns_only_unowned_novels_to_admin(setup_accounts):
    engine, client, users = setup_accounts
    graph(client, users['writer'])
    with engine.begin() as connection:
        old_id = connection.execute(insert(tables['novel_project']).values(**PROJECT)).lastrowid
    migrate_schema(engine)
    migrate_schema(engine)
    assert client.get('/api/v1/projects/' + str(old_id), headers=headers(users['admin'])).status_code == 404
    assert bind_legacy(engine, 'admin') == {'projects': 1, 'sessions': 0}
    assert bind_legacy(engine, 'admin') == {'projects': 0, 'sessions': 0}
    assert client.get('/api/v1/projects/' + str(old_id), headers=headers(users['admin'])).status_code == 200
    assert len(client.get('/api/v1/projects', headers=headers(users['writer'])).json()['items']) == 1


def test_migration_upgrades_tables_that_do_not_yet_have_account_columns():
    engine = create_engine('sqlite://')
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE novel_project (id INTEGER PRIMARY KEY)')
            connection.exec_driver_sql('CREATE TABLE novel_design_session (id TEXT PRIMARY KEY, project_id INTEGER)')
            connection.exec_driver_sql('INSERT INTO novel_project (id) VALUES (2)')
            connection.exec_driver_sql("INSERT INTO novel_design_session (id,project_id) VALUES ('old',2)")
        migrate_schema(engine)
        migrate_schema(engine)
        assert 'account_id' in {c['name'] for c in inspect(engine).get_columns('novel_project')}
        create_account(engine, 'admin', 'test-password')
        with pytest.raises(ValueError) as error:
            create_account(engine, 'bad', 'short')
        assert 'short' not in str(error.value)
        assert bind_legacy(engine, 'admin') == {'projects': 1, 'sessions': 1}
        assert bind_legacy(engine, 'admin') == {'projects': 0, 'sessions': 0}
    finally:
        engine.dispose()


def test_mcp_uses_the_current_account_and_refuses_other_accounts_data(setup_accounts):
    _, client, users = setup_accounts
    other = graph(client, users['writer'])
    async def run():
        server = create_server('http://aitxt.test/api/v1', transport=httpx.ASGITransport(app=app),
                               auth_token=users['admin']['access_token'])
        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool('list_novel_project', {})
            assert not result.isError and result.structuredContent['items'] == []
            result = await session.call_tool('read_novel_project', {'record_id': other['projects']['id']})
            assert result.isError and '404' in result.content[0].text
            result = await session.call_tool('create_novel_project', {'payload': PROJECT})
            assert not result.isError and result.structuredContent['account_id'] == users['admin']['account']['id']
    asyncio.run(run())
    token = AUTH_TOKEN.set(users['writer']['access_token'])
    try:
        assert server_parameters().env['AITXT_AUTH_TOKEN'] == users['writer']['access_token']
    finally:
        AUTH_TOKEN.reset(token)


def test_worldbuilding_generation_retains_account_through_background_stream_and_mcp(setup_accounts, monkeypatch):
    _, client, users = setup_accounts
    from test_worldbuilding_generation import BLUEPRINT, VALUES
    from db.models import NovelBlueprint
    monkeypatch.setattr(worldbuilding_agent.WorldbuildingAssistant, '_generate_with_crewai',
                        staticmethod(lambda values: NovelBlueprint.model_validate(BLUEPRINT)))

    @asynccontextmanager
    async def mcp_session():
        server = create_server('http://aitxt.test/api/v1', transport=httpx.ASGITransport(app=app), auth_token=AUTH_TOKEN.get())
        async with create_connected_server_and_client_session(server) as session:
            yield session
    monkeypatch.setattr(worldbuilding_agent, 'worldbuilding_session', mcp_session)
    assistant._sessions['owned'] = worldbuilding_agent.ConversationState(
        account_id=users['admin']['account']['id'], values=VALUES.copy())
    response = client.post('/api/v1/novel-assistant/sessions/owned/messages/stream', json={'content': '确认生成'},
                           headers=headers(users['admin']))
    assert response.status_code == 200 and 'event: result' in response.text and 'event: error' not in response.text
    rows = client.get('/api/v1/projects', headers=headers(users['admin'])).json()['items']
    assert len(rows) == 1 and rows[0]['account_id'] == users['admin']['account']['id']
    assert client.get('/api/v1/projects', headers=headers(users['writer'])).json()['items'] == []
