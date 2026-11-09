"""Pi SDK 子进程桥接。模型凭据通过 stdin 传递，工具执行和阶段校验仍在宿主中。"""
import asyncio
import contextlib
import json
import os
import signal
import time
from pathlib import Path

from ..config import get_settings
from .agent_tools import ToolError, command_env
from .tool_agent import repair_unicode_escapes, session_path
from .context_management import ContextRun

BRIDGE = Path(__file__).resolve().parents[2] / 'pi' / 'bridge.mjs'


async def _run_pi_agent(llm, messages, tools, *, context, max_turns=80, on_operation=None, stream=None,
                       deadline=None, log_context='agent', temperature=None, workspace=None):
    if workspace is None:
        raise ValueError('Pi 执行需要隔离工作树')
    if not (BRIDGE.parent / 'node_modules' / '@earendil-works' / 'pi-coding-agent' / 'package.json').exists():
        raise ValueError('Pi 依赖未安装，请在 backend/pi 执行 npm ci（Node.js >=22.19）')
    provider = llm.client.provider
    settings = get_settings()
    path = session_path(workspace.root).with_suffix('.chat.pi.jsonl' if log_context == 'discussion' else '.pi.jsonl')
    config = {
        'type': 'start', 'cwd': str(workspace.root), 'session': str(path.resolve()),
        'baseUrl': provider.base_url, 'apiKey': provider.api_key, 'model': llm.model,
        'maxTokens': llm.defaults.get('max_tokens', 16000), 'maxTurns': max_turns,
        'contextWindow': context.budget.window,
        'reserveTokens': context.budget.window - context.budget.trigger,
        'keepRecentTokens': min(20000, max(2048, context.budget.target // 2)),
        'inputLimit': context.budget.limit,
        'temperature': temperature if temperature is not None else llm.defaults.get('temperature'),
        'system': messages[0]['content'], 'tools': [tool.schema() for tool in tools],
        'prompt': messages[-1]['content'],
    }
    process = await asyncio.create_subprocess_exec(settings.pi_node_command, str(BRIDGE),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=str(workspace.root), env=command_env(), start_new_session=True, limit=32 * 1024 * 1024)
    by_name = {tool.name: tool for tool in tools}
    outcome = None
    terminal_seen = False
    agent_id = getattr(stream, 'agent_id', None)
    from . import conversation
    sent = set()
    ready = asyncio.Event()
    in_question = False

    async def send(value):
        process.stdin.write((json.dumps(value, ensure_ascii=False) + '\n').encode())
        await process.stdin.drain()

    async def stderr():
        while chunk := await process.stderr.read(4096):
            pass  # 排空 stderr 避免阻塞；不记录可能含提供商凭据的原始诊断。

    async def steer():
        await ready.wait()
        while True:
            if agent_id and not in_question and not terminal_seen:
                for item in await conversation.pending(agent_id):
                    if item['id'] not in sent:
                        sent.add(item['id'])
                        await send({'type': 'steer', 'text': f"[会话消息 #{item['id']}]\n{item['text']}\n补充信息不代表批准方案或合并。", 'images': item['images'], 'ids': [item['id']]})
            await asyncio.sleep(0.3)

    reader = asyncio.create_task(stderr())
    steering = asyncio.create_task(steer())
    try:
        await send(config)
        async with asyncio.timeout(max(1, (deadline or time.monotonic() + settings.agent_timeout) - time.monotonic())):
            while line := await process.stdout.readline():
                event = json.loads(line)
                kind = event.get('type')
                if kind == 'ready':
                    ready.set()
                elif kind == 'delivered' and agent_id:
                    await conversation.delivered(agent_id, event['ids'])
                elif kind == 'turn' and stream:
                    await stream.finish()
                    stream.start(f"Pi · {log_context} · 请求 {event['turn']}")
                elif kind == 'text' and stream:
                    await stream.content(event['text'])
                elif kind == 'thinking' and stream:
                    await stream.reasoning(event['text'])
                elif kind == 'assistant' and stream:
                    await stream.say(event['text'])
                elif kind == 'note' and on_operation:
                    await on_operation(event['text'])
                elif kind == 'context_metric':
                    metric = event.get('event')
                    if metric in ('compaction_start', 'compaction', 'request_start', 'request_end', 'budget_blocked'):
                        fields = {key: event[key] for key in ('status', 'reason', 'before_tokens', 'after_tokens',
                            'duration_ms', 'summary_chars', 'estimated_input_tokens', 'input_limit', 'usage', 'turn', 'will_retry',
                            'request_id', 'request_kind') if key in event}
                        context.emit(metric, **fields)
                        if metric == 'compaction' and on_operation:
                            status = event.get('status')
                            if status == 'success':
                                await on_operation(f"Pi 上下文已整理：约 {event.get('before_tokens')} → {event.get('after_tokens')} tokens；历史工具证据可回查")
                            else:
                                await on_operation('Pi 上下文整理已取消' if status == 'cancelled' else 'Pi 上下文整理失败，原会话已保留')
                elif kind == 'tool':
                    name, args = event['name'], repair_unicode_escapes(event['args'])
                    tool = by_name.get(name)
                    result = {'type': 'result', 'id': event['id']}
                    try:
                        if terminal_seen:
                            raise ToolError('当前阶段已经提交')
                        if tool is None:
                            raise ToolError(f'未知工具：{name}')
                        in_question = name == 'ask_user'
                        # A submitted stage must not silently drop steering input still waiting in Pi.
                        if tool.terminal and agent_id and await conversation.pending(agent_id):
                            raise ToolError('用户刚补充了信息，请先读取下一轮用户消息并处理，再提交结果')
                        value = await context.execute(tool, event['id'], args)
                        if tool.terminal:
                            async with conversation.lock_for(agent_id):
                                if agent_id and await conversation.pending(agent_id):
                                    raise ToolError('验证期间收到用户补充，请处理后重新提交')
                                terminal_seen = True
                                outcome = (name, value)
                            result.update(text='已提交', terminal=True)
                        elif isinstance(value, dict) and 'images' in value:
                            result.update(text=value['text'], images=value['images'])
                        else:
                            result['text'] = str(value)[:120000]
                    except (ToolError, ValueError, KeyError, TypeError, OSError) as exc:
                        result['error'] = str(exc)
                    finally:
                        in_question = False
                    await send(result)
                    if on_operation:
                        detail = result.get('error') or ('已提交' if result.get('terminal') else '完成')
                        await on_operation(f'源码操作 Pi（{name}）：{detail[:500]}')
                elif kind in ('fatal', 'done'):
                    if kind == 'fatal' or not event.get('submitted') or outcome is None:
                        raise ValueError(str(event.get('error') or 'Pi 未提交有效结果').replace(provider.api_key, '[redacted]') if provider.api_key else str(event.get('error') or 'Pi 未提交有效结果'))
                    await asyncio.wait_for(process.wait(), 10)
                    return outcome
            # Do not echo arbitrary subprocess stderr, which may contain provider payloads.
            raise ValueError(f'Pi 子进程意外退出（退出码 {await process.wait()}），会话与工作树已保留')
    finally:
        steering.cancel()
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), 3)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
        reader.cancel()
        await asyncio.gather(steering, reader, return_exceptions=True)
        if stream:
            await stream.finish()


async def run_pi_agent(llm, messages, tools, **kwargs):
    workspace = kwargs.get('workspace')
    if workspace is None:
        raise ValueError('Pi 执行需要隔离工作树')
    context = ContextRun(workspace, engine='pi', phase=kwargs.get('log_context', 'agent'), llm=llm,
                         agent_id=getattr(kwargs.get('stream'), 'agent_id', None))
    status = 'failed'
    try:
        result = await _run_pi_agent(llm, messages, [*tools, context.evidence_tool()], context=context, **kwargs)
        status = 'submitted'
        return result
    except asyncio.CancelledError:
        status = 'cancelled'
        raise
    finally:
        context.finish(status)
