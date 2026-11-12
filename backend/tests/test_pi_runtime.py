"""Exercise the real pinned Pi SDK against a local OpenAI-compatible test server."""
import json
import threading
import asyncio
import shlex
import sys
import os
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from pathlib import Path

import pytest

from src.config import get_settings
from src.core.agent_tools import Workspace, build_tools
from src.core.pi_agent import run_pi_agent
from src.core.tool_agent import submit_tool


@pytest.fixture
def pi_script(tmp_path, monkeypatch):
    """真实 SDK 和原生工具，只模拟模型响应；禁止测试隐式下载工具。"""
    from src.core import pi_agent
    from src.core.agent_tools import command_env
    monkeypatch.setattr(get_settings(), 'database_url', f'sqlite+aiosqlite:///{tmp_path}/tasks.db')
    monkeypatch.setattr(pi_agent, 'command_env', lambda: {**command_env(), 'PI_OFFLINE': '1'})

    @contextmanager
    def serve(calls):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(request)
                current = calls[min(len(requests) - 1, len(calls) - 1)]
                batch = current if isinstance(current, list) else [current]
                delta = {'role': 'assistant', 'tool_calls': [{'index': index, 'id': f'call{len(requests)}_{index}', 'type': 'function',
                    'function': {'name': name, 'arguments': json.dumps(args)}} for index, (name, args) in enumerate(batch)]}
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                for chunk in [
                    {'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                    {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}],
                     'usage': {'prompt_tokens': 10, 'completion_tokens': 10}},
                ]:
                    self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
                self.wfile.write(b'data: [DONE]\n\n')
                self.wfile.flush()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        llm = SimpleNamespace(client=SimpleNamespace(provider=SimpleNamespace(
            base_url=f'http://127.0.0.1:{server.server_port}/v1', api_key='test-only-secret')),
            model='test-model', defaults={'max_tokens': 2048})
        try:
            yield llm, requests
        finally:
            server.shutdown()
            server.server_close()
    return serve


def reply_tool():
    async def reply(args): return args['reply']
    return submit_tool('submit_reply', '提交回复', {'type': 'object', 'properties': {'reply': {'type': 'string'}},
                       'required': ['reply']}, reply)


@pytest.mark.asyncio
async def test_native_coding_tools_permissions_and_evidence(tmp_path, pi_script):
    from src.core.agent_tools import sandbox_available
    from src.core.context_management import context_directory
    root = tmp_path / 'repo'
    root.mkdir()
    (root / 'app.py').write_text('value = 1\nlabel = "old"\n')
    (root / 'test_repro.py').write_text('assert True\n')
    (root / '.env').write_text('value = PRIVATE_ENV_SENTINEL\n')
    (root / 'link.py').symlink_to(tmp_path / 'outside.py')
    (tmp_path / 'outside.py').write_text('DO_NOT_READ_OUTSIDE')
    workspace = Workspace(root, mode='fix', protected={'test_repro.py'})
    calls = [
        ('read', {'path': 'app.py'}),
        ('edit', {'path': 'app.py', 'edits': [{'oldText': 'value = 1', 'newText': 'value = 2'},
                                            {'oldText': 'label = "old"', 'newText': 'label = "new"'}]}),
        ('write', {'path': 'new.py', 'content': 'created_by_pi = True\n'}),
        ('ls', {}),
        ('find', {'pattern': '*.py'}),
        ('grep', {'pattern': 'value', 'path': '.'}),
        ('read', {'path': 'test_repro.py'}),
        ('edit', {'path': 'test_repro.py', 'edits': [{'oldText': 'True', 'newText': 'False'}]}),
        ('read', {'path': '../outside.py'}),
        ('read', {'path': 'link.py'}),
        ('write', {'path': '.env', 'content': 'should_not_write'}),
    ]
    if sandbox_available():
        command = shlex.quote(sys.executable) + ' -c ' + shlex.quote('print("native-command-evidence")')
        calls.append(('bash', {'command': command, 'timeout': 10}))
    calls.append(('submit_reply', {'reply': 'done'}))
    with pi_script(calls) as (llm, requests):
        result = await asyncio.wait_for(run_pi_agent(llm, [{'role': 'system', 'content': '使用工具修复'},
            {'role': 'user', 'content': '执行当前阶段'}], build_tools(workspace) + [reply_tool()],
            workspace=workspace, max_turns=25), 35)
    assert result == ('submit_reply', 'done')
    assert (root / 'app.py').read_text() == 'value = 2\nlabel = "new"\n'
    assert (root / 'new.py').read_text() == 'created_by_pi = True\n'
    assert (root / 'test_repro.py').read_text() == 'assert True\n'
    assert (root / '.env').read_text() == 'value = PRIVATE_ENV_SENTINEL\n'
    history = json.dumps(requests[-1], ensure_ascii=False)
    assert 'PRIVATE_ENV_SENTINEL' not in history
    assert 'DO_NOT_READ_OUTSIDE' not in history
    assert '已确认的复现测试' in history and '符号链接' in history and '路径不能包含 ..' in history
    assert {item['path'] for item in workspace.read_requests} >= {'app.py', 'test_repro.py'}
    evidence = [json.loads(path.read_text()) for path in context_directory(root).glob('evidence/*.json')]
    assert any(item['tool'] == 'edit' and 'value = 2' in item['content'] for item in evidence)
    assert any(item['tool'] == 'grep' and 'app.py' in item['content'] and 'value = 2' in item['content'] for item in evidence)
    assert any(item['tool'] == 'find' and ('app.py' in item['content'] or 'fd is not available' in item['content']) for item in evidence)
    if sandbox_available():
        assert any(item['tool'] == 'bash' and 'native-command-evidence' in item['content']
                   and '"exit_code": 0' in item['content'] for item in evidence)
    names = {tool['function']['name'] for tool in requests[0]['tools']}
    assert {'read', 'edit', 'write', 'ls', 'find', 'grep'} <= names
    assert not names & {'read_file', 'edit_file', 'run_command', 'apply_patch', 'list_dir', 'find_files'}


