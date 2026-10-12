import subprocess
from pathlib import Path
from unittest.mock import patch

import aiosqlite
import pytest
from fastapi import HTTPException

from src.api import projects
from src.core.projects import resolve_project_config, project_id_from_url


@pytest.fixture
async def project_db(tmp_path, monkeypatch):
    path = tmp_path / 'projects.db'
    async with aiosqlite.connect(path) as db:
        await db.executescript(Path('src/db/init.sql').read_text())
        await db.executemany("INSERT INTO bugs (bug_id,title,priority,source,source_project_id,source_project) VALUES (?, 'Bug', 'normal', 'yunxiao', ?, '同名项目')", [('one', 'p1'), ('two', 'p2')])
        await db.commit()
    monkeypatch.setattr(projects, 'get_db_path', lambda: str(path))
    return path


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / 'repo with spaces'
    path.mkdir()
    subprocess.run(['git', 'init', '-b', 'main', str(path)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(path), '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '--allow-empty', '-m', 'init'], check=True, capture_output=True)
    return path


@pytest.mark.asyncio
async def test_binding_by_id_preserves_same_name_projects_and_bug_override(project_db, repo):
    config = projects.ProjectConfig(name='同名项目', repo_path=str(repo), branch='main', test_command='npm test')
    await projects.save_project('p1', config)
    listed = {p['project_id']: p for p in await projects.list_projects()}
    assert listed['p1']['repo_path'] == str(repo)
    assert listed['p2']['repo_path'] is None
    async with aiosqlite.connect(project_db) as db:
        mapped = await resolve_project_config(db, {'source': 'yunxiao', 'source_project_id': 'p1', 'repo_path': None})
        assert mapped['repo_path'] == str(repo)
        assert mapped['branch'] == 'main'
        assert mapped['test_command'] == 'npm test'
        overridden = await resolve_project_config(db, {'source': 'yunxiao', 'source_project_id': 'p1', 'repo_path': '/manual', 'branch': 'custom'})
        assert overridden['repo_path'] == '/manual'
        assert overridden['branch'] == 'custom'
    await projects.unbind_project('p1')
    assert len(await projects.list_projects()) == 2
    async with aiosqlite.connect(project_db) as db:
        assert not (await resolve_project_config(db, {'source_project_id': 'p1'})).get('repo_path')


@pytest.mark.asyncio
async def test_invalid_repository_or_branch_is_not_saved(project_db, repo):
    for path, branch in [('relative/path', 'main'), (str(repo), 'missing'), (str(repo), '--bad')]:
        with pytest.raises(HTTPException) as exc:
            await projects.save_project('p1', projects.ProjectConfig(name='project', repo_path=path, branch=branch))
        assert exc.value.status_code == 422
    assert all(p['repo_path'] is None for p in await projects.list_projects())


def test_legacy_url_project_identity():
    assert project_id_from_url('https://devops.aliyun.com/projex/project/project-id/bug/bug-id') == 'project-id'
    assert project_id_from_url('https://example.com/projex/project/project-id/bug/bug-id') is None
    assert project_id_from_url(None) is None


@pytest.mark.asyncio
async def test_migration_backfills_old_bugs_without_changing_repair_state(tmp_path):
    from src.db import database
    path = tmp_path / 'old.db'
    async with aiosqlite.connect(path) as db:
        await db.execute("CREATE TABLE bugs (id INTEGER PRIMARY KEY, bug_id TEXT UNIQUE, title TEXT, description TEXT, priority TEXT, status TEXT, source TEXT, source_url TEXT, repo_path TEXT, branch TEXT, assigned_agent_id TEXT, created_at TEXT, updated_at TEXT, resolved_at TEXT)")
        await db.execute("INSERT INTO bugs (bug_id, status, source, source_url, repo_path) VALUES ('old','fixing','yunxiao','https://devops.aliyun.com/projex/project/legacy/bug/old','/existing')")
        await db.commit()
    with patch.object(database.settings, 'database_url', f'sqlite+aiosqlite:///{path}'):
        await database.init_db()
        await database.init_db()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute('SELECT source_project_id, status, repo_path FROM bugs')
        assert await cursor.fetchone() == ('legacy', 'fixing', '/existing')
