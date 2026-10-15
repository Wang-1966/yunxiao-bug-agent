"""分析 / 修复 / 审查三类 Agent 运行：在工具循环之上加入“可验证”的约束。

- 分析：症状契约 + 多假设 + 失败的复现测试（由系统亲自运行校验）+ 业务源码只读 + 独立对抗审查。
- 修复：续接分析会话；复现测试受保护；提交时系统重新运行复现命令；改动再经审查。
- 置信度由证据决定，而不是模型自评。
"""
import hashlib
import json
import time
from collections import Counter
from pathlib import Path
from typing import Optional

from loguru import logger

from ..config import get_settings
from ..prompts import render_prompt
from .agent_tools import (ToolError, Workspace, build_tools, clip_output, is_test_path, prefers_apply_patch, run_in_worktree, sandbox_available)
from .browser_tool import close_browser
from .repair_executor import changed_files, git
from .tool_agent import load_session, run_tool_agent, save_session, session_path, submit_tool

MAX_REVIEW_ROUNDS = 2
REVIEW_MAX_TURNS = 40
REPRO_TIMEOUT = 600
MAX_DIFF_CHARS = 40_000
MAX_NEW_FILE_CHARS = 6_000
MAX_CONTEXT_READS = 12


def _text(value, label, *, required=True) -> str:
    if value is None and not required:
        return ''
    if not isinstance(value, str) or (required and not value.strip()):
        raise ToolError(f'{label} 必须是非空字符串')
    return value.strip()


def _strings(value, label, *, minimum=0) -> list:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value) or len(value) < minimum:
        raise ToolError(f'{label} 必须是字符串数组' + (f'，至少 {minimum} 条' if minimum else ''))
    return [item.strip() for item in value]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def protected_dirs() -> list:
    """沙箱内禁止读取的后端目录（LLM Key、云效令牌等）。"""
    data = Path(get_settings().database_url.replace('sqlite+aiosqlite:///', '')).resolve().parent
    return [data, Path('.env').resolve()]


async def repository_context(worktree) -> dict:
    """AGENTS.md 全文与根目录概览，写入分析任务提示词。"""
    root = Path(worktree)
    names = [n for n in (await git(root, 'ls-files', '-z')).split('\0') if n]
    counts = Counter(name.split('/')[0] for name in names)
    inventory = '\n'.join(f'{name}：{count} 个文件' for name, count in sorted(counts.items()))
    return {'instructions': Workspace(root).instructions() or '无', 'inventory': inventory}


def system_prompt(editor='edit_file', browser=False) -> str:
    note = 'run_command（沙箱内运行测试、类型检查、构建、安装依赖）' if sandbox_available() else '本环境不支持命令沙箱，无法运行命令，只能静态分析（系统在你提交时仍会运行复现命令）'
    browser_note = 'browser（打开项目预览，点击、输入、截图核对界面）' if browser else '未配置预览命令，不能打开页面'
    return render_prompt('agent_system', commands_note=note, editor=editor, browser_note=browser_note)


def new_session(user_prompt: str, extra_system: str = '', *, editor='edit_file', browser=False) -> list:
    return [{'role': 'system', 'content': system_prompt(editor, browser) + extra_system}, {'role': 'user', 'content': user_prompt}]


# ---------------- 审查 ----------------

def _review_tool():
    async def validate(args):
        refuted = args.get('refuted')
        if type(refuted) is not bool:
            raise ToolError('refuted 必须是布尔值')
        summary = _text(args.get('summary'), 'summary')
        findings = args.get('findings') or []
        if not isinstance(findings, list) or not all(isinstance(f, dict) and f.get('location') and f.get('detail') for f in findings):
            raise ToolError('findings 的每一项都需要 location 与 detail')
        if refuted and not findings:
            raise ToolError('判定 refuted=true 时必须给出具体问题 findings（location + detail），让工程师能直接修正')
        return {'refuted': refuted, 'summary': summary, 'findings': [{'location': str(f['location']), 'detail': str(f['detail'])} for f in findings]}

    return submit_tool(
        'submit_review', '提交审查结论。refuted=true 表示结论/改动不成立，必须附 findings。',
        {'type': 'object', 'properties': {
            'refuted': {'type': 'boolean'}, 'summary': {'type': 'string', 'description': '一句话总结证据'},
            'findings': {'type': 'array', 'items': {'type': 'object', 'properties': {'location': {'type': 'string'}, 'detail': {'type': 'string'}}, 'required': ['location', 'detail']}}},
         'required': ['refuted', 'summary']}, validate)