@pytest.mark.asyncio
async def test_native_phase_restrictions_and_project_skills(tmp_path, pi_script):
    root = tmp_path / 'repo'
    root.mkdir()
    (root / 'app.py').write_text('value = 1\n')
    (root / 'AGENTS.md').write_text('PROJECT_RULE_SENTINEL：保持接口兼容。')
    skill = root / '.agents/skills/debugging'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text('---\nname: debugging\ndescription: PROJECT_SKILL_SENTINEL\n---\nInvestigate first.')
    outside = tmp_path / 'outside-skill'
    outside.mkdir()
    (outside / 'SKILL.md').write_text('---\nname: outside\ndescription: OUTSIDE_SKILL_SENTINEL\n---\nNot allowed.')
    (root / '.agents/skills/outside').symlink_to(outside, target_is_directory=True)
    workspace = Workspace(root, mode='analyze')
    calls = [('write', {'path': 'app.py', 'content': 'wrong'}),
             ('write', {'path': 'test_added.py', 'content': 'assert False\n'}),
             ('read', {'path': '.agents/skills/debugging/SKILL.md'}),
             ('submit_reply', {'reply': 'done'})]
    with pi_script(calls) as (llm, requests):
        await run_pi_agent(llm, [{'role': 'system', 'content': '调查'}, {'role': 'user', 'content': '分析'}],
                           build_tools(workspace, commands=False) + [reply_tool()], workspace=workspace)
    assert (root / 'app.py').read_text() == 'value = 1\n'
    assert (root / 'test_added.py').read_text() == 'assert False\n'
    history = json.dumps(requests[-1], ensure_ascii=False)
    assert '分析阶段只能' in history
    assert 'PROJECT_RULE_SENTINEL' in history and 'PROJECT_SKILL_SENTINEL' in history
    assert 'OUTSIDE_SKILL_SENTINEL' not in history


@pytest.mark.asyncio
async def test_native_readonly_cannot_call_write_or_bash(tmp_path, pi_script):
    root = tmp_path / 'repo'
    root.mkdir()
    calls = [('write', {'path': 'forbidden.py', 'content': 'wrong'}),
             ('bash', {'command': 'touch forbidden.py'}), ('submit_reply', {'reply': 'done'})]
    workspace = Workspace(root, mode='read')
    with pi_script(calls) as (llm, requests):
        await run_pi_agent(llm, [{'role': 'system', 'content': '只读讨论'}, {'role': 'user', 'content': '解释'}],
                           build_tools(workspace, commands=False) + [reply_tool()], workspace=workspace)
    assert not (root / 'forbidden.py').exists()
    names = {tool['function']['name'] for tool in requests[0]['tools']}
    assert not names & {'write', 'edit', 'bash'}


