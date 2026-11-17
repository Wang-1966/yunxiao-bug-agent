import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import aiosqlite
import pytest
from fastapi import HTTPException

from src.api import agents
from src.config import get_settings
from src.core import conversation, agent_discussion, orchestrator
from src.core.agent_stream import ThinkingStream
from src.core.agent_tools import Workspace
from src.core.agent_runtime import run_tool_agent
from src.core.agent_protocol import submit_tool
from src.models.agent import ConversationMessageRequest, ProposalConfirmation
from fake_agent import ScriptedLLM, turn


@pytest.fixture
async def chat_db(tmp_path, monkeypatch):
    path = tmp_path / 'chat.db'
    async with aiosqlite.connect(path) as db:
        await db.executescript(Path('src/db/init.sql').read_text())
        await db.execute("INSERT INTO bugs (bug_id, title, priority, source, assigned_agent_id) VALUES ('bug', 'Bug', 'normal', 'manual', 'agent')")
        await db.execute("INSERT INTO agents (agent_id, name, status, current_bug_id) VALUES ('agent', 'Agent', 'busy', 'bug')")
        await db.commit()
    monkeypatch.setattr(get_settings(), 'database_url', f'sqlite+aiosqlite:///{path}')
    monkeypatch.setattr(agents, 'get_db_path', lambda: str(path))
    runner = orchestrator.MultiAgentOrchestrator()
    monkeypatch.setattr(agents, 'get_orchestrator', lambda: runner)
    yield path, runner
    await agent_discussion.stop_all()
    conversation.active.clear()


@pytest.mark.asyncio
async def test_persistence_idempotency_and_ownership(chat_db):
    path, runner = chat_db
    request = ConversationMessageRequest(text='只处理 Backspace', client_id='client-1')
    first = await agents.send_conversation('agent', request)
    second = await agents.send_conversation('agent', request)
    assert first['id'] == second['id']
    rows = await conversation.pending('agent')
    assert len(rows) == 1 and rows[0]['text'] == request.text
    assert not runner.running_agents and not agent_discussion.tasks
    async with aiosqlite.connect(path) as db:
        await db.execute("UPDATE bugs SET assigned_agent_id='new-agent' WHERE bug_id='bug'")
        await db.commit()
    with pytest.raises(HTTPException) as exc:
        await agents.send_conversation('agent', ConversationMessageRequest(text='旧页面', client_id='client-2'))
    assert exc.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize('status,bug_status,outcome', [
    ('awaiting_confirmation', 'awaiting_confirmation', '修复完成，等待你的验收'),
    ('idle', 'resolved', '修复已验收并交付'),
    ('error', 'failed', '本轮执行未完成，可继续对话处理'),
    ('busy', 'fixing', None),
])
async def test_conversation_returns_persisted_summary_and_accurate_outcome(chat_db, status, bug_status, outcome):
    path, runner = chat_db
    brief = {'root_cause': '初始值错误。', 'solution': '已修正初始值。'}
    async with aiosqlite.connect(path) as db:
        await db.execute('UPDATE agents SET status=?', (status,))
        await db.execute('UPDATE bugs SET status=?', (bug_status,))
        await db.execute("INSERT INTO task_history (task_id, bug_id, agent_id, stage, status, result) VALUES ('task', 'bug', 'agent', 'verifying', 'running', ?)",
                         (json.dumps({'repair_summary': brief, 'fix_summary': '完整记录' * 500}),))
        await db.commit()
    result = await agents.get_conversation('agent')
    assert result['repair_summary'] == brief
    assert result['outcome'] == outcome
    assert result['can_send'] is True
    assert not runner.running_agents


@pytest.mark.asyncio
async def test_summary_survives_workflow_save_and_runtime_restart(chat_db):
    from types import SimpleNamespace
    path, runner = chat_db
    brief = {'root_cause': '连续数字无法在行内换行。', 'solution': '调整正文换行规则，保留公式行为。'}
    async with aiosqlite.connect(path) as db:
        await db.execute("INSERT INTO task_history (task_id, bug_id, agent_id, stage, status) VALUES ('task', 'bug', 'agent', 'fixing', 'running')")
        await db.commit()
    runner.agent_states['agent'] = {'state': {'stage': 'awaiting_confirmation', 'repair_summary': brief, 'fix_summary': '完整工程记录'}, 'progress': 90}
    task = SimpleNamespace(task_id='task', bug_id='bug', input_snapshot={})
    await runner._save_status('agent', task, 'awaiting_confirmation', 'awaiting_confirmation', '等待验收')
    runner.agent_states.clear()
    assert (await agents.get_conversation('agent'))['repair_summary'] == brief
    async with aiosqlite.connect(path) as db:
        saved = json.loads((await (await db.execute('SELECT result FROM task_history')).fetchone())[0])
    assert saved['fix_summary'] == '完整工程记录'


