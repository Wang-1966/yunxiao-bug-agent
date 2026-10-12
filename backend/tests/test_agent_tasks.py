import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest
from fastapi import HTTPException

from src.api import agents
from src.core import orchestrator
from src.models.agent import TaskSubmitRequest
from src.models.agent import RepairConfirmation
import json
import subprocess
import sys


@pytest.fixture
async def task_db(tmp_path, monkeypatch):
    path = tmp_path / "tasks.db"
    async with aiosqlite.connect(path) as db:
        await db.executescript(Path("src/db/init.sql").read_text())
        await db.executemany(
            "INSERT INTO bugs (bug_id, title, priority, source) VALUES (?, ?, 'normal', 'manual')",
            [("one", "真实 Bug"), ("two", "第二个 Bug")],
        )
        await db.commit()
    monkeypatch.setattr(orchestrator.settings, "database_url", f"sqlite+aiosqlite:///{path}")
    monkeypatch.setattr(agents, "get_db_path", lambda: str(path))
    return path


@pytest.mark.asyncio
async def test_api_really_enqueues_and_rejects_duplicate(task_db):
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    with patch.object(agents, "get_orchestrator", return_value=runner):
        result = await agents.submit_task(TaskSubmitRequest(bug_id="one", priority=5))
        assert runner.task_queue.qsize() == 1
        assert runner.task_queue._queue[0][2].task_id == result.task_id
        with pytest.raises(HTTPException) as exc:
            await agents.submit_task(TaskSubmitRequest(bug_id="one", priority=5))
        assert exc.value.status_code == 409
        await agents.submit_task(TaskSubmitRequest(bug_id="two", priority=5))
        assert [runner.task_queue.get_nowait()[2].bug_id for _ in range(2)] == ["one", "two"]
        statuses = await agents.list_agents()
        assert len(statuses) == 2
        assert all(a.status == "busy" and a.current_stage == "queued" for a in statuses)


@pytest.mark.asyncio
async def test_stopped_scheduler_does_not_report_success(task_db):
    with patch.object(agents, "get_orchestrator", return_value=orchestrator.MultiAgentOrchestrator()):
        with pytest.raises(HTTPException) as exc:
            await agents.submit_task(TaskSubmitRequest(bug_id="one"))
    assert exc.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize('resumed', [False, True])
@pytest.mark.parametrize('shutdown', [False, True])
async def test_cancellation_records_service_shutdown_or_user_stop(task_db, tmp_path, resumed, shutdown):
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    task_id = await runner.submit_task('one')
    _, _, task = runner.task_queue.get_nowait()
    agent_id = f'agent-{task_id}'
    source = tmp_path / 'app.py'
    source.write_text('saved repair\n')
    ready = asyncio.Event()

    class VerifyingWorkflow:
        async def astream(self, state):
            yield {'implement_fix': {'stage': 'verifying', 'worktree_path': str(tmp_path), 'modified_files': ['app.py']}}
            ready.set()
            await asyncio.Event().wait()

    state = {'bug_id': 'one', 'stage': 'fixing'}
    runner.agent_states[agent_id]['state'] = state
    with patch('src.core.orchestrator.compile_graph', return_value=VerifyingWorkflow()), \
         patch('src.core.orchestrator.resolve_project_config', AsyncMock(return_value={})), \
         patch('src.agent.nodes.fetch_bug_node', AsyncMock(return_value={'bug_info': {'title': 'bug'}})):
        handle = asyncio.create_task(runner._resume_proposal(agent_id, task, state, False) if resumed else runner._run_agent(agent_id, task))
        runner.running_agents[agent_id] = handle
        await asyncio.wait_for(ready.wait(), 5)
        if shutdown:
            await runner.stop()
        else:
            await runner.stop_agent(agent_id)
    expected = '服务停止，任务执行中断；已有修改保留在工作树，请重新执行' if shutdown else '任务已取消'
    async with aiosqlite.connect(task_db) as db:
        row = await (await db.execute('SELECT error,result FROM task_history WHERE task_id=?', (task_id,))).fetchone()
        logs = await (await db.execute('SELECT message FROM agent_logs WHERE agent_id=?', (agent_id,))).fetchall()
    assert row[0] == expected
    assert json.loads(row[1])['error'] == expected
    assert (expected,) in logs
    assert source.read_text() == 'saved repair\n'


