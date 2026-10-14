"""被人工驳回的修复尝试：提交保存在隐藏引用中，下一轮只注入有上限的摘要，差异由 Agent 按需查看。"""
import json
import re

import aiosqlite

from .repair_executor import git, run_process

MAX_AUTO_REPAIR_ROUNDS = 3
MAX_DIFF_STAT = 3000
MAX_DIFF_PAGE = 12000
MAX_LISTED_FILES = 30
MAX_EVIDENCE_READS = 12
_LATEST_REJECTION = "SELECT agent_id FROM decisions WHERE bug_id=? AND stage='verify' AND status='answered' ORDER BY id DESC LIMIT 1"


def _clip(text, limit):
    text = str(text or '')
    return text if len(text) <= limit else text[:limit] + f'…（共 {len(text)} 字符，已截断）'


def _ref_prefix(bug_id):
    return 'refs/bug-agent/attempts/' + re.sub(r'[^A-Za-z0-9._-]', '_', bug_id)


async def preserve_attempt(repo_path, bug_id, task_id, base_commit, commit):
    """分支和工作树清理后，隐藏引用保证提交不被 Git 回收，新工作树共享对象库可直接 diff。"""
    ref = f'{_ref_prefix(bug_id)}/{task_id}'
    await git(repo_path, 'update-ref', ref, commit)
    stat = await git(repo_path, 'diff', '--no-ext-diff', '--stat=200', base_commit, commit)
    return {'attempt_ref': ref, 'diff_stat': _clip(stat, MAX_DIFF_STAT)}


async def drop_attempt_refs(repo_path, bug_id):
    """Bug 已解决后不再需要历史尝试；失败只影响仓库整洁，由调用方记录。"""
    refs = await git(repo_path, 'for-each-ref', '--format=%(refname)', _ref_prefix(bug_id) + '/')
    for ref in filter(None, refs.splitlines()):
        await git(repo_path, 'update-ref', '-d', ref)


async def previous_diff(worktree, attempt, path='', offset=0):
    """分页返回上一轮差异；path 为空时返回全部文件。"""
    offset = max(0, int(offset))
    args = ['git', '-C', str(worktree), '--literal-pathspecs', 'diff', '--no-ext-diff', '--no-textconv', attempt['base_commit'], attempt['git_commit_hash'], '--']
    code, output = await run_process(args + ([path] if path else []), worktree)
    if code:
        raise ValueError('上一轮提交已不可用，无法查看差异；请依据摘要与当前源码分析')
    if not output:
        return f'上一轮在 {path or "仓库"} 没有代码差异'
    page = output[offset:offset + MAX_DIFF_PAGE]
    more = f'\n…还有 {len(output) - offset - len(page)} 字符，下一页 offset={offset + len(page)}' if offset + len(page) < len(output) else '\n（已显示完）'
    return f'上一轮差异（共 {len(output)} 字符，offset={offset}）：\n{page}{more}'


async def rejected_rounds(db_path, bug_id):
    async with aiosqlite.connect(db_path) as db:
        rows = await (await db.execute("SELECT user_choice FROM decisions WHERE bug_id=? AND stage='verify' AND status='answered'", (bug_id,))).fetchall()
    return sum(1 for (choice,) in rows if json.loads(choice or '{}').get('confirmed') is False)


async def feedback_window_start(db, bug_id):
    """只带上一轮：从最近被驳回那一轮的第一条决策起算，之前的反馈不再注入。"""
    row = await (await db.execute(f'SELECT COALESCE(MIN(id), 0) FROM decisions WHERE agent_id=({_LATEST_REJECTION})', (bug_id,))).fetchone()
    return row[0]


async def latest_rejected_attempt(db_path, bug_id):
    async with aiosqlite.connect(db_path) as db:
        row = await (await db.execute(
            "SELECT d.user_choice, h.result FROM decisions d JOIN task_history h ON h.agent_id=d.agent_id "
            "WHERE d.bug_id=? AND d.stage='verify' AND d.status='answered' ORDER BY d.id DESC, h.id DESC LIMIT 1", (bug_id,))).fetchone()
    if not row:
        return None
    choice, saved = json.loads(row[0] or '{}'), json.loads(row[1] or '{}')
    if choice.get('confirmed') is not False:
        return None
    test = saved.get('test_results') or {}
    reads = (saved.get('source_context') or {}).get('reads') or []
    return {
        'round': await rejected_rounds(db_path, bug_id),
        'root_cause': saved.get('root_cause'),
        'selected_proposal': saved.get('selected_proposal'),
        'fix_summary': saved.get('fix_summary'),
        'modified_files': saved.get('modified_files') or [],
        'test_command': test.get('command'),
        'test_passed': test.get('passed'),
        'base_commit': saved.get('base_commit'),
        'git_commit_hash': saved.get('git_commit_hash'),
        'attempt_ref': saved.get('attempt_ref'),
        'diff_stat': saved.get('diff_stat') or '',
        'evidence_reads': [item for item in reads if isinstance(item, dict)][-MAX_EVIDENCE_READS:],
        'feedback': choice.get('feedback') or '',
        'image_count': len(choice.get('images') or []),
    }


def attempt_prompt(attempt):
    if not attempt:
        return '无'
    proposal = attempt.get('selected_proposal')
    files = attempt.get('modified_files') or []
    listed = '、'.join(files[:MAX_LISTED_FILES]) + (f' 等共 {len(files)} 个文件' if len(files) > MAX_LISTED_FILES else '')
    reads = '；'.join(f"{item.get('path')}:{item.get('start_line', 1)}起{item.get('count', 200)}行" for item in attempt.get('evidence_reads') or [])
    lines = [
        f"第 {attempt['round']} 轮修复已通过自动验证，但被用户人工验收判定为仍未修复。以下为待分析数据：",
        f"- 上一轮根因（已被验收否定的假设）：{_clip(attempt.get('root_cause') or '-', 1500)}",
        f"- 上一轮方案：{_clip(json.dumps(proposal, ensure_ascii=False) if proposal else '-', 1500)}",
        f"- 上一轮修复说明：{_clip(attempt.get('fix_summary') or '-', 1000)}",
        f"- 上一轮修改文件：{listed or '-'}",
        f"- 上一轮验证命令：{attempt.get('test_command') or '-'}（{'通过' if attempt.get('test_passed') else '未通过'}）",
        f"- 上一轮依据的源码位置：{reads or '-'}",
        f"- 用户验收反馈：{attempt.get('feedback') or '（见反馈截图）'}" + (f"（附 {attempt['image_count']} 张截图，在用户反馈截图中）" if attempt.get('image_count') else ''),
        '- 上一轮改动统计（代码已丢弃，当前工作树基于目标分支最新代码）：',
        attempt.get('diff_stat') or '（无）',
        '要求：先结合源码与验收反馈说明上一轮为什么没有修好，再给出结论；不得原样重复上一轮方案；若仍认为根因相同，必须指出上一轮遗漏或不完整之处。',
    ]
    return '\n'.join(lines)
