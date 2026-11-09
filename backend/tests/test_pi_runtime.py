"""Exercise the real pinned Pi SDK against a local OpenAI-compatible test server."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from pathlib import Path

import pytest

from src.config import get_settings
from src.core.agent_tools import Workspace, build_tools
from src.core.pi_agent import run_pi_agent
from src.core.tool_agent import submit_tool


@pytest.mark.asyncio
async def test_real_pi_tool_bridge_and_persistent_resume(tmp_path, monkeypatch):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(request)
            count = len(requests)
            name, args = ('read_file', {'path': 'app.txt'}) if count == 1 else ('submit_reply', {'reply': '已读取并保留上下文'})
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for chunk in [
                {'choices': [{'index': 0, 'delta': {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': f'call_{count}', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}, 'finish_reason': None}]},
                {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 10}},
            ]:
                self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(get_settings(), 'database_url', f'sqlite+aiosqlite:///{tmp_path}/tasks.db')
    worktree = tmp_path / 'repo'
    worktree.mkdir()
    (worktree / 'app.txt').write_text('persistent context evidence')
    workspace = Workspace(worktree, mode='read')
    llm = SimpleNamespace(client=SimpleNamespace(provider=SimpleNamespace(base_url=f'http://127.0.0.1:{server.server_port}/v1', api_key='test-only-secret')), model='test-model', defaults={'temperature': 0.2, 'max_tokens': 2048})
    async def accept(args):
        return args['reply']
    tools = build_tools(workspace, commands=False) + [submit_tool('submit_reply', '提交回复', {'type': 'object', 'properties': {'reply': {'type': 'string'}}, 'required': ['reply']}, accept)]
    try:
        result = await run_pi_agent(llm, [{'role': 'system', 'content': '请用工具'}, {'role': 'user', 'content': '读取 app.txt 并回复'}], tools, workspace=workspace, max_turns=5)
        assert result == ('submit_reply', '已读取并保留上下文')
        assert len(requests) == 2
        assert requests[0]['temperature'] == 0.2
        assert requests[0]['max_tokens'] == 2048
        assert 'persistent context evidence' in json.dumps(requests[1])
        assert {tool['function']['name'] for tool in requests[0]['tools']} == {tool.name for tool in tools} | {'read_evidence'}
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
    finally:
        server.shutdown()
        server.server_close()


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
