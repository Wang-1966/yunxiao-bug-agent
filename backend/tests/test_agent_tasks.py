import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
from fake_agent import REVIEW_OK, ScriptedLLM, adapt_operate, analysis_args, turn
import httpx
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
    monkeypatch.setattr('src.tools.yunxiao.openapi_client.YunxiaoClient.set_bug_in_progress',
                        AsyncMock(return_value={'status_id': 'test-progress', 'source_status': '处理中'}))
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
async def test_submit_assigns_a_nickname(task_db):
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    await runner.submit_task("one")
    await runner.submit_task("two")
    async with aiosqlite.connect(task_db) as db:
        names = [row[0] for row in await (await db.execute("SELECT name FROM agents")).fetchall()]
    assert len(names) == 2 and len(set(names)) == 2
    assert set(names) <= set(orchestrator.AGENT_NICKNAMES)


def test_nickname_adds_a_number_after_the_pool_is_used():
    taken = set(orchestrator.AGENT_NICKNAMES)
    base, number = orchestrator.pick_agent_name(taken).rsplit(" ", 1)
    assert base in orchestrator.AGENT_NICKNAMES and number == "2"
    taken.update(f"{name} 2" for name in orchestrator.AGENT_NICKNAMES)
    base, number = orchestrator.pick_agent_name(taken).rsplit(" ", 1)
    assert base in orchestrator.AGENT_NICKNAMES and number == "3"


