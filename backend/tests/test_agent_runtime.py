from unittest.mock import AsyncMock

import pytest

from src.config import Settings
from src.core import agent_runtime, pi_agent, runtime_config


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.delenv('AGENT_ENGINE', raising=False)
    settings = Settings(_env_file=None, database_url=f'sqlite+aiosqlite:///{tmp_path}/tasks.db')
    monkeypatch.setattr(runtime_config, 'get_settings', lambda: settings)
    return settings


@pytest.mark.asyncio
async def test_unconfigured_runtime_dispatches_to_pi(runtime, monkeypatch):
    pi = AsyncMock(return_value=('submit_fix', 'done'))
    native = AsyncMock()
    monkeypatch.setattr(pi_agent, 'run_pi_agent', pi)
    monkeypatch.setattr(agent_runtime, 'run_native_agent', native)
    messages = [{'role': 'system', 'content': 'constraints'}, {'role': 'user', 'content': 'fix'}]

    assert await agent_runtime.run_tool_agent(None, messages, []) == ('submit_fix', 'done')
    assert runtime_config.engine() == 'pi'
    pi.assert_awaited_once_with(None, messages, [])
    native.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_native_setting_keeps_legacy_executor(runtime, monkeypatch):
    runtime_config.save_engine('native')
    native = AsyncMock(return_value=('submit_fix', 'legacy'))
    monkeypatch.setattr(agent_runtime, 'run_native_agent', native)
    assert await agent_runtime.run_tool_agent(None, [], []) == ('submit_fix', 'legacy')
    native.assert_awaited_once()


def test_environment_and_saved_engine_override_default(runtime, monkeypatch):
    monkeypatch.setenv('AGENT_ENGINE', 'native')
    settings = Settings(_env_file=None, database_url=runtime.database_url)
    monkeypatch.setattr(runtime_config, 'get_settings', lambda: settings)
    assert runtime_config.engine() == 'native'
    runtime_config.save_engine('pi')
    assert runtime_config.engine() == 'pi'


@pytest.mark.asyncio
async def test_pi_failure_does_not_fall_back_to_native(runtime, monkeypatch):
    monkeypatch.setattr(pi_agent, 'run_pi_agent', AsyncMock(side_effect=ValueError('Pi 依赖未安装')))
    native = AsyncMock()
    monkeypatch.setattr(agent_runtime, 'run_native_agent', native)
    with pytest.raises(ValueError, match='Pi 依赖未安装'):
        await agent_runtime.run_tool_agent(None, [], [])
    native.assert_not_awaited()
