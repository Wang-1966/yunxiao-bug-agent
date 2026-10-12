from pathlib import Path
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import bugs


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
