import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from src.config import get_settings
from src.core.agent_tools import Tool, ToolError, Workspace
from src.core.context_management import ContextRun, context_directory
from src.core.context_report import summarize
from fake_agent import ScriptedLLM, turn


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), 'database_url', f'sqlite+aiosqlite:///{tmp_path}/tasks.db')
    root = tmp_path / 'repo'
    root.mkdir()
    return Workspace(root, mode='fix')


def model(window=64000):
    return SimpleNamespace(model='test', defaults={'max_tokens': 4000}, client=SimpleNamespace(
        provider=SimpleNamespace(context_windows={'test': window}, api_key='private-test-key')))


def history():
    messages = [{'role': 'system', 'content': '禁止绕过审批'}, {'role': 'user', 'content': '保持向后兼容'}]
    for i in range(12):
        messages += [{'role': 'assistant', 'content': None, 'tool_calls': [{'id': f'call{i}', 'type': 'function',
            'function': {'name': 'run_command', 'arguments': json.dumps({'command': f'test-{i}'})}}]},
            {'role': 'tool', 'tool_call_id': f'call{i}', 'content': f'开始 {i}\n' + 'x' * 30000 + f'\n关键错误 TAIL-{i}'}]
    return messages


def records(context):
    return [json.loads(line) for line in context.log_path.read_text().splitlines()]


def test_model_window_is_forwarded_without_host_input_budget(workspace):
    context = ContextRun(workspace, engine='pi', phase='fix', llm=model(8192))
    assert context.context_window == 8192
    start = records(context)[0]
    assert start['window_source'] == 'configured'
    assert 'input_limit' not in start
    assert start['policy_version'] == 2


def test_missing_window_is_left_to_pi_without_global_fallback(workspace, monkeypatch):
    monkeypatch.setenv('AGENT_CONTEXT_WINDOW', '128000')
    llm = model()
    llm.client.provider.context_windows = {}
    context = ContextRun(workspace, engine='pi', phase='fix', llm=llm)
    assert context.context_window is None
    assert records(context)[0]['window_source'] == 'pending_pi_catalog'


def test_model_switch_uses_each_models_own_configured_window(workspace):
    llm = model()
    llm.client.provider.context_windows = {'test': 64000, 'other': 1000000}
    assert ContextRun(workspace, engine='pi', phase='analyze', llm=llm).context_window == 64000
    llm.model = 'other'
    assert ContextRun(workspace, engine='pi', phase='fix', llm=llm).context_window == 1000000


@pytest.mark.asyncio
async def test_evidence_is_retrievable_after_preview_truncation_and_is_scoped(workspace, tmp_path):
    context = ContextRun(workspace, engine='pi', phase='fix', llm=model())
    text = 'START\n' + 'x' * 40000 + '\nMIDDLE-FACT\n' + 'y' * 40000 + '\nEND private-test-key'
    async def command(_):
        return text
    result = await context.execute(Tool('run_command', 'run', {}, command, readonly=False), 'call1', {'command': 'dummy'})
    assert 'MIDDLE-FACT' not in result and 'END' in result
    evidence_id = next(row['evidence_id'] for row in records(context) if row['event'] == 'evidence_saved')
    restored = await context.evidence_tool().handler({'evidence_id': evidence_id, 'query': 'MIDDLE-FACT'})
    assert 'MIDDLE-FACT' in restored
    stored = (context.directory / 'evidence' / f'{evidence_id}.json').read_text()
    assert 'private-test-key' not in stored
    other = ContextRun(Workspace(tmp_path / 'different'), engine='pi', phase='fix', llm=model())
    with pytest.raises(ToolError, match='没有该证据'):
        await other.evidence_tool().handler({'evidence_id': evidence_id})
    with pytest.raises(ToolError, match='ID 无效'):
        await context.evidence_tool().handler({'evidence_id': '../other'})
    assert 'MIDDLE-FACT' not in context.log_path.read_text()
    assert context.log_path.stat().st_mode & 0o077 == 0


def test_report_handles_incomplete_lines_and_groups_policy(workspace):
    context = ContextRun(workspace, engine='pi', phase='fix', llm=model())
    context.emit('request_start', turn=1, estimated_input_tokens=100)
    context.emit('request_end', turn=1, usage={'input_tokens': 120, 'output_tokens': 10, 'cached_tokens': 50})
    context.emit('tool_start', execution_id='pending', tool='write')
    with context.log_path.open('a') as file:
        file.write('{incomplete')
    report = summarize(context.directory.parent)
    group = report['groups'][0]
    assert report['malformed_lines'] == 1
    assert group['input_tokens'] == 120 and group['input_actual_to_estimate_p95'] == 1.2
    assert group['unmatched_tool_starts'] == 1 and group['runs_without_end'] == 1


@pytest.mark.asyncio
async def test_command_output_is_bounded_and_retains_tail_and_timeout(workspace):
    import sys
    import shlex
    from src.core.agent_tools import run_in_worktree
    script = "import sys; sys.stdout.write('START' + 'x' * (9 * 1024 * 1024) + 'TAIL-ERROR')"
    code, output = await run_in_worktree(workspace.root, f'{shlex.quote(sys.executable)} -c {shlex.quote(script)}', sandbox=False)
    assert code == 0 and output.startswith('START') and output.endswith('TAIL-ERROR')
    assert '命令输出超过保留上限' in output and len(output) < 8 * 1024 * 1024 + 200
    code, output = await run_in_worktree(workspace.root, 'printf diagnostic; sleep 10', timeout=0.1, sandbox=False)
    assert code == 124 and 'diagnostic' in output and '被终止' in output