@pytest.mark.asyncio
async def test_native_submit_blocks_later_writes_in_same_turn(tmp_path, pi_script):
    root = tmp_path / 'repo'
    root.mkdir()
    workspace = Workspace(root, mode='fix')
    with pi_script([[('submit_reply', {'reply': 'done'}), ('write', {'path': 'late.py', 'content': 'wrong'})]]) as (llm, _):
        assert await run_pi_agent(llm, [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'task'}],
            build_tools(workspace, commands=False) + [reply_tool()], workspace=workspace) == ('submit_reply', 'done')
    assert not (root / 'late.py').exists()


@pytest.mark.asyncio
async def test_native_images_truncation_and_secret_redaction(tmp_path, pi_script):
    import struct
    import zlib
    from src.core.context_management import context_directory
    root = tmp_path / 'repo'
    root.mkdir()
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    (root / 'tiny.png').write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
                                   + chunk(b'IDAT', zlib.compress(b'\0\xff\0\0')) + chunk(b'IEND', b''))
    (root / 'long.txt').write_text('\n'.join(f'row {i}' for i in range(2100)))
    (root / 'key.txt').write_text('test-only-secret')
    workspace = Workspace(root)
    calls = [('read', {'path': 'tiny.png'}), ('read', {'path': 'long.txt'}),
             ('read', {'path': 'key.txt'}), ('submit_reply', {'reply': 'done'})]
    with pi_script(calls) as (llm, requests):
        await run_pi_agent(llm, [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'task'}],
            build_tools(workspace, commands=False) + [reply_tool()], workspace=workspace)
    history = json.dumps(requests[-1])
    assert 'data:image/png;base64,' in history and '[REDACTED]' in history
    assert 'test-only-secret' not in history
    evidence = [json.loads(path.read_text()) for path in context_directory(root).glob('evidence/*.json')]
    assert any(item.get('arguments', {}).get('path') == 'long.txt' and item['truncated'] for item in evidence)
    assert all('test-only-secret' not in path.read_text() for path in (tmp_path / 'agent_sessions').glob('*.pi.jsonl'))


@pytest.mark.asyncio
async def test_native_analysis_then_fix_uses_real_reproduction(tmp_path, pi_script, monkeypatch):
    import subprocess
    from fake_agent import analysis_args
    from src.core.agent_tools import sandbox_available
    from src.core.repair_agent import repository_context, run_analysis, run_fix
    if not sandbox_available():
        pytest.skip('需要命令沙箱运行真实复现')
    monkeypatch.setattr(get_settings(), 'agent_engine', 'pi')
    root = tmp_path / 'repo'
    root.mkdir()
    (root / 'app.py').write_text('value = 1\n')
    (root / 'AGENTS.md').write_text('PI_CONTEXT_RULE：保持兼容。')
    subprocess.run(['git', 'init', '-b', 'main', str(root)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                    'commit', '-m', 'initial'], check=True, capture_output=True)
    command = shlex.quote(sys.executable) + ' -B test_repro.py'
    analysis_call = analysis_args(reproduction={'command': command, 'test_files': ['test_repro.py']},
        decision_questions=[], assumptions=[], verification_limits=[],
        acceptance_checks=[{'criterion_index': 0, 'method': 'reproduction', 'detail': 'assert value == 2', 'test_files': ['test_repro.py']}],
        proposals=[{'name': '修正赋值', 'steps': ['value 改为 2'], 'files': ['app.py'],
                    'impact': {'categories': [], 'evidence': 'app.py:1 仅修正赋值，不改变接口'}}])
    context = await repository_context(root)
    assert 'PI_CONTEXT_RULE' not in context['instructions']
    with pi_script([('read', {'path': 'app.py'}),
                    ('write', {'path': 'test_repro.py', 'content': 'from app import value\nassert value == 2\n'}),
                    analysis_call]) as (llm, requests):
        analysis = await run_analysis(llm, None, root, '分析 value 错误\n' + context['instructions'], bug={})
    assert requests[0]['messages'][0]['content'].count('PI_CONTEXT_RULE') == 1
    assert analysis['repro']['reproduced'] and analysis['repro']['exit_before'] != 0
    original_test = (root / 'test_repro.py').read_bytes()
    with pi_script([('read', {'path': 'app.py'}),
                    ('edit', {'path': 'app.py', 'edits': [{'oldText': 'value = 1', 'newText': 'value = 2'}]}),
                    ('submit_fix', {'summary': '修正 value 赋值'})]) as (llm, requests):
        result = await run_fix(llm, None, root, '将 value 修复为 2', bug={},
                               analysis=analysis['analysis'], repro=analysis['repro'])
    assert result['repro']['exit_after'] == 0
    assert (root / 'test_repro.py').read_bytes() == original_test
    assert result['source_context']['reads'][0]['path'] == 'app.py'
    system = requests[0]['messages'][0]['content']
    assert 'read_file' not in system and 'run_command' not in system