@pytest.mark.asyncio
async def test_start_renames_stiff_agent_names(task_db):
    async with aiosqlite.connect(task_db) as db:
        await db.execute(
            "INSERT INTO agents (agent_id, name, status) VALUES ('agent-task-abc12345', '修复 Agent task-abc12345', 'idle')"
        )
        await db.commit()
    runner = orchestrator.MultiAgentOrchestrator()
    await runner.start()
    try:
        async with aiosqlite.connect(task_db) as db:
            name = (await (await db.execute("SELECT name FROM agents")).fetchone())[0]
        assert name in orchestrator.AGENT_NICKNAMES
    finally:
        await runner.stop()


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
    client = ScriptedLLM([
        turn(('read_file', {'path': 'app.py'})),
        turn(analysis_args()),
        turn(REVIEW_OK),
        turn(('read_file', {'path': 'app.py'})),
        turn(('edit_file', {'path': 'app.py', 'old_string': 'value = 1', 'new_string': 'value = 2'})),
        turn(('submit_fix', {'summary': 'Correct value assignment'})),
        turn(REVIEW_OK),
    ])
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
    seen = []

    async def operate(llm, path, prompt, writable=False, **kwargs):
        seen.append((prompt, writable))
        if writable:
            source.write_text('value = 2\n')
            return {'summary': '修正'}
        return {'root_cause': '修正后的理解', 'confidence': 'high'}

    with patch('src.api.bugs.get_bug_details', AsyncMock(return_value={'title': 'Bug', 'description': '描述'})), \
         patch('src.agent.nodes.LLMFactory.create', return_value=client), \
         patch.multiple('src.agent.nodes', **dict(zip(('run_analysis', 'run_fix'), adapt_operate(operate)))), \
         patch('src.agent.nodes.repository_context', AsyncMock(return_value={'instructions': '无', 'inventory': 'app.py'})), \
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
    from src.tools.yunxiao.openapi_client import YunxiaoClient
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
            assert result.repair_branch.startswith('agentfix/one-真实-Bug-')
            assert result.confirmation['status'] == 'pending'
            assert 'value = 2' in (await agents.agent_diff(agent_id))['diff']
            assert result.git_commit_hash
            worktree = Path(result.worktree_path)
            repair_branch = result.repair_branch
            verified_commit = result.git_commit_hash
            repo = tmp_path / 'source'
            assert subprocess.check_output(['git', '-C', str(worktree), 'status', '--porcelain']).decode() == ''
            if confirmed:
                # 修改隔离已单独断言；成功交付场景要求目标仓库干净。
                subprocess.run(['git', '-C', str(repo), 'restore', 'app.py'], check=True)
            decision = RepairConfirmation(confirmed=confirmed, feedback='' if confirmed else 'value 在其他输入下仍错误')
            confirmed_result = await agents.confirm_agent_repair(agent_id, decision)
            assert YunxiaoClient.set_bug_in_progress.await_count == (1 if confirmed else 0)
            if confirmed:
                assert confirmed_result['yunxiao_sync']['status'] == 'succeeded'
            result = await agents.get_agent(agent_id)
            # 驳回后自动开始下一轮，Bug 已被新任务认领。
            assert result.current_bug['status'] == ('resolved' if confirmed else 'analyzing')
            assert bool(result.current_bug['resolved_at']) is confirmed
            assert result.confirmation['user_choice']['confirmed'] is confirmed
            assert not worktree.exists()
            assert result.worktree_path is None
            if confirmed:
                assert (repo / 'app.py').read_text().startswith('value = 2')
                assert result.confirmation['user_choice']['merge']['cleanup_pending'] is False
                assert result.current_bug['source_status'] == '处理中'
                message = subprocess.check_output(['git', '-C', str(repo), 'log', '-1', '--format=%B']).decode().strip()
                assert message == f"Merge branch '{repair_branch}' into main"
                assert subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD^2']).decode().strip() == verified_commit
                assert subprocess.check_output(['git', '-C', str(repo), 'show', '-s', '--format=%s', verified_commit]).decode().strip() == 'fix: 真实 Bug'
            with pytest.raises(HTTPException) as exc:
                await agents.confirm_agent_repair(agent_id, decision)
            assert exc.value.status_code == 409
            if not confirmed:
                restart = confirmed_result['restart']
                assert restart['started'] is True and restart['round'] == 2
                assert result.current_bug['assigned_agent_id'] == restart['agent_id']
                assert subprocess.run(['git', '-C', str(repo), 'show-ref', '--verify', f'refs/heads/{result.repair_branch}']).returncode != 0
                from src.core.repair_review import latest_repair_feedback
                from src.core.repair_attempts import latest_rejected_attempt
                assert await latest_repair_feedback(str(task_db), 'one') == ''
                attempt = await latest_rejected_attempt(str(task_db), 'one')
                assert attempt['round'] == 1 and attempt['feedback'] == decision.feedback
                assert attempt['modified_files'] == ['app.py'] and 'app.py' in attempt['diff_stat']
                # 分支已删，隐藏引用仍保住提交，可按需查看差异。
                assert subprocess.check_output(['git', '-C', str(repo), 'rev-parse', attempt['attempt_ref']]).decode().strip() == attempt['git_commit_hash']
                from src.core.repair_attempts import previous_diff
                assert '+value = 2' in await previous_diff(repo, attempt, 'app.py')
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
async def test_verify_failure_goes_back_to_the_fixer(monkeypatch):
    from src.agent import nodes

    async def fail(*args, **kwargs):
        return {'test_results': {'passed': False, 'exit_code': 2, 'command': 'git diff --check', 'output': 'src/main.scss:659: new blank line at EOF.\n'}}

    monkeypatch.setattr(nodes, 'verify_and_commit_repair', fail)
    state = {'bug_id': 'one', 'worktree_path': '/tmp', 'repair_branch': 'b', 'base_commit': 'abc', 'bug_info': {'title': 't', 'test_command': 'true'}, 'repro': {}}
    first = await nodes.verify_fix_node(state)
    assert first.get('error') is None
    assert first['stage'] == 'fixing'
    assert 'new blank line at EOF' in first['retry_context']
    stopped = await nodes.verify_fix_node({**state, 'verify_rounds': nodes.MAX_VERIFY_ROUNDS - 1})
    assert 'new blank line at EOF' in stopped['error']


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
    state = runner.get_agent_status(agent_id)['state']
    repo = tmp_path / 'source'
    await runner.delete_agent(agent_id)
    assert not Path(state['worktree_path']).exists()
    assert state['worktree_path'] not in subprocess.check_output(['git', '-C', str(repo), 'worktree', 'list']).decode()
    assert not subprocess.check_output(['git', '-C', str(repo), 'branch', '--list', state['repair_branch']]).strip()
    assert await runner.submit_task('one')


