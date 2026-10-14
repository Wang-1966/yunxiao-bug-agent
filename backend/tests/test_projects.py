import asyncio
import shlex
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import aiosqlite
import httpx
import pytest
from fastapi import FastAPI, HTTPException

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


@pytest.mark.asyncio
async def test_branch_endpoint_lists_only_local_branches_without_changing_repository(repo):
    repo = repo.rename(repo.parent / '中文仓库 with spaces')
    for name in ['develop', 'feature/中文', 'hotfix/handout-0928']:
        subprocess.run(['git', '-C', str(repo), 'branch', name], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(repo), 'tag', 'main'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(repo), 'update-ref', 'refs/remotes/origin/remote-only', 'HEAD'], check=True, capture_output=True)
    (repo / 'unfinished.txt').write_text('keep my changes')
    app = FastAPI()
    app.include_router(projects.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/projects/branches', params={'repo_path': str(repo)})
        assert response.status_code == 200
        assert response.json() == {
            'repo_path': str(repo.resolve()), 'current_branch': 'main',
            'branches': ['main', 'develop', 'feature/中文', 'hotfix/handout-0928'],
        }
        assert (await client.get('/api/projects/branches')).status_code == 422
    assert (repo / 'unfinished.txt').read_text() == 'keep my changes'
    assert subprocess.check_output(['git', '-C', str(repo), 'symbolic-ref', 'HEAD']).strip() == b'refs/heads/main'


@pytest.mark.asyncio
async def test_detached_head_still_lists_local_branches(repo):
    subprocess.run(['git', '-C', str(repo), 'checkout', '--detach', 'HEAD'], check=True, capture_output=True)
    result = await projects.list_branches(str(repo))
    assert result['current_branch'] is None
    assert result['branches'] == ['main']


@pytest.mark.asyncio
async def test_unborn_repository_has_no_selectable_branches(tmp_path):
    subprocess.run(['git', 'init', '-b', 'main', str(tmp_path)], check=True, capture_output=True)
    result = await projects.list_branches(str(tmp_path))
    assert result['branches'] == []
    assert result['current_branch'] is None


@pytest.mark.asyncio
async def test_branch_lookup_and_save_reject_the_same_invalid_paths(project_db, repo, tmp_path):
    subdirectory = repo / 'nested'
    subdirectory.mkdir()
    for path in ['relative/path', '', str(tmp_path / 'missing'), str(tmp_path), str(subdirectory)]:
        with pytest.raises(HTTPException) as lookup:
            await projects.list_branches(path)
        with pytest.raises(HTTPException) as save:
            await projects.save_project('p1', projects.ProjectConfig(name='project', repo_path=path or ' ', branch='main'))
        assert lookup.value.status_code == save.value.status_code == 422
        assert lookup.value.detail == save.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['timeout', 'git_missing', 'branches_failed', 'head_failed'])
async def test_branch_lookup_reports_git_failures(repo, monkeypatch, failure):
    original = projects.git_output

    async def failing_git(path, *args):
        if args[0] == 'for-each-ref':
            if failure == 'timeout':
                raise asyncio.TimeoutError()
            if failure == 'git_missing':
                raise FileNotFoundError('git')
            if failure == 'branches_failed':
                return 128, ''
        if args[0] == 'symbolic-ref' and failure == 'head_failed':
            return 128, ''
        return await original(path, *args)

    monkeypatch.setattr(projects, 'git_output', failing_git)
    with pytest.raises(HTTPException) as exc:
        await projects.list_branches(str(repo))
    assert exc.value.status_code == 422
    assert '无法读取' in exc.value.detail


@pytest.mark.asyncio
async def test_saving_nonlocal_git_references_remains_supported(project_db, repo):
    subprocess.run(['git', '-C', str(repo), 'tag', 'release-v1'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(repo), 'update-ref', 'refs/remotes/origin/main', 'HEAD'], check=True, capture_output=True)
    commit = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD']).decode().strip()
    for ref in ['origin/main', 'release-v1', commit]:
        await projects.save_project('p1', projects.ProjectConfig(name='project', repo_path=str(repo), branch=ref))
        listed = {project['project_id']: project for project in await projects.list_projects()}
        assert listed['p1']['branch'] == ref


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


