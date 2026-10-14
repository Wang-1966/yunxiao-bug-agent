"""管理工作树中的验收预览；同一仓库按需保留一个服务。"""
from __future__ import annotations

import asyncio
import codecs
import os
import signal
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

from ..config import get_settings
from .repair_executor import git, run_process
from .preview_gateway import PreviewGateway

START_TIMEOUT = 120
OUTPUT_LIMIT = 20000
# 本机单后端进程管理开发服务和验收预览。
_lock = asyncio.Lock()
_previews: dict[str, 'Preview'] = {}
_projects: dict[Path, 'ProjectServer'] = {}


@dataclass
class Preview:
    repo_path: Path
    worktree_path: Path
    port: int
    commit: str
    status: str = 'starting'
    output: str = ''
    error: str = ''
    process: asyncio.subprocess.Process | None = None
    task: asyncio.Task | None = None
    project: 'ProjectServer | None' = None
    agent_id: str | None = None

    def snapshot(self):
        return {
            'status': self.status,
            'url': f'http://localhost:{self.project.gateway.port if self.project else self.port}' if self.status == 'running' else None,
            'port': self.project.gateway.port if self.project else self.port,
            'internal_port': self.port, 'mode': 'fixed' if self.project else 'direct',
            'git_commit_hash': self.commit,
            'output': self.output, 'error': self.error,
        }


@dataclass
class ProjectServer:
    repo_path: Path
    command: str
    gateway: PreviewGateway
    main: Preview | None = None
    active: Preview | None = None

    def snapshot(self):
        result = self.main.snapshot()
        if self.active and self.active.status == 'running':
            result['status'] = 'running'
        result.update(url=f'http://localhost:{self.gateway.port}',
                      source='repair' if self.active else 'project',
                      agent_id=self.active.agent_id if self.active else None)
        return result

    async def ready(self, service):
        if service is self.main:
            if self.active:
                return
        else:
            self.active = service
        await self.gateway.switch(service.port)

    async def finished(self, service):
        if self.active is service:
            self.active = None
            await self.gateway.switch(self.main.port if self.main.status == 'running' else None)
        elif service is self.main and not self.active:
            await self.gateway.switch(None)


def _free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def validate_preview_config(command, env_files):
    if command.strip() and '{port}' not in command:
        raise ValueError('预览命令必须包含 {port}，以便系统分配端口；Vite 请同时使用 --strictPort')
    for name in env_files:
        path = Path(name)
        if not name or path.is_absolute() or '..' in path.parts or '.git' in path.parts or '\\' in name or '\0' in name or name.startswith('-'):
            raise ValueError('环境文件必须是仓库内的相对文件路径，不能包含 .. 或 .git')


async def _copy_environment(repo, worktree, names):
    pending = []
    for name in names:
        source, target = repo / name, worktree / name
        if not source.resolve().is_relative_to(repo) or not target.resolve().is_relative_to(worktree) or source.is_symlink() or target.is_symlink():
            raise ValueError(f'环境文件不能通过符号链接指向其他位置：{name}')
        if not source.is_file() or source.stat().st_size > 1_000_000:
            raise ValueError(f'环境文件不存在或超过 1 MB：{name}')
        code, _ = await run_process(['git', '-C', str(worktree), 'check-ignore', '--quiet', '--', name], worktree)
        if code:
            raise ValueError(f'环境文件必须被 Agent 工作树的 Git 忽略规则排除：{name}')
        if target.exists():
            if not target.is_file():
                raise ValueError(f'工作树中的环境文件路径不是文件：{name}')
            continue  # 保留工作树已有配置，避免覆盖人工调整。
        pending.append((source, target))
    for source, target in pending:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as file:
            os.chmod(target, 0o600)
            file.write(source.read_bytes())


async def _terminate(process):
    # shell 退出后其子进程仍可能存活，因此也清理整个进程组。
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout=3)
    except asyncio.TimeoutError:
        pass
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if process.stdin:
            process.stdin.close()
    await process.wait()


