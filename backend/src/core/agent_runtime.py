"""两个执行内核共用工具、阶段约束与对话消息；同一任务禁止重入。"""
import asyncio
import time
from dataclasses import replace

from ..config import get_settings
from . import conversation
from .agent_tools import Tool, ToolError
from .tool_agent import run_tool_agent as run_native_agent


async def run_tool_agent(llm, messages, tools, **kwargs):
    stream = kwargs.get('stream')
    agent_id = getattr(stream, 'agent_id', None)
    deadline = kwargs.get('deadline') or time.monotonic() + get_settings().agent_timeout
    if agent_id:
        async with conversation.lock_for(agent_id):
            if agent_id in conversation.active:
                raise ValueError('该会话正在执行，请等待当前操作完成')
            conversation.active[agent_id] = asyncio.current_task()

        async def ask(args):
            question = args.get('question')
            if not isinstance(question, str) or not question.strip():
                raise ToolError('question 不能为空')
            await stream.say(question.strip())
            while time.monotonic() < deadline:
                items = await conversation.pending(agent_id)
                if items:
                    await conversation.delivered(agent_id, [item['id'] for item in items])
                    return {'text': '\n'.join(item['text'] for item in items), 'images': [url for item in items for url in item['images']]}
                await asyncio.sleep(0.5)
            raise ToolError('等待用户回复超时；消息保留，可重新执行')

        async def view_chat_image(args):
            url = await conversation.image_data(agent_id, args.get('message_id'), args.get('index', 1) - 1)
            return {'text': '用户会话截图，作为需求与现象的补充资料。', 'images': [url]}

        tools = [*tools, Tool('view_conversation_image', '按消息编号查看历史对话中的截图，index 从 1 开始。',
                             {'type': 'object', 'properties': {'message_id': {'type': 'integer'}, 'index': {'type': 'integer'}}, 'required': ['message_id', 'index']}, view_chat_image), Tool('ask_user', '只有调查代码、测试、文档和历史反馈后，仍存在必须由用户决定且实质改变修复方向的问题，才提问并等待回复。给出推荐选项和影响。技术定位、环境配置、测试覆盖不足应自行处理或记录，不能转交用户决策；不能用它请求合并批准。',
                             {'type': 'object', 'properties': {'question': {'type': 'string'}}, 'required': ['question']}, ask, readonly=False)]
        messages[0] = {**messages[0], 'content': messages[0]['content'] + '\n先自行调查，只有无法确定兼容默认值的关键产品决策才调用 ask_user，优先一次询问当前阻塞问题。已回答的问题不要重复询问；用户会在对话中回复。用户聊天不代表批准实施或合并。'}
        tracked_tools = []
        for tool in tools:
            async def tracked(args, tool=tool):
                await stream.finish()
                token = stream.tool_started(tool.name)
                try:
                    return await tool.handler(args)
                finally:
                    stream.tool_finished(token)
            tracked_tools.append(replace(tool, handler=tracked))
        tools = tracked_tools
    try:
        from .runtime_config import engine
        if engine() == 'pi':
            from .pi_agent import run_pi_agent
            return await run_pi_agent(llm, messages, tools, **kwargs)
        return await run_native_agent(llm, messages, tools, **kwargs)
    finally:
        if agent_id:
            async with conversation.lock_for(agent_id):
                conversation.active.pop(agent_id, None)
