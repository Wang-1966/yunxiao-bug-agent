import asyncio
import json
from unittest.mock import patch

import aiosqlite
import httpx
import pytest

from src.config import Settings
from src.core.bug_sync import run_yunxiao_sync_loop, sync_my_bugs
from src.tools.yunxiao.openapi_client import YunxiaoClient, YunxiaoError


@pytest.mark.asyncio
async def test_pagination_and_owner_filter():
    pages = []

    def respond(request):
        if request.method == "GET":
            return httpx.Response(200, json={"id": "me"})
        body = json.loads(request.content)
        assert json.loads(body["conditions"])["conditionGroups"][0][0]["value"] == ["me"]
        assert body["category"] == "Bug"
        pages.append(body["page"])
        return httpx.Response(200, json=[{"id": str(body["page"]), "subject": "Bug", "categoryId": "Bug", "assignedTo": {"id": "me"}}], headers={"x-next-page": str(body["page"] + 1), "x-total-pages": "2"})

    original = httpx.AsyncClient
    with patch("src.tools.yunxiao.openapi_client.httpx.AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(respond), **kw)):
        items = await YunxiaoClient(Settings(_env_file=None, yunxiao_access_token="token", yunxiao_org_id="org", yunxiao_project_id="project")).list_my_bugs()
    assert pages == [1, 2]
    assert len(items) == 2


@pytest.mark.asyncio
async def test_all_projects_scope():
    searched = []

    def respond(request):
        if request.method == "GET":
            return httpx.Response(200, json={"id": "me"})
        if request.url.path.endswith("projects:search"):
            return httpx.Response(200, json=[{"id": "a"}, {"id": "b"}], headers={"x-next-page": "2", "x-total-pages": "1"})
        body = json.loads(request.content)
        searched.append(body["spaceId"])
        return httpx.Response(200, json=[], headers={"x-next-page": "2", "x-total-pages": "0"})

    original = httpx.AsyncClient
    with patch("src.tools.yunxiao.openapi_client.httpx.AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(respond), **kw)):
        items = await YunxiaoClient(Settings(_env_file=None, yunxiao_access_token="token", yunxiao_org_id="org", yunxiao_project_id="*")).list_my_bugs()
    assert searched == ["a", "b"]
    assert items == []


@pytest.mark.asyncio
async def test_repeated_sync_preserves_repair_state(tmp_path):
    path = tmp_path / "db.sqlite"
    async with aiosqlite.connect(path) as db:
        await db.executescript(open("src/db/init.sql").read())
    settings = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{path}")
    items = [{"id": "bug", "subject": "first", "description": "description", "space": {"id": "project", "name": "项目"}}]

    async def fetch(_):
        return items

    with patch.object(YunxiaoClient, "list_my_bugs", fetch):
        assert (await sync_my_bugs(settings))["created"] == 1
        assert (await sync_my_bugs(settings))["unchanged"] == 1
        async with aiosqlite.connect(path) as db:
            await db.execute("UPDATE bugs SET status='fixing', repo_path='/repo', assigned_agent_id='agent', source_url=NULL WHERE bug_id='bug'")
            await db.commit()
        assert (await sync_my_bugs(settings))["updated"] == 1
        assert (await sync_my_bugs(settings))["unchanged"] == 1
        items[0]["subject"] = "updated"
        assert (await sync_my_bugs(settings))["updated"] == 1
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute("SELECT status,repo_path,assigned_agent_id,title,source_url FROM bugs")
        assert await cursor.fetchone() == ("fixing", "/repo", "agent", "updated", "https://devops.aliyun.com/projex/project/project/bug/bug")


@pytest.mark.asyncio
async def test_failure_does_not_report_success():
    def respond(request):
        return httpx.Response(403, json={"errorCode": "Forbidden.InvalidUser.UserNotInCurrentOrganization"})

    original = httpx.AsyncClient
    with patch("src.tools.yunxiao.openapi_client.httpx.AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(respond), **kw)):
        with pytest.raises(YunxiaoError, match="实际组织 ID"):
            await YunxiaoClient(Settings(_env_file=None, yunxiao_access_token="token", yunxiao_org_id="org", yunxiao_project_id="project")).list_my_bugs()


@pytest.mark.asyncio
async def test_missing_tracked_bug_refresh_failure_does_not_write_partial_sync(tmp_path):
    path = tmp_path / 'db.sqlite'
    async with aiosqlite.connect(path) as db:
        await db.executescript(open('src/db/init.sql').read())
        await db.execute("INSERT INTO bugs (bug_id,title,priority,source,source_status) VALUES ('transferred','Bug','normal','yunxiao','待确认')")
        await db.commit()
    settings = Settings(_env_file=None, database_url=f'sqlite+aiosqlite:///{path}')

    async def search(self):
        return [{'id': 'new', 'subject': 'New Bug'}]

    async def detail(self, bug_id):
        assert bug_id == 'transferred'
        raise YunxiaoError('当前账号无权访问')

    with patch.object(YunxiaoClient, 'list_my_bugs', search), patch.object(YunxiaoClient, 'get_bug', detail):
        with pytest.raises(YunxiaoError, match='transferred.*无权访问'):
            await sync_my_bugs(settings)
    async with aiosqlite.connect(path) as db:
        assert await (await db.execute('SELECT bug_id,source_status FROM bugs')).fetchall() == [('transferred', '待确认')]


@pytest.mark.asyncio
async def test_concurrent_sync_does_not_overlap(tmp_path):
    path = tmp_path / "db.sqlite"
    async with aiosqlite.connect(path) as db:
        await db.executescript(open("src/db/init.sql").read())
    settings = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{path}")
    active = 0
    peak = 0

    async def fetch(_self):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return [{"id": "bug", "subject": "t", "description": "", "space": {"id": "project", "name": "项目"}}]

    with patch.object(YunxiaoClient, "list_my_bugs", fetch):
        await asyncio.gather(sync_my_bugs(settings), sync_my_bugs(settings))
    assert peak == 1


@pytest.mark.asyncio
async def test_auto_sync_retries_after_failure_and_waits_for_config():
    settings = Settings(_env_file=None, yunxiao_sync_interval_seconds=300)
    calls = []

    async def fake(_settings):
        calls.append("sync")
        if len(calls) == 1:
            raise YunxiaoError("down")
        return {"total": 0, "created": 0, "updated": 0, "unchanged": 0}

    async def sleep(_seconds):
        if not settings.yunxiao_access_token:
            settings.yunxiao_access_token = "t"
            settings.yunxiao_org_id = "o"
            settings.yunxiao_project_id = "p"
            return
        if len(calls) >= 2:
            raise asyncio.CancelledError

    with patch("src.core.bug_sync.sync_my_bugs", fake):
        with pytest.raises(asyncio.CancelledError):
            await run_yunxiao_sync_loop(settings, sleep=sleep)
    assert calls == ["sync", "sync"]


@pytest.mark.asyncio
async def test_auto_sync_disabled_when_interval_not_positive():
    settings = Settings(
        _env_file=None,
        yunxiao_sync_interval_seconds=0,
        yunxiao_access_token="t",
        yunxiao_org_id="o",
        yunxiao_project_id="p",
    )

    async def fake(_settings):
        raise AssertionError("should not sync")

    with patch("src.core.bug_sync.sync_my_bugs", fake):
        await run_yunxiao_sync_loop(settings, sleep=fake)