@pytest.mark.asyncio
async def test_visual_context_survives_database_and_proposal_resume(task_db, tmp_path):
    from src.agent.nodes import ensure_visual_context
    from src.core.bug_visual import visual_source
    bug = {'title': 'bug', 'description': '<img src="https://cdn.example/repro.png">', 'agent_images': ['https://cdn.example/repro.png']}
    context = {'source': visual_source(bug), 'observations': [{'image': 1, 'controls': [], 'visible_text': [], 'observed_behavior': 'selected control', 'uncertainties': []}]}
    source_context = {'reads': [{'action': 'read', 'path': 'app.py', 'start_line': 1, 'count': 200}]}
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    task_id = await runner.submit_task('one')
    _, _, task = runner.task_queue.get_nowait()
    agent_id = f'agent-{task_id}'
    runner.agent_states[agent_id]['state'] = {'stage': 'awaiting_plan_confirmation', 'worktree_path': str(tmp_path), 'proposals': [{'name': 'fix'}], 'visual_context': context, 'source_context': source_context}
    await runner._save_status(agent_id, task, 'awaiting_plan_confirmation', 'awaiting_plan_confirmation', 'ready')
    restarted = orchestrator.MultiAgentOrchestrator()
    resumed = []
    client = AsyncMock()

    async def resume(agent_id, task, state, reanalyze):
        assert state['source_context'] == source_context
        state['bug_info'] = bug
        resumed.append(await ensure_visual_context(state, client))

    with patch.object(restarted, '_resume_proposal', side_effect=resume):
        await restarted.confirm_proposal(agent_id, 0, '核对选择状态')
        await restarted.running_agents[agent_id]
    assert resumed == [context]
    client.ainvoke.assert_not_awaited()
    async with aiosqlite.connect(task_db) as db:
        row = await (await db.execute('SELECT result FROM task_history WHERE task_id=?', (task_id,))).fetchone()
    assert json.loads(row[0])['visual_context'] == context
    assert json.loads(row[0])['source_context'] == source_context
    assert 'https://cdn.example' not in row[0]


@pytest.mark.asyncio
async def test_retry_creates_new_task_preserves_failure_and_blocks_duplicate(task_db):
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    with patch.object(agents, "get_orchestrator", return_value=runner):
        first = await agents.submit_task(TaskSubmitRequest(bug_id="one", priority=5))
        _, _, failed_task = runner.task_queue.get_nowait()
        await runner._save_status(f"agent-{first.task_id}", failed_task, "error", "failed", "首次执行失败")
        retry = await agents.submit_task(TaskSubmitRequest(bug_id="one", priority=5))
        assert retry.task_id != first.task_id
        assert runner.task_queue.qsize() == 1
        history = {agent.agent_id: agent for agent in await agents.list_agents()}
        assert history[f"agent-{first.task_id}"].status == "error"
        assert "首次执行失败" in history[f"agent-{first.task_id}"].logs
        assert history[f"agent-{retry.task_id}"].status == "busy"
        with pytest.raises(HTTPException) as exc:
            await agents.submit_task(TaskSubmitRequest(bug_id="one", priority=5))
        assert exc.value.status_code == 409
        assert runner.task_queue.qsize() == 1


