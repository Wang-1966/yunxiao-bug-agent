from pathlib import Path
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import bugs
from src.config import Settings
from src.tools.yunxiao.openapi_client import YunxiaoClient


@pytest.mark.parametrize('remote_status', ['已关闭', '处理中'])
def test_sync_refreshes_tracked_bugs_after_owner_changes(tmp_path, monkeypatch, remote_status):
    path = tmp_path / 'bugs.db'
    with sqlite3.connect(path) as db:
        db.executescript(Path('src/db/init.sql').read_text())
        db.execute(
            "INSERT INTO bugs (bug_id,title,priority,source,source_status,status,repo_path,branch,assigned_agent_id) "
            "VALUES ('transferred','Bug','normal','yunxiao','待确认','pending','/repo','feature','agent')"
        )
        db.execute("INSERT INTO bugs (bug_id,title,priority,source) VALUES ('manual','Manual','normal','manual')")
    monkeypatch.setattr(bugs, 'settings', Settings(_env_file=None, database_url=f'sqlite+aiosqlite:///{path}'))
    fetched = []

    async def search(self):
        return []  # 转交他人后，该 Bug 不再出现在当前账号的负责人查询中。

    async def detail(self, bug_id):
        fetched.append(bug_id)
        return {'id': bug_id, 'subject': 'Bug', 'categoryId': 'Bug',
                'assignedTo': {'id': 'someone-else'}, 'status': {'name': remote_status},
                'space': {'id': 'project', 'name': '项目'}}

    monkeypatch.setattr(YunxiaoClient, 'list_my_bugs', search)
    monkeypatch.setattr(YunxiaoClient, 'get_bug', detail)
    app = FastAPI()
    app.include_router(bugs.router)
    client = TestClient(app)
    response = client.post('/api/bugs/sync')
    assert response.status_code == 200
    assert response.json() == {'total': 1, 'created': 0, 'updated': 1, 'unchanged': 0}
    refreshed = client.get('/api/bugs/transferred').json()
    assert refreshed['source_status'] == remote_status
    assert (refreshed['status'], refreshed['repo_path'], refreshed['branch'], refreshed['assigned_agent_id']) == (
        'pending', '/repo', 'feature', 'agent',
    )
    visible = client.get('/api/bugs', params={'exclude_closed_yunxiao': True}).json()
    assert {item['bug_id'] for item in visible['items']} == (
        {'manual'} if remote_status == '已关闭' else {'manual', 'transferred'}
    )
    assert client.post('/api/bugs/sync').json() == {'total': 1, 'created': 0, 'updated': 0, 'unchanged': 1}
    assert fetched == ['transferred', 'transferred']


def test_source_status_filter_before_pagination(tmp_path, monkeypatch):
    path = tmp_path / "bugs.db"
    with sqlite3.connect(path) as db:
        db.executescript(Path("src/db/init.sql").read_text())
        db.executemany(
            "INSERT INTO bugs (bug_id, title, priority, source, source_status) VALUES (?, 'Bug', 'normal', ?, ?)",
            [("closed", "yunxiao", "已关闭"), ("wontfix", "yunxiao", "暂不修复"),
             ("fixed", "yunxiao", "已修复"), ("open", "yunxiao", "处理中"),
             ("unknown", "yunxiao", None), ("manual", "manual", "已关闭")],
        )
    monkeypatch.setattr(bugs, "get_db_path", lambda: str(path))
    app = FastAPI()
    app.include_router(bugs.router)
    client = TestClient(app)
    assert client.get('/api/bugs').json()['total'] == 6
    visible = set()
    for page in (1, 2):
        result = client.get('/api/bugs', params={"exclude_closed_yunxiao": True, "page_size": 2, "page": page}).json()
        assert result['total'] == 3
        assert len(result['items']) == (2 if page == 1 else 1)
        visible.update(item['bug_id'] for item in result['items'])
    assert visible == {'open', 'unknown', 'manual'}
    assert client.get('/api/bugs/closed').status_code == 200
    assert client.get('/api/bugs/filters/source-statuses').json() == ['处理中']
    result = client.get('/api/bugs', params={
        'exclude_closed_yunxiao': True, 'source_status': '处理中', 'status': 'pending',
    }).json()
    assert result['total'] == 1
    assert result['items'][0]['bug_id'] == 'open'
    assert client.get('/api/bugs', params={'source_status': '处理中', 'status': 'resolved'}).json()['total'] == 0
    assert client.get('/api/bugs', params={'exclude_closed_yunxiao': True, 'source_status': '已关闭'}).json()['total'] == 0


def test_yunxiao_status_can_be_changed_from_the_list(tmp_path, monkeypatch):
    path = tmp_path / 'bugs.db'
    with sqlite3.connect(path) as db:
        db.executescript(Path('src/db/init.sql').read_text())
        db.execute("INSERT INTO bugs (bug_id, title, priority, source, source_status) VALUES ('open', 'Bug', 'normal', 'yunxiao', '待确认')")
        db.execute("INSERT INTO bugs (bug_id, title, priority, source) VALUES ('local', '手工', 'normal', 'manual')")
    monkeypatch.setattr(bugs, 'get_db_path', lambda: str(path))

    async def names(self, bug_id):
        assert bug_id == 'open'
        return ['待确认', '处理中', '已关闭']

    async def update(self, bug_id, status):
        assert status == '处理中'
        return {'status_id': 'doing', 'source_status': '处理中'}

    monkeypatch.setattr(YunxiaoClient, 'list_bug_status_names', names)
    monkeypatch.setattr(YunxiaoClient, 'set_bug_status', update)
    app = FastAPI()
    app.include_router(bugs.router)
    client = TestClient(app)
    assert client.get('/api/bugs/open/yunxiao-statuses').json() == {'statuses': ['待确认', '处理中', '已关闭']}
    assert client.put('/api/bugs/open/yunxiao-status', json={'status': '处理中'}).json() == {'source_status': '处理中'}
    assert client.get('/api/bugs/open').json()['source_status'] == '处理中'
    assert client.put('/api/bugs/local/yunxiao-status', json={'status': '处理中'}).status_code == 409
    assert client.put('/api/bugs/missing/yunxiao-status', json={'status': '处理中'}).status_code == 404


def test_dashboard_counts_all_visible_bugs_and_verified_repairs(tmp_path, monkeypatch):
    path = tmp_path / 'dashboard.db'
    with sqlite3.connect(path) as db:
        db.executescript(Path('src/db/init.sql').read_text())
        db.executemany("INSERT INTO bugs (bug_id,title,priority,source) VALUES (?, 'Bug', 'critical', 'manual')", [(str(i),) for i in range(25)])
        db.executemany(
            "INSERT INTO bugs (bug_id,title,priority,source,source_status,status,resolved_at) VALUES (?, 'Bug', 'critical', 'yunxiao', ?, ?, ?)",
            [('synced', '已关闭', 'resolved', None),
             ('accepted', '待确认', 'resolved', '2026-10-10 00:00:00'),
             ('accepted-closed', '已关闭', 'resolved', '2026-10-10 00:00:00'),
             ('rejected', '待确认', 'failed', None)],
        )
    monkeypatch.setattr(bugs, 'get_db_path', lambda: str(path))
    app = FastAPI()
    app.include_router(bugs.router)
    client = TestClient(app)
    assert client.get('/api/bugs/stats/dashboard').json() == {'total': 27, 'critical': 27, 'resolved': 2}
    listing = client.get('/api/bugs', params={'exclude_closed_yunxiao': True, 'page_size': 10}).json()
    assert listing['total'] == 27
    assert len(listing['items']) == 10
