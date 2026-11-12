"""消息分发与只读讨论；待验收/待确认方案时由模型判断用户意图，harness 校验后执行对应步骤。"""
import asyncio
import json
import re
from pathlib import Path

import aiosqlite
from loguru import logger

from . import conversation
from .agent_tools import ToolError, Workspace, build_tools
from .agent_stream import ThinkingStream
from .agent_runtime import run_tool_agent
from .tool_agent import submit_tool
from ..llm.factory import LLMFactory

tasks = {}

# 用户消息里每种意图对应的下一步。none 只回答，不改变流程。
ACTIONS = {
    'awaiting_confirmation': {
        'none': '仅回答或澄清，不改变流程',
        'accept': '验收通过：合并到修复分支，清理 Agent 分支与工作树，同步云效状态',
        'refix': '根因与方案认可，实现不到位：在原工作树保留已有修改继续修复，再次验证后请用户验收',
        'replan': '根因认可，方案需要更换或优化：新一轮保留根因、重新制定方案，等用户确认后再修复',
        'reanalyze': '根因不对：新一轮重新分析根因并给出方案',
    },
    'awaiting_plan_confirmation': {
        'none': '仅回答或澄清，不改变流程',
        'approve_plan': '确认方案：按 proposal_index 指定的方案开始修改代码',
        'revise_plan': '对理解或方案有意见：带着用户意见重新分析并生成新方案，再请用户确认',
    },
}
APPROVALS = {'accept', 'approve_plan'}
_QUESTION = re.compile(r'[?？]|(?:吗|么|嘛|呢)[\s。.!！~～]*$')
_RESERVATION = re.compile(r'但|不过|可是|然而|只是|就是有|还有(?:个|一个|些|点)?问题|(?<!没)有问题|仍然|依然|还是(?:不|有|没)|没(?:有)?修好|不对|不行|不太|如果|假如|要是|先不|先别|别合|不要合|不合并|再看看|稍等|等一下')


def approval_blocker(text):
    """模型判为同意时的硬性复核：问句、保留意见、条件句一律不算批准。"""
    text = (text or '').strip()
    if not text:
        return '为空'
    if _QUESTION.search(text):
        return '是问句'
    if _RESERVATION.search(text):
        return '带有保留意见或条件'
    return None


def busy(agent_id):
    task = tasks.get(agent_id)
    return task is not None and not task.done()


async def stop(agent_id):
    task = tasks.get(agent_id)
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def stop_all():
    for agent_id in list(tasks):
        await stop(agent_id)


def start(agent_id):
    if not busy(agent_id):
        task = asyncio.create_task(discuss(agent_id))
        tasks[agent_id] = task
        def finished(done):
            if tasks.get(agent_id) is done:
                tasks.pop(agent_id, None)
            if not done.cancelled() and done.exception() is None and done.result() is True:
                asyncio.create_task(resume_pending(agent_id))
        task.add_done_callback(finished)


async def resume_pending(agent_id, runner=None):
    from .orchestrator import get_orchestrator
    runner = runner or get_orchestrator()
    async with runner._agent_control_lock, conversation.lock_for(agent_id):
        if not runner._running or agent_id in runner.running_agents:
            return
        async with aiosqlite.connect(conversation.db_path()) as db:
            row = await (await db.execute('SELECT status FROM agents WHERE agent_id=?', (agent_id,))).fetchone()
        if row:
            try:
                await dispatch_pending(agent_id, runner, status=row[0], pending=await conversation.pending(agent_id))
            except (ValueError, RuntimeError) as exc:
                logger.warning('待处理消息暂时无法启动：{}；{}', agent_id, exc)


async def dispatch_pending(agent_id, runner, *, status, pending):
    """调用方持有控制和会话锁；即时消息与结束后的待处理消息使用同一规则。"""
    if not pending or status in ('busy', 'paused') or agent_id in runner.running_agents:
        return None
    resume_ids = [item['id'] for item in pending if conversation.is_resume_request(item['text'])]
    if status == 'error' and resume_ids:
        result = await runner._retry_agent_locked(agent_id)
        await conversation.delivered(agent_id, resume_ids)
        return result
    start(agent_id)
    return None


