import asyncio
from unittest.mock import AsyncMock

import pytest

from src.core.agent_stream import ThinkingStream, live_stream, tool_activity
from src.core.agent_tools import Tool, Workspace
from src.core.agent_runtime import run_tool_agent
from src.core.agent_protocol import submit_tool
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