async def run_review(llm, worktree, prompt, *, images=None, on_operation=None, stream=None, deadline=None, log_context='review'):
    """全新上下文、只读工具；审查失败不阻断流程，但不会提高置信度。"""
    workspace = Workspace(worktree, mode='read')
    tools = build_tools(workspace, commands=False, images=images) + [_review_tool()]
    messages = [{'role': 'system', 'content': render_prompt('review_system')}, {'role': 'user', 'content': prompt}]
    try:
        _, review = await run_tool_agent(llm, messages, tools, max_turns=REVIEW_MAX_TURNS, on_operation=on_operation, stream=stream, deadline=deadline, log_context=log_context, workspace=workspace)
    except ValueError as exc:
        logger.warning(f'审查未能完成：{exc}')
        if on_operation:
            await on_operation(f'审查员未能完成审查（不影响流程，但不提高置信度）：{str(exc)[:300]}')
        return {'status': 'skipped', 'refuted': False, 'summary': str(exc)[:300], 'findings': []}
    review['status'] = 'refuted' if review['refuted'] else 'passed'
    if on_operation:
        detail = '；'.join(f"{f['location']}：{f['detail']}" for f in review['findings'])
        await on_operation(f"审查员{'未通过' if review['refuted'] else '通过'}（{log_context}）：{review['summary']}" + (f'；{detail}' if detail else ''))
    return review


def _gaps(review) -> str:
    return '\n'.join(f"- {f['location']}：{f['detail']}" for f in review['findings']) or '无'


# ---------------- 分析 ----------------