async def discuss(agent_id):
    try:
        async with aiosqlite.connect(conversation.db_path()) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute('''SELECT a.worktree_path, a.status, b.title, b.description, h.result
                FROM agents a JOIN bugs b ON a.current_bug_id=b.bug_id
                LEFT JOIN task_history h ON h.id=(SELECT MAX(id) FROM task_history WHERE agent_id=a.agent_id)
                WHERE a.agent_id=?''', (agent_id,))).fetchone()
        if not row:
            return
        saved = json.loads(row['result'] or '{}')
        worktree = row['worktree_path']
        # 工作树已清理时，用独立空目录回复历史，不把业务仓库暴露给讨论执行器。
        if not worktree or not Path(worktree).is_dir():
            worktree = Path(conversation.db_path()).resolve().parent / 'discussion_workspaces' / agent_id
            worktree.mkdir(parents=True, exist_ok=True)
        workspace = Workspace(worktree, mode='read')
        async def record(message, level, metadata):
            async with aiosqlite.connect(conversation.db_path()) as db:
                await db.execute('''INSERT INTO agent_logs (agent_id, bug_id, log_level, message, metadata)
                    SELECT agent_id, current_bug_id, ?, ?, ? FROM agents WHERE agent_id=?''',
                    (level, message, json.dumps(metadata) if metadata else None, agent_id))
                await db.commit()
        stream = ThinkingStream(agent_id, record)
        actions = ACTIONS.get(row['status'])
        proposals = saved.get('proposals') or []
        history = await conversation.messages(agent_id, load_images=False)
        # 决策阶段把最新消息直接交给模型判断意图，不经执行器的“补充不代表批准”包装。
        incoming = await conversation.pending(agent_id) if actions else []
        if incoming:
            await conversation.delivered(agent_id, [item['id'] for item in incoming])
        latest = incoming[-1]['text'] if incoming else next((m['text'] for m in reversed(history) if m['role'] == 'user'), '')
        user_text = '\n'.join(item['text'] for item in incoming) or latest
        images = [url for item in incoming for url in item['images']][:4]

        async def reply(args):
            text = args.get('reply')
            if not isinstance(text, str) or not text.strip():
                raise ToolError('回复不能为空')
            action = args.get('action') or 'none'
            if action not in (actions or {'none': ''}):
                raise ToolError(f'当前阶段不支持 action={action}')
            if action in APPROVALS:
                evidence = (args.get('evidence') or '').strip()
                if not evidence or evidence not in latest:
                    raise ToolError('evidence 必须逐字引用用户最新消息中表示同意的原话')
                blocker = approval_blocker(latest)
                if blocker:
                    raise ToolError(f'用户最新消息{blocker}，不能判为同意。请按提问或保留意见处理：回答/澄清用 action=none，或选择对应的返工动作。')
            index = args.get('proposal_index', 0)
            if action in ('approve_plan', 'revise_plan') and (type(index) is not int or not 0 <= index < len(proposals)):
                raise ToolError(f'proposal_index 必须是 0 到 {len(proposals) - 1} 之间的整数')
            return {'reply': text.strip(), 'action': action, 'proposal_index': index, 'feedback': (args.get('feedback') or '').strip()}

        properties = {'reply': {'type': 'string', 'description': '给用户的回复'}}
        system = '你是当前 Bug 修复会话的工程师。现在仅讨论，不修改代码、执行命令或批准交付。可以读取源码解释根因与方案。回答完成调用 submit_reply。不要声称已实施修改。'
        if actions:
            properties.update(
                action={'type': 'string', 'enum': list(actions), 'description': '；'.join(f'{k}：{v}' for k, v in actions.items())},
                evidence={'type': 'string', 'description': 'action 为同意类时，逐字引用用户最新消息中表示同意的原话'},
                feedback={'type': 'string', 'description': '返工类动作：用一两句话概括用户指出的问题与要求，交给下一步执行'},
            )
            if row['status'] == 'awaiting_plan_confirmation':
                properties['proposal_index'] = {'type': 'integer', 'description': 'context.proposals 的下标，从 0 开始'}
            stage = ('修复已完成并通过自动验证，正在等待用户人工验收。此阶段的“下一步”就是验收通过后合并交付。'
                     if row['status'] == 'awaiting_confirmation' else
                     '根因分析与修复方案已完成，正在等待用户确认方案。此阶段的“下一步”就是按方案开始修改代码。')
            system = (f'你是当前 Bug 修复会话的工程师。{stage}\n'
                      '用户最新消息就是对当前阶段的意见。请判断其意图并调用 submit_reply 提交 action，harness 会据此执行下一步：\n'
                      + '\n'.join(f'- {k}：{v}' for k, v in actions.items()) +
                      '\n判定规则：\n'
                      '1. 只有用户明确表示满意或同意继续（如“没问题”“已修复”“很好，继续下一步吧”“可以合并”“方案 2 可以，开始吧”）才选同意类动作，并在 evidence 中逐字引用原话。\n'
                      '2. 问句（如“可以合并了吗？”）、附带任何问题或保留意见（“基本没问题，但……”）、条件句（“如果……就合并”）都不是同意。\n'
                      '3. 指出仍有问题时，按问题性质选择返工动作：实现遗漏、细节不对、仍有残留现象选 refix；认为思路或方案不合适选 replan；明确说原因找错了选 reanalyze。无法分辨时选 none 并简短反问。\n'
                      '4. 多个方案而用户没指明选哪个时，选 none 并反问。\n'
                      '5. reply 用一两句话说明你的理解与即将执行的动作（例如“判断为验收通过，开始合并到修复分支并清理工作树”），不要声称已经完成；action=none 时正常回答。\n'
                      '可以读取源码辅助判断，但不修改代码。')
        tools = build_tools(workspace, commands=False) + [submit_tool('submit_reply', '提交给用户的回复' + ('与下一步动作。' if actions else '，不批准或执行代码修改。'),
            {'type': 'object', 'properties': properties, 'required': ['reply'] + (['action'] if actions else [])}, reply)]
        context = {key: saved.get(key) for key in ('root_cause', 'analysis', 'proposals', 'fix_summary', 'repair_summary', 'test_results', 'error')}
        payload = {'bug': row['title'], 'description': row['description'], 'status': row['status'], 'context': context,
                   'history': [{'id': m['id'], 'role': m['role'], 'text': m['text'], 'image_count': len(m['images'])} for m in history[-40:] if m['status'] != 'queued']}
        content = json.dumps(payload, ensure_ascii=False)
        if incoming:
            content += '\n\n用户最新消息：\n' + user_text
            if images:
                content = [{'type': 'text', 'text': content}] + [{'type': 'image_url', 'image_url': {'url': url}} for url in images]
        messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': content}]
        _, answer = await run_tool_agent(LLMFactory.create(stage='analyze'), messages, tools, workspace=workspace, stream=stream,
            log_context='discussion', max_turns=20, on_operation=lambda text: record(text, 'info', None))
        await stream.say(answer['reply'])
        if answer['action'] != 'none':
            try:
                result = await execute_action(agent_id, answer, user_text=user_text, images=images, saved=saved)
            except (ValueError, RuntimeError, KeyError) as exc:
                result = f'未能执行：{exc}'
            await conversation.append_assistant(agent_id, result)
        return True
    except asyncio.CancelledError:
        raise
    except Exception:
        await conversation.append_assistant(agent_id, '这次对话未能完成。消息已保存，请稍后重试；修复和验收状态没有改变。')


