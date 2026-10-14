import asyncio
import os
import shlex
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from src.core import preview_manager as previews
from src.core.repair_executor import git, prepare_worktree


@pytest.fixture
async def repository(tmp_path, monkeypatch):
    repo = tmp_path / 'repository with spaces'
    repo.mkdir()
    subprocess.run(['git', 'init', '-b', 'main', str(repo)], check=True, capture_output=True)
    (repo / '.gitignore').write_text('.env.local\n')
    (repo / 'page.txt').write_text('verified repair')
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-m', 'initial'], check=True, capture_output=True)
    monkeypatch.setattr(previews.get_settings(), 'worktrees_dir', str(tmp_path / 'worktrees'))
    yield repo
    await previews.stop_all()


async def start(repo, agent_id='one', command=None, env_files=None, public_port=None):
    prepared = await prepare_worktree({'repo_path': str(repo), 'branch': 'main'})
    result = await previews.start_preview(
        agent_id, worktree_path=prepared['worktree_path'], repo_path=str(repo),
        command=command or f'{shlex.quote(sys.executable)} -u -m http.server {{port}} --bind 127.0.0.1',
        env_files=env_files or [], commit=prepared['base_commit'], branch=prepared['repair_branch'], public_port=public_port,
    )
    return prepared, result


async def wait_status(agent_id, expected):
    async with asyncio.timeout(10):
        while True:
            result = await previews.preview_status(agent_id)
            if result['status'] == expected:
                return result
            if result['status'] == 'failed' and expected != 'failed':
                pytest.fail(result['error'] + result['output'])
            await asyncio.sleep(0.05)


async def fetch(port, path='/page.txt'):
    reader, writer = await asyncio.open_connection('127.0.0.1', port)
    writer.write(f'GET {path} HTTP/1.0\r\nHost: localhost\r\n\r\n'.encode())
    await writer.drain()
    result = await reader.read()
    writer.close()
    await writer.wait_closed()
    return result


async def assert_port_closed(port):
    with pytest.raises(OSError):
        _, writer = await asyncio.open_connection('127.0.0.1', port)
        writer.close()


async def test_preview_serves_worktree_reuses_service_and_switches_exclusively(repository):
    prepared, result = await start(repository)
    first = await wait_status('one', 'running')
    assert b'verified repair' in await fetch(first['port'])
    reused = await previews.start_preview(
        'one', worktree_path=prepared['worktree_path'], repo_path=str(repository),
        command=f'{shlex.quote(sys.executable)} -m http.server {{port}}', env_files=[],
        commit=prepared['base_commit'], branch=prepared['repair_branch'],
    )
    assert reused['port'] == result['port']
    await asyncio.gather(start(repository, 'two'), start(repository, 'three'))
    snapshots = [await previews.preview_status(agent) for agent in ('one', 'two', 'three')]
    assert sum(item['status'] in ('starting', 'running') for item in snapshots) == 1
    await assert_port_closed(first['port'])
    await previews.stop_all()
    for item in snapshots:
        await assert_port_closed(item['port'])
    assert await git(repository, 'branch', '--show-current') == 'main'


async def test_environment_copy_preserves_existing_files_and_rejects_escape(repository, tmp_path):
    (repository / '.env.local').write_text('LOCAL_SETTING=initial')
    prepared, _ = await start(repository, env_files=['.env.local'])
    await wait_status('one', 'running')
    target = Path(prepared['worktree_path']) / '.env.local'
    assert target.read_text() == 'LOCAL_SETTING=initial'
    assert target.stat().st_mode & 0o777 == 0o600
    assert await git(prepared['worktree_path'], 'status', '--porcelain') == ''
    await previews.stop_preview('one')
    target.write_text('LOCAL_SETTING=custom')
    await previews._copy_environment(repository, Path(prepared['worktree_path']), ['.env.local'])
    assert target.read_text() == 'LOCAL_SETTING=custom'
    with pytest.raises(ValueError, match='Git 忽略'):
        await previews._copy_environment(repository, Path(prepared['worktree_path']), ['page.txt'])
    target.unlink()
    target.symlink_to(tmp_path / 'outside')
    with pytest.raises(ValueError, match='符号链接'):
        await previews._copy_environment(repository, Path(prepared['worktree_path']), ['.env.local'])
    with pytest.raises(ValueError, match='相对文件路径'):
        previews.validate_preview_config('dev --port {port}', ['../outside'])


async def test_startup_failure_logs_and_timeout_are_reported(repository, monkeypatch):
    await start(repository, command="printf 'preview failed at {port}'; exit 7")
    result = await wait_status('one', 'failed')
    await previews._previews['one'].task
    assert '退出码 7' in result['error']
    assert 'preview failed' in (await previews.preview_status('one'))['output']
    monkeypatch.setattr(previews, 'START_TIMEOUT', 0.1)
    await start(repository, 'two', command='echo {port}; sleep 30')
    result = await wait_status('two', 'failed')
    await previews._previews['two'].task
    assert '未就绪' in result['error']
    await assert_port_closed(result['port'])


async def test_dirty_or_changed_worktree_is_rejected(repository):
    prepared = await prepare_worktree({'repo_path': str(repository), 'branch': 'main'})
    (Path(prepared['worktree_path']) / 'page.txt').write_text('unverified modification')
    with pytest.raises(ValueError, match='已验证提交'):
        await previews.start_preview(
            'one', worktree_path=prepared['worktree_path'], repo_path=str(repository),
            command='dev --port {port}', env_files=[], commit=prepared['base_commit'], branch=prepared['repair_branch'],
        )
    assert (await previews.preview_status('one'))['status'] == 'stopped'


