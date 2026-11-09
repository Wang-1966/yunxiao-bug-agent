import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from src.core import repair_executor as executor
from src.core.agent_tools import ToolError, Workspace


@pytest.fixture
def repository(tmp_path, monkeypatch):
    path = tmp_path / 'repo'
    path.mkdir()
    subprocess.run(['git', 'init', '-b', 'main', str(path)], check=True, capture_output=True)
    (path / 'app.py').write_text('value = 1\nassert value == 2\n')
    (path / 'AGENTS.md').write_text('Keep the public interface compatible.')
    subprocess.run(['git', '-C', str(path), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(path), '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-m', 'initial'], check=True, capture_output=True)
    monkeypatch.setattr(executor.get_settings(), 'worktrees_dir', str(tmp_path / 'worktrees'))
    return path


def edit(worktree, before, after):
    files = Workspace(worktree, mode='fix')
    files.read_file('app.py')
    files.edit_file('app.py', before, after)
    return files


@pytest.mark.asyncio
async def test_isolated_worktree_preserves_source_dirty_files_and_retries(repository):
    (repository / 'app.py').write_text('uncommitted user work\n')
    first = await executor.prepare_worktree({'repo_path': str(repository), 'branch': 'main'})
    second = await executor.prepare_worktree({'repo_path': str(repository), 'branch': 'main'})
    assert first['worktree_path'] != second['worktree_path']
    assert first['repair_branch'].startswith('agentfix/')
    assert first['repair_branch'] != second['repair_branch']
    edit(first['worktree_path'], 'value = 1', 'value = 2')
    assert (repository / 'app.py').read_text() == 'uncommitted user work\n'
    assert (Path(second['worktree_path']) / 'app.py').read_text().startswith('value = 1')
    result = await executor.verify_worktree(first['worktree_path'], f'{sys.executable} app.py')
    assert result['passed'] is True
    assert await executor.changed_files(first['worktree_path']) == ['app.py']


@pytest.mark.asyncio
async def test_verify_and_commit_repair_commits_only_verified_tree(repository):
    prepared = await executor.prepare_worktree({'repo_path': str(repository), 'branch': 'main'})
    worktree = Path(prepared['worktree_path'])
    edit(worktree, 'value = 1', 'value = 2')
    result = await executor.verify_and_commit_repair(worktree, f'{sys.executable} -c "assert open(\'app.py\').read().startswith(\'value = 2\')"', 'one', prepared['repair_branch'], prepared['base_commit'], bug_title='讲义历史\n样式异常')
    assert result['git_commit_hash'] == await executor.git(worktree, 'rev-parse', 'HEAD')
    assert await executor.git(worktree, 'status', '--porcelain') == ''
    assert await executor.git(worktree, 'diff', '--name-only', prepared['base_commit'], result['git_commit_hash']) == 'app.py'
    assert await executor.git(worktree, 'log', '-1', '--format=%B') == 'fix: 讲义历史 样式异常\n\nBug: one'


@pytest.mark.asyncio
async def test_failing_reproduction_blocks_an_otherwise_green_build(repository):
    prepared = await executor.prepare_worktree({'repo_path': str(repository), 'branch': 'main'})
    worktree = Path(prepared['worktree_path'])
    edit(worktree, 'value = 1', 'value = 2')
    result = await executor.verify_worktree(worktree, f'{sys.executable} app.py', f'{sys.executable} -c "raise SystemExit(3)"')
    assert result['passed'] is False
    assert result['repro_exit_code'] == 3
    assert '复现测试' in result['command']


@pytest.mark.parametrize('title', ['讲义历史样式异常', '很长的讲义历史样式异常' * 20,
                                  ' ..bad / title\\with : [symbols] ~^?* \n .lock ', ' /?* '])
async def test_repair_branch_is_readable_bounded_and_valid(repository, title):
    bug = {'repo_path': str(repository), 'bug_id': 'c790db24ab1f652a169f7280ea', 'title': title}
    first = await executor.prepare_worktree(bug)
    second = await executor.prepare_worktree(bug)
    branch = first['repair_branch']
    assert branch.startswith('agentfix/c790db24-')
    assert len(branch) <= len('agentfix/') + 8 + 1 + 24 + 1 + 12
    assert (await executor.run_process(['git', 'check-ref-format', '--branch', branch], repository))[0] == 0
    assert branch != second['repair_branch']
    assert await executor.git(first['worktree_path'], 'branch', '--show-current') == branch
    if title == '讲义历史样式异常':
        assert title in branch


@pytest.mark.asyncio
async def test_verification_mutating_source_cannot_be_committed(repository):
    prepared = await executor.prepare_worktree({'repo_path': str(repository)})
    worktree = Path(prepared['worktree_path'])
    (worktree / 'app.py').write_text('value = 2\n')
    command = f'''{sys.executable} -c "open('app.py', 'w').write('value = 3\\n')"'''
    with pytest.raises(ValueError, match='验证期间'):
        await executor.verify_and_commit_repair(worktree, command, 'one', prepared['repair_branch'], prepared['base_commit'])
    assert await executor.git(worktree, 'rev-parse', 'HEAD') == prepared['base_commit']


@pytest.mark.asyncio
async def test_diff_paths_reject_escape_secrets_and_symlinks(repository, tmp_path):
    worktree = (await executor.prepare_worktree({'repo_path': str(repository)}))['worktree_path']
    files = executor.RepositoryFiles(worktree)
    for name in ['../outside', '/tmp/outside', '.git/config', '.env', '.env.prod', 'node_modules/a']:
        with pytest.raises(ValueError):
            files.path(name)
    (Path(worktree) / 'link').symlink_to(tmp_path)
    with pytest.raises(ValueError):
        files.path('link/outside')


