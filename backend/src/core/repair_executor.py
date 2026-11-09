"""真实代码修复：Git 隔离、受限文件工具、明确的本地验证结果。"""
import asyncio
import json
import os
import re
import signal
import sys
import tempfile
import uuid
from pathlib import Path

from loguru import logger

from ..config import get_settings

REPAIR_BRANCH_PREFIX = 'agentfix/'


async def run_process(args, cwd, timeout=60, input=None):
    """停止/超时同时终止子进程组，避免后台安装或构建继续执行。"""
    process = await asyncio.create_subprocess_exec(
        *args, cwd=str(cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.PIPE if input is not None else asyncio.subprocess.DEVNULL, start_new_session=True,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(input), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
        raise
    return process.returncode, output.decode('utf-8', errors='replace')


async def git(path, *args):
    code, output = await run_process(['git', '-C', str(path), *args], path)
    if code:
        raise ValueError(f'Git 操作失败：{output[-3000:]}')
    return output.strip()


def repair_branch_name(bug, run_id):
    def slug(value, limit, fallback):
        text = re.sub(r'[^\w-]+', '-', str(value or ''), flags=re.UNICODE)
        return text[:limit].strip('-_') or fallback

    bug_id = slug(bug.get('bug_id') or bug.get('id'), 8, 'bug')
    title = slug(bug.get('title'), 24, 'repair')
    return f'{REPAIR_BRANCH_PREFIX}{bug_id}-{title}-{run_id[:12]}'


async def prepare_worktree(bug):
    if not bug.get('repo_path'):
        raise ValueError('未配置修复仓库，请先到项目管理绑定此云效项目')
    repo = Path(bug['repo_path']).expanduser().resolve()
    if not repo.is_dir() or Path(await git(repo, 'rev-parse', '--show-toplevel')).resolve() != repo:
        raise ValueError('修复仓库路径必须是现有 Git 仓库的根目录')
    ref = bug.get('branch') or 'HEAD'
    base = await git(repo, 'rev-parse', '--verify', '--end-of-options', f'{ref}^{{commit}}')
    # 每次执行独立路径，失败或重试不覆盖上一轮修改。
    run_id = uuid.uuid4().hex
    root = Path(get_settings().worktrees_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    worktree = root / f'repair-{run_id}'
    branch = repair_branch_name(bug, run_id)
    await git(repo, 'worktree', 'add', '-b', branch, str(worktree), base)
    await prepare_worktree_dependencies(repo, worktree)
    return {'worktree_path': str(worktree), 'repair_branch': branch, 'base_commit': base}


async def prepare_worktree_dependencies(repo, worktree):
    """复用匹配的本地 Node 依赖副本；不共享可写文件、不复制安装凭据。"""
    repo, worktree = Path(repo).resolve(), Path(worktree).resolve()
    source, target = repo / 'node_modules', worktree / 'node_modules'
    if target.exists() or target.is_symlink() or not source.is_dir() or source.is_symlink():
        return False
    if not (worktree / 'package.json').is_file():
        return False
    # 不把本地依赖带入交付差异，也不复用另一版本/工作区配置下的安装结果。
    if (await run_process(['git', 'check-ignore', '--quiet', '--', 'node_modules/'], worktree))[0]:
        return False
    config_names = {'package.json', 'package-lock.json', 'npm-shrinkwrap.json', 'pnpm-lock.yaml',
                    'pnpm-workspace.yaml', 'yarn.lock', 'bun.lock', 'bun.lockb', '.pnpmfile.cjs'}
    names = set(config_names)
    for root in (repo, worktree):
        names.update(name for name in (await git(root, 'ls-files', '-z')).split('\0')
                     if Path(name).name in config_names)
    for name in names:
        left, right = repo / name, worktree / name
        if left.is_symlink() or right.is_symlink():
            return False
        if left.is_file() != right.is_file() or (left.is_file() and left.read_bytes() != right.read_bytes()):
            logger.info('依赖配置不同，工作树需独立安装依赖：{}', name)
            return False

    def links_are_internal():
        for directory, dirs, files in os.walk(source):
            for name in dirs + files:
                link = Path(directory) / name
                if link.is_symlink() and (os.path.isabs(os.readlink(link)) or not link.resolve().is_relative_to(source)):
                    return False
        return True

    if not await asyncio.to_thread(links_are_internal):
        logger.info('源依赖包含外部链接，工作树需独立安装依赖：{}', worktree)
        return False
    # 临时目录完成后再发布，取消或失败不会留下半份 node_modules。
    with tempfile.TemporaryDirectory(prefix='.repair-deps-', dir=worktree) as staging:
        snapshot = Path(staging) / 'node_modules'
        # APFS 写时复制；其他平台普通复制，后续写入都彼此隔离。
        flag = '-cRP' if sys.platform == 'darwin' else '-RP'
        code, output = await run_process(['cp', flag, str(source), str(snapshot)], worktree, timeout=300)
        if code:
            logger.warning('无法准备本地依赖副本，工作树需独立安装依赖：{}；{}', worktree, output[-1000:])
            return False
        if target.exists() or target.is_symlink():
            return False
        snapshot.rename(target)
    logger.info('已为修复工作树准备独立依赖副本：{}', worktree)
    return True


def parse_json_objects(response):
    """提取回复中连续的 JSON 对象，返回 (对象列表, 对象外的说明文字)。

    部分中转模型不遵守 json_object 响应格式，会在 JSON 前写进度说明或拼接多个对象。
    """
    if not isinstance(response, str):
        raise ValueError('模型没有返回文本')
    text = '\n'.join(line for line in response.strip().splitlines() if not line.strip().startswith('```'))
    decoder = json.JSONDecoder()
    first_error = None
    start = text.find('{')
    while start != -1:
        try:
            value, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError as exc:
            first_error = first_error or exc
            start = text.find('{', start + 1)
            continue
        if isinstance(value, dict):
            break
        start = text.find('{', end)
    if start == -1:
        if first_error:
            raise ValueError(f'回复中的 JSON 无效：{first_error.msg}（行 {first_error.lineno}，列 {first_error.colno}）；只返回一个完整的 JSON 对象')
        raise ValueError('回复中没有 JSON 对象，只有说明文字；说明请写在动作对象的 note 字段中')
    objects, position = [value], end
    while True:
        rest = text[position:]
        offset = len(rest) - len(rest.lstrip())
        if not rest[offset:offset + 1] == '{':
            break
        try:
            value, end = decoder.raw_decode(text, position + offset)
        except json.JSONDecodeError:
            break
        if not isinstance(value, dict):
            break
        objects.append(value)
        position = end
    prose = (text[:start] + ' ' + text[position:]).strip()
    return objects, prose


def parse_json(response):
    objects, _ = parse_json_objects(response)
    if len(objects) > 1:
        raise ValueError('回复包含多个 JSON 对象，只能返回一个')
    return objects[0]


class RepositoryFiles:
    """校验路径位于 worktree 内；拒绝目录穿越、符号链接、Git 元数据和密钥文件。"""
    def __init__(self, worktree):
        self.root = Path(worktree).resolve()

    def path(self, name):
        if not isinstance(name, str) or not name:
            raise ValueError('文件路径不能为空')
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('只能操作仓库内的相对路径')
        if any(p in ('.git', 'node_modules', '.venv', 'venv', 'dist', '.npmrc', '.pypirc') or p == '.env' or p.startswith('.env.') or p.endswith(('.pem', '.key', '.p12')) for p in relative.parts):
            raise ValueError('禁止读取或修改依赖、构建产物、Git 元数据或密钥文件')
        path = self.root
        for part in relative.parts:
            path = path / part
            if path.is_symlink():
                raise ValueError('禁止通过符号链接读写文件')
        if not path.resolve().is_relative_to(self.root):
            raise ValueError('文件路径越过仓库边界')
        return path


async def changed_files(worktree):
    tracked = await git(worktree, 'diff', '--name-only', '-z', 'HEAD')
    new = await git(worktree, 'ls-files', '--others', '--exclude-standard', '-z')
    return sorted({name for name in (tracked + '\0' + new).split('\0') if name})


async def verify_worktree(worktree, command, repro_command=None):
    """先跑项目验证命令；通过后再跑复现测试命令（修复前失败、修复后必须通过）。"""
    if not command or not command.strip():
        raise ValueError('代码修改已保留，但未配置验证命令；请在项目管理填写后重新执行')
    code, output = await run_process(['git', 'diff', '--check', 'HEAD'], worktree)
    if code:
        return {'passed': False, 'command': 'git diff --check', 'exit_code': code, 'output': output[-20000:]}
    # 只执行用户保存的命令和系统校验过的复现命令；模型本身不具备无沙箱的 shell。
    code, output = await run_process(['/bin/sh', '-c', command], worktree, get_settings().agent_timeout)
    result = {'passed': code == 0, 'command': command, 'exit_code': code, 'output': output[-20000:]}
    if code == 0 and repro_command:
        code, output = await run_process(['/bin/sh', '-c', repro_command], worktree, get_settings().agent_timeout)
        result.update(repro_command=repro_command, repro_exit_code=code, repro_output=output[-20000:])
        if code:
            result.update(passed=False, command=f'复现测试：{repro_command}', exit_code=code, output=output[-20000:])
    return result


async def verify_and_commit_repair(worktree, command, bug_id, repair_branch, base_commit, *, bug_title='', repro_command=None):
    """系统提交与验证完全相同的树，模型不能提交或变更交付对象。"""
    if not repair_branch or not base_commit:
        raise ValueError('修复分支或基础提交记录缺失，请重新执行')
    if await git(worktree, 'branch', '--show-current') != repair_branch:
        raise ValueError('修复工作树分支已变化，请重新执行')
    head = await git(worktree, 'rev-parse', 'HEAD')
    if head != base_commit:
        raise ValueError('修复工作树基础提交已变化，请重新执行')

    async def stage_tree():
        names = await changed_files(worktree)
        if not names:
            raise ValueError('没有实际修改，无法生成已验证提交')
        files = RepositoryFiles(worktree)
        for name in names:
            files.path(name)
        await git(worktree, '--literal-pathspecs', 'add', '-A', '--', *names)
        return names, await git(worktree, 'write-tree')

    names, tree = await stage_tree()
    result = await verify_worktree(worktree, command, repro_command)
    if not result['passed']:
        return {'test_results': result}
    current_names, current_tree = await stage_tree()
    if current_tree != tree or current_names != names or await git(worktree, 'rev-parse', 'HEAD') != head:
        raise ValueError('验证期间源码或提交发生变化，不能交付；请重新执行验证')
    # commit-tree 固定已验证的树，不运行可能修改内容的提交钩子，也不依赖用户全局身份。
    title = ' '.join(bug_title.split())[:120] or f'repair bug {bug_id}'
    commit = await git(worktree, '-c', 'user.name=Bug Repair Agent', '-c', 'user.email=bug-agent@localhost',
                       'commit-tree', tree, '-p', head, '-m', f'fix: {title}\n\nBug: {bug_id}')
    await git(worktree, 'update-ref', f'refs/heads/{repair_branch}', commit, head)
    if await git(worktree, 'status', '--porcelain'):
        raise ValueError('生成提交后工作树发生变化，已保留提交但不能进入验收')
    return {'test_results': result, 'git_commit_hash': commit, 'modified_files': names}
