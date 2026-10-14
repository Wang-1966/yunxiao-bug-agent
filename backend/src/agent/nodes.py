"""Agent 节点实现"""
from typing import Dict, Any
from pathlib import Path
from loguru import logger

from .state import BugFixState
from ..llm.factory import LLMFactory
from ..prompts import render_prompt
from ..core.repair_executor import prepare_worktree, repository_agent, changed_files, verify_and_commit_repair, parse_json
from ..config import get_settings
from ..core.bug_media import agent_bug_images
from ..core.bug_inputs import apply_input_snapshot, capture_bug, read_supplement
from ..core.feedback_images import latest_feedback_images
from ..core.repair_attempts import attempt_prompt
from ..core.bug_visual import describe_bug_images, visual_prompt, visual_source
from ..llm.diagnostics import response_summary
from ..core.agent_stream import ThinkingStream

MAX_PROPOSAL_ATTEMPTS = 3


def build_agent_prompt(stage, state, **context):
    bug = state['bug_info']
    style = LLMFactory.get_response_style()
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
    return render_prompt(
        stage,
        bug_id=bug.get('id', state['bug_id']),
        title=bug['title'],
        description=bug.get('description') or '',
        feedback=feedback or '无',
        previous_attempt=attempt_prompt(state.get('previous_attempt')),
        root_cause=state.get('root_cause') or 'N/A',
        response_style=render_prompt(f'styles/{style}'),
        **context,
    )


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
    """分析根因"""
    logger.info(f"Analyzing root cause for bug: {state['bug_id']}")
    visual_context = state.get('visual_context')

    try:
        # 创建 LLM 客户端（使用 analyze 阶段配置）
        llm = LLMFactory.create(stage="analyze")
        visual_context = await ensure_visual_context(state, llm)

        prompt = build_agent_prompt('analyze', state)

        prompt += visual_prompt(visual_context)
        result = await repository_agent(llm, state['worktree_path'], prompt, on_operation=lambda message: record_source_operation(state, message), image_urls=state['bug_info'].get('agent_images'), images_on_demand=True, previous_attempt=state.get('previous_attempt'), stream=thinking_stream(state))
        if not result.get('root_cause'):
            raise ValueError('分析未返回有效的源码根因')
        if result.get('confidence') == 'low':
            raise ValueError('源码分析置信度低，需补充复现步骤或人工确认后再修复：' + str(result['root_cause']))

        logger.info(f"Analysis complete: confidence={result.get('confidence')}")

        return {
            "root_cause": result.get("root_cause"),
            "source_context": result.get('source_context'),
            "visual_context": visual_context,
            "confidence": result.get("confidence", "medium"),
            "need_logging": result.get("need_logging", False),
            "stage": "proposing"
        }

    except Exception as e:
        logger.error(f"Failed to analyze: {e}")
        return {
            "error": str(e) or type(e).__name__,
            "visual_context": visual_context,
            "stage": "completed"
        }