@pytest.mark.parametrize('text', ['继续', '继续修复', '请继续执行！', '重试', '恢复任务'])
def test_explicit_continue_requests(text):
    assert conversation.is_resume_request(text)


@pytest.mark.parametrize('text', ['不要继续', '先别继续修复', '继续解释一下', '为什么没有继续？', '继续分析方案是否合理'])
def test_discussion_is_not_resume_authorization(text):
    assert not conversation.is_resume_request(text)


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['awaiting_plan_confirmation', 'awaiting_confirmation'])
async def test_continue_does_not_bypass_confirmations(chat_db, monkeypatch, status):
    path, runner = chat_db
    async with aiosqlite.connect(path) as db:
        await db.execute('UPDATE agents SET status=?', (status,))
        await db.commit()
    retry = AsyncMock()
    monkeypatch.setattr(runner, '_retry_agent_locked', retry)
    from unittest.mock import Mock
    discussion = Mock()
    monkeypatch.setattr(agent_discussion, 'start', discussion)
    result = await agents.send_conversation('agent', ConversationMessageRequest(text='继续', client_id='no-approval'))
    assert 'retry' not in result
    retry.assert_not_awaited()
    discussion.assert_called_once_with('agent')


@pytest.mark.asyncio
async def test_continue_with_stopped_scheduler_reports_failure(chat_db, monkeypatch):
    path, runner = chat_db
    async with aiosqlite.connect(path) as db:
        await db.execute("UPDATE agents SET status='error'")
        await db.commit()
    with pytest.raises(HTTPException) as error:
        await agents.send_conversation('agent', ConversationMessageRequest(text='继续', client_id='stopped'))
    assert error.value.status_code == 503
    assert not agent_discussion.busy('agent')
    assert len(await conversation.pending('agent')) == 1








@pytest.mark.asyncio
async def test_discussion_cannot_approve_plan_and_stop_preserves_stage(chat_db, monkeypatch):
    path, runner = chat_db
    async with aiosqlite.connect(path) as db:
        await db.execute("UPDATE agents SET status='awaiting_plan_confirmation' WHERE agent_id='agent'")
        await db.commit()
    gate = asyncio.Event()
    monkeypatch.setattr(agent_discussion, 'discuss', lambda agent_id: gate.wait())
    await agents.send_conversation('agent', ConversationMessageRequest(text='为什么修改这里？', client_id='question'))
    assert agent_discussion.busy('agent')
    with pytest.raises(HTTPException) as exc:
        await agents.confirm_agent_proposal('agent', ProposalConfirmation(proposal_index=0))
    assert exc.value.status_code == 409
    assert not runner.running_agents
    await agents.stop_agent('agent')
    assert not agent_discussion.busy('agent')
    async with aiosqlite.connect(path) as db:
        status = (await (await db.execute("SELECT status FROM agents WHERE agent_id='agent'")).fetchone())[0]
    assert status == 'awaiting_plan_confirmation'


