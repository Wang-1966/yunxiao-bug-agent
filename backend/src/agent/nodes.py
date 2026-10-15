"""Agent 节点实现"""
from typing import Dict, Any
from loguru import logger

from .state import BugFixState
from ..llm.factory import LLMFactory
from ..prompts import render_prompt
from ..core.repair_executor import prepare_worktree, changed_files, verify_and_commit_repair
from ..core.repair_agent import repository_context, run_analysis, run_fix
from ..config import get_settings
from ..core.bug_media import agent_bug_images
from ..core.bug_inputs import apply_input_snapshot, capture_bug, read_supplement
from ..core.feedback_images import latest_feedback_images
from ..core.repair_attempts import attempt_prompt
from ..core.bug_visual import describe_bug_images, visual_prompt, visual_source
from ..core.agent_stream import ThinkingStream


def feedback_text(state):
    bug = state['bug_info']
    feedback = state.get('repair_feedback') or bug.get('repair_feedback') or ''
    supplement = bug.get('supplement_notes') or ''
    if bug.get('supplement_image_names'):
        first = bug['supplement_image_start']
        last = first + len(bug['supplement_image_names']) - 1
        supplement = '\n\n'.join(filter(None, [supplement, f'修复前补充截图：原图编号 {first}–{last}，请结合复现说明核对。']))
    feedback = '\n\n'.join(filter(None, [f'修复前补充信息：\n{supplement}' if supplement else '', f'验收及方案反馈：\n{feedback}' if feedback else '']))
    if bug.get('feedback_image_names'):
        first, last = bug['feedback_image_start'], bug['feedback_image_start'] + len(bug['feedback_image_names']) - 1
        feedback = '\n\n'.join(filter(None, [feedback, f'用户验收反馈附带截图：原图编号 {first}–{last}（最新的人工验收证据，必须用 view_image 核对并优先于旧推断）。']))
    if state.get('retry_context'):
        feedback = '\n\n'.join(filter(None, [feedback, state['retry_context']]))
    return feedback


def build_agent_prompt(stage, state, **context):
    bug = state['bug_info']
    style = LLMFactory.get_response_style()
    return render_prompt(
        stage,
        bug_id=bug.get('id', state['bug_id']),
        title=bug['title'],
        description=bug.get('description') or '',
        feedback=feedback_text(state) or '无',
        previous_attempt=attempt_prompt(state.get('previous_attempt')),
        root_cause=state.get('root_cause') or 'N/A',
        response_style=render_prompt(f'styles/{style}'),
        **context,
    )


def agent_preview(state):
    """有预览命令时，分析/修复可以用 browser 打开当前工作树。"""
    bug = state.get('bug_info') or {}
    source = {**bug, **(state.get('bug_config') or {})}
    command = (source.get('preview_command') or '').strip()
    if not command or not source.get('repo_path') or not state.get('worktree_path'):
        return None
    return {'agent_id': bug.get('assigned_agent_id') or f"bug-{state.get('bug_id')}", 'worktree_path': state['worktree_path'],
            'repo_path': source['repo_path'], 'command': command, 'env_files': source.get('preview_env_files') or [],
            'public_port': source.get('preview_port'), 'branch': state.get('repair_branch') or ''}


def review_bug(state):
    """审查员使用的 Bug 材料：与分析/修复看到的症状输入一致。"""
    bug = state['bug_info']
    return {'title': bug['title'], 'description': bug.get('description') or '', 'feedback': feedback_text(state),
            'previous_attempt': attempt_prompt(state.get('previous_attempt'))}


def repro_text(repro):
    if not repro:
        return '无'
    if repro.get('reproduced'):
        return f"{repro['command']}（测试文件：{', '.join(repro['test_files'])}；修复前失败，修复后必须通过，且不能修改这些文件）"
    return f"未能自动复现：{repro.get('not_reproducible_reason', '')}"


async def ensure_visual_context(state, llm):
    bug = state['bug_info']
    context = state.get('visual_context')
    if context and context.get('source') == visual_source(bug):
        await record_source_operation(state, '复用已保存的截图观察记录，后续请求默认不携带原图')
        return context
    # 兼容从旧任务状态或直接节点调用进入的流程。
    if bug.get('agent_images') is None:
        bug['agent_images'] = await agent_bug_images(bug)
    if bug['agent_images']:
        await record_source_operation(state, f"首次识别 {len(bug['agent_images'])} 张截图，后续请求使用文字观察；需要核对时按需重读原图")
    return await describe_bug_images(llm, bug)