def _analysis_tool(workspace, worktree, holder):
    async def validate(args):
        symptom = args.get('symptom')
        if not isinstance(symptom, dict):
            raise ToolError('symptom 必须是对象，包含 expected / actual / steps / acceptance')
        symptom = {'expected': _text(symptom.get('expected'), 'symptom.expected'), 'actual': _text(symptom.get('actual'), 'symptom.actual'),
                   'steps': _strings(symptom.get('steps'), 'symptom.steps', minimum=1), 'acceptance': _strings(symptom.get('acceptance'), 'symptom.acceptance', minimum=1)}
        hypotheses = args.get('hypotheses')
        if not isinstance(hypotheses, list) or len(hypotheses) < 2:
            raise ToolError('hypotheses 至少需要 2 个候选原因（含已排除的），每个带 evidence 与 status')
        normalized = []
        for item in hypotheses:
            if not isinstance(item, dict) or item.get('status') not in ('confirmed', 'rejected'):
                raise ToolError('每个假设需要 description、evidence，且 status 为 confirmed 或 rejected')
            normalized.append({'description': _text(item.get('description'), 'hypothesis.description'), 'evidence': _text(item.get('evidence'), 'hypothesis.evidence'), 'status': item['status']})
        if not any(h['status'] == 'confirmed' for h in normalized):
            raise ToolError('没有任何已证实（confirmed）的假设；请继续调查，或在 root_cause 中如实说明无法确定并把 open_questions 写清楚')
        root_cause = _text(args.get('root_cause'), 'root_cause')
        paths = _strings(args.get('trigger_paths') or [], 'trigger_paths')
        proposals = args.get('proposals')
        if not isinstance(proposals, list) or not proposals:
            raise ToolError('proposals 必须是非空数组')
        clean = []
        for item in proposals:
            if not isinstance(item, dict):
                raise ToolError('proposals 的每一项必须是对象')
            clean.append({'name': _text(item.get('name'), 'proposal.name'), 'steps': _strings(item.get('steps'), 'proposal.steps', minimum=1),
                          'pros': _strings(item.get('pros') or [], 'proposal.pros'), 'cons': _strings(item.get('cons') or [], 'proposal.cons'),
                          'risks': str(item.get('risks') or '').strip()})
        open_questions = _strings(args.get('open_questions') or [], 'open_questions')

        changed = await changed_files(worktree)
        business = [name for name in changed if not is_test_path(name)]
        if business:
            raise ToolError('分析阶段不能留下业务源码改动：' + '、'.join(business[:10]) + '。请还原（新增文件用 rm 删除，已有文件可用 git show HEAD:路径 > 路径 还原），只保留测试文件后重新提交')

        repro = args.get('reproduction')
        if not isinstance(repro, dict):
            raise ToolError('reproduction 必须是对象：{command, test_files, not_reproducible_reason}')
        command = repro.get('command')
        record = {'command': None, 'test_files': [], 'hashes': {}, 'reproduced': False, 'exit_before': None, 'output_before': '', 'not_reproducible_reason': ''}
        if command:
            command = _text(command, 'reproduction.command')
            test_files = _strings(repro.get('test_files'), 'reproduction.test_files', minimum=1)
            for name in test_files:
                if name not in changed or not is_test_path(name) or not workspace.resolve(name).is_file():
                    raise ToolError(f'复现测试文件 {name} 必须是本阶段新增或修改的测试文件（需存在于改动列表中）。当前改动：{", ".join(changed) or "无"}')
            code, output = await run_in_worktree(worktree, command, REPRO_TIMEOUT, deny_read=workspace.deny_read)
            if code == 0:
                raise ToolError('复现命令在当前代码上通过了，说明测试没有复现这个 Bug。请让测试驱动真实行为并因症状而失败，再提交。命令输出：\n' + clip_output(output, 3000))
            if code == 124:
                raise ToolError(f'复现命令超过 {REPRO_TIMEOUT} 秒；请缩小测试范围后重新提交')
            record.update(command=command, test_files=test_files, hashes={n: _sha(workspace.resolve(n)) for n in test_files},
                          reproduced=True, exit_before=code, output_before=clip_output(output, 3000))
        else:
            reason = _text(repro.get('not_reproducible_reason'), 'reproduction.not_reproducible_reason（没有复现命令时必须说明无法自动化复现的具体原因）')
            record['not_reproducible_reason'] = reason
        holder['analysis'] = {'symptom': symptom, 'open_questions': open_questions, 'hypotheses': normalized, 'root_cause': root_cause,
                              'trigger_paths': paths, 'proposals': clean}
        holder['repro'] = record
        return holder['analysis']

    hypothesis = {'type': 'object', 'properties': {'description': {'type': 'string'}, 'evidence': {'type': 'string', 'description': '文件:行号 与简述'}, 'status': {'type': 'string', 'enum': ['confirmed', 'rejected']}}, 'required': ['description', 'evidence', 'status']}
    proposal = {'type': 'object', 'properties': {'name': {'type': 'string'}, 'steps': {'type': 'array', 'items': {'type': 'string'}}, 'pros': {'type': 'array', 'items': {'type': 'string'}},
                                                 'cons': {'type': 'array', 'items': {'type': 'string'}}, 'risks': {'type': 'string'}}, 'required': ['name', 'steps']}
    return submit_tool(
        'submit_analysis', '提交根因分析。系统会重新运行 reproduction.command，必须在当前代码上失败。业务源码不得有改动。',
        {'type': 'object', 'properties': {
            'symptom': {'type': 'object', 'properties': {'expected': {'type': 'string'}, 'actual': {'type': 'string'}, 'steps': {'type': 'array', 'items': {'type': 'string'}}, 'acceptance': {'type': 'array', 'items': {'type': 'string'}}},
                        'required': ['expected', 'actual', 'steps', 'acceptance']},
            'open_questions': {'type': 'array', 'items': {'type': 'string'}},
            'hypotheses': {'type': 'array', 'items': hypothesis},
            'root_cause': {'type': 'string', 'description': '触发条件到症状的完整因果链，引用 文件:行号'},
            'trigger_paths': {'type': 'array', 'items': {'type': 'string'}, 'description': '所有能触发该症状的代码路径'},
            'reproduction': {'type': 'object', 'properties': {'command': {'type': 'string', 'description': '复现命令；无法复现时留空'}, 'test_files': {'type': 'array', 'items': {'type': 'string'}}, 'not_reproducible_reason': {'type': 'string'}}},
            'proposals': {'type': 'array', 'items': proposal}},
         'required': ['symptom', 'hypotheses', 'root_cause', 'reproduction', 'proposals']}, validate)


def compose_report(analysis, repro, review) -> str:
    """面向人阅读的分析报告，作为 root_cause 展示与注入下游提示词。"""
    s = analysis['symptom']
    lines = [f"症状理解：期望 {s['expected']}；实际 {s['actual']}", '验收标准：' + '；'.join(s['acceptance'])]
    if analysis['open_questions']:
        lines.append('待确认：' + '；'.join(analysis['open_questions']))
    lines.append('根因：' + analysis['root_cause'])
    if analysis['trigger_paths']:
        lines.append('触发路径：' + '；'.join(analysis['trigger_paths']))
    lines.append('假设排查：' + '；'.join(f"[{'已证实' if h['status'] == 'confirmed' else '已排除'}] {h['description']}（{h['evidence']}）" for h in analysis['hypotheses']))
    lines.append(f"复现：{repro['command']}（修复前失败，退出码 {repro['exit_before']}）" if repro['reproduced'] else f"未能自动复现：{repro['not_reproducible_reason']}")
    if review.get('status') == 'refuted':
        lines.append('⚠ 审查员仍不认可该结论：' + '；'.join(f"{f['location']}：{f['detail']}" for f in review['findings']))
    elif review.get('status') == 'skipped':
        lines.append('审查未完成：' + review.get('summary', ''))
    return '\n'.join(lines)