@pytest.mark.asyncio
async def test_scheduler_executes_updates_and_stops_on_node_error(task_db):
    runner = orchestrator.MultiAgentOrchestrator()
    finished = asyncio.Event()

    class Workflow:
        async def astream(self, state):
            yield {"fetch_bug": {"bug_info": {"title": "真实 Bug"}, "stage": "analyzing"}}
            yield {"analyze_root_cause": {"error": "模型调用失败", "stage": "completed"}}
            pytest.fail("出错后不应执行下一个节点")

    original = runner._on_agent_complete

    def complete(agent_id, task):
        original(agent_id, task)
        finished.set()

    runner._on_agent_complete = complete
    with patch.object(orchestrator, "create_bug_fix_graph", return_value=None), patch.object(orchestrator, "compile_graph", return_value=Workflow()):
        await runner.start()
        try:
            task_id = await runner.submit_task("one")
            await asyncio.wait_for(finished.wait(), timeout=3)
            runtime = runner.get_agent_status(f"agent-{task_id}")
            assert runtime["state"]["bug_info"]["title"] == "真实 Bug"
            assert runtime["status"] == "failed"
            async with aiosqlite.connect(task_db) as db:
                cursor = await db.execute("SELECT status FROM bugs WHERE bug_id='one'")
                assert await cursor.fetchone() == ("failed",)
                cursor = await db.execute("SELECT message FROM agent_logs WHERE log_level='error'")
                assert "模型调用失败" in (await cursor.fetchone())[0]
        finally:
            await runner.stop()


@pytest.mark.asyncio
async def test_fetch_reads_real_details_and_implementation_cannot_fake_success():
    from src.agent.nodes import fetch_bug_node, implement_fix_node, verify_fix_node

    with patch("src.api.bugs.get_bug_details", AsyncMock(return_value={"title": "真实标题", "description": "真实描述"})):
        result = await fetch_bug_node({"bug_id": "one"})
    assert result["bug_info"]["title"] == "真实标题"
    assert result["bug_info"]["id"] == "one"
    assert (await implement_fix_node({"bug_id": "one"}))["error"]
    assert (await verify_fix_node({"bug_id": "one"}))["error"]


def test_real_workflow_compiles():
    from src.agent.graph import create_bug_fix_graph, compile_graph

    assert compile_graph(create_bug_fix_graph()) is not None


@pytest.mark.asyncio
async def test_stopping_queued_agent_allows_retry_and_prevents_execution(task_db):
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    with patch.object(agents, "get_orchestrator", return_value=runner):
        submitted = await agents.submit_task(TaskSubmitRequest(bug_id="one"))
        agent_id = f"agent-{submitted.task_id}"
        await agents.stop_agent(agent_id)
        _, _, task = runner.task_queue.get_nowait()
        await runner._start_agent(task)
        assert not runner.running_agents
        assert task.status == "cancelled"
        retry = await agents.submit_task(TaskSubmitRequest(bug_id="one"))
        assert retry.task_id != submitted.task_id


@pytest.mark.asyncio
async def test_delete_cleans_dependencies_preserves_bug_and_new_assignment(task_db):
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    old_id = await runner.submit_task("one")
    old_agent = f"agent-{old_id}"
    await runner.stop_agent(old_agent)
    new_id = await runner.submit_task("one")
    async with aiosqlite.connect(task_db) as db:
        await db.execute("INSERT INTO task_history (task_id, bug_id, agent_id, stage, status) VALUES ('history', 'one', ?, 'analyze', 'failed')", (old_agent,))
        await db.execute("INSERT INTO decisions (decision_id, bug_id, agent_id, stage, question, options) VALUES ('decision', 'one', ?, 'analyze', 'question', '[]')", (old_agent,))
        await db.commit()
    with patch.object(agents, "get_orchestrator", return_value=runner):
        await agents.delete_agent(old_agent)
        with pytest.raises(HTTPException) as exc:
            await agents.delete_agent(old_agent)
        assert exc.value.status_code == 404
    assert old_agent not in runner.agent_states
    async with aiosqlite.connect(task_db) as db:
        for table in ('agents', 'agent_logs', 'decisions', 'task_history'):
            cursor = await db.execute(f"SELECT COUNT(*) FROM {table} WHERE agent_id=?", (old_agent,))
            assert await cursor.fetchone() == (0,)
        cursor = await db.execute("SELECT status, assigned_agent_id FROM bugs WHERE bug_id='one'")
        assert await cursor.fetchone() == ('analyzing', f'agent-{new_id}')


