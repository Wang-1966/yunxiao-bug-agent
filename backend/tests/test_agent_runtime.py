from unittest.mock import AsyncMock
import pytest
from src.config import Settings
from src.core import agent_runtime

@pytest.mark.asyncio
async def test_only_pi_executes_even_with_legacy_environment(monkeypatch):
    monkeypatch.setenv('AGENT_ENGINE', 'native')
    assert 'agent_engine' not in Settings.model_fields
    pi = AsyncMock(return_value=('submit_fix', 'done'))
    monkeypatch.setattr(agent_runtime, 'run_pi_agent', pi)
    messages = [{'role': 'system', 'content': 'constraints'}, {'role': 'user', 'content': 'fix'}]
    assert await agent_runtime.run_tool_agent(None, messages, []) == ('submit_fix', 'done')
    pi.assert_awaited_once_with(None, messages, [])

@pytest.mark.asyncio
async def test_pi_failure_propagates(monkeypatch):
    monkeypatch.setattr(agent_runtime, 'run_pi_agent', AsyncMock(side_effect=ValueError('Pi 依赖未安装')))
    with pytest.raises(ValueError, match='Pi 依赖未安装'):
        await agent_runtime.run_tool_agent(None, [], [])