async def add_logging_node(state: BugFixState) -> Dict[str, Any]:
    """添加日志"""
    logger.info(f"Adding logging for bug: {state['bug_id']}")

    try:
        # TODO: 实现添加日志逻辑
        # 1. 识别需要添加日志的位置
        # 2. 生成日志代码
        # 3. 修改文件
        # 4. 提交到 git worktree

        return {
            "stage": "proposing"
        }

    except Exception as e:
        logger.error(f"Failed to add logging: {e}")
        return {
            "error": str(e) or type(e).__name__,
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
    """设计修复方案"""
    logger.info(f"Designing proposal for bug: {state['bug_id']}")

    try:
        # 创建 LLM 客户端（使用 propose 阶段配置）
        llm = LLMFactory.create(stage="propose")

        prompt = build_agent_prompt('propose', state)

        messages = [
            {
                "role": "system",
                "content": "你是修复方案设计器。本阶段没有任何工具，不能读取源码，根因分析已经完成；不要描述接下来要核对或执行什么，直接依据已有信息给出方案。必须只返回一个 JSON 对象，不要输出 Markdown、解释文字或 JSON 之外的内容；对象必须包含 proposals 数组。",
            },
            {"role": "user", "content": prompt + visual_prompt(state.get('visual_context'))},
        ]
        import json
        stream = thinking_stream(state)
        # 部分中转模型会忽略 json_object，只回一句“我先核对……”；把错误反馈给模型有界重试。
        for attempt in range(1, MAX_PROPOSAL_ATTEMPTS + 1):
            response_diagnostics = {}
            log_context = f"{Path(state['worktree_path']).name}/propose/{attempt}"
            if stream:
                stream.start(f'设计方案 · 请求 {attempt}')
            try:
                response = await llm.ainvoke(
                    list(messages),
                    temperature=0.2,
                    response_format={"type": "json_object"},
                    on_response=response_diagnostics.update,
                    log_context=log_context,
                    on_chunk=stream.content if stream else None,
                    on_reasoning=stream.reasoning if stream else None,
                )
            finally:
                if stream:
                    await stream.finish()
            try:
                result = parse_json(response)
                proposals = result.get("proposals", [])
                if not isinstance(proposals, list) or not proposals:
                    raise ValueError('模型未返回可执行的修复方案，proposals 必须是非空数组')
                break
            except ValueError as exc:
                diagnostic = response_diagnostics or response_summary(response)
                message = f'方案模型响应解析失败（第 {attempt}/{MAX_PROPOSAL_ATTEMPTS} 次）：{log_context}；{json.dumps(diagnostic, ensure_ascii=False)}'
                logger.warning(message)
                if stream and isinstance(response, str) and '{' not in response:
                    await stream.say(response)
                await record_source_operation(state, message, 'warning')
                if attempt == MAX_PROPOSAL_ATTEMPTS:
                    raise ValueError(f'连续 {MAX_PROPOSAL_ATTEMPTS} 次未返回有效方案：{exc}') from exc
                messages += [
                    {'role': 'assistant', 'content': response if isinstance(response, str) else ''},
                    {'role': 'user', 'content': f'上一条回复无效：{exc}。你没有工具可用，无需再核对源码；现在直接输出包含 proposals 数组的完整 JSON 对象，JSON 之外不要写任何文字。'},
                ]
        logger.info(f"Generated {len(proposals)} proposals")

        return {
            "proposals": proposals,
            "stage": "awaiting_plan_confirmation",
            "waiting_for_user": True,
        }

    except Exception as e:
        logger.error(f"Failed to design proposal: {e}")
        return {
            "error": str(e) or type(e).__name__,
            "stage": "completed"
        }


async def implement_fix_node(state: BugFixState) -> Dict[str, Any]:
    """实施修复"""
    logger.info(f"Implementing fix for bug: {state['bug_id']}")
    visual_context = state.get('visual_context')

    try:
        import json
        bug = state['bug_info']
        selected = state.get('selected_proposal') or state['proposals'][0]
        llm = LLMFactory.create(stage='fix')
        visual_context = await ensure_visual_context(state, llm)
        prompt = build_agent_prompt('fix', state, proposal=json.dumps(selected, ensure_ascii=False))
        prompt += visual_prompt(visual_context)
        result = await repository_agent(llm, state['worktree_path'], prompt, writable=True, on_operation=lambda message: record_source_operation(state, message), image_urls=bug.get('agent_images'), images_on_demand=True, source_context=state.get('source_context'), previous_attempt=state.get('previous_attempt'), stream=thinking_stream(state))
        files = await changed_files(state['worktree_path'])
        if not files:
            raise ValueError('执行器没有产生实际代码修改，不能标记为修复成功')
        return {'modified_files': files, 'fix_summary': result.get('summary', ''), 'visual_context': visual_context, 'stage': 'verifying'}
    except Exception as exc:
        files = await changed_files(state['worktree_path']) if state.get('worktree_path') else []
        return {'error': str(exc) or type(exc).__name__, 'visual_context': visual_context, 'modified_files': files, 'stage': 'completed'}


async def verify_fix_node(state: BugFixState) -> Dict[str, Any]:
    """仅以实际命令退出结果判定验证是否通过。"""
    try:
        verified = await verify_and_commit_repair(state['worktree_path'], state['bug_info'].get('test_command'), state['bug_id'], state.get('repair_branch'), state.get('base_commit'), bug_title=state['bug_info'].get('title') or '')
        result = verified['test_results']
        if not result['passed']:
            return {'test_results': result, 'error': f"验证失败（退出码 {result['exit_code']}）：{result['output'][-3000:]}", 'stage': 'completed'}
        return {**verified, 'stage': 'awaiting_confirmation', 'waiting_for_user': True}
    except Exception as exc:
        return {'error': str(exc) or type(exc).__name__, 'stage': 'completed'}


async def prepare_repository_node(state: BugFixState) -> Dict[str, Any]:
    """分析前准备隔离仓库，不覆盖调用方的分支或未提交修改。"""
    try:
        prepared = await prepare_worktree(state.get('bug_config') or state['bug_info'])
        return {**prepared, 'stage': 'analyzing'}
    except Exception as exc:
        return {'error': str(exc) or type(exc).__name__, 'stage': 'completed'}
