"""消息分发与只读讨论；失败续跑交回调度器，不替代方案/验收审批。"""
import asyncio
import json
from pathlib import Path

import aiosqlite
from loguru import logger

from . import conversation
from .agent_tools import Workspace, build_tools
from .agent_stream import ThinkingStream
from .agent_runtime import run_tool_agent
from .tool_agent import submit_tool
from ..llm.factory import LLMFactory

tasks = {}


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
        async def reply(args):
            text = args.get('reply')
            if not isinstance(text, str) or not text.strip():
                raise ValueError('回复不能为空')
            return text.strip()
        tools = build_tools(workspace, commands=False) + [submit_tool('submit_reply', '提交给用户的回复，不批准或执行代码修改。',
            {'type': 'object', 'properties': {'reply': {'type': 'string'}}, 'required': ['reply']}, reply)]
        history = await conversation.messages(agent_id, load_images=False)
        context = {key: saved.get(key) for key in ('root_cause', 'analysis', 'proposals', 'fix_summary', 'test_results', 'error')}
        messages = [
            {'role': 'system', 'content': '你是当前 Bug 修复会话的工程师。现在仅讨论，不修改代码、执行命令或批准交付。可以读取源码解释根因与方案。用户要求调整方案时，说明应使用方案反馈操作；验收未通过时，说明应使用“仍未修复”继续修复。回答完成调用 submit_reply。不要声称已实施修改。'},
            {'role': 'user', 'content': json.dumps({'bug': row['title'], 'description': row['description'], 'status': row['status'], 'context': context,
                'history': [{'id': m['id'], 'role': m['role'], 'text': m['text'], 'image_count': len(m['images'])} for m in history[-40:] if m['status'] != 'queued']}, ensure_ascii=False)},
        ]
        _, answer = await run_tool_agent(LLMFactory.create(stage='analyze'), messages, tools, workspace=workspace, stream=stream,
            log_context='discussion', max_turns=20, on_operation=lambda text: record(text, 'info', None))
        await stream.say(answer)
        return True
    except asyncio.CancelledError:
        raise
    except Exception:
        await conversation.append_assistant(agent_id, '这次对话未能完成。消息已保存，请稍后重试；修复和验收状态没有改变。')