async def test_managed_service_disables_automatic_browser_open(repository, monkeypatch):
    monkeypatch.setenv('BROWSER', 'some-browser')
    script = 'import os, http.server; print("BROWSER=" + os.environ.get("BROWSER", ""), flush=True); http.server.test(HandlerClass=http.server.SimpleHTTPRequestHandler, bind="127.0.0.1", port=int(os.environ["PORT"]))'
    command = f'{shlex.quote(sys.executable)} -u -c {shlex.quote(script)} # {{port}}'
    await start(repository, command=command)
    await wait_status('one', 'running')
    assert 'BROWSER=none' in (await previews.preview_status('one'))['output']
    await previews.stop_preview('one')
    await previews.start_project(repository, command, previews._free_port())
    project = await wait_project(repository, 'running')
    assert 'BROWSER=none' in project['output']


async def test_parent_pipe_loss_terminates_preview_process_group(tmp_path):
    # 直接关闭后端持有的管道，模拟重载或异常退出；子进程也必须被杀掉。
    command = f'{shlex.quote(sys.executable)} -u -c ' + shlex.quote('import os, time; print(os.getpid(), flush=True); time.sleep(30)')
    process = await asyncio.create_subprocess_exec(
        sys.executable, str(Path(previews.__file__).with_name('preview_process.py')), command,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, start_new_session=True,
    )
    try:
        child_pid = int(await asyncio.wait_for(process.stdout.readline(), 5))
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 5)
        async with asyncio.timeout(5):
            while True:
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    break
                await asyncio.sleep(0.05)
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


async def wait_project(repo, expected):
    async with asyncio.timeout(10):
        while True:
            result = await previews.project_status(str(repo))
            if result['status'] == expected:
                return result
            if result['status'] == 'failed' and expected != 'failed':
                pytest.fail(result['error'] + result['output'])
            await asyncio.sleep(0.05)


async def test_fixed_entry_switches_verified_worktree_and_restores_original(repository):
    public_port = previews._free_port()
    command = f'{shlex.quote(sys.executable)} -u -m http.server {{port}} --bind 127.0.0.1'
    await previews.start_project(str(repository), command, public_port)
    main = await wait_project(repository, 'running')
    internal_port = main['internal_port']
    # 原项目存在未提交修改，也不得被预览覆盖。
    (repository / 'page.txt').write_text('my original project')
    path = '/page.txt?id=123&resource=456'
    assert b'my original project' in await fetch(public_port, path)
    prepared, _ = await start(repository, public_port=public_port)
    repair = await wait_status('one', 'running')
    assert repair['url'] == main['url'] == f'http://localhost:{public_port}'
    assert repair['mode'] == 'fixed'
    assert repair['internal_port'] != public_port
    assert b'verified repair' in await fetch(public_port, path)
    assert (await previews.project_status(repository))['source'] == 'repair'
    await previews.stop_preview('one')
    assert b'my original project' in await fetch(public_port, path)
    assert (await previews.project_status(repository))['internal_port'] == internal_port
    assert (await previews.project_status(repository))['source'] == 'project'
    assert await git(repository, 'branch', '--show-current') == 'main'
    assert await git(prepared['worktree_path'], 'status', '--porcelain') == ''
    assert (repository / 'page.txt').read_text() == 'my original project'
    await start(repository, 'two', public_port=public_port)
    second = await wait_status('two', 'running')
    os.killpg(previews._previews['two'].process.pid, signal.SIGTERM)
    await wait_status('two', 'failed')
    await previews._previews['two'].task
    assert b'my original project' in await fetch(public_port, path)
    await previews.stop_project(str(repository))
    for port in (public_port, internal_port, repair['internal_port'], second['internal_port']):
        await assert_port_closed(port)


async def test_fixed_preview_start_failure_leaves_original_running(repository):
    public_port = previews._free_port()
    command = f'{shlex.quote(sys.executable)} -u -m http.server {{port}} --bind 127.0.0.1'
    await previews.start_project(repository, command, public_port)
    await wait_project(repository, 'running')
    prepared = await prepare_worktree({'repo_path': str(repository), 'branch': 'main'})
    # 两个目录使用同一启动命令，只有工作树中的模拟服务失败。
    conditional = f'if [ "$PWD" = {shlex.quote(str(repository))} ]; then {command}; else echo {{port}}; exit 9; fi'
    await previews.stop_project(repository)
    await previews.start_project(repository, conditional, public_port)
    await wait_project(repository, 'running')
    await previews.start_preview('one', worktree_path=prepared['worktree_path'], repo_path=str(repository),
        command=conditional, env_files=[], commit=prepared['base_commit'], branch=prepared['repair_branch'], public_port=public_port)
    assert '退出码 9' in (await wait_status('one', 'failed'))['error']
    await previews._previews['one'].task
    assert b'verified repair' in await fetch(public_port)


async def test_occupied_fixed_port_does_not_terminate_existing_service(repository):
    prepared, _ = await start(repository)
    existing = await wait_status('one', 'running')
    with pytest.raises(ValueError, match='占用'):
        await previews.start_project(repository, 'echo {port}', existing['port'])
    assert b'verified repair' in await fetch(existing['port'])
    assert (await previews.project_status(repository))['status'] == 'stopped'
