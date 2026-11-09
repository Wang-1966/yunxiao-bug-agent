import asyncio
import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest
from fastapi import HTTPException

from src.api import agents
from src.core import orchestrator
from src.core import conversation
from src.models.agent import ConversationMessageRequest
from src.core.repair_executor import prepare_worktree
from fake_agent import adapt_operate


@pytest.fixture
async def task_db(tmp_path, monkeypatch):
    path = tmp_path / 'tasks.db'
    async with aiosqlite.connect(path) as db:
        await db.executescript(Path('src/db/init.sql').read_text())
        await db.execute("INSERT INTO bugs (bug_id, title, priority, source) VALUES ('one', '真实 Bug', 'normal', 'manual')")
        await db.commit()
    monkeypatch.setattr(orchestrator.settings, 'database_url', f'sqlite+aiosqlite:///{path}')
    monkeypatch.setattr(orchestrator.settings, 'worktrees_dir', str(tmp_path / 'worktrees'))
    monkeypatch.setattr(agents, 'get_db_path', lambda: str(path))
    return path


@pytest.fixture
async def worktree(tmp_path):
    repo = tmp_path / 'source'
    repo.mkdir()
    (repo / 'app.py').write_text('value = 1\n')
    subprocess.run(['git', 'init', '-b', 'main', str(repo)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-m', 'initial'], check=True, capture_output=True)
    return await prepare_worktree({'repo_path': str(repo), 'branch': 'main'})


async def fail_task(runner, state, error, failed_node):
    task_id = await runner.submit_task('one')
    _, _, task = runner.task_queue.get_nowait()
    agent_id = f'agent-{task_id}'
    runner.agent_states[agent_id]['state'] = {**state, 'stage': 'failed', 'error': error, 'failed_node': failed_node}
    async with aiosqlite.connect(orchestrator.settings.database_url.replace('sqlite+aiosqlite:///', '')) as db:
        await db.execute('UPDATE agents SET worktree_path=? WHERE agent_id=?', (state.get('worktree_path'), agent_id))
        await db.commit()
    await runner._save_status(agent_id, task, 'error', 'failed', f'任务执行失败：{error}')
    return agent_id


async def run_queued(runner):
    _, _, task = runner.task_queue.get_nowait()
    await runner._start_agent(task)
    await runner.running_agents[f'agent-{task.task_id}']
    return task


@pytest.mark.asyncio
async def test_chat_continue_resumes_failed_worktree_once(task_db, worktree, monkeypatch):
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    monkeypatch.setattr(agents, 'get_orchestrator', lambda: runner)
    state = {**worktree, 'root_cause': '已有根因', 'selected_proposal': {'name': '原方案'}}
    agent_id = await fail_task(runner, state, '测试依赖缺失', 'implement_fix')
    source = Path(worktree['worktree_path']) / 'app.py'
    source.write_text('value = 2\n')
    request = ConversationMessageRequest(text='继续', client_id='continue-once')
    with patch('src.core.agent_discussion.start') as discuss:
        first, second = await asyncio.gather(agents.send_conversation(agent_id, request), agents.send_conversation(agent_id, request))
        assert first['id'] == second['id']
        assert first['retry']['agent_id'] == agent_id
        assert first['retry']['start_node'] == 'implement_fix'
        assert runner.task_queue.qsize() == 1
        assert not discuss.called
    assert source.read_text() == 'value = 2\n'
    assert runner.agent_states[agent_id]['task'].resume_state['worktree_path'] == worktree['worktree_path']
    assert not await conversation.pending(agent_id)
    async with aiosqlite.connect(task_db) as db:
        assert await (await db.execute('SELECT status FROM agents WHERE agent_id=?', (agent_id,))).fetchone() == ('busy',)
        # 即使下一轮又失败，重放同一个客户端请求也不能再次启动。
        await db.execute("UPDATE agents SET status='error' WHERE agent_id=?", (agent_id,))
        await db.execute("UPDATE bugs SET status='failed' WHERE bug_id='one'")
        await db.commit()
    replay = await agents.send_conversation(agent_id, request)
    assert 'retry' not in replay and runner.task_queue.qsize() == 1


@pytest.mark.asyncio
async def test_chat_continue_navigates_to_restart_when_original_cannot_resume(task_db, worktree, monkeypatch):
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    monkeypatch.setattr(agents, 'get_orchestrator', lambda: runner)
    agent_id = await fail_task(runner, worktree, '失败', 'implement_fix')
    with patch.object(runner, '_resume_point', AsyncMock(return_value=(None, '原工作树已生成提交'))):
        result = await agents.send_conversation(agent_id, ConversationMessageRequest(text='请继续修复！', client_id='restart'))
    assert result['retry']['mode'] == 'restart'
    assert result['retry']['agent_id'] != agent_id
    assert runner.task_queue.qsize() == 1


@pytest.mark.asyncio
async def test_pending_continue_after_failure_uses_same_resume_flow(task_db, worktree):
    from src.core import agent_discussion
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    agent_id = await fail_task(runner, {**worktree, 'selected_proposal': {'name': '原方案'}}, '失败', 'implement_fix')
    await conversation.add_user(agent_id, ConversationMessageRequest(text='继续修复', client_id='pending-resume'))
    with patch('src.core.agent_discussion.start') as discuss:
        await agent_discussion.resume_pending(agent_id, runner)
    assert runner.task_queue.qsize() == 1
    assert runner.agent_states[agent_id]['task'].start_node == 'implement_fix'
    assert not await conversation.pending(agent_id)
    assert not discuss.called


def patched_workflow(client, operate):
    return (
        patch('src.api.bugs.get_bug_details', AsyncMock(return_value={'title': 'Bug', 'description': '描述'})),
        patch('src.core.orchestrator.resolve_project_config', AsyncMock(return_value={'repo_path': '/repo', 'branch': 'main', 'test_command': 'true'})),
        patch('src.agent.nodes.LLMFactory.create', return_value=client),
        patch.multiple('src.agent.nodes', **dict(zip(('run_analysis', 'run_fix'), adapt_operate(operate)))),
    )


@pytest.mark.asyncio
async def test_proposal_failure_resumes_same_agent_without_reanalysis(task_db, worktree):
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    agent_id = await fail_task(runner, {**worktree, 'root_cause': '已确认根因', 'proposals': [{'name': '已有方案', 'steps': ['改']}], 'confidence': 'high'}, '分析没有产出可执行的修复方案', 'design_proposal')
    operate = AsyncMock(side_effect=AssertionError('不应重新分析源码'))
    p1, p2, p3, p4 = patched_workflow(AsyncMock(), operate)
    with p1, p2, p3, p4, patch.object(agents, 'get_orchestrator', return_value=runner):
        result = await agents.retry_agent(agent_id)
        assert result['mode'] == 'resume' and result['agent_id'] == agent_id and result['start_node'] == 'design_proposal'
        await run_queued(runner)
        statuses = await agents.list_agents()
    assert len(statuses) == 1
    assert statuses[0].status == 'awaiting_plan_confirmation'
    assert statuses[0].root_cause == '已确认根因'
    assert statuses[0].worktree_path == worktree['worktree_path']
    assert runner.agent_states[agent_id]['state']['proposals'] == [{'name': '已有方案', 'steps': ['改']}]


@pytest.mark.asyncio
async def test_fix_failure_resumes_implementation_in_same_worktree(task_db, worktree):
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    state = {**worktree, 'root_cause': '根因', 'proposals': [{'name': 'A'}, {'name': 'B'}], 'selected_proposal': {'name': 'B'}}
    agent_id = await fail_task(runner, state, '执行器没有产生实际代码修改', 'implement_fix')
    source = Path(worktree['worktree_path']) / 'app.py'
    prompts = []

    async def operate(llm, path, prompt, writable=False, **kwargs):
        assert writable and path == worktree['worktree_path']
        async with aiosqlite.connect(task_db) as db:
            stage = await (await db.execute('SELECT stage FROM task_history WHERE agent_id=?', (agent_id,))).fetchone()
        assert stage == ('fixing',)  # 工具执行期间不应仍展示“排队中”。
        prompts.append(prompt)
        source.write_text('value = 2\n')
        return {'summary': '修正'}

    p1, p2, p3, p4 = patched_workflow(AsyncMock(), operate)
    with p1, p2, p3, p4, patch('src.core.orchestrator.prepare_worktree_dependencies', AsyncMock()) as dependencies:
        result = await runner.retry_agent(agent_id)
        assert result['start_node'] == 'implement_fix'
        await run_queued(runner)
    dependencies.assert_awaited_once_with('/repo', worktree['worktree_path'])
    runtime = runner.get_agent_status(agent_id)
    assert runtime['status'] == 'awaiting_confirmation', runtime.get('error')
    assert '"B"' in prompts[0] and '执行器没有产生实际代码修改' in prompts[0]
    async with aiosqlite.connect(task_db) as db:
        assert await (await db.execute("SELECT status FROM bugs WHERE bug_id='one'")).fetchone() == ('awaiting_confirmation',)
        assert await (await db.execute('SELECT COUNT(*) FROM agents')).fetchone() == (1,)


@pytest.mark.asyncio
async def test_verify_without_command_resumes_at_verification_only(task_db, worktree):
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    (Path(worktree['worktree_path']) / 'app.py').write_text('value = 2\n')
    state = {**worktree, 'root_cause': '根因', 'selected_proposal': {'name': 'A'}, 'modified_files': ['app.py'], 'test_results': {}}
    agent_id = await fail_task(runner, state, '代码修改已保留，但未配置验证命令', 'verify_fix')
    p1, p2, p3, p4 = patched_workflow(AsyncMock(), AsyncMock(side_effect=AssertionError('不应再调用模型')))
    with p1, p2, p3, p4:
        assert (await runner.retry_agent(agent_id))['start_node'] == 'verify_fix'
        await run_queued(runner)
    runtime = runner.get_agent_status(agent_id)
    assert runtime['status'] == 'awaiting_confirmation', runtime.get('error')
    assert runtime['state']['git_commit_hash']


@pytest.mark.asyncio
async def test_failed_verification_goes_back_to_fixing_with_test_output(task_db, worktree):
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    state = {**worktree, 'root_cause': '根因', 'selected_proposal': {'name': 'A'}, 'test_results': {'passed': False, 'exit_code': 1}}
    agent_id = await fail_task(runner, state, '验证失败（退出码 1）：AssertionError', 'verify_fix')
    result = await runner.retry_agent(agent_id)
    _, _, task = runner.task_queue.get_nowait()
    assert result['start_node'] == 'implement_fix'
    assert task.resume_state['test_results'] == {}
    assert 'AssertionError' in task.resume_state['retry_context']


@pytest.mark.asyncio
async def test_missing_worktree_reruns_same_agent_from_start(task_db, tmp_path):
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    agent_id = await fail_task(runner, {'worktree_path': str(tmp_path / 'gone')}, '详情读取失败', 'fetch_bug')
    result = await runner.retry_agent(agent_id)
    _, _, task = runner.task_queue.get_nowait()
    assert result['mode'] == 'resume' and result['start_node'] == 'fetch_bug'
    assert task.task_id == agent_id.removeprefix('agent-') and task.resume_state is None
    async with aiosqlite.connect(task_db) as db:
        assert await (await db.execute("SELECT status FROM agents WHERE agent_id=?", (agent_id,))).fetchone() == ('busy',)
        assert await (await db.execute("SELECT status, assigned_agent_id FROM bugs WHERE bug_id='one'")).fetchone() == ('analyzing', agent_id)


@pytest.mark.asyncio
async def test_rejected_review_creates_new_task(task_db, worktree):
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    agent_id = await fail_task(runner, worktree, '用户反馈仍未修复', None)
    async with aiosqlite.connect(task_db) as db:
        await db.execute("INSERT INTO decisions (decision_id, bug_id, agent_id, stage, question, options, status) VALUES ('review', 'one', ?, 'verify', 'q', '[]', 'answered')", (agent_id,))
        await db.commit()
    result = await runner.retry_agent(agent_id)
    assert result['mode'] == 'restart' and result['agent_id'] != agent_id
    async with aiosqlite.connect(task_db) as db:
        assert await (await db.execute("SELECT status FROM agents WHERE agent_id=?", (agent_id,))).fetchone() == ('error',)


@pytest.mark.asyncio
async def test_retry_rejects_running_reassigned_or_duplicate(task_db, worktree):
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    agent_id = await fail_task(runner, {**worktree, 'root_cause': '根因'}, '失败', 'design_proposal')
    with patch.object(agents, 'get_orchestrator', return_value=runner):
        await agents.retry_agent(agent_id)
        with pytest.raises(HTTPException) as exc:
            await agents.retry_agent(agent_id)
        assert exc.value.status_code == 409
        await runner.stop_agent(agent_id)
        assert runner.task_queue.qsize() == 1
        new_task = await runner.submit_task('one')
        with pytest.raises(HTTPException) as exc:
            await agents.retry_agent(agent_id)
        assert exc.value.status_code == 409
        with pytest.raises(HTTPException) as exc:
            await agents.retry_agent('agent-missing')
        assert exc.value.status_code == 404
    async with aiosqlite.connect(task_db) as db:
        assert await (await db.execute("SELECT assigned_agent_id FROM bugs WHERE bug_id='one'")).fetchone() == (f'agent-{new_task}',)


@pytest.mark.asyncio
async def test_legacy_failure_resumes_after_newer_task_is_deleted(task_db, worktree):
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    # 旧版本记录没有 failed_node，续跑点只能由已保存的结果推断。
    old_agent = await fail_task(runner, {**worktree, 'root_cause': '根因'}, '模型未返回可执行的修复方案', None)
    newer = f'agent-{await runner.submit_task("one")}'
    with pytest.raises(ValueError, match='其他任务'):
        await runner.retry_agent(old_agent)
    await runner.delete_agent(newer)
    runner.task_queue.get_nowait()
    result = await runner.retry_agent(old_agent)
    # 旧记录只有根因、没有分析 Agent 产出的方案，必须重新分析。
    assert result['mode'] == 'resume' and result['start_node'] == 'analyze_root_cause'
    async with aiosqlite.connect(task_db) as db:
        assert await (await db.execute("SELECT status, assigned_agent_id FROM bugs WHERE bug_id='one'")).fetchone() == ('analyzing', old_agent)


def test_graph_can_start_from_each_resume_point():
    from src.agent.graph import create_bug_fix_graph, compile_graph
    for node in orchestrator.RESUME_POINTS:
        assert compile_graph(create_bug_fix_graph(node)) is not None
    with pytest.raises(ValueError):
        create_bug_fix_graph('add_logging')