async def record_source_operation(state, message, level='info', metadata=None):
    agent_id = state.get('bug_info', {}).get('assigned_agent_id')
    if not agent_id:
        return
    import aiosqlite
    import json
    from ..config import get_settings
    db_path = get_settings().database_url.replace('sqlite+aiosqlite:///', '')
    async with aiosqlite.connect(db_path) as db:
        await db.execute('''INSERT INTO agent_logs (agent_id, bug_id, log_level, message, metadata)
            SELECT agent_id, current_bug_id, ?, ?, ? FROM agents
            WHERE agent_id=? AND worktree_path=?''', (level, message, json.dumps(metadata, ensure_ascii=False) if metadata else None, agent_id, state['worktree_path']))
        await db.commit()


def thinking_stream(state):
    agent_id = state.get('bug_info', {}).get('assigned_agent_id')
    if not agent_id:
        return None
    return ThinkingStream(agent_id, lambda message, level, metadata: record_source_operation(state, message, level, metadata))


async def fetch_bug_node(state: BugFixState) -> Dict[str, Any]:
    """获取 Bug 详情"""
    logger.info(f"Fetching bug: {state['bug_id']}")

    try:
        from ..api.bugs import get_bug_details
        import aiosqlite
        db_path = get_settings().database_url.replace('sqlite+aiosqlite:///', '')
        snapshot = dict(state.get('input_snapshot') or {})
        if 'supplement' not in snapshot:
            async with aiosqlite.connect(db_path) as db:
                snapshot['supplement'] = await read_supplement(db, state['bug_id'])
        if 'bug' not in snapshot:
            details = await get_bug_details(state['bug_id'])
            snapshot['bug'] = await capture_bug(db_path, details)
        bug_info = apply_input_snapshot(db_path, snapshot)
        bug_info["id"] = state["bug_id"]
        bug_info['repair_feedback'] = state.get('repair_feedback', '')
        # 验收反馈截图排在 Bug 原图之后，与原图共用编号和按需看图机制。
        feedback_images = await latest_feedback_images(db_path, state['bug_id'])
        bug_info['feedback_image_start'] = len(bug_info['agent_images']) + 1
        bug_info['feedback_image_names'] = [name for name, _ in feedback_images]
        bug_info['agent_images'] += [url for _, url in feedback_images]

        return {
            "bug_info": bug_info,
            "input_snapshot": snapshot,
            "stage": "analyzing"
        }

    except Exception as e:
        message = str(e) or type(e).__name__
        logger.error(f"Failed to fetch bug: {message}")
        return {
            "error": message,
            "stage": "completed"
        }


async def analyze_root_cause_node(state: BugFixState) -> Dict[str, Any]:
    """分析根因：症状契约、假设、失败的复现测试、独立审查。"""
    logger.info(f"Analyzing root cause for bug: {state['bug_id']}")
    visual_context = state.get('visual_context')

    try:
        llm = LLMFactory.create(stage="analyze")
        visual_context = await ensure_visual_context(state, llm)
        context = await repository_context(state['worktree_path'])
        visual = visual_prompt(visual_context)
        prompt = build_agent_prompt('analyze', state, **context) + visual
        result = await run_analysis(
            llm, LLMFactory.create(stage="analyze"), state['worktree_path'], prompt, bug=review_bug(state), visual=visual,
            images=state['bug_info'].get('agent_images'), previous_attempt=state.get('previous_attempt'), preview=agent_preview(state),
            on_operation=lambda message: record_source_operation(state, message), stream=thinking_stream(state))
        logger.info(f"Analysis complete: confidence={result['confidence']}")
        return {
            "root_cause": result['root_cause'],
            "analysis": result['analysis'],
            "repro": result['repro'],
            "review": result['review'],
            "proposals": result['proposals'],
            "source_context": result['source_context'],
            "visual_context": visual_context,
            "confidence": result['confidence'],
            "stage": "proposing"
        }

    except Exception as e:
        logger.error(f"Failed to analyze: {e}")
        return {
            "error": str(e) or type(e).__name__,
            "visual_context": visual_context,
            "stage": "completed"
        }


