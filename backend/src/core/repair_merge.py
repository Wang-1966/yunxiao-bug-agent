"""将已验收的 Agent worktree 合并到项目修复分支。"""
import asyncio
import json
import shutil
from pathlib import Path

import aiosqlite
from loguru import logger

from ..config import get_settings
from .repair_executor import git, run_process, REPAIR_BRANCH_PREFIX


async def merge_confirmed_repair(*, worktree_path: str, repair_branch: str, repo_path: str, target_branch: str, git_commit_hash: str, base_commit: str):
    repo = Path(repo_path).expanduser().resolve()
    worktree = Path(worktree_path).expanduser().resolve()
    if not repo.is_dir() or not worktree.is_dir():
        raise ValueError('修复仓库或 Agent 工作树已不存在')
    if not target_branch or target_branch == repair_branch:
        raise ValueError('目标修复分支无效')
    if Path(await git(repo, 'rev-parse', '--show-toplevel')).resolve() != repo:
        raise ValueError('目标路径不是 Git 仓库根目录')
    if Path(await git(worktree, 'rev-parse', '--show-toplevel')).resolve() != worktree:
        raise ValueError('Agent 工作树无效')
    if await git(worktree, 'branch', '--show-current') != repair_branch:
        raise ValueError('Agent 分支与任务记录不一致')
    if await git(repo, 'rev-parse', '--path-format=absolute', '--git-common-dir') != await git(worktree, 'rev-parse', '--path-format=absolute', '--git-common-dir'):
        raise ValueError('Agent 工作树不属于目标仓库')
    if not git_commit_hash or not base_commit:
        raise ValueError('缺少已验证提交记录，请重新执行修复')
    if await git(worktree, 'rev-parse', 'HEAD') != git_commit_hash or await git(repo, 'rev-parse', repair_branch) != git_commit_hash:
        raise ValueError('Agent 提交已变化，与已验证结果不一致，请重新执行')
    if await git(worktree, 'status', '--porcelain'):
        raise ValueError('Agent 工作树在验证后发生修改，请重新执行验证')
    if await git(worktree, 'rev-list', '--parents', '-n', '1', git_commit_hash) != f'{git_commit_hash} {base_commit}':
        raise ValueError('已验证提交与基础提交不一致')
    if not await git(worktree, 'diff', '--name-only', base_commit, git_commit_hash):
        raise ValueError('已验证提交没有代码差异')
    if await git(repo, 'status', '--porcelain'):
        raise ValueError('目标修复仓库存在未提交修改，请先提交或清理后再合并')
    await git(repo, 'rev-parse', '--verify', '--end-of-options', f'{target_branch}^{{commit}}')
    # 上次合并已成功但数据库写入中断时，只补记结果，不创建重复合并提交。
    code, _ = await run_process(['git', 'merge-base', '--is-ancestor', git_commit_hash, target_branch], repo)
    if code == 0:
        return {'merge_commit': await git(repo, 'rev-parse', target_branch), 'target_branch': target_branch, 'git_commit_hash': git_commit_hash}
    if code != 1:
        raise ValueError('无法检查目标分支的合并状态')
    if await git(repo, 'branch', '--show-current') != target_branch:
        await git(repo, 'switch', target_branch)
    try:
        message = f"Merge branch '{repair_branch}' into {target_branch}"
        await git(repo, '-c', 'user.name=Bug Repair Agent', '-c', 'user.email=bug-agent@localhost', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false', 'merge', '--no-ff', '-m', message, git_commit_hash)
    except Exception as exc:
        # 只撤销本次由系统开始的合并；进入前已要求目标工作树干净。
        code, _ = await run_process(['git', 'rev-parse', '-q', '--verify', 'MERGE_HEAD'], repo)
        if code == 0:
            await git(repo, 'merge', '--abort')
        raise ValueError(f'合并冲突或 Git 合并失败；Agent 分支和工作树已保留：{exc}')
    merge_commit = await git(repo, 'rev-parse', 'HEAD')
    return {'merge_commit': merge_commit, 'target_branch': target_branch, 'git_commit_hash': git_commit_hash}


BUSY_WORKTREE = '工作树目录仍被进程占用（如在其中运行的 dev server），关闭后重试即可完成清理'


async def _remove_residue(worktree):
    await asyncio.to_thread(shutil.rmtree, worktree, True)
    if worktree.exists():
        raise ValueError(BUSY_WORKTREE)


async def cleanup_confirmed_repair(worktree_path, repair_branch, repo_path, git_commit_hash):
    """交付落盘后清理；失败不得回滚已成功的交付，也不得强删后续修改。"""
    worktree = Path(worktree_path).resolve()
    # 没有 .git 说明 git 已注销并删掉受控文件，只剩运行中程序写回的缓存；此时 git -C 会落到上层仓库。
    if (worktree / '.git').exists():
        if await git(worktree, 'branch', '--show-current') != repair_branch or await git(worktree, 'rev-parse', 'HEAD') != git_commit_hash:
            raise ValueError('工作树提交已变化，保留工作树供人工审查')
        if await git(worktree, 'status', '--porcelain'):
            raise ValueError('工作树有新增修改，保留工作树供人工审查')
        code, output = await run_process(['git', '-C', str(repo_path), 'worktree', 'remove', str(worktree)], repo_path)
        if code and (worktree / '.git').exists():
            raise ValueError(f'Git 操作失败：{output[-3000:]}')
    if worktree.exists():
        await _remove_residue(worktree)
    await git(repo_path, 'worktree', 'prune')
    code, output = await run_process(['git', 'show-ref', '--verify', '--hash', f'refs/heads/{repair_branch}'], repo_path)
    if code == 0:
        if output.strip() != git_commit_hash:
            raise ValueError('Agent 分支已变化，保留分支供人工审查')
        await git(repo_path, 'update-ref', '-d', f'refs/heads/{repair_branch}', git_commit_hash)
    elif code != 1:
        raise ValueError('无法检查待清理的 Agent 分支')


def is_repair_branch(branch):
    return branch.startswith((REPAIR_BRANCH_PREFIX, 'bug-agent/'))


async def discard_worktree(worktree_path, repair_branch=None):
    """Agent 记录已删除：不再有人能审查该工作树，连同未合并修改和 Agent 分支一起丢弃。"""
    root = Path(get_settings().worktrees_dir).expanduser().resolve()
    worktree = Path(worktree_path).expanduser().resolve()
    if worktree.parent != root or not worktree.name.startswith('repair-'):
        raise ValueError(f'拒绝删除工作树目录之外的路径：{worktree}')
    git_file = worktree / '.git'
    common = None
    if git_file.is_file() and (text := git_file.read_text().strip()).startswith('gitdir:'):
        common = Path(text.removeprefix('gitdir:').strip()).parents[1]
        if repair_branch is None:
            code, branch = await run_process(['git', '-C', str(worktree), 'branch', '--show-current'], worktree)
            if code == 0:
                repair_branch = branch.strip()
    if worktree.exists():
        await _remove_residue(worktree)
    if common and common.is_dir():
        await run_process(['git', f'--git-dir={common}', 'worktree', 'prune'], root)
        if repair_branch and is_repair_branch(repair_branch):
            code, _ = await run_process(['git', f'--git-dir={common}', 'show-ref', '--verify', f'refs/heads/{repair_branch}'], root)
            if code == 0:
                code, output = await run_process(['git', f'--git-dir={common}', 'branch', '-D', repair_branch], root)
                if code:
                    raise ValueError(f'Agent 分支未清理：{output[-3000:]}')


async def sweep_orphan_worktrees(db_path):
    """启动时（尚无任务运行）删除没有 Agent 记录引用的工作树和 Agent 分支。"""
    root = Path(get_settings().worktrees_dir).expanduser().resolve()
    async with aiosqlite.connect(db_path) as db:
        rows = await (await db.execute('''SELECT a.worktree_path, h.result FROM agents a
            LEFT JOIN task_history h ON h.id=(SELECT MAX(id) FROM task_history WHERE agent_id=a.agent_id)
            WHERE a.worktree_path IS NOT NULL''')).fetchall()
        used = {Path(p).resolve() for p, _ in rows}
        used_branches = {json.loads(result or '{}').get('repair_branch') for _, result in rows}
        repos = {p for (p,) in await (await db.execute('SELECT repo_path FROM projects UNION SELECT repo_path FROM bugs')).fetchall() if p}
    for worktree in root.glob('repair-*') if root.is_dir() else []:
        if worktree.resolve() not in used:
            try:
                await discard_worktree(worktree)
            except (ValueError, OSError) as exc:
                logger.warning(f'孤儿工作树未清理 {worktree.name}：{exc}')
    for repo in repos:
        repo = Path(repo).expanduser()
        if not repo.is_dir():
            continue
        await run_process(['git', '-C', str(repo), 'worktree', 'prune'], repo)
        code, worktrees = await run_process(['git', '-C', str(repo), 'worktree', 'list', '--porcelain', '-z'], repo)
        if code:
            continue
        attached_branches = {part.removeprefix('branch refs/heads/') for part in worktrees.split('\0') if part.startswith('branch refs/heads/')}
        code, branches = await run_process(['git', '-C', str(repo), 'for-each-ref', '--format=%(refname:short)',
                                             'refs/heads/bug-agent/', f'refs/heads/{REPAIR_BRANCH_PREFIX}'], repo)
        for branch in branches.split() if code == 0 else []:
            if branch not in used_branches and branch not in attached_branches:
                await run_process(['git', '-C', str(repo), 'branch', '-D', branch], repo)