@pytest.mark.asyncio
async def test_startup_sweep_removes_only_unreferenced_worktrees(task_db, tmp_path, monkeypatch):
    from src.core.repair_merge import sweep_orphan_worktrees
    runner, agent_id = await run_verified_repair(task_db, tmp_path, monkeypatch)
    kept = runner.get_agent_status(agent_id)['state']['worktree_path']
    repo = tmp_path / 'source'
    orphan = tmp_path / 'worktrees' / 'repair-orphan'
    subprocess.run(['git', '-C', str(repo), 'worktree', 'add', '-b', 'bug-agent/orphan', str(orphan), 'main'], check=True, capture_output=True)
    (orphan / 'node_modules').mkdir()
    subprocess.run(['git', '-C', str(repo), 'branch', 'bug-agent/gone', 'main'], check=True)
    new_orphan = tmp_path / 'worktrees' / 'repair-new-orphan'
    subprocess.run(['git', '-C', str(repo), 'worktree', 'add', '-b', 'agentfix/orphan-标题-123456abcdef', str(new_orphan), 'main'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(repo), 'branch', 'agentfix/gone-标题-123456abcdef', 'main'], check=True)
    external = tmp_path / 'manual-worktree'
    external_branch = 'agentfix/manual-标题-123456abcdef'
    subprocess.run(['git', '-C', str(repo), 'worktree', 'add', '-b', external_branch, str(external), 'main'], check=True, capture_output=True)
    await sweep_orphan_worktrees(task_db)
    assert Path(kept).is_dir() and not orphan.exists()
    assert not new_orphan.exists() and external.exists()
    branches = subprocess.check_output(['git', '-C', str(repo), 'branch', '--list', 'bug-agent/*', 'agentfix/*']).decode()
    assert 'orphan' not in branches and 'gone' not in branches
    assert runner.get_agent_status(agent_id)['state']['repair_branch'] in branches
    assert external_branch in branches


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
async def test_cleanup_retry_removes_cache_left_by_running_dev_server(task_db, tmp_path, monkeypatch):
    runner, agent_id = await run_verified_repair(task_db, tmp_path, monkeypatch)
    repo = tmp_path / 'source'
    subprocess.run(['git', '-C', str(repo), 'restore', 'app.py'], check=True)
    with patch.object(agents, 'get_orchestrator', return_value=runner):
        from src.core import repair_merge
        worktree = Path((await agents.get_agent(agent_id)).worktree_path)
        real_run = repair_merge.run_process

        async def remove_then_dev_server_writes(args, cwd, *rest):
            if 'remove' not in args:
                return await real_run(args, cwd, *rest)
            subprocess.run(['git', '-C', str(repo), 'worktree', 'remove', str(worktree)], check=True)
            (worktree / '.vite' / 'deps').mkdir(parents=True)
            return 1, "error: failed to delete: Directory not empty"

        with patch('src.core.repair_merge.run_process', side_effect=remove_then_dev_server_writes), \
             patch('src.core.repair_merge.shutil.rmtree'):
            result = await agents.confirm_agent_repair(agent_id, RepairConfirmation(confirmed=True))
        assert result['merge']['cleanup_pending'] is True
        assert '进程占用' in result['merge']['cleanup_error']
        result = await agents.confirm_agent_repair(agent_id, RepairConfirmation(confirmed=True))
        assert result['merge']['cleanup_pending'] is False
        assert not worktree.exists()
        assert not subprocess.check_output(['git', '-C', str(repo), 'branch', '--list', 'bug-agent/*', 'agentfix/*']).strip()


