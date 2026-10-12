"""将已验收的 Agent worktree 合并到项目修复分支。"""
from pathlib import Path

from .repair_executor import git, run_process


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
        await git(repo, '-c', 'user.name=Bug Repair Agent', '-c', 'user.email=bug-agent@localhost', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false', 'merge', '--no-ff', '--no-edit', git_commit_hash)
    except Exception as exc:
        # 只撤销本次由系统开始的合并；进入前已要求目标工作树干净。
        code, _ = await run_process(['git', 'rev-parse', '-q', '--verify', 'MERGE_HEAD'], repo)
        if code == 0:
            await git(repo, 'merge', '--abort')
        raise ValueError(f'合并冲突或 Git 合并失败；Agent 分支和工作树已保留：{exc}')
    merge_commit = await git(repo, 'rev-parse', 'HEAD')
    return {'merge_commit': merge_commit, 'target_branch': target_branch, 'git_commit_hash': git_commit_hash}


async def cleanup_confirmed_repair(worktree_path, repair_branch, repo_path, git_commit_hash):
    """交付落盘后清理；失败不得回滚已成功的交付，也不得强删后续修改。"""
    worktree = Path(worktree_path).resolve()
    if worktree.exists():
        if await git(worktree, 'branch', '--show-current') != repair_branch or await git(worktree, 'rev-parse', 'HEAD') != git_commit_hash:
            raise ValueError('工作树提交已变化，保留工作树供人工审查')
        if await git(worktree, 'status', '--porcelain'):
            raise ValueError('工作树有新增修改，保留工作树供人工审查')
        await git(repo_path, 'worktree', 'remove', str(worktree))
    code, output = await run_process(['git', 'show-ref', '--verify', '--hash', f'refs/heads/{repair_branch}'], repo_path)
    if code == 0:
        if output.strip() != git_commit_hash:
            raise ValueError('Agent 分支已变化，保留分支供人工审查')
        await git(repo_path, 'update-ref', '-d', f'refs/heads/{repair_branch}', git_commit_hash)
    elif code != 1:
        raise ValueError('无法检查待清理的 Agent 分支')