@pytest.mark.asyncio
async def test_native_bash_cancellation_kills_child_and_clears_activity(tmp_path, pi_script):
    from unittest.mock import AsyncMock
    from src.core.agent_tools import sandbox_available
    from src.core.agent_stream import ThinkingStream, tool_activity
    from src.core.context_management import context_directory
    if not sandbox_available():
        pytest.skip('需要命令沙箱')
    root = tmp_path / 'repo'
    root.mkdir()
    program = 'import os,time; from pathlib import Path; Path("pid").write_text(str(os.getpid())); time.sleep(30)'
    command = shlex.quote(sys.executable) + ' -c ' + shlex.quote(program)
    workspace = Workspace(root, mode='fix')
    with pi_script([('bash', {'command': command}), ('submit_reply', {'reply': 'done'})]) as (llm, _):
        task = asyncio.create_task(run_pi_agent(llm, [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'task'}],
            build_tools(workspace) + [reply_tool()], workspace=workspace, stream=ThinkingStream(None, AsyncMock())))
        try:
            async with asyncio.timeout(10):
                while not (root / 'pid').exists():
                    await asyncio.sleep(0.05)
            pid = int((root / 'pid').read_text())
            assert tool_activity(None)['tools'] == ['bash']
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            async with asyncio.timeout(3):
                while True:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    await asyncio.sleep(0.05)
            assert tool_activity(None) is None
            events = [json.loads(line) for path in context_directory(root).glob('runs/*.jsonl')
                      for line in path.read_text().splitlines()]
            assert any(item['event'] == 'tool_end' and item['tool'] == 'bash' and item['status'] == 'uncertain' for item in events)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_real_pi_tool_bridge_and_persistent_resume(tmp_path, pi_script):
    worktree = tmp_path / 'repo'
    worktree.mkdir()
    (worktree / 'app.txt').write_text('persistent context evidence')
    workspace = Workspace(worktree, mode='read')
    tools = build_tools(workspace, commands=False) + [reply_tool()]
    with pi_script([('read', {'path': 'app.txt'}), ('submit_reply', {'reply': '已读取并保留上下文'})]) as (llm, requests):
        llm.defaults['temperature'] = 0.2
        result = await run_pi_agent(llm, [{'role': 'system', 'content': '请用工具'}, {'role': 'user', 'content': '读取 app.txt 并回复'}], tools, workspace=workspace, max_turns=5)
        assert result == ('submit_reply', '已读取并保留上下文')
        assert len(requests) == 2
        assert requests[0]['temperature'] == 0.2
        assert requests[0]['max_tokens'] == 2048
        assert 'persistent context evidence' in json.dumps(requests[1])
        assert {tool['function']['name'] for tool in requests[0]['tools']} == {'read', 'grep', 'find', 'ls', 'submit_reply', 'read_evidence'}
        result = await run_pi_agent(llm, [{'role': 'system', 'content': '请用工具'}, {'role': 'user', 'content': '继续之前的调查'}], tools, workspace=workspace, max_turns=5)
        assert result[0] == 'submit_reply'
        assert 'persistent context evidence' in json.dumps(requests[-1])
        sessions = list((tmp_path / 'agent_sessions').glob('*.pi.jsonl'))
        assert len(sessions) == 1
        assert 'test-only-secret' not in sessions[0].read_text()
        logs = list((tmp_path / 'agent_context').glob('*/runs/*.jsonl'))
        assert len(logs) == 2
        records = [json.loads(line) for path in logs for line in path.read_text().splitlines()]
        assert sum(row['event'] == 'run_end' for row in records) == 2
        assert sum(row['event'] == 'request_end' and (row.get('usage') or {}).get('input_tokens', 0) > 0 for row in records) == len(requests)
        assert any(row['event'] == 'evidence_saved' for row in records)
        assert all('test-only-secret' not in path.read_text() for path in logs)