@pytest.mark.asyncio
async def test_failure_and_missing_command_never_pass(repository):
    worktree = (await executor.prepare_worktree({'repo_path': str(repository)}))['worktree_path']
    result = await executor.verify_worktree(worktree, f'{sys.executable} app.py')
    assert result['passed'] is False
    assert result['exit_code'] != 0
    with pytest.raises(ValueError, match='未配置验证命令'):
        await executor.verify_worktree(worktree, '')


@pytest.mark.asyncio
async def test_cancel_terminates_subprocess(repository, tmp_path):
    marker = tmp_path / 'late-write'
    command = f"import time; from pathlib import Path; time.sleep(1); Path({str(marker)!r}).write_text('bad')"
    task = asyncio.create_task(executor.run_process([sys.executable, '-c', command], repository))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(1.1)
    assert not marker.exists()


@pytest.mark.asyncio
async def test_graph_stops_before_analysis_when_repository_is_missing():
    from unittest.mock import AsyncMock, patch
    from src.agent.graph import create_bug_fix_graph, compile_graph

    with patch('src.api.bugs.get_bug_details', AsyncMock(return_value={'title': 'bug', 'description': ''})), patch('src.agent.nodes.LLMFactory.create') as factory:
        result = await compile_graph(create_bug_fix_graph()).ainvoke({'bug_id': 'one', 'error': None})
    assert '未配置修复仓库' in result['error']
    factory.assert_not_called()


def test_attempt_prompt_is_bounded():
    from src.core.repair_attempts import attempt_prompt
    prompt = attempt_prompt({'round': 1, 'root_cause': '因' * 5000, 'modified_files': [f'f{i}.ts' for i in range(50)], 'fix_summary': '说' * 5000})
    assert '共 5000 字符，已截断' in prompt and '等共 50 个文件' in prompt and 'f30.ts' not in prompt
    assert len(prompt) < 6000


def test_edit_requires_a_read_and_rejects_business_files_during_analysis(repository):
    fixing = Workspace(repository, mode='fix')
    with pytest.raises(ToolError, match='read_file'):
        fixing.edit_file('app.py', 'value = 1', 'value = 2')
    files = Workspace(repository, mode='analyze')
    files.read_file('app.py')
    with pytest.raises(ToolError, match='业务源码'):
        files.edit_file('app.py', 'value = 1', 'value = 2')
    assert (repository / 'app.py').read_text().startswith('value = 1')
    files.edit_file('test_repro.py', '', 'assert False\n')
    assert (repository / 'test_repro.py').read_text() == 'assert False\n'


@pytest.mark.asyncio
async def test_worktree_copies_matching_dependencies_without_sharing_writes(repository):
    (repository / 'package.json').write_text('{"dependencies":{"demo":"1.0.0"}}')
    (repository / '.gitignore').write_text('node_modules/\n')
    await executor.git(repository, 'add', 'package.json', '.gitignore')
    await executor.git(repository, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-m', 'node project')
    modules = repository / 'node_modules'
    package = modules / '.pnpm/demo/node_modules/demo'
    package.mkdir(parents=True)
    (package / 'index.js').write_text('original')
    (modules / 'demo').symlink_to('.pnpm/demo/node_modules/demo')
    prepared = await executor.prepare_worktree({'repo_path': str(repository)})
    worktree = Path(prepared['worktree_path'])
    assert (worktree / 'node_modules/demo/index.js').read_text() == 'original'
    (worktree / 'node_modules/demo/index.js').write_text('isolated')
    assert (package / 'index.js').read_text() == 'original'
    assert await executor.prepare_worktree_dependencies(repository, worktree) is False
    assert (worktree / 'node_modules/demo/index.js').read_text() == 'isolated'
    assert await executor.changed_files(worktree) == []


@pytest.mark.asyncio
async def test_dependency_copy_rejects_mismatch_and_external_links(repository, tmp_path):
    prepared = await executor.prepare_worktree({'repo_path': str(repository)})
    worktree = Path(prepared['worktree_path'])
    for root in (repository, worktree):
        (root / 'package.json').write_text('{"dependencies":{}}')
        (root / '.gitignore').write_text('node_modules/\n')
    modules = repository / 'node_modules'
    modules.mkdir()
    (repository / 'package.json').write_text('{"dependencies":{"other":"1"}}')
    assert await executor.prepare_worktree_dependencies(repository, worktree) is False
    (repository / 'package.json').write_text('{"dependencies":{}}')
    (modules / 'external').symlink_to(tmp_path)
    assert await executor.prepare_worktree_dependencies(repository, worktree) is False
    assert not (worktree / 'node_modules').exists()


@pytest.mark.asyncio
async def test_failed_dependency_copy_leaves_no_partial_install(repository, monkeypatch):
    worktree = Path((await executor.prepare_worktree({'repo_path': str(repository)}))['worktree_path'])
    for root in (repository, worktree):
        (root / 'package.json').write_text('{}')
        (root / '.gitignore').write_text('node_modules/\n')
    (repository / 'node_modules').mkdir()
    run = executor.run_process

    async def fail_copy(args, cwd, **kwargs):
        if args[0] == 'cp':
            Path(args[-1]).mkdir()
            (Path(args[-1]) / 'partial').write_text('incomplete')
            return 1, 'copy failed'
        return await run(args, cwd, **kwargs)

    monkeypatch.setattr(executor, 'run_process', fail_copy)
    assert await executor.prepare_worktree_dependencies(repository, worktree) is False
    assert not (worktree / 'node_modules').exists()
    assert not list(worktree.glob('.repair-deps-*'))