async def execute_action(agent_id, answer, *, user_text, images, saved):
    """与页面按钮走同一套校验与交付逻辑，返回给用户的执行结果。"""
    from .orchestrator import get_orchestrator
    from .repair_review import settle_repair
    from ..models.agent import RepairConfirmation
    runner = get_orchestrator()
    db_path = conversation.db_path()
    action = answer['action']
    feedback = '\n\n'.join(filter(None, [answer['feedback'], f'用户原话：{user_text}' if user_text else '']))[:4000]
    if action == 'accept':
        result = await settle_repair(db_path, runner, agent_id, RepairConfirmation(confirmed=True, git_commit_hash=saved.get('git_commit_hash')))
        sync = result.get('yunxiao_sync') or {}
        if sync.get('status') == 'succeeded':
            result['message'] += '；云效 Bug 已更新为处理中'
        elif sync.get('status') == 'failed':
            result['message'] += f"；云效状态同步失败：{sync.get('error')}，可在验收面板重试同步"
        return result['message']
    if action == 'refix':
        async with runner._agent_control_lock:
            result = await runner.continue_fix(agent_id, feedback, images)
        return f"已保留根因与方案，在原工作树继续修复（第 {result['round']} 轮）；验证通过后会再次请你验收。"
    if action in ('replan', 'reanalyze'):
        result = await settle_repair(db_path, runner, agent_id, RepairConfirmation(
            confirmed=False, feedback=feedback, images=images, rework='proposal' if action == 'replan' else 'analysis'))
        restart = result.get('restart') or {}
        if not restart.get('started'):
            return f"反馈已保存。{restart.get('reason') or '自动重新修复未启动，可手动重新执行'}"
        step = '保留根因、重新制定修复方案' if action == 'replan' else '重新分析根因'
        return f"已开始第 {restart['round']} 轮：{step}；新方案生成后会请你确认。"
    proposal = (saved.get('proposals') or [])[answer['proposal_index']]
    async with runner._agent_control_lock:
        if action == 'approve_plan':
            await runner.confirm_proposal(agent_id, answer['proposal_index'])
            return f"已按「{proposal.get('name') or '所选方案'}」开始修改代码。"
        await runner.confirm_proposal(agent_id, answer['proposal_index'], feedback, None, images)
    return '已带着你的意见重新分析，新方案生成后会再次请你确认。'