def confidence_of(repro, review) -> str:
    if review.get('status') == 'refuted':
        return 'low'
    if review.get('status') == 'passed':
        return 'high' if repro['reproduced'] else 'medium'
    return 'medium' if repro['reproduced'] else 'low'


def _editor(llm) -> str:
    return 'apply_patch' if prefers_apply_patch(getattr(llm, 'model', '') or '') else 'edit_file'


async def run_analysis(llm, reviewer, worktree, prompt, *, bug, visual='', images=None, previous_attempt=None, on_operation=None, stream=None, preview=None):
    deadline = time.monotonic() + get_settings().agent_timeout
    workspace = Workspace(worktree, mode='analyze', deny_read=protected_dirs())
    holder = {}
    previous = None
    if previous_attempt:
        from .repair_attempts import previous_diff

        async def previous(path, offset):
            return await previous_diff(worktree, previous_attempt, path, offset)
    editor = _editor(llm)
    tools = build_tools(workspace, commands=sandbox_available(), images=images, previous=previous, editor=editor, preview=preview) + [_analysis_tool(workspace, worktree, holder)]
    messages = new_session(prompt, editor=editor, browser=bool(preview))
    review, prior = {}, '无'
    try:
        for round_ in range(1, MAX_REVIEW_ROUNDS + 2):
            await run_tool_agent(llm, messages, tools, on_operation=on_operation, stream=stream, deadline=deadline, log_context='analyze', workspace=workspace)
            analysis, repro = holder['analysis'], holder['repro']
            review_prompt = render_prompt('review_analysis', title=bug.get('title') or '', description=bug.get('description') or '', feedback=bug.get('feedback') or '无',
                                          visual=visual or '无', previous_attempt=bug.get('previous_attempt') or '无', analysis=json.dumps(analysis, ensure_ascii=False, indent=1),
                                          repro_result=_repro_text(repro), prior_gaps=prior)
            review = await run_review(reviewer, worktree, review_prompt, images=images, on_operation=on_operation, stream=stream, deadline=deadline, log_context=f'review/analysis/{round_}')
            if not review['refuted'] or round_ > MAX_REVIEW_ROUNDS:
                break
            prior = _gaps(review)
            messages.append({'role': 'user', 'content': '独立审查员未认可你的分析，指出以下问题。请逐条核对源码（必要时补充测试或修正复现），解决后重新调用 submit_analysis；若你认为审查有误，在回复中给出具体证据。\n' + prior})
        save_session(session_path(worktree), messages)
        return {'analysis': analysis, 'repro': repro, 'review': review, 'root_cause': compose_report(analysis, repro, review),
                'confidence': confidence_of(repro, review), 'proposals': analysis['proposals'],
                'source_context': {'reads': workspace.read_requests[-MAX_CONTEXT_READS:]}}
    finally:
        await close_browser(worktree)


def _repro_text(repro) -> str:
    if repro.get('reproduced'):
        return f"命令：{repro['command']}\n修复前退出码 {repro['exit_before']}（失败，符合预期）；输出：\n{repro.get('output_before', '')}" + (f"\n修复后：退出码 {repro['exit_after']}\n{repro.get('output_after', '')}" if 'exit_after' in repro else '')
    return f"未复现：{repro.get('not_reproducible_reason', '')}"


# ---------------- 修复 ----------------

async def _diff_text(worktree) -> str:
    root = Path(worktree)
    tracked = await git(root, '--literal-pathspecs', 'diff', '--no-ext-diff', '--no-textconv', 'HEAD')
    parts = [tracked]
    for name in (await git(root, 'ls-files', '--others', '--exclude-standard', '-z')).split('\0'):
        if not name:
            continue
        try:
            path = Workspace(root).resolve(name)
            parts.append(f'\n新增文件 {name}：\n' + path.read_text(encoding='utf-8')[:MAX_NEW_FILE_CHARS])
        except (ToolError, OSError, UnicodeDecodeError):
            continue
    text = '\n'.join(parts)
    return text[:MAX_DIFF_CHARS] + (f'\n…（差异共 {len(text)} 字符，已截断，请直接读取当前源码）' if len(text) > MAX_DIFF_CHARS else '')