async def _read_output(preview):
    decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
    while chunk := await preview.process.stdout.read(4096):
        preview.output = (preview.output + decoder.decode(chunk))[-OUTPUT_LIMIT:]
    preview.output = (preview.output + decoder.decode(b'', final=True))[-OUTPUT_LIMIT:]


async def _http_ready(port):
    writer = None
    try:
        async with asyncio.timeout(1):
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            writer.write(f'GET / HTTP/1.0\r\nHost: localhost:{port}\r\n\r\n'.encode())
            await writer.drain()
            parts = (await reader.readline()).split()
            return len(parts) > 1 and parts[0].startswith(b'HTTP/') and 200 <= int(parts[1]) < 500
    except (OSError, ValueError, TimeoutError):
        return False
    finally:
        if writer:
            writer.close()


async def _run(preview, command):
    reader_task = None
    try:
        # stdin 管道由守护进程监视；后端异常退出也会终止预览进程组。
        spawning = asyncio.create_task(asyncio.create_subprocess_exec(
            sys.executable, str(Path(__file__).with_name('preview_process.py')),
            command.replace('{port}', str(preview.port)), cwd=str(preview.worktree_path),
            # Vite 的 server.open 等配置会自动打开内部端口；托管服务统一由固定入口访问。
            env={**os.environ, 'PORT': str(preview.port), 'BROWSER': 'none'},
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, start_new_session=True,
        ))
        try:
            preview.process = await asyncio.shield(spawning)
        except asyncio.CancelledError:
            preview.process = await spawning
            raise
        reader_task = asyncio.create_task(_read_output(preview))
        deadline = asyncio.get_running_loop().time() + START_TIMEOUT
        while preview.process.returncode is None:
            if await _http_ready(preview.port):
                preview.status = 'running'
                if preview.project:
                    await preview.project.ready(preview)
                break
            if asyncio.get_running_loop().time() >= deadline:
                raise ValueError(f'预览在 {START_TIMEOUT} 秒内未就绪，请查看日志并检查依赖、启动命令和端口配置')
            await asyncio.sleep(0.25)
        while preview.process.returncode is None:
            await asyncio.sleep(0.25)
        code = preview.process.returncode
        raise ValueError(f'预览命令已退出（退出码 {code}），请查看启动日志')
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        preview.status, preview.error = 'failed', str(exc)
    finally:
        try:
            if preview.project:
                await preview.project.finished(preview)
        finally:
            try:
                if preview.process:
                    await _terminate(preview.process)
            finally:
                if reader_task:
                    await reader_task


async def _stop(preview):
    if preview.task and not preview.task.done():
        preview.task.cancel()
        await asyncio.gather(preview.task, return_exceptions=True)
    preview.status, preview.error = 'stopped', ''


async def _stop_project(project):
    for preview in _previews.values():
        if preview.project is project:
            await _stop(preview)
    await _stop(project.main)
    await project.gateway.close()
    _projects.pop(project.repo_path, None)


async def _ensure_project(repo, command, port):
    if not command.strip() or port is None:
        raise ValueError('请先配置开发启动命令和固定入口端口')
    validate_preview_config(command, [])
    if not 1 <= port <= 65535:
        raise ValueError('固定入口端口必须在 1 到 65535 之间')
    current = _projects.get(repo)
    if current:
        if current.gateway.port != port or current.command != command:
            raise ValueError('项目正在使用其他端口或启动命令，请先停止项目再启动')
        if current.main.status in ('starting', 'running') or current.active:
            return current
        await _stop_project(current)
    gateway = PreviewGateway(port)
    try:
        await gateway.start()
    except OSError as exc:
        raise ValueError(f'无法使用固定入口 {port}：请先停止占用该端口的原项目，再点击启动项目。{exc}') from exc
    project = ProjectServer(repo, command, gateway)
    project.main = Preview(repo, repo, _free_port(), '', project=project)
    _projects[repo] = project
    project.main.task = asyncio.create_task(_run(project.main, command))
    return project