@pytest.mark.asyncio
async def test_merge_conflict_keeps_both_sides_when_resolver_edits(task_db, tmp_path, monkeypatch):
    runner, agent_id = await run_verified_repair(task_db, tmp_path, monkeypatch)
    repo = tmp_path / 'source'
    (repo / 'app.py').write_text('value = 99\n')
    subprocess.run(['git', '-C', str(repo), 'add', 'app.py'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-m', 'conflict'], check=True, capture_output=True)

    async def resolver(worktree, files):
        assert 'app.py' in files
        (Path(worktree) / 'app.py').write_text('value = 2\nassert value == 2\nvalue_from_target = 99\n')

    with patch.object(agents, 'get_orchestrator', return_value=runner), \
         patch('src.core.repair_merge.resolve_merge_with_agent', side_effect=resolver):
        result = await agents.confirm_agent_repair(agent_id, RepairConfirmation(confirmed=True))
    text = (repo / 'app.py').read_text()
    assert result['merge']['conflict_resolved'] is True
    assert 'value = 2' in text and 'value_from_target = 99' in text
    assert '<<<<<<<' not in text
    assert subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain']).decode() == ''


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


PNG = 'data:image/png;base64,' + __import__('base64').b64encode(b'\x89PNG\r\n\x1a\n' + b'feedback').decode()


@pytest.mark.asyncio
async def test_image_only_proposal_feedback_reanalyzes_with_screenshot(task_db, tmp_path):
    worktree = tmp_path / 'worktree'
    worktree.mkdir()
    (worktree / 'app.py').write_text('value = 1\n')
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    task_id = await runner.submit_task('one')
    _, _, task = runner.task_queue.get_nowait()
    agent_id = f'agent-{task_id}'
    runner.agent_states[agent_id]['state'] = {'bug_id': 'one', 'stage': 'awaiting_plan_confirmation', 'proposals': [{'name': '旧方案'}], 'worktree_path': str(worktree), 'repair_branch': 'repair'}
    await runner._save_status(agent_id, task, 'awaiting_plan_confirmation', 'awaiting_plan_confirmation', '待确认')
    client = AsyncMock()
    seen = []

    async def operate(llm, path, prompt, writable=False, **kwargs):
        seen.append((prompt, kwargs.get('image_urls')))
        return {'root_cause': '看图后的理解', 'confidence': 'high'}

    with pytest.raises(ValueError, match='PNG'):
        await runner.confirm_proposal(agent_id, 0, '', None, ['data:text/html;base64,AAAA'])
    with patch('src.api.bugs.get_bug_details', AsyncMock(return_value={'title': 'Bug', 'description': '描述'})), \
         patch('src.agent.nodes.LLMFactory.create', return_value=client), \
         patch('src.agent.nodes.describe_bug_images', AsyncMock(return_value=None)), \
         patch('src.agent.nodes.repository_context', AsyncMock(return_value={'instructions': '无', 'inventory': 'app.py'})), \
         patch.multiple('src.agent.nodes', **dict(zip(('run_analysis', 'run_fix'), adapt_operate(operate)))):
        result = await runner.confirm_proposal(agent_id, 0, '  ', None, [PNG])
        assert result['status'] == 'analyzing'
        await runner.running_agents[agent_id]
    assert seen[-1][1] == [PNG]
    assert '原图编号 1–1' in seen[-1][0] and 'view_image' in seen[-1][0]
    assert runner.agent_states[agent_id]['status'] == 'awaiting_plan_confirmation'
    async with aiosqlite.connect(task_db) as db:
        choice = json.loads((await (await db.execute("SELECT user_choice FROM decisions WHERE agent_id=? AND status='answered'", (agent_id,))).fetchone())[0])
    assert len(choice['images']) == 1 and PNG not in json.dumps(choice)


async def add_decision(db, decision_id, agent_id, stage, choice):
    await db.execute("INSERT INTO decisions (decision_id, bug_id, agent_id, stage, question, options, user_choice, status) VALUES (?, 'one', ?, ?, 'q', '[]', ?, 'answered')",
                     (decision_id, agent_id, stage, json.dumps(choice, ensure_ascii=False)))


@pytest.mark.asyncio
async def test_only_latest_rejected_round_is_carried_and_auto_restart_is_capped(task_db):
    from src.core.repair_review import latest_repair_feedback
    from src.core.repair_attempts import latest_rejected_attempt
    async with aiosqlite.connect(task_db) as db:
        for agent_id, root in (('agent-a', '第一轮根因'), ('agent-b', '第二轮根因')):
            await db.execute("INSERT INTO task_history (task_id, bug_id, agent_id, stage, status, result) VALUES (?, 'one', ?, 'rejected', 'rejected', ?)",
                             (agent_id, agent_id, json.dumps({'root_cause': root, 'diff_stat': f'diff {root}', 'source_context': {'reads': [{'path': f'{agent_id}.vue'}]}}, ensure_ascii=False)))
        await add_decision(db, 'p-a', 'agent-a', 'proposal', {'feedback': '第一轮纠正'})
        await add_decision(db, 'v-a', 'agent-a', 'verify', {'confirmed': False, 'feedback': '第一轮仍错'})
        await add_decision(db, 'p-b', 'agent-b', 'proposal', {'feedback': '第二轮纠正'})
        await add_decision(db, 'v-b', 'agent-b', 'verify', {'confirmed': False, 'feedback': '第二轮仍错', 'images': ['x.png']})
        await db.commit()
    assert await latest_repair_feedback(str(task_db), 'one') == '第二轮纠正'
    attempt = await latest_rejected_attempt(str(task_db), 'one')
    assert attempt['round'] == 2 and attempt['root_cause'] == '第二轮根因' and attempt['feedback'] == '第二轮仍错'
    assert attempt['diff_stat'] == 'diff 第二轮根因' and attempt['image_count'] == 1
    assert attempt['evidence_reads'] == [{'path': 'agent-b.vue'}]

    runner = orchestrator.MultiAgentOrchestrator()
    with patch.object(agents, 'get_orchestrator', return_value=runner):
        failed = await agents._restart_rejected_repair('one')
        assert failed['started'] is False and '可手动重新执行' in failed['reason']
        runner._running = True
        async with aiosqlite.connect(task_db) as db:
            await add_decision(db, 'v-c', 'agent-b', 'verify', {'confirmed': False, 'feedback': '第三轮仍错'})
            await db.commit()
        capped = await agents._restart_rejected_repair('one')
    assert capped['started'] is False and '连续 3 轮' in capped['reason']
    assert runner.task_queue.empty()


@pytest.mark.parametrize('action', ['confirm', 'reject', 'delete'])
@pytest.mark.parametrize('fixed', [False, True])
async def test_preview_api_and_review_cleanup(task_db, tmp_path, monkeypatch, action, fixed):
    import shlex
    from src.api import projects
    from src.core import preview_manager

    runner, agent_id = await run_verified_repair(task_db, tmp_path, monkeypatch)
    repo = tmp_path / 'source'
    monkeypatch.setattr(projects, 'get_db_path', lambda: str(task_db))
    await projects.save_project('preview-project', projects.ProjectConfig(
        name='preview', repo_path=str(repo), branch='main',
        preview_command=f'{shlex.quote(sys.executable)} -u -m http.server {{port}} --bind 127.0.0.1',
        preview_port=preview_manager._free_port() if fixed else None,
    ))
    async with aiosqlite.connect(task_db) as db:
        await db.execute("UPDATE bugs SET source_project_id='preview-project' WHERE bug_id='one'")
        await db.commit()
    with patch.object(agents, 'get_orchestrator', return_value=runner):
        result = await agents.start_agent_preview(agent_id)
        port = result['internal_port']
        try:
            async with asyncio.timeout(10):
                while (await agents.get_preview(agent_id))['status'] != 'running':
                    await asyncio.sleep(0.05)
            # 验收和删除必须主动停止预览，才能清理工作树。
            if action == 'delete':
                await agents.delete_agent(agent_id)
            else:
                subprocess.run(['git', '-C', str(repo), 'restore', 'app.py'], check=True)
                from src.core.repair_review import confirm_repair
                await confirm_repair(str(task_db), agent_id, RepairConfirmation(
                    confirmed=action == 'confirm', feedback='仍然错误' if action == 'reject' else '',
                ))
            assert (await preview_manager.preview_status(agent_id))['status'] == 'stopped'
            with pytest.raises(OSError):
                _, writer = await asyncio.open_connection('127.0.0.1', port)
                writer.close()
            if fixed:
                async with asyncio.timeout(10):
                    while (await preview_manager.project_status(str(repo)))['status'] != 'running':
                        await asyncio.sleep(0.05)
                current = await preview_manager.project_status(str(repo))
                assert current['source'] == 'project'
                async with httpx.AsyncClient() as browser:
                    assert (await browser.get(current['url'] + '/app.py')).status_code == 200
            if action != 'delete':
                with pytest.raises(HTTPException) as stale:
                    await agents.start_agent_preview(agent_id)
                assert stale.value.status_code == 409
        finally:
            await preview_manager.stop_all()


async def test_failed_yunxiao_sync_retries_without_remerging(task_db, tmp_path, monkeypatch):
    from src.tools.yunxiao.openapi_client import YunxiaoClient, YunxiaoError
    runner, agent_id = await run_verified_repair(task_db, tmp_path, monkeypatch)
    repo = tmp_path / 'source'
    subprocess.run(['git', '-C', str(repo), 'restore', 'app.py'], check=True)
    sync = AsyncMock(side_effect=YunxiaoError('Token 缺少工作项读写权限'))
    monkeypatch.setattr(YunxiaoClient, 'set_bug_in_progress', sync)
    with patch.object(agents, 'get_orchestrator', return_value=runner):
        with pytest.raises(HTTPException) as pending:
            await agents.retry_yunxiao_status_sync(agent_id)
        assert pending.value.status_code == 409
        sync.assert_not_awaited()
        result = await agents.confirm_agent_repair(agent_id, RepairConfirmation(confirmed=True))
        assert result['status'] == 'resolved' and result['yunxiao_sync']['status'] == 'failed'
        before = await agents.get_agent(agent_id)
        assert before.confirmation['user_choice']['yunxiao_sync']['error'] == 'Token 缺少工作项读写权限'
        assert before.current_bug['status'] == 'resolved' and before.worktree_path is None
        head = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD']).strip()
        # 模拟后端重启，只从数据库恢复待同步记录。
        restarted = orchestrator.MultiAgentOrchestrator()
        sync.side_effect = None
        sync.return_value = {'status_id': 'progress', 'source_status': '处理中'}
        with patch.object(agents, 'get_orchestrator', return_value=restarted), \
             patch('src.core.repair_review.merge_confirmed_repair', AsyncMock()) as merge:
            result = await agents.retry_yunxiao_status_sync(agent_id)
            assert result['status'] == 'succeeded'
            assert (await agents.retry_yunxiao_status_sync(agent_id))['status'] == 'succeeded'
        merge.assert_not_awaited()
        assert sync.await_count == 2
        assert subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD']).strip() == head
        after = await agents.get_agent(agent_id)
        assert after.current_bug['source_status'] == '处理中' and after.current_bug['status'] == 'resolved'


async def test_manual_bug_confirmation_does_not_write_yunxiao(task_db, tmp_path, monkeypatch):
    from src.tools.yunxiao.openapi_client import YunxiaoClient
    runner, agent_id = await run_verified_repair(task_db, tmp_path, monkeypatch)
    async with aiosqlite.connect(task_db) as db:
        await db.execute("UPDATE bugs SET source='manual' WHERE bug_id='one'")
        await db.commit()
    subprocess.run(['git', '-C', str(tmp_path / 'source'), 'restore', 'app.py'], check=True)
    with patch.object(agents, 'get_orchestrator', return_value=runner):
        result = await agents.confirm_agent_repair(agent_id, RepairConfirmation(confirmed=True))
    assert result['status'] == 'resolved' and result['yunxiao_sync'] is None
    YunxiaoClient.set_bug_in_progress.assert_not_awaited()