@pytest.mark.asyncio
async def test_delete_waits_for_running_agent_and_releases_bug(task_db):
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    started = asyncio.Event()
    exited = asyncio.Event()

    class Workflow:
        async def astream(self, state):
            started.set()
            try:
                await asyncio.Event().wait()
                yield {}
            finally:
                exited.set()

    with patch.object(orchestrator, 'create_bug_fix_graph', return_value=None), patch.object(orchestrator, 'compile_graph', return_value=Workflow()):
        task_id = await runner.submit_task('one')
        _, _, task = runner.task_queue.get_nowait()
        agent_id = f'agent-{task_id}'
        await runner._start_agent(task)
        await asyncio.wait_for(started.wait(), timeout=3)
        await runner.delete_agent(agent_id)
    assert exited.is_set()
    assert agent_id not in runner.running_agents
    assert agent_id not in runner.agent_states
    async with aiosqlite.connect(task_db) as db:
        cursor = await db.execute("SELECT status, assigned_agent_id FROM bugs WHERE bug_id='one'")
        assert await cursor.fetchone() == ('failed', None)
        cursor = await db.execute('SELECT COUNT(*) FROM agent_logs WHERE agent_id=?', (agent_id,))
        assert await cursor.fetchone() == (0,)
    assert await runner.submit_task('one')


@pytest.mark.asyncio
async def test_real_workflow_requires_repository_before_model_calls(task_db):
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    with patch("src.api.bugs.get_bug_details", AsyncMock(return_value={"title": "真实标题", "description": "真实描述"})), patch("src.agent.nodes.LLMFactory.create") as factory:
        task_id = await runner.submit_task("one")
        agent_id = f"agent-{task_id}"
        task = runner.agent_states[agent_id]["task"]
        await runner._run_agent(agent_id, task)
    assert task.status == "failed"
    assert "未配置修复仓库" in runner.agent_states[agent_id]["error"]
    factory.assert_not_called()