async def wait_user_decision_node(state: BugFixState) -> Dict[str, Any]:
    """等待用户决策"""
    logger.info(f"Waiting for user decision: {state['bug_id']}")

    # 设置等待标志
    return {
        "waiting_for_user": True
    }


async def design_proposal_node(state: BugFixState) -> Dict[str, Any]:
    """方案随分析一起产出（分析 Agent 手握源码和复现结果）；此处只做校验并进入人工确认。"""
    logger.info(f"Proposals ready for bug: {state['bug_id']}")
    proposals = state.get('proposals')
    if not isinstance(proposals, list) or not proposals:
        return {"error": '分析没有产出可执行的修复方案，请重新执行分析', "stage": "completed"}
    return {"proposals": proposals, "stage": "awaiting_plan_confirmation", "waiting_for_user": True}


async def implement_fix_node(state: BugFixState) -> Dict[str, Any]:
    """实施修复：续接分析会话，复现测试受保护，提交前系统重新运行复现命令并经审查。"""
    logger.info(f"Implementing fix for bug: {state['bug_id']}")
    visual_context = state.get('visual_context')

    try:
        import json
        bug = state['bug_info']
        selected = state.get('selected_proposal') or state['proposals'][0]
        llm = LLMFactory.create(stage='fix')
        visual_context = await ensure_visual_context(state, llm)
        context = await repository_context(state['worktree_path'])
        visual = visual_prompt(visual_context)
        prompt = build_agent_prompt('fix', state, proposal=json.dumps(selected, ensure_ascii=False), repro=repro_text(state.get('repro'))) + visual
        result = await run_fix(
            llm, LLMFactory.create(stage='analyze'), state['worktree_path'], prompt, bug=review_bug(state), analysis=state.get('analysis'), repro=state.get('repro'),
            visual=visual, images=bug.get('agent_images'), previous_attempt=state.get('previous_attempt'), instructions=context['instructions'], preview=agent_preview(state),
            on_operation=lambda message: record_source_operation(state, message), stream=thinking_stream(state))
        files = await changed_files(state['worktree_path'])
        if not files:
            raise ValueError('执行器没有产生实际代码修改，不能标记为修复成功')
        return {'modified_files': files, 'fix_summary': result['summary'], 'repro': result['repro'], 'review': result['review'],
                'source_context': result['source_context'], 'visual_context': visual_context, 'stage': 'verifying'}
    except Exception as exc:
        files = await changed_files(state['worktree_path']) if state.get('worktree_path') else []
        return {'error': str(exc) or type(exc).__name__, 'visual_context': visual_context, 'modified_files': files, 'stage': 'completed'}


async def verify_fix_node(state: BugFixState) -> Dict[str, Any]:
    """仅以实际命令退出结果判定验证是否通过。"""
    try:
        verified = await verify_and_commit_repair(state['worktree_path'], state['bug_info'].get('test_command'), state['bug_id'], state.get('repair_branch'), state.get('base_commit'), bug_title=state['bug_info'].get('title') or '', repro_command=(state.get('repro') or {}).get('command'))
        result = verified['test_results']
        if not result['passed']:
            return {'test_results': result, 'error': f"验证失败（退出码 {result['exit_code']}）：{result['output'][-3000:]}", 'stage': 'completed'}
        return {**verified, 'stage': 'awaiting_confirmation', 'waiting_for_user': True}
    except Exception as exc:
        return {'error': str(exc) or type(exc).__name__, 'stage': 'completed'}


async def prepare_repository_node(state: BugFixState) -> Dict[str, Any]:
    """分析前准备隔离仓库，不覆盖调用方的分支或未提交修改。"""
    try:
        config = state.get('bug_config') or {}
        prepared = await prepare_worktree(config or state['bug_info'])
        info = dict(state.get('bug_info') or {})
        for key in ('repo_path', 'preview_command', 'preview_env_files', 'preview_port'):
            if key in config:
                info[key] = config[key]
        return {**prepared, 'bug_info': info, 'stage': 'analyzing'}
    except Exception as exc:
        return {'error': str(exc) or type(exc).__name__, 'stage': 'completed'}
