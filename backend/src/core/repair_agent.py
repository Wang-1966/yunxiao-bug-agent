"""分析 / 修复 Agent 运行：在工具循环之上加入"可验证"的约束。

- 分析：症状契约 + 多假设 + 失败的复现测试（由系统亲自运行校验）+ 业务源码只读。
- 修复：续接分析会话；复现测试受保护；提交时系统重新运行复现命令。
- 置信度由证据决定：复现测试通过 = high，否则 = medium。
- 验证依赖工具（测试运行、编译检查）而非 LLM 审查，提升效率和可靠性。
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


def compose_report_simple(analysis, repro) -> str:
    """简化版分析报告，不包含审查信息。"""
    s = analysis['symptom']
    lines = [f"症状理解：期望 {s['expected']}；实际 {s['actual']}", '验收标准：' + '；'.join(s['acceptance'])]
    if analysis['open_questions']:
        lines.append('待确认：' + '；'.join(analysis['open_questions']))
    lines.append('根因：' + analysis['root_cause'])
    if analysis['trigger_paths']:
        lines.append('触发路径：' + '；'.join(analysis['trigger_paths']))
    lines.append('假设排查：' + '；'.join(f"[{'已证实' if h['status'] == 'confirmed' else '已排除'}] {h['description']}（{h['evidence']}）" for h in analysis['hypotheses']))
    lines.append(f"复现：{repro['command']}（修复前失败，退出码 {repro['exit_before']}）" if repro['reproduced'] else f"未能自动复现：{repro['not_reproducible_reason']}")
    return '\n'.join(lines)


def compose_report(analysis, repro, review) -> str:
    """保留原函数以向后兼容，但不再依赖 review。"""
    return compose_report_simple(analysis, repro)


def _editor(llm) -> str:
    return 'apply_patch' if prefers_apply_patch(getattr(llm, 'model', '') or '') else 'edit_file'


async def run_analysis(llm, reviewer, worktree, prompt, *, bug, visual='', images=None, previous_attempt=None, on_operation=None, stream=None, preview=None):
    """执行根因分析，依赖工具验证而非 LLM 审查。

    保留 reviewer 参数以保持向后兼容，但不再使用。
    """
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

    # 增强系统提示：强调自我检查和工具验证
    extra_system = '\n\n重要：提交前请自我检查：\n- 复现测试是否真的失败？\n- 根因定位是否有源码证据支持？\n- 多个假设是否都经过验证？\n- 不确定的地方是否标注在 open_questions 中？'
    messages = new_session(prompt + extra_system, editor=editor, browser=bool(preview))

    try:
        # 直接运行一次，依赖工具验证和自我检查
        await run_tool_agent(llm, messages, tools, on_operation=on_operation, stream=stream, deadline=deadline, log_context='analyze', workspace=workspace)
        analysis, repro = holder['analysis'], holder['repro']

        # 简化的置信度评估：只看是否有复现测试
        confidence = 'high' if repro.get('reproduced') else 'medium'

        # 生成报告（不再包含 review 信息）
        root_cause = compose_report_simple(analysis, repro)

        save_session(session_path(worktree), messages)

        if on_operation:
            await on_operation(f"分析完成，置信度：{confidence}，{'已复现' if repro.get('reproduced') else '未能自动复现'}")

        return {
            'analysis': analysis,
            'repro': repro,
            'review': {'status': 'skipped', 'refuted': False, 'summary': '已改为工具验证模式'},
            'root_cause': root_cause,
            'confidence': confidence,
            'proposals': analysis['proposals'],
            'source_context': {'reads': workspace.read_requests[-MAX_CONTEXT_READS:]}
        }
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


async def run_merge_resolution(worktree, files, llm=None):
    """冲突文件里同时保留目标分支已有功能和本次修复。由调用方完成 git 提交。"""
    from ..llm.factory import LLMFactory
    llm = llm or LLMFactory.create(stage='fix')
    workspace = Workspace(worktree, mode='fix')
    listed = '\n'.join(files)

    async def validate(args):
        summary = _text(args.get('summary'), 'summary')
        pending = [name for name in (await git(worktree, 'diff', '--name-only', '--diff-filter=U')).splitlines() if name]
        if pending:
            raise ToolError('还有未解决的冲突：' + '、'.join(pending))
        for name in files:
            path = Path(worktree) / name
            if path.is_file() and any(mark in path.read_text(encoding='utf-8', errors='replace') for mark in ('<<<<<<<', '>>>>>>>')):
                raise ToolError(f'{name} 仍有冲突标记')
        return {'summary': summary}

    editor = _editor(llm)
    tools = build_tools(workspace, commands=sandbox_available(), editor=editor) + [
        submit_tool('submit_merge', '冲突已解决并保留双方功能时提交。系统会检查冲突标记并完成合并提交。',
                     {'type': 'object', 'properties': {'summary': {'type': 'string', 'description': '双方分别保留了什么'}}, 'required': ['summary']}, validate)]
    prompt = (
        '把目标分支合并进当前修复时发生冲突。冲突文件：\n' + listed +
        '\n\n请先 read_file 这些文件。冲突标记 <<<<<<< ======= >>>>>>> 之间，上面是本次修复，下面是目标分支已有改动。'
        '编辑文件去掉标记，两边的功能都要留下，不能只保留一边。完成后调用 submit_merge。不要执行 git commit。'
    )
    messages = [{'role': 'system', 'content': '你只负责解决合并冲突，保留双方已有功能。阶段结束必须调用 submit_merge。'},
                {'role': 'user', 'content': prompt}]
    try:
        await run_tool_agent(llm, messages, tools, max_turns=30, log_context='merge', workspace=workspace)
    finally:
        await close_browser(worktree)


async def run_fix(llm, reviewer, worktree, prompt, *, bug, analysis, repro, visual='', images=None, previous_attempt=None, instructions='', on_operation=None, stream=None, preview=None):
    """执行修复，依赖工具验证而非 LLM 审查。

    保留 reviewer 参数以保持向后兼容，但不再使用。
    """
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

    # 增强提示：强调测试验证和自我检查
    extra_instructions = '\n\n重要：\n- 修改代码后立即运行相关测试验证\n- 确保复现测试能通过\n- 不确定时用 run_command 运行测试检查\n- 提交前确认所有改动都是必要的'

    if messages:
        messages.append({'role': 'user', 'content': prompt + extra_instructions})
    else:
        full_prompt = prompt + ('\n\n仓库约束（AGENTS.md）：\n' + instructions if instructions else '') + extra_instructions
        messages = new_session(full_prompt, editor=editor, browser=bool(preview))

    try:
        # 直接运行一次，依赖工具验证
        await run_tool_agent(llm, messages, tools, on_operation=on_operation, stream=stream, deadline=deadline, log_context='fix', workspace=workspace)

        save_session(path, messages)
        summary = holder['summary']

        if on_operation:
            files = await changed_files(worktree)
            await on_operation(f"修复完成，已修改 {len(files)} 个文件，{'复现测试通过' if repro.get('reproduced') and holder['repro'].get('exit_after') == 0 else '准备验证'}")

        return {
            'summary': summary,
            'review': {'status': 'skipped', 'refuted': False, 'summary': '已改为工具验证模式'},
            'repro': holder['repro'],
            'source_context': {'reads': workspace.read_requests[-MAX_CONTEXT_READS:]}
        }
    finally:
        await close_browser(worktree)
