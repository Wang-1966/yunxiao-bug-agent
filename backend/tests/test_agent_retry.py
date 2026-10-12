import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest
from fastapi import HTTPException

from src.api import agents
from src.core import orchestrator
from src.core.repair_executor import prepare_worktree


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


def patched_workflow(client, operate):
    return (
        patch('src.api.bugs.get_bug_details', AsyncMock(return_value={'title': 'Bug', 'description': '描述'})),
        patch('src.core.orchestrator.resolve_project_config', AsyncMock(return_value={'repo_path': '/repo', 'branch': 'main', 'test_command': 'true'})),
        patch('src.agent.nodes.LLMFactory.create', return_value=client),
        patch('src.agent.nodes.repository_agent', side_effect=operate),
    )


@pytest.mark.asyncio
async def test_proposal_failure_resumes_same_agent_without_reanalysis(task_db, worktree):
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    agent_id = await fail_task(runner, {**worktree, 'root_cause': '已确认根因', 'confidence': 'high'}, '模型未返回可执行的修复方案', 'design_proposal')
    client = AsyncMock()
    client.ainvoke.return_value = json.dumps({'proposals': [{'name': '新方案'}]})
    operate = AsyncMock(side_effect=AssertionError('不应重新分析源码'))
    p1, p2, p3, p4 = patched_workflow(client, operate)
    with p1, p2, p3, p4, patch.object(agents, 'get_orchestrator', return_value=runner):
        result = await agents.retry_agent(agent_id)
        assert result['mode'] == 'resume' and result['agent_id'] == agent_id and result['start_node'] == 'design_proposal'
        await run_queued(runner)
        statuses = await agents.list_agents()
    assert len(statuses) == 1
    assert statuses[0].status == 'awaiting_plan_confirmation'
    assert statuses[0].root_cause == '已确认根因'
    assert statuses[0].worktree_path == worktree['worktree_path']
    prompt = client.ainvoke.call_args.args[0][0]['content']
    assert '已确认根因' in prompt and '模型未返回可执行的修复方案' in prompt


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
        prompts.append(prompt)
        source.write_text('value = 2\n')
        return {'summary': '修正'}

    p1, p2, p3, p4 = patched_workflow(AsyncMock(), operate)
    with p1, p2, p3, p4:
        result = await runner.retry_agent(agent_id)
        assert result['start_node'] == 'implement_fix'
        await run_queued(runner)
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
    assert result['mode'] == 'resume' and result['start_node'] == 'design_proposal'
    async with aiosqlite.connect(task_db) as db:
        assert await (await db.execute("SELECT status, assigned_agent_id FROM bugs WHERE bug_id='one'")).fetchone() == ('analyzing', old_agent)


def test_graph_can_start_from_each_resume_point():
    from src.agent.graph import create_bug_fix_graph, compile_graph
    for node in orchestrator.RESUME_POINTS:
        assert compile_graph(create_bug_fix_graph(node)) is not None
    with pytest.raises(ValueError):
        create_bug_fix_graph('add_logging')
