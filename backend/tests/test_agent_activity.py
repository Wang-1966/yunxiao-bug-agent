import asyncio
from unittest.mock import AsyncMock

import pytest

from src.core.agent_stream import ThinkingStream, live_stream, tool_activity
from src.core.agent_tools import Tool, Workspace
from src.core.agent_runtime import run_tool_agent
from src.core.tool_agent import submit_tool
from fake_agent import ScriptedLLM, turn


def test_parallel_tool_activity_remains_until_last_tool_finishes():
    stream = ThinkingStream('parallel-activity', AsyncMock())
    first = stream.tool_started('read_file')
    second = stream.tool_started('run_command')
    assert tool_activity(stream.agent_id)['tools'] == ['read_file', 'run_command']
    stream.tool_finished(first)
    assert tool_activity(stream.agent_id)['tools'] == ['run_command']
    stream.tool_finished(second)
    assert tool_activity(stream.agent_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True])
async def test_activity_is_visible_during_tool_and_cleared_afterwards(tmp_path, monkeypatch, cancel):
    from src.core import conversation, runtime_config
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), 'database_url', f'sqlite+aiosqlite:///{tmp_path}/tasks.db')
    stream = ThinkingStream('activity-agent', AsyncMock())
    monkeypatch.setattr(conversation, 'pending', AsyncMock(return_value=[]))
    monkeypatch.setattr(conversation, 'delivered', AsyncMock())
    monkeypatch.setattr(runtime_config, 'engine', lambda: 'native')
    started = asyncio.Event()
    release = asyncio.Event()

    async def command(args):
        assert live_stream(stream.agent_id) is None
        assert tool_activity(stream.agent_id)['tools'] == ['run_command']
        started.set()
        await release.wait()
        return 'finished'

    async def accept(args):
        return args

    llm = ScriptedLLM([turn(('run_command', {})), turn(('submit_fix', {}))])
    task = asyncio.create_task(run_tool_agent(llm, [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'task'}],
        [Tool('run_command', 'test', {'type': 'object'}, command), submit_tool('submit_fix', 'done', {'type': 'object'}, accept)],
        workspace=Workspace(tmp_path), stream=stream))
    try:
        await asyncio.wait_for(started.wait(), 3)
        assert tool_activity(stream.agent_id)['elapsed_ms'] >= 0
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            release.set()
            assert (await asyncio.wait_for(task, 3))[0] == 'submit_fix'
        assert tool_activity(stream.agent_id) is None
        assert stream.agent_id not in conversation.active
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