async def test_preview_config_persists_without_changing_bug_overrides(project_db, repo):
    config = projects.ProjectConfig(name='project', repo_path=str(repo), branch='main',
                                    preview_command='pnpm dev --port {port} --strictPort', preview_env_files=['.env.local'], preview_port=3000)
    await projects.save_project('p1', config)
    # 旧调用方未传新字段时保留已有预览配置。
    await projects.save_project('p1', projects.ProjectConfig(name='renamed', repo_path=str(repo), branch='main'))
    listed = {p['project_id']: p for p in await projects.list_projects()}
    assert listed['p1']['preview_command'] == config.preview_command
    assert listed['p1']['preview_env_files'] == ['.env.local']
    assert listed['p1']['preview_port'] == 3000
    async with aiosqlite.connect(project_db) as db:
        same_repo = await resolve_project_config(db, {'source_project_id': 'p1', 'repo_path': str(repo), 'branch': 'override'})
        assert same_repo['branch'] == 'override' and same_repo['preview_env_files'] == ['.env.local']
        assert same_repo['preview_port'] == 3000
        other_repo = await resolve_project_config(db, {'source_project_id': 'p1', 'repo_path': '/other'})
        assert 'preview_command' not in other_repo
    with pytest.raises(HTTPException) as invalid:
        await projects.save_project('p1', projects.ProjectConfig(name='project', repo_path=str(repo), branch='main', preview_command='pnpm dev'))
    assert invalid.value.status_code == 422


async def test_preview_migration_preserves_existing_project(tmp_path, monkeypatch):
    from src.db import database
    path = tmp_path / 'old.db'
    async with aiosqlite.connect(path) as db:
        await db.execute("CREATE TABLE projects (project_id TEXT PRIMARY KEY, name TEXT NOT NULL, repo_path TEXT NOT NULL, branch TEXT NOT NULL, test_command TEXT NOT NULL DEFAULT '', updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await db.execute("INSERT INTO projects (project_id, name, repo_path, branch, test_command) VALUES ('old', 'existing', '/repo', 'main', 'pnpm test')")
        await db.commit()
    monkeypatch.setattr(database.settings, 'database_url', f'sqlite+aiosqlite:///{path}')
    await database.init_db()
    await database.init_db()  # 重复启动也保持幂等。
    async with aiosqlite.connect(path) as db:
        row = await (await db.execute("SELECT name, test_command, preview_command, preview_env_files, preview_port FROM projects WHERE project_id='old'")).fetchone()
    assert row == ('existing', 'pnpm test', '', '[]', None)


async def test_development_api_and_running_configuration_guard(project_db, repo, tmp_path):
    from src.core import preview_manager

    port = preview_manager._free_port()
    config = dict(name='project', repo_path=str(repo), branch='main', preview_port=port,
                  preview_command=f'{shlex.quote(sys.executable)} -u -m http.server {{port}} --bind 127.0.0.1')
    other = tmp_path / 'other'
    subprocess.run(['git', 'clone', str(repo), str(other)], check=True, capture_output=True)
    app = FastAPI()
    app.include_router(projects.router)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            endpoint = '/api/projects/p1/development'
            assert (await client.get('/api/projects/missing/development')).status_code == 404
            assert (await client.put('/api/projects/p1', json=config)).status_code == 200
            assert (await client.get(endpoint)).json()['status'] == 'stopped'
            started = await client.post(endpoint)
            assert started.status_code == 200 and started.json()['port'] == port
            async with asyncio.timeout(10):
                while True:
                    current = (await client.get(endpoint)).json()
                    assert current['status'] != 'failed', current
                    if current['status'] == 'running':
                        break
                    await asyncio.sleep(0.05)
            async with httpx.AsyncClient() as browser:
                assert (await browser.get(f'http://localhost:{port}/?id=kept')).status_code == 200
            # 描述字段可以保存，未传预览字段必须保留运行中的配置。
            assert (await client.put('/api/projects/p1', json={
                'name': 'renamed', 'repo_path': str(repo), 'branch': 'main',
            })).status_code == 200
            for changes in ({'preview_port': None}, {'preview_port': port + 1},
                            {'preview_command': config['preview_command'] + ' --directory .'},
                            {'repo_path': str(other)}):
                assert (await client.put('/api/projects/p1', json={**config, **changes})).status_code == 409
            assert (await client.delete('/api/projects/p1')).status_code == 409
            stopped = await client.delete(endpoint)
            assert stopped.status_code == 200 and stopped.json()['status'] == 'stopped'
            with pytest.raises(OSError):
                _, writer = await asyncio.open_connection('127.0.0.1', port)
                writer.close()
            assert (await client.put('/api/projects/p1', json={**config, 'preview_port': None})).status_code == 200
            assert (await client.post(endpoint)).status_code == 409
            assert (await client.delete('/api/projects/p1')).status_code == 200
    finally:
        await preview_manager.stop_all()
