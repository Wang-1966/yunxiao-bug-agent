import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from src.config import get_settings
from src.core.agent_tools import Tool, ToolError, Workspace
from src.core.context_management import ContextBudget, ContextRun, context_directory, estimate_tokens
from src.core.context_report import summarize
from src.core.tool_agent import compact_history, load_session, run_tool_agent, session_path, submit_tool
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


def test_budget_counts_chinese_tools_and_images(workspace):
    budget = ContextBudget.for_llm(model())
    assert budget.source == 'configured' and budget.limit < 64000 - 4000
    assert estimate_tokens('中' * 100) > estimate_tokens('a' * 100)
    assert estimate_tokens([{'type': 'image_url', 'image_url': {'url': 'data:' + 'a' * 100000}}]) < 5000
    assert estimate_tokens({'tools': [{'arguments': '补丁' * 100}]}) > 300
    with pytest.raises(ValueError, match='窗口不足'):
        ContextBudget.for_llm(model(8192))


@pytest.mark.asyncio
async def test_evidence_is_retrievable_after_preview_truncation_and_is_scoped(workspace, tmp_path):
    context = ContextRun(workspace, engine='native', phase='fix', llm=model())
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
    other = ContextRun(Workspace(tmp_path / 'different'), engine='native', phase='fix', llm=model())
    with pytest.raises(ToolError, match='没有该证据'):
        await other.evidence_tool().handler({'evidence_id': evidence_id})
    with pytest.raises(ToolError, match='ID 无效'):
        await context.evidence_tool().handler({'evidence_id': '../other'})
    assert 'MIDDLE-FACT' not in context.log_path.read_text()
    assert context.log_path.stat().st_mode & 0o077 == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True])
async def test_failed_summary_does_not_modify_messages(workspace, cancel):
    messages = history()
    before = copy.deepcopy(messages)
    context = ContextRun(workspace, engine='native', phase='fix', llm=model())
    class Failed:
        async def ainvoke(self, *_args, **_kwargs):
            raise asyncio.CancelledError() if cancel else RuntimeError('private-test-key')
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            await compact_history(messages, Failed(), workspace, context=context)
    else:
        assert await compact_history(messages, Failed(), workspace, context=context) is None
    assert messages == before
    assert not list((context.directory / 'checkpoints').glob('*'))
    assert records(context)[-1]['status'] == ('cancelled' if cancel else 'failed')
    assert 'private-test-key' not in context.log_path.read_text()


@pytest.mark.asyncio
async def test_summary_includes_every_selected_tail_and_keeps_one_checkpoint(workspace):
    from src.core.tool_agent import SUMMARY_PREFIX
    context = ContextRun(workspace, engine='native', phase='fix', llm=model())
    messages = history()
    messages.insert(2, {'role': 'user', 'content': SUMMARY_PREFIX + '用户已纠正：不能删除非空节点'})
    class Summary:
        async def ainvoke(self, prompt, **kwargs):
            text = prompt[-1]['content']
            assert '不能删除非空节点' in text
            for i in range(6):
                assert f'TAIL-{i}' in text
            return '用户纠正：不能删除非空节点。多个命令失败，继续调查。'
    result = await compact_history(messages, Summary(), workspace, context=context)
    assert result[0] == 6
    assert messages[0]['content'] == '禁止绕过审批'
    assert messages[1]['content'] == '保持向后兼容'
    assert sum(str(m.get('content')).startswith(SUMMARY_PREFIX) for m in messages) == 1
    assert len(list((context.directory / 'checkpoints').glob('*.json'))) == 1
    assert len(list((context.directory / 'evidence').glob('*.json'))) == 6
    assert records(context)[-1]['status'] == 'success'
    assert len([m for m in messages if m['role'] == 'tool']) == 12


@pytest.mark.asyncio
async def test_budget_overflow_never_calls_model(workspace):
    llm = model(16000)
    llm.achat = lambda *args, **kwargs: pytest.fail('oversized request was sent')
    with pytest.raises(ValueError, match='超过模型输入预算'):
        await run_tool_agent(llm, [{'role': 'system', 'content': '约束' * 8000}], [], workspace=workspace, log_context='fix')
    assert load_session(session_path(workspace.root))
    report = summarize(context_directory(workspace.root).parent)
    assert report['groups'][0]['budget_blocked'] == 1


@pytest.mark.asyncio
async def test_cancel_after_write_saves_pending_call_without_replaying(workspace):
    started = asyncio.Event()
    async def write(_):
        (workspace.root / 'changed.txt').write_text('one write')
        started.set()
        await asyncio.Event().wait()
    llm = ScriptedLLM([turn(('write', {}))])
    messages = [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'task'}]
    task = asyncio.create_task(run_tool_agent(llm, messages, [Tool('write', 'write', {}, write, readonly=False)], workspace=workspace, log_context='fix'))
    await asyncio.wait_for(started.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    restored = load_session(session_path(workspace.root))
    assert restored[-1]['role'] == 'tool' and '可能已生效' in restored[-1]['content']
    assert (workspace.root / 'changed.txt').read_text() == 'one write'
    report = summarize(context_directory(workspace.root).parent)
    assert report['groups'][0]['uncertain_tools'] == 1
    assert report['groups'][0]['cancelled_runs'] == 1


@pytest.mark.asyncio
async def test_pending_user_message_is_saved_before_delivered(workspace, monkeypatch):
    from src.core import conversation
    incoming = {'id': 20, 'text': '新的纠正', 'images': []}
    monkeypatch.setattr(conversation, 'pending', lambda _id: asyncio.sleep(0, result=[incoming]))
    async def delivered(_id, ids):
        if ids:
            saved = load_session(session_path(workspace.root))
            assert any('新的纠正' in str(m.get('content')) for m in saved)
    monkeypatch.setattr(conversation, 'delivered', delivered)
    llm = ScriptedLLM([turn(content='working')])
    # No stream ID still exercises the checkpoint; use a lightweight stream for receipt.
    class Stream:
        agent_id = 'agent-test'
        def start(self, _): pass
        async def finish(self): pass
        async def content(self, _): pass
        async def reasoning(self, _): pass
        async def say(self, _): pass
    with pytest.raises(ValueError, match='轮请求上限'):
        await run_tool_agent(llm, [{'role': 'system', 'content': 'sys'}], [], workspace=workspace, log_context='fix', max_turns=1, stream=Stream())


def test_report_handles_incomplete_lines_and_groups_policy(workspace):
    context = ContextRun(workspace, engine='native', phase='fix', llm=model())
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