async def start_project(repo_path, command, port):
    repo = Path(repo_path).expanduser().resolve()
    async with _lock:
        return (await _ensure_project(repo, command.strip(), port)).snapshot()


async def project_status(repo_path):
    async with _lock:
        project = _projects.get(Path(repo_path).expanduser().resolve()) if repo_path else None
        return project.snapshot() if project else {'status': 'stopped', 'url': None, 'source': 'project', 'error': '', 'output': ''}


async def stop_project(repo_path):
    async with _lock:
        project = _projects.get(Path(repo_path).expanduser().resolve())
        if project:
            await _stop_project(project)
    return {'status': 'stopped', 'url': None, 'source': 'project', 'error': '', 'output': ''}


async def check_project_change(previous, updated):
    """项目配置写入和启停 API 共用配置锁；这里保护运行中的仓库映射。"""
    if not previous or not previous.get('repo_path'):
        return
    async with _lock:
        project = _projects.get(Path(previous['repo_path']).expanduser().resolve())
        if project and (updated is None or
                        Path(updated['repo_path']).expanduser().resolve() != project.repo_path or
                        updated['preview_port'] != project.gateway.port or
                        updated['preview_command'].strip() != project.command):
            raise ValueError('请先停止项目，再修改仓库、固定端口、启动命令或解除绑定')


async def start_preview(agent_id, *, worktree_path, repo_path, command, env_files, commit, branch, public_port=None):
    command = command.strip()
    if not command:
        raise ValueError('请先到项目管理配置人工验收预览命令')
    validate_preview_config(command, env_files)
    repo, worktree = Path(repo_path).expanduser().resolve(), Path(worktree_path).resolve()
    root = Path(get_settings().worktrees_dir).expanduser().resolve()
    if not worktree.is_dir() or worktree.parent != root or not worktree.name.startswith('repair-'):
        raise ValueError('修复工作树已移除或不在配置的工作树目录中')
    if Path(await git(worktree, 'rev-parse', '--show-toplevel')).resolve() != worktree or await git(repo, 'rev-parse', '--path-format=absolute', '--git-common-dir') != await git(worktree, 'rev-parse', '--path-format=absolute', '--git-common-dir'):
        raise ValueError('Agent 工作树不属于当前项目仓库')
    if not commit or await git(worktree, 'rev-parse', 'HEAD') != commit or await git(worktree, 'branch', '--show-current') != branch or await git(worktree, 'status', '--porcelain'):
        raise ValueError('工作树与已验证提交不一致，请重新执行验证')
    async with _lock:
        current = _previews.get(agent_id)
        if current and current.status in ('starting', 'running') and current.commit == commit and (
            (current.project.gateway.port if current.project else None) == public_port
        ):
            return current.snapshot()
        await _copy_environment(repo, worktree, env_files)
        project = await _ensure_project(repo, command, public_port) if public_port is not None else None
        # 完整串行停止/启动，避免并发点击留下同仓库多个服务。
        for key, preview in _previews.items():
            if key == agent_id or preview.repo_path == repo:
                await _stop(preview)
        preview = Preview(repo, worktree, _free_port(), commit, project=project, agent_id=agent_id)
        _previews[agent_id] = preview
        preview.task = asyncio.create_task(_run(preview, command))
        return preview.snapshot()


async def preview_status(agent_id):
    async with _lock:
        preview = _previews.get(agent_id)
        return preview.snapshot() if preview else {'status': 'stopped', 'url': None, 'output': '', 'error': ''}


async def stop_preview(agent_id, *, forget=False):
    async with _lock:
        preview = _previews.get(agent_id)
        if preview:
            await _stop(preview)
            if forget:
                del _previews[agent_id]
        return preview.snapshot() if preview else {'status': 'stopped', 'url': None, 'output': '', 'error': ''}


async def stop_all():
    async with _lock:
        for preview in _previews.values():
            await _stop(preview)
        for project in list(_projects.values()):
            await _stop_project(project)
        _previews.clear()
