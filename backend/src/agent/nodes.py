"""Agent 节点实现"""
import time
import re
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
    """分析根因：症状契约、假设、失败的复现测试。"""
    start_time = time.time()
    logger.info(f"Analyzing root cause for bug: {state['bug_id']}")
    visual_context = state.get('visual_context')

    try:
        llm = LLMFactory.create(stage="analyze")
        visual_context = await ensure_visual_context(state, llm)
        context = await repository_context(state['worktree_path'])
        visual = visual_prompt(visual_context)
        prompt = build_agent_prompt('analyze', state, **context) + visual
        if state['bug_info'].get('assigned_agent_id'):
            from ..core.conversation import history_context
            prompt += await history_context(state['bug_info']['assigned_agent_id'])
        result = await run_analysis(
            llm, LLMFactory.create(stage="analyze"), state['worktree_path'], prompt, bug=review_bug(state), visual=visual,
            images=state['bug_info'].get('agent_images'), previous_attempt=state.get('previous_attempt'), preview=agent_preview(state),
            on_operation=lambda message: record_source_operation(state, message), stream=thinking_stream(state))

        elapsed_time = time.time() - start_time
        logger.info(f"Analysis complete: confidence={result['confidence']}, time={elapsed_time:.1f}s")

        # 记录阶段耗时到日志
        await record_source_operation(
            state,
            f"分析阶段完成，耗时 {elapsed_time:.1f} 秒（{elapsed_time/60:.1f} 分钟）",
            level='info',
            metadata={'stage': 'analyze', 'duration_seconds': elapsed_time}
        )

        return {
            "root_cause": result['root_cause'],
            "analysis": result['analysis'],
            "repro": result['repro'],
            "review": result['review'],
            "proposals": result['proposals'],
            "source_context": result['source_context'],
            "visual_context": visual_context,
            "confidence": result['confidence'],
            "stage_duration": elapsed_time,  # 新增：阶段耗时
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
    """方案随分析一起产出（分析 Agent 手握源码和复现结果）；根据配置判断是否需要人工确认。

    支持三种确认模式：
    - always: 总是需要确认（保守，适合生产环境）
    - smart: 智能判断是否需要确认（默认，推荐）
    - never: 从不需要确认（激进，适合开发环境）
    """
    logger.info(f"Proposals ready for bug: {state['bug_id']}")
    proposals = state.get('proposals')
    if not isinstance(proposals, list) or not proposals:
        return {"error": '分析没有产出可执行的修复方案，请重新执行分析', "stage": "completed"}

    # 加载确认策略配置
    from ..core.confirmation_manager import load_confirmation_config
    config = load_confirmation_config()

    # 根据配置模式决定是否需要确认
    if config.mode == 'always':
        # 总是需要确认
        logger.info("Confirmation mode: always - requiring user confirmation")
        await record_source_operation(state, "⚙️ 配置为总是确认方案")
        return {
            "proposals": proposals,
            "stage": "awaiting_plan_confirmation",
            "waiting_for_user": True
        }
    elif config.mode == 'never':
        # 从不需要确认
        logger.info("Confirmation mode: never - auto-approving all proposals")
        await record_source_operation(state, "⚙️ 配置为自动执行（从不确认）")
        return {
            "proposals": proposals,
            "selected_proposal": proposals[0],
            "stage": "fixing",
            "waiting_for_user": False
        }
    else:
        # smart 模式：智能判断
        need_confirmation = _should_require_user_confirmation(state, proposals, config)

        if need_confirmation:
            reason = state.get('confirmation_reason', '方案需要人工确认')
            logger.info(f"Smart mode: requiring confirmation - {reason}")
            await record_source_operation(state, f"需要用户确认方案：{reason}")
            return {
                "proposals": proposals,
                "stage": "awaiting_plan_confirmation",
                "waiting_for_user": True
            }
        else:
            # 自动选择第一个方案并继续执行
            logger.info("Smart mode: auto-approving - high confidence and low risk")
            await record_source_operation(state, "✅ 自动执行修复（置信度高、风险低）")
            return {
                "proposals": proposals,
                "selected_proposal": proposals[0],
                "stage": "fixing",
                "waiting_for_user": False
            }


def _should_require_user_confirmation(state: BugFixState, proposals: list, config) -> bool:
    """智能判断是否需要用户确认方案（基于配置规则）。

    返回 True 表示需要确认，False 表示自动执行。
    """
    from ..models.confirmation_config import ConfirmationConfig

    bug_info = state.get('bug_info', {})
    title = (bug_info.get('title') or '').lower()
    description = (bug_info.get('description') or '').lower()

    analysis = state.get('analysis') or {}
    classified = analysis.get('decision_schema_version') == 1
    # 自动执行的是推荐的第一项，仅评估这一项实际将执行的操作。
    impacts = [proposal.get('impact') for proposal in proposals[:1]]
    impact_categories = {category for impact in impacts if isinstance(impact, dict) for category in impact.get('categories', [])}

    # 检查强制确认模式（优先级最高）
    for pattern in config.force_confirm_patterns:
        if re.search(pattern, title, re.IGNORECASE):
            state['confirmation_reason'] = f'匹配强制确认规则：{pattern}'
            return True

    # 旧报告保留标题豁免；新报告依据实际风险，不让标题跳过决策。
    for pattern in config.auto_approve_patterns:
        if not classified and re.search(pattern, title, re.IGNORECASE):
            logger.info(f"Auto-approved by pattern: {pattern}")
            return False

    reasons = []
    rules = config.rules
    if classified and any(not isinstance(impact, dict) or not impact.get('evidence') or not isinstance(impact.get('categories'), list) for impact in impacts):
        reasons.append('方案缺少实际改动风险评估')

    # 1. 检查置信度
    if rules.require_on_low_confidence:
        confidence = state.get('confidence', 'low')
        if confidence == 'low':
            reasons.append('置信度低（无复现测试）')

    # 2. 检查方案数量
    if rules.require_on_multiple_proposals and not classified:
        if len(proposals) > rules.multiple_proposals_threshold:
            reasons.append(f'多个方案（{len(proposals)} 个）需要选择')

    # 3. 检查是否有复现测试
    if rules.require_on_low_confidence:
        repro = state.get('repro', {})
        if not repro.get('reproduced'):
            reasons.append('未能自动复现问题')

    # 新报告按拟执行操作判断；旧报告保留关键词规则。
    if rules.require_on_high_risk:
        if classified:
            labels = {'data_deletion': '删除数据', 'permission_change': '权限变化', 'breaking_api': '不兼容接口',
                      'external_side_effect': '外部副作用', 'product_behavior_change': '产品行为变化'}
            risky = [label for category, label in labels.items() if category in impact_categories]
            if risky:
                reasons.append('方案涉及：' + '、'.join(risky))
        else:
            text_to_check = f"{title} {description} {state.get('root_cause') or ''}".lower()
            found = [kw for kw in rules.high_risk_keywords if kw in text_to_check]
            if found:
                reasons.append(f'高风险修改（涉及：{", ".join(found[:2])}）')

    # 5. 检查改动范围
    if rules.require_on_large_changes:
        proposal = proposals[0] if proposals else {}
        estimated_files = len(proposal.get('files', []))
        if estimated_files > rules.large_changes_threshold:
            reasons.append(f'改动范围大（预计 {estimated_files} 个文件）')

    # 6. 检查是否有待确认问题
    if rules.require_on_open_questions:
        analysis = state.get('analysis', {})
        open_questions = analysis.get('decision_questions' if classified else 'open_questions', []) if isinstance(analysis, dict) else []
        if open_questions:
            reasons.append(f'有待确认问题（{len(open_questions)} 个）')

    # 7. 检查优先级
    if rules.require_on_critical_priority:
        priority = bug_info.get('priority', 'normal')
        if priority == 'critical':
            reasons.append('关键 bug 需要人工确认')

    # 持久化结构迁移才是结构变更，节点字段/模型名本身不是。
    if rules.require_on_schema_changes:
        if classified:
            if 'schema_migration' in impact_categories:
                reasons.append('涉及持久化数据结构迁移')
        else:
            text_to_check = f"{title} {description} {state.get('root_cause', '')}".lower()
            if any(kw in text_to_check for kw in rules.schema_keywords):
                reasons.append('涉及数据结构变更')

    # 技术假设与验证范围说明不会单独触发暂停。
    need_confirmation = len(reasons) > 0

    if need_confirmation:
        state['confirmation_reason'] = '；'.join(reasons)
        logger.info(f"Requiring confirmation: {reasons}")
    else:
        logger.info("Auto-execution approved: high confidence, single clear solution, low risk")

    return need_confirmation


async def implement_fix_node(state: BugFixState) -> Dict[str, Any]:
    """实施修复：续接分析会话，复现测试受保护，提交前系统重新运行复现命令。"""
    start_time = time.time()
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
        if bug.get('assigned_agent_id'):
            from ..core.conversation import history_context
            prompt += await history_context(bug['assigned_agent_id'])
        result = await run_fix(
            llm, LLMFactory.create(stage='analyze'), state['worktree_path'], prompt, bug=review_bug(state), analysis=state.get('analysis'), repro=state.get('repro'),
            visual=visual, images=bug.get('agent_images'), previous_attempt=state.get('previous_attempt'), instructions=context['instructions'], preview=agent_preview(state),
            on_operation=lambda message: record_source_operation(state, message), stream=thinking_stream(state))
        files = await changed_files(state['worktree_path'])
        if not files:
            raise ValueError('执行器没有产生实际代码修改，不能标记为修复成功')

        elapsed_time = time.time() - start_time
        logger.info(f"Fix implementation complete, time={elapsed_time:.1f}s")

        # 记录阶段耗时到日志
        await record_source_operation(
            state,
            f"修复阶段完成，耗时 {elapsed_time:.1f} 秒（{elapsed_time/60:.1f} 分钟），修改了 {len(files)} 个文件",
            level='info',
            metadata={'stage': 'fix', 'duration_seconds': elapsed_time, 'files_changed': len(files)}
        )

        return {
            'modified_files': files,
            'fix_summary': result['summary'],
            'repro': result['repro'],
            'review': result['review'],
            'source_context': result['source_context'],
            'visual_context': visual_context,
            'stage_duration': elapsed_time,  # 新增：阶段耗时
            'stage': 'verifying'
        }
    except Exception as exc:
        elapsed_time = time.time() - start_time
        files = await changed_files(state['worktree_path']) if state.get('worktree_path') else []
        logger.error(f"Fix implementation failed after {elapsed_time:.1f}s: {exc}")
        return {
            'error': str(exc) or type(exc).__name__,
            'visual_context': visual_context,
            'modified_files': files,
            'stage_duration': elapsed_time,
            'stage': 'completed'
        }


MAX_VERIFY_ROUNDS = 3


async def verify_fix_node(state: BugFixState) -> Dict[str, Any]:
    """验证失败时把输出交回修复，而不是直接停下等人点重新执行。"""
    start_time = time.time()
    rounds = int(state.get('verify_rounds') or 0) + 1
    try:
        verified = await verify_and_commit_repair(state['worktree_path'], state['bug_info'].get('test_command'), state['bug_id'], state.get('repair_branch'), state.get('base_commit'), bug_title=state['bug_info'].get('title') or '', repro_command=(state.get('repro') or {}).get('command'))
        result = verified['test_results']

        elapsed_time = time.time() - start_time

        if result['passed']:
            logger.info(f"Verification passed, time={elapsed_time:.1f}s")
            await record_source_operation(
                state,
                f"验证阶段完成，测试通过，耗时 {elapsed_time:.1f} 秒",
                level='info',
                metadata={'stage': 'verify', 'duration_seconds': elapsed_time, 'passed': True, 'rounds': rounds}
            )
            return {**verified, 'verify_rounds': rounds, 'stage_duration': elapsed_time, 'stage': 'awaiting_confirmation', 'waiting_for_user': True}

        message = f"验证失败（退出码 {result['exit_code']}）：{result['output'][-3000:]}"
        logger.warning(f"Verification failed after {elapsed_time:.1f}s: exit_code={result['exit_code']}")

        if rounds >= MAX_VERIFY_ROUNDS:
            await record_source_operation(
                state,
                f"验证阶段失败，已达到最大重试次数 {rounds}，耗时 {elapsed_time:.1f} 秒",
                level='error',
                metadata={'stage': 'verify', 'duration_seconds': elapsed_time, 'passed': False, 'rounds': rounds}
            )
            return {'test_results': result, 'verify_rounds': rounds, 'stage_duration': elapsed_time, 'error': message, 'stage': 'completed'}

        await record_source_operation(state, f'验证未通过，交回修复（第 {rounds} 次），耗时 {elapsed_time:.1f} 秒')
        return {'test_results': result, 'verify_rounds': rounds, 'stage_duration': elapsed_time, 'retry_context': message + '\n请直接修正验证指出的问题，然后继续。', 'stage': 'fixing'}
    except Exception as exc:
        elapsed_time = time.time() - start_time
        logger.error(f"Verification error after {elapsed_time:.1f}s: {exc}")
        return {'error': str(exc) or type(exc).__name__, 'verify_rounds': rounds, 'stage_duration': elapsed_time, 'stage': 'completed'}


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