@pytest.mark.asyncio
async def test_real_pi_steering_and_cancellation(chat_db, tmp_path, monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from types import SimpleNamespace
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            value = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(value)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            chunks = [{'choices': [{'index': 0, 'delta': {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': f'call_{len(requests)}', 'type': 'function', 'function': {'name': 'submit_fix', 'arguments': '{}'}}]}, 'finish_reason': None}]}, {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}]}]
            for chunk in chunks:
                self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    await conversation.add_user('agent', ConversationMessageRequest(text='只处理 Backspace，Delete 保持现状', client_id='pi-steer'))
    llm = SimpleNamespace(client=SimpleNamespace(provider=SimpleNamespace(base_url=f'http://127.0.0.1:{server.server_port}/v1', api_key='test-key', context_windows={'test-model': 64000})), model='test-model', defaults={})
    async def accept(args):
        return args
    try:
        result = await asyncio.wait_for(run_tool_agent(llm, [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'task'}],
            [submit_tool('submit_fix', '提交', {'type': 'object', 'properties': {}}, accept)], stream=ThinkingStream('agent', AsyncMock()), workspace=Workspace(tmp_path), max_turns=8), 15)
        assert result[0] == 'submit_fix'
        assert not await conversation.pending('agent')
        assert 'Delete 保持现状' in json.dumps(requests[-1], ensure_ascii=False)
        entered = asyncio.Event()
        processes = []
        original_spawn = asyncio.create_subprocess_exec
        async def spawn(*args, **kwargs):
            process = await original_spawn(*args, **kwargs)
            processes.append(process)
            return process
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
        async def slow_submit(args):
            entered.set()
            await asyncio.Event().wait()
        task = asyncio.create_task(run_tool_agent(llm, [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'task'}],
            [submit_tool('submit_fix', '提交', {'type': 'object', 'properties': {}}, slow_submit)],
            stream=ThinkingStream('agent', AsyncMock()), workspace=Workspace(tmp_path), max_turns=8))
        try:
            await asyncio.wait_for(entered.wait(), 10)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert processes and processes[0].returncode is not None
        assert not conversation.active

    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.asyncio
async def test_screenshots_survive_and_are_scoped_to_bug(chat_db):
    import base64
    image = 'data:image/png;base64,' + base64.b64encode(b'\x89PNG\r\n\x1a\nfixture').decode()
    message_id = await conversation.add_user('agent', ConversationMessageRequest(text='这个状态', images=[image], client_id='image'))
    assert await conversation.image_data('agent', message_id, 0) == image
    with pytest.raises(ValueError):
        await conversation.image_data('other-agent', message_id, 0)
    with pytest.raises(ValueError):
        await conversation.image_data('agent', message_id, -1)
    data = await agents.get_conversation('agent')
    assert data['messages'][0]['images'] == [f'/api/agents/agent/conversation/{message_id}/images/0']
    assert (await conversation.pending('agent'))[0]['images'] == [image]


@pytest.mark.asyncio
@pytest.mark.parametrize('waiting', [False, True])
async def test_workflow_obeys_confirmation_decision(monkeypatch, waiting):
    from src.agent import graph
    implementation = AsyncMock(return_value={'stage': 'verifying'})
    monkeypatch.setattr(graph, 'design_proposal_node', AsyncMock(return_value={'waiting_for_user': waiting}))
    monkeypatch.setattr(graph, 'implement_fix_node', implementation)
    monkeypatch.setattr(graph, 'verify_fix_node', AsyncMock(return_value={'test_results': {'passed': True}}))
    result = await graph.compile_graph(graph.create_bug_fix_graph('design_proposal')).ainvoke({'require_plan_confirmation': True})
    assert implementation.await_count == (0 if waiting else 1)


@pytest.mark.asyncio
async def test_idle_discussion_answers_with_only_read_tools(chat_db, tmp_path, monkeypatch):
    from src.llm.factory import LLMFactory
    path, _ = chat_db
    async with aiosqlite.connect(path) as db:
        await db.execute("UPDATE agents SET status='awaiting_confirmation', worktree_path=? WHERE agent_id='agent'", (str(tmp_path),))
        await db.commit()
    await conversation.add_user('agent', ConversationMessageRequest(text='为什么这样修改？', client_id='why'))
    from src.core import agent_runtime
    from src.core.pi_tools import PiTools
    async def pi_reply(llm, messages, tools, **kwargs):
        workspace = kwargs['workspace']
        assert workspace.mode == 'read'
        names = set(PiTools(workspace, tools).names) | {tool.name for tool in tools}
        assert not names & {'bash', 'edit', 'write', 'submit_fix', 'submit_analysis', 'browser'}
        terminal = next(tool for tool in tools if tool.name == 'submit_reply')
        return 'submit_reply', await terminal.handler({'reply': '修改只覆盖 Backspace 的空公式删除行为，尚未合并。'})
    monkeypatch.setattr(LLMFactory, 'create', lambda **kwargs: None)
    monkeypatch.setattr(agent_runtime, 'run_pi_agent', pi_reply)
    assert await agent_discussion.discuss('agent') is True
    items = await conversation.messages('agent')
    assert items[-1]['role'] == 'assistant' and '尚未合并' in items[-1]['text']
    async with aiosqlite.connect(path) as db:
        assert (await (await db.execute("SELECT status FROM agents WHERE agent_id='agent'")).fetchone())[0] == 'awaiting_confirmation'