def _fix_tool(workspace, worktree, repro, holder):
    async def validate(args):
        summary = _text(args.get('summary'), 'summary')
        changed = await changed_files(worktree)
        if not [name for name in changed if not is_test_path(name)]:
            raise ToolError('还没有修改任何业务源码（只有测试文件的改动不算修复）。请修改业务源码后再提交')
        for name, digest in (repro.get('hashes') or {}).items():
            path = workspace.resolve(name)
            if not path.is_file() or _sha(path) != digest:
                raise ToolError(f'复现测试 {name} 被修改或删除，这是不允许的；请还原它，并修改业务源码使其通过')
        result = dict(repro)
        if repro.get('command'):
            code, output = await run_in_worktree(worktree, repro['command'], REPRO_TIMEOUT, deny_read=workspace.deny_read)
            result.update(exit_after=code, output_after=clip_output(output, 3000))
            if code != 0:
                raise ToolError(f'复现命令在修复后仍然失败（退出码 {code}），问题还没有修好。输出：\n{clip_output(output, 4000)}')
        holder.update(summary=summary, repro=result)
        return {'summary': summary}

    return submit_tool('submit_fix', '提交修复。系统会重新运行复现命令，必须通过；复现测试不能被修改。',
                       {'type': 'object', 'properties': {'summary': {'type': 'string', 'description': '实际修改了什么、为什么能消除症状、运行了哪些验证'}}, 'required': ['summary']}, validate)


async def run_fix(llm, reviewer, worktree, prompt, *, bug, analysis, repro, visual='', images=None, previous_attempt=None, instructions='', on_operation=None, stream=None, preview=None):
    deadline = time.monotonic() + get_settings().agent_timeout
    repro = repro or {'command': None, 'test_files': [], 'hashes': {}, 'reproduced': False}
    workspace = Workspace(worktree, mode='fix', protected=set(repro.get('test_files') or []), deny_read=protected_dirs())
    holder = {}
    previous = None
    if previous_attempt:
        from .repair_attempts import previous_diff

        async def previous(path, offset):
            return await previous_diff(worktree, previous_attempt, path, offset)
    editor = _editor(llm)
    tools = build_tools(workspace, commands=sandbox_available(), images=images, previous=previous, editor=editor, preview=preview) + [_fix_tool(workspace, worktree, repro, holder)]
    path = session_path(worktree)
    messages = load_session(path)
    if messages:
        messages.append({'role': 'user', 'content': prompt})
    else:
        messages = new_session(prompt + ('\n\n仓库约束（AGENTS.md）：\n' + instructions if instructions else ''), editor=editor, browser=bool(preview))
    review, prior = {}, '无'
    symptom = json.dumps((analysis or {}).get('symptom') or {}, ensure_ascii=False, indent=1)
    try:
        for round_ in range(1, MAX_REVIEW_ROUNDS + 2):
            await run_tool_agent(llm, messages, tools, on_operation=on_operation, stream=stream, deadline=deadline, log_context='fix', workspace=workspace)
            review_prompt = render_prompt('review_fix', title=bug.get('title') or '', description=bug.get('description') or '', feedback=bug.get('feedback') or '无', symptom=symptom,
                                          analysis=json.dumps({k: (analysis or {}).get(k) for k in ('root_cause', 'trigger_paths', 'proposals')}, ensure_ascii=False, indent=1),
                                          summary=holder['summary'], files=', '.join(await changed_files(worktree)), diff=await _diff_text(worktree),
                                          repro_result=_repro_text(holder['repro']), prior_gaps=prior)
            review = await run_review(reviewer, worktree, review_prompt, images=images, on_operation=on_operation, stream=stream, deadline=deadline, log_context=f'review/fix/{round_}')
            if not review['refuted'] or round_ > MAX_REVIEW_ROUNDS:
                break
            prior = _gaps(review)
            messages.append({'role': 'user', 'content': '独立审查员认为改动还不成立，指出以下问题。请逐条核对当前源码并修正，解决后重新调用 submit_fix；若你认为审查有误，在回复中给出具体证据。\n' + prior})
        save_session(path, messages)
        summary = holder['summary']
        if review.get('status') == 'refuted':
            summary += '\n⚠ 审查员仍不认可：' + '；'.join(f"{f['location']}：{f['detail']}" for f in review['findings'])
        return {'summary': summary, 'review': review, 'repro': holder['repro'], 'source_context': {'reads': workspace.read_requests[-MAX_CONTEXT_READS:]}}
    finally:
        await close_browser(worktree)