async def run_verified_repair(task_db, tmp_path, monkeypatch, command=None):
    repo = tmp_path / 'source'
    repo.mkdir()
    (repo / 'app.py').write_text('value = 1\nassert value == 2\n')
    subprocess.run(['git', 'init', '-b', 'main', str(repo)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-m', 'initial'], check=True, capture_output=True)
    (repo / 'app.py').write_text('uncommitted original work\n')
    monkeypatch.setattr(orchestrator.settings, 'worktrees_dir', str(tmp_path / 'worktrees'))
    async with aiosqlite.connect(task_db) as db:
        await db.execute("UPDATE bugs SET repo_path=?, branch='main', test_command=?, source='yunxiao', source_project_id='legacy-project' WHERE bug_id='one'", (str(repo), command or f'{sys.executable} app.py'))
        await db.execute("INSERT INTO projects (project_id, name, repo_path, branch, test_command) VALUES ('legacy-project', 'Legacy', ?, 'main', ?)", (str(repo), command or f'{sys.executable} app.py'))
        await db.commit()
    client = AsyncMock()
    client.ainvoke.side_effect = [
        json.dumps({'action': 'read', 'path': 'app.py'}),
        json.dumps({'action': 'finish', 'result': {'root_cause': 'value should be 2', 'confidence': 'high'}}),
        json.dumps({'proposals': [{'name': 'correct value'}]}),
        json.dumps({'action': 'read', 'path': 'app.py'}),
        json.dumps({'action': 'edit', 'edits': [{'path': 'app.py', 'before': 'value = 1', 'after': 'value = 2'}]}),
        json.dumps({'action': 'finish', 'result': {'summary': 'Correct value assignment'}}),
    ]
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    with patch.object(agents, 'get_orchestrator', return_value=runner), patch('src.agent.nodes.LLMFactory.create', return_value=client):
        submitted = await agents.submit_task(TaskSubmitRequest(bug_id='one'))
        _, _, task = runner.task_queue.get_nowait()
        await runner._run_agent(f'agent-{submitted.task_id}', task)
        await runner.confirm_proposal(f'agent-{submitted.task_id}', 0)
        handle = runner.running_agents[f'agent-{submitted.task_id}']
        await handle
        assert (repo / 'app.py').read_text() == 'uncommitted original work\n'
    return runner, f'agent-{submitted.task_id}'


@pytest.mark.asyncio
async def test_proposal_feedback_reanalyzes_until_explicit_confirmation(task_db, tmp_path):
    worktree = tmp_path / 'worktree'
    worktree.mkdir()
    source = worktree / 'app.py'
    source.write_text('value = 1\n')
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    task_id = await runner.submit_task('one')
    _, _, task = runner.task_queue.get_nowait()
    agent_id = f'agent-{task_id}'
    state = {'bug_id': 'one', 'stage': 'awaiting_plan_confirmation', 'root_cause': '旧理解',
             'proposals': [{'name': '旧方案'}], 'worktree_path': str(worktree), 'repair_branch': 'repair',
             'repair_feedback': '', 'require_plan_confirmation': True}
    runner.agent_states[agent_id]['state'] = state
    await runner._save_status(agent_id, task, 'awaiting_plan_confirmation', 'awaiting_plan_confirmation', '待确认')
    client = AsyncMock()
    client.ainvoke.side_effect = [json.dumps({'proposals': [{'name': '新方案'}]})] * 2
    seen = []

    async def operate(llm, path, prompt, writable=False, **kwargs):
        seen.append((prompt, writable))
        if writable:
            source.write_text('value = 2\n')
            return {'summary': '修正'}
        return {'root_cause': '修正后的理解', 'confidence': 'high'}

    with patch('src.api.bugs.get_bug_details', AsyncMock(return_value={'title': 'Bug', 'description': '描述'})), \
         patch('src.agent.nodes.LLMFactory.create', return_value=client), \
         patch('src.agent.nodes.repository_agent', side_effect=operate), \
         patch('src.agent.nodes.changed_files', AsyncMock(return_value=['app.py'])), \
         patch('src.agent.nodes.verify_and_commit_repair', AsyncMock(return_value={'test_results': {'passed': True}, 'git_commit_hash': 'a' * 40})):
        for feedback in ('实际问题是无法输入', '仅发生在分式公式之后'):
            # 模拟服务重启：只从数据库恢复，不能依赖原协调器的内存。
            runner = orchestrator.MultiAgentOrchestrator()
            async with aiosqlite.connect(task_db) as db:
                previous_id = (await (await db.execute("SELECT decision_id FROM decisions WHERE agent_id=? AND status='pending'", (agent_id,))).fetchone())[0]
            result = await runner.confirm_proposal(agent_id, 0, feedback, previous_id)
            with pytest.raises(ValueError, match='已处理'):
                await runner.confirm_proposal(agent_id, 0, feedback, previous_id)
            assert result['status'] == 'analyzing'
            await runner.running_agents[agent_id]
            assert source.read_text() == 'value = 1\n'
            assert runner.agent_states[agent_id]['status'] == 'awaiting_plan_confirmation'
            async with aiosqlite.connect(task_db) as db:
                row = await (await db.execute("SELECT COUNT(*) FROM decisions WHERE agent_id=? AND stage='proposal' AND status='pending'", (agent_id,))).fetchone()
                assert row == (1,)
            with pytest.raises(ValueError, match='已更新'):
                await runner.confirm_proposal(agent_id, 0, decision_id=previous_id)
        assert all(not writable for _, writable in seen)
        assert '实际问题是无法输入' in seen[-1][0] and '仅发生在分式公式之后' in seen[-1][0]
        for call in client.ainvoke.call_args_list:
            assert '实际问题是无法输入' in call.args[0][0]['content']
        runner = orchestrator.MultiAgentOrchestrator()
        result = await runner.confirm_proposal(agent_id, 0)
        assert result['status'] == 'fixing'
        await runner.running_agents[agent_id]
        assert seen[-1][1] is True
        assert '实际问题是无法输入' in seen[-1][0] and '仅发生在分式公式之后' in seen[-1][0]
        assert runner.agent_states[agent_id]['status'] == 'awaiting_confirmation'
        from src.core.repair_review import latest_repair_feedback
        assert await latest_repair_feedback(str(task_db), 'one') == '实际问题是无法输入\n\n仅发生在分式公式之后'


@pytest.mark.asyncio
async def test_feedback_resume_failure_is_recorded_and_retry_keeps_feedback(task_db, tmp_path):
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    task_id = await runner.submit_task('one')
    _, _, task = runner.task_queue.get_nowait()
    agent_id = f'agent-{task_id}'
    runner.agent_states[agent_id]['state'] = {'stage': 'awaiting_plan_confirmation', 'proposals': [{'name': '方案'}], 'worktree_path': str(tmp_path)}
    await runner._save_status(agent_id, task, 'awaiting_plan_confirmation', 'awaiting_plan_confirmation', '待确认')
    with patch('src.api.bugs.get_bug_details', AsyncMock(side_effect=RuntimeError('详情读取失败'))):
        await runner.confirm_proposal(agent_id, 0, '仅发生在分式之后')
        await runner.running_agents[agent_id]
    assert runner.agent_states[agent_id]['status'] == 'failed'
    async with aiosqlite.connect(task_db) as db:
        assert await (await db.execute("SELECT status FROM bugs WHERE bug_id='one'")).fetchone() == ('failed',)
    from src.core.repair_review import latest_repair_feedback
    assert await latest_repair_feedback(str(task_db), 'one') == '仅发生在分式之后'
    assert await runner.submit_task('one')


@pytest.mark.asyncio
@pytest.mark.parametrize('confirmed', [True, False])
async def test_real_repair_waits_for_confirmation_survives_restart_and_records_decision(task_db, tmp_path, monkeypatch, confirmed):
    runner, agent_id = await run_verified_repair(task_db, tmp_path, monkeypatch)
    # 用新协调器模拟内存完全丢失，所有证据必须从数据库读取。
    restarted = orchestrator.MultiAgentOrchestrator()
    with patch.object(agents, 'get_orchestrator', return_value=restarted):
        await restarted.start()
        try:
            result = await agents.get_agent(agent_id)
            assert result.status == 'awaiting_confirmation'
            assert result.current_bug['status'] == 'awaiting_confirmation'
            assert result.current_bug['resolved_at'] is None
            assert result.test_results['passed'] is True
            assert result.modified_files == ['app.py']
            assert result.fix_summary == 'Correct value assignment'
            assert result.repair_branch
            assert result.confirmation['status'] == 'pending'
            assert 'value = 2' in (await agents.agent_diff(agent_id))['diff']
            assert result.git_commit_hash
            worktree = Path(result.worktree_path)
            repo = tmp_path / 'source'
            assert subprocess.check_output(['git', '-C', str(worktree), 'status', '--porcelain']).decode() == ''
            if confirmed:
                # 修改隔离已单独断言；成功交付场景要求目标仓库干净。
                subprocess.run(['git', '-C', str(repo), 'restore', 'app.py'], check=True)
            decision = RepairConfirmation(confirmed=confirmed, feedback='' if confirmed else 'value 在其他输入下仍错误')
            await agents.confirm_agent_repair(agent_id, decision)
            result = await agents.get_agent(agent_id)
            assert result.current_bug['status'] == ('resolved' if confirmed else 'failed')
            assert bool(result.current_bug['resolved_at']) is confirmed
            assert result.confirmation['user_choice']['confirmed'] is confirmed
            if confirmed:
                assert (repo / 'app.py').read_text().startswith('value = 2')
                assert not worktree.exists()
                assert result.worktree_path is None
                assert result.confirmation['user_choice']['merge']['cleanup_pending'] is False
            with pytest.raises(HTTPException) as exc:
                await agents.confirm_agent_repair(agent_id, decision)
            assert exc.value.status_code == 409
            if not confirmed:
                from src.core.repair_review import latest_repair_feedback
                assert await latest_repair_feedback(str(task_db), 'one') == decision.feedback
                await agents.submit_task(TaskSubmitRequest(bug_id='one'))
                with pytest.raises(HTTPException):
                    await agents.confirm_agent_repair(agent_id, RepairConfirmation(confirmed=True))
        finally:
            await restarted.stop()


@pytest.mark.asyncio
async def test_empty_rejection_and_running_task_cannot_resolve_bug(task_db, tmp_path, monkeypatch):
    runner, agent_id = await run_verified_repair(task_db, tmp_path, monkeypatch)
    with patch.object(agents, 'get_orchestrator', return_value=runner):
        with pytest.raises(HTTPException):
            await agents.confirm_agent_repair(agent_id, RepairConfirmation(confirmed=False, feedback=' '))
        assert (await agents.get_agent(agent_id)).status == 'awaiting_confirmation'
        async with aiosqlite.connect(task_db) as db:
            await db.execute("UPDATE bugs SET status='pending', assigned_agent_id=NULL WHERE bug_id='two'")
            await db.commit()
        submitted = await agents.submit_task(TaskSubmitRequest(bug_id='two'))
        other_agent = f'agent-{submitted.task_id}'
        with pytest.raises(HTTPException):
            await agents.confirm_agent_repair(other_agent, RepairConfirmation(confirmed=True))


@pytest.mark.asyncio
async def test_real_failed_command_cannot_publish_a_confirmation(task_db, tmp_path, monkeypatch):
    runner, agent_id = await run_verified_repair(task_db, tmp_path, monkeypatch, f"{sys.executable} -c 'raise SystemExit(2)'")
    with patch.object(agents, 'get_orchestrator', return_value=runner):
        result = await agents.get_agent(agent_id)
        assert result.status == 'error'
        assert result.current_bug['status'] == 'failed'
        assert result.test_results['passed'] is False
        assert result.test_results['exit_code'] == 2
        assert result.confirmation is None
        with pytest.raises(HTTPException):
            await agents.confirm_agent_repair(agent_id, RepairConfirmation(confirmed=True))


@pytest.mark.asyncio
async def test_deleted_pending_review_releases_bug_for_retry(task_db, tmp_path, monkeypatch):
    runner, agent_id = await run_verified_repair(task_db, tmp_path, monkeypatch)
    path = runner.get_agent_status(agent_id)['state']['worktree_path']
    await runner.delete_agent(agent_id)
    assert Path(path).is_dir()
    assert await runner.submit_task('one')


@pytest.mark.asyncio
async def test_delivery_rejects_dirty_target_and_changed_review_commit(task_db, tmp_path, monkeypatch):
    runner, agent_id = await run_verified_repair(task_db, tmp_path, monkeypatch)
    with patch.object(agents, 'get_orchestrator', return_value=runner):
        with pytest.raises(HTTPException) as exc:
            await agents.confirm_agent_repair(agent_id, RepairConfirmation(confirmed=True))
        assert '未提交修改' in exc.value.detail
        result = await agents.get_agent(agent_id)
        assert result.confirmation['status'] == 'pending'
        assert (tmp_path / 'source' / 'app.py').read_text() == 'uncommitted original work\n'
        subprocess.run(['git', '-C', str(tmp_path / 'source'), 'restore', 'app.py'], check=True)
        with pytest.raises(HTTPException) as exc:
            await agents.confirm_agent_repair(agent_id, RepairConfirmation(confirmed=True, git_commit_hash='a' * 40))
        assert '页面审查' in exc.value.detail
        Path(result.worktree_path, 'app.py').write_text('changed after validation\n')
        with pytest.raises(HTTPException) as exc:
            await agents.confirm_agent_repair(agent_id, RepairConfirmation(confirmed=True, git_commit_hash=result.git_commit_hash))
        assert '验证后发生修改' in exc.value.detail
        assert (await agents.get_agent(agent_id)).current_bug['status'] == 'awaiting_confirmation'


@pytest.mark.asyncio
async def test_cleanup_failure_records_delivery_and_retry_never_remerges(task_db, tmp_path, monkeypatch):
    runner, agent_id = await run_verified_repair(task_db, tmp_path, monkeypatch)
    repo = tmp_path / 'source'
    subprocess.run(['git', '-C', str(repo), 'restore', 'app.py'], check=True)
    with patch.object(agents, 'get_orchestrator', return_value=runner):
        before = await agents.get_agent(agent_id)
        with patch('src.core.repair_review.cleanup_confirmed_repair', AsyncMock(side_effect=ValueError('cleanup failed'))):
            result = await agents.confirm_agent_repair(agent_id, RepairConfirmation(confirmed=True))
        assert result['status'] == 'resolved'
        assert result['merge']['cleanup_pending'] is True
        assert Path(before.worktree_path).exists()
        head = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD']).decode().strip()
        restarted = orchestrator.MultiAgentOrchestrator()
        with patch.object(agents, 'get_orchestrator', return_value=restarted), patch('src.core.repair_review.merge_confirmed_repair', AsyncMock()) as merge:
            result = await agents.confirm_agent_repair(agent_id, RepairConfirmation(confirmed=True))
        merge.assert_not_awaited()
        assert result['merge']['cleanup_pending'] is False
        assert not Path(before.worktree_path).exists()
        assert subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD']).decode().strip() == head


@pytest.mark.asyncio
async def test_merge_conflict_aborts_only_system_merge_and_keeps_repair(task_db, tmp_path, monkeypatch):
    runner, agent_id = await run_verified_repair(task_db, tmp_path, monkeypatch)
    repo = tmp_path / 'source'
    (repo / 'app.py').write_text('value = 99\n')
    subprocess.run(['git', '-C', str(repo), 'add', 'app.py'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-m', 'conflict'], check=True, capture_output=True)
    with patch.object(agents, 'get_orchestrator', return_value=runner):
        with pytest.raises(HTTPException) as exc:
            await agents.confirm_agent_repair(agent_id, RepairConfirmation(confirmed=True))
        assert '合并冲突' in exc.value.detail
        result = await agents.get_agent(agent_id)
        assert result.confirmation['status'] == 'pending'
        assert Path(result.worktree_path).exists()
        assert subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain']).decode() == ''
        assert (repo / 'app.py').read_text() == 'value = 99\n'


@pytest.mark.asyncio
async def test_retry_after_merge_succeeded_does_not_duplicate_merge(task_db, tmp_path, monkeypatch):
    from src.core.repair_merge import merge_confirmed_repair
    runner, agent_id = await run_verified_repair(task_db, tmp_path, monkeypatch)
    repo = tmp_path / 'source'
    subprocess.run(['git', '-C', str(repo), 'restore', 'app.py'], check=True)

    async def merge_then_interrupt(**kwargs):
        await merge_confirmed_repair(**kwargs)
        raise ValueError('interrupted after merge')

    with patch.object(agents, 'get_orchestrator', return_value=runner):
        with patch('src.core.repair_review.merge_confirmed_repair', side_effect=merge_then_interrupt):
            with pytest.raises(HTTPException):
                await agents.confirm_agent_repair(agent_id, RepairConfirmation(confirmed=True))
        head = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD']).decode().strip()
        assert (await agents.get_agent(agent_id)).confirmation['status'] == 'pending'
        result = await agents.confirm_agent_repair(agent_id, RepairConfirmation(confirmed=True))
        assert result['merge']['merge_commit'] == head
        assert result['status'] == 'resolved'