@pytest.mark.asyncio
@pytest.mark.parametrize('summary_failure', [False, True])
async def test_real_pi_compaction_outcome_is_logged(tmp_path, monkeypatch, summary_failure):
    import subprocess
    from src.core.tool_agent import session_path
    from src.core.context_management import context_directory
    monkeypatch.setattr(get_settings(), 'database_url', f'sqlite+aiosqlite:///{tmp_path}/tasks.db')
    worktree = tmp_path / 'repo'
    worktree.mkdir()
    path = session_path(worktree).with_suffix('.pi.jsonl')
    path.parent.mkdir(parents=True)
    seed = '''
      import { SessionManager } from '@earendil-works/pi-coding-agent';
      const manager = SessionManager.open(process.argv[1], undefined, process.argv[2]);
      for (let i = 0; i < 5; i++) {
        manager.appendMessage({ role: 'user', content: [{ type: 'text', text: 'historical evidence ' + 'x'.repeat(10000) }], timestamp: Date.now() });
        manager.appendMessage({ role: 'assistant', content: [{ type: 'text', text: 'Investigated; preserve user constraints.' }],
          api: 'openai-completions', provider: 'yunxiao', model: 'test-model', timestamp: Date.now(), stopReason: 'stop',
          usage: { input: 23000, output: 10, cacheRead: 0, cacheWrite: 0, totalTokens: 23010,
            cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } } });
      }
    '''
    subprocess.run([get_settings().pi_node_command, '--input-type=module', '-e', seed, str(path), str(worktree)],
                   cwd=Path(__file__).resolve().parents[1] / 'pi', check=True, capture_output=True)
    summaries = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            is_summary = not request.get('tools')
            if is_summary:
                summaries.append(request)
                delta = {'role': 'assistant', 'content': 'Goal: preserve constraints. Progress: inspected. Next: verify. Evidence remains available.'}
                reason = 'length' if summary_failure else 'stop'
            else:
                delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'submit', 'type': 'function',
                    'function': {'name': 'submit_reply', 'arguments': '{"reply":"done"}'}}]}
                reason = 'tool_calls'
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for chunk in [
                {'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                {'choices': [{'index': 0, 'delta': {}, 'finish_reason': reason}], 'usage': {'prompt_tokens': 100, 'completion_tokens': 20}},
            ]:
                self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    llm = SimpleNamespace(client=SimpleNamespace(provider=SimpleNamespace(base_url=f'http://127.0.0.1:{server.server_port}/v1',
        api_key='test-only-secret', context_windows={'test-model': 32000})), model='test-model', defaults={'max_tokens': 2048})
    async def accept(args): return args['reply']
    tools = [submit_tool('submit_reply', '提交', {'type': 'object', 'properties': {'reply': {'type': 'string'}}, 'required': ['reply']}, accept)]
    try:
        result = await run_pi_agent(llm, [{'role': 'system', 'content': 'Do not bypass approval.'}, {'role': 'user', 'content': 'Continue.'}],
                                    tools, workspace=Workspace(worktree), max_turns=4)
        assert result == ('submit_reply', 'done')
        assert summaries
        events = [json.loads(line) for file in context_directory(worktree).glob('runs/*.jsonl') for line in file.read_text().splitlines()]
        compact = [event for event in events if event['event'] == 'compaction']
        assert compact and compact[0]['status'] == ('failed' if summary_failure else 'success')
        entries = [json.loads(line) for line in path.read_text().splitlines()]
        assert any(entry['type'] == 'compaction' for entry in entries) is (not summary_failure)
        assert any('historical evidence' in json.dumps(entry) for entry in entries)
    finally:
        server.shutdown()
        server.server_close()
