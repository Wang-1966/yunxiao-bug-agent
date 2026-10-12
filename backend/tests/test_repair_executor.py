import asyncio
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.core import repair_executor as executor


@pytest.fixture
def repository(tmp_path, monkeypatch):
    path = tmp_path / 'repo'
    path.mkdir()
    subprocess.run(['git', 'init', '-b', 'main', str(path)], check=True, capture_output=True)
    (path / 'app.py').write_text('value = 1\nassert value == 2\n')
    (path / 'AGENTS.md').write_text('Keep the public interface compatible.')
    subprocess.run(['git', '-C', str(path), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(path), '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-m', 'initial'], check=True, capture_output=True)
    monkeypatch.setattr(executor.get_settings(), 'worktrees_dir', str(tmp_path / 'worktrees'))
    return path


@pytest.mark.asyncio
async def test_isolated_worktree_preserves_source_dirty_files_and_retries(repository):
    (repository / 'app.py').write_text('uncommitted user work\n')
    first = await executor.prepare_worktree({'repo_path': str(repository), 'branch': 'main'})
    second = await executor.prepare_worktree({'repo_path': str(repository), 'branch': 'main'})
    assert first['worktree_path'] != second['worktree_path']
    files = executor.RepositoryFiles(first['worktree_path'])
    await files.initialize()
    files.read('app.py')
    files.edit([{'path': 'app.py', 'before': 'value = 1', 'after': 'value = 2'}])
    assert (repository / 'app.py').read_text() == 'uncommitted user work\n'
    assert (Path(second['worktree_path']) / 'app.py').read_text().startswith('value = 1')
    result = await executor.verify_worktree(first['worktree_path'], f'{sys.executable} app.py')
    assert result['passed'] is True
    assert await executor.changed_files(first['worktree_path']) == ['app.py']


@pytest.mark.asyncio
async def test_verify_and_commit_repair_commits_only_verified_tree(repository):
    prepared = await executor.prepare_worktree({'repo_path': str(repository), 'branch': 'main'})
    worktree = Path(prepared['worktree_path'])
    files = executor.RepositoryFiles(worktree)
    await files.initialize()
    files.read('app.py')
    files.edit([{'path': 'app.py', 'before': 'value = 1', 'after': 'value = 2'}])
    result = await executor.verify_and_commit_repair(worktree, f'{sys.executable} -c "assert open(\'app.py\').read().startswith(\'value = 2\')"', 'one', prepared['repair_branch'], prepared['base_commit'])
    assert result['git_commit_hash'] == await executor.git(worktree, 'rev-parse', 'HEAD')
    assert await executor.git(worktree, 'status', '--porcelain') == ''
    assert await executor.git(worktree, 'diff', '--name-only', prepared['base_commit'], result['git_commit_hash']) == 'app.py'


@pytest.mark.asyncio
async def test_verification_mutating_source_cannot_be_committed(repository):
    prepared = await executor.prepare_worktree({'repo_path': str(repository)})
    worktree = Path(prepared['worktree_path'])
    (worktree / 'app.py').write_text('value = 2\n')
    command = f'''{sys.executable} -c "open('app.py', 'w').write('value = 3\\n')"'''
    with pytest.raises(ValueError, match='验证期间'):
        await executor.verify_and_commit_repair(worktree, command, 'one', prepared['repair_branch'], prepared['base_commit'])
    assert await executor.git(worktree, 'rev-parse', 'HEAD') == prepared['base_commit']


@pytest.mark.asyncio
async def test_file_boundaries_and_invalid_batch_cannot_write(repository, tmp_path):
    worktree = (await executor.prepare_worktree({'repo_path': str(repository)}))['worktree_path']
    files = executor.RepositoryFiles(worktree)
    await files.initialize()
    for name in ['../outside', '/tmp/outside', '.git/config', '.env', '.env.prod', 'node_modules/a']:
        with pytest.raises(ValueError):
            files.path(name)
    (Path(worktree) / 'link').symlink_to(tmp_path)
    with pytest.raises(ValueError):
        files.path('link/outside')
    with pytest.raises(ValueError, match='读取'):
        files.edit([{'path': 'app.py', 'before': 'value = 1', 'after': 'value = 2'}])
    files.read('app.py')
    with pytest.raises(ValueError, match='精确匹配'):
        files.edit([{'path': 'app.py', 'before': 'value = 1', 'after': 'value = 2'}, {'path': 'app.py', 'before': 'missing', 'after': 'new'}])
    assert (Path(worktree) / 'app.py').read_text().startswith('value = 1')


@pytest.mark.asyncio
async def test_failure_and_missing_command_never_pass(repository):
    worktree = (await executor.prepare_worktree({'repo_path': str(repository)}))['worktree_path']
    result = await executor.verify_worktree(worktree, f'{sys.executable} app.py')
    assert result['passed'] is False
    assert result['exit_code'] != 0
    with pytest.raises(ValueError, match='未配置验证命令'):
        await executor.verify_worktree(worktree, '')


@pytest.mark.asyncio
async def test_cancel_terminates_subprocess(repository, tmp_path):
    marker = tmp_path / 'late-write'
    command = f"import time; from pathlib import Path; time.sleep(1); Path({str(marker)!r}).write_text('bad')"
    task = asyncio.create_task(executor.run_process([sys.executable, '-c', command], repository))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(1.1)
    assert not marker.exists()


@pytest.mark.asyncio
async def test_real_graph_reads_applies_and_runs_verification(repository):
    from src.agent.graph import create_bug_fix_graph, compile_graph
    from src.agent.state import BugFixState

    client = AsyncMock()
    client.ainvoke.side_effect = [
        json.dumps({'images': [{'image': 1, 'controls': ['value'], 'visible_text': ['1'], 'observed_behavior': 'value is one', 'uncertainties': []}]}),
        json.dumps({'action': 'read', 'path': 'app.py'}),
        json.dumps({'action': 'finish', 'result': {'root_cause': 'app.py value assignment wrong', 'confidence': 'high', 'need_logging': False}}),
        json.dumps({'proposals': [{'name': 'correct assignment', 'steps': ['update value']}]}),
        json.dumps({'action': 'read', 'path': 'app.py'}),
        json.dumps({'action': 'edit', 'edits': [{'path': 'app.py', 'before': 'value = 1', 'after': 'value = 2'}]}),
        json.dumps({'action': 'finish', 'result': {'summary': 'corrected assignment'}}),
    ]
    initial: BugFixState = {'bug_id': 'one', 'error': None, 'bug_info': {}}
    details = {'title': 'Incorrect value', 'description': 'value should be two <img src="https://cdn.example/repro.png">', 'repo_path': str(repository), 'branch': 'main', 'test_command': f'{sys.executable} app.py'}
    with patch('src.api.bugs.get_bug_details', AsyncMock(return_value=details)), patch('src.agent.nodes.LLMFactory.create', return_value=client):
        result = await compile_graph(create_bug_fix_graph()).ainvoke(initial)
    assert result['error'] is None
    assert result['test_results']['passed'] is True
    assert result['modified_files'] == ['app.py']
    assert (Path(result['worktree_path']) / 'app.py').read_text().startswith('value = 2')
    assert (repository / 'app.py').read_text().startswith('value = 1')
    assert result['fix_summary'] == 'corrected assignment'
    content = client.ainvoke.await_args_list[0].args[0][1]['content']
    assert content[1] == {'type': 'image_url', 'image_url': {'url': 'https://cdn.example/repro.png'}}
    assert result['visual_context']['observations'][0]['observed_behavior'] == 'value is one'
    for call in client.ainvoke.await_args_list[1:]:
        assert all(isinstance(message['content'], str) for message in call.args[0])
        assert any('value is one' in message['content'] for message in call.args[0])


@pytest.mark.asyncio
async def test_image_reread_is_one_request_and_retains_observation(repository):
    client = AsyncMock()
    client.ainvoke.side_effect = [
        json.dumps({'action': 'view_image', 'image': 0}),
        json.dumps({'action': 'view_image', 'image': 1}),
        json.dumps({'action': 'read', 'path': 'app.py', 'image_observation': 'The screenshot shows value 1'}),
        json.dumps({'action': 'view_image', 'image': 1}),
        json.dumps({'action': 'read', 'path': 'AGENTS.md', 'image_observation': 'Confirmed value 1'}),
        json.dumps({'action': 'finish', 'result': {'root_cause': 'wrong value'}}),
    ]
    await executor.repository_agent(client, str(repository), 'analyze', image_urls=['https://cdn.example/repro.png'], images_on_demand=True)
    calls = client.ainvoke.await_args_list
    assert [index for index, call in enumerate(calls) if any(isinstance(m['content'], list) for m in call.args[0])] == [2, 4]
    assert any('The screenshot shows value 1' in m['content'] for m in calls[-1].args[0])
    assert any('图片编号无效' in m['content'] for m in calls[1].args[0])


@pytest.mark.asyncio
async def test_visual_context_reused_after_serialization_and_invalidated_by_description():
    from src.agent.nodes import ensure_visual_context
    from src.core.bug_visual import describe_bug_images
    bug = {'title': 'bug', 'description': '<img src="https://cdn.example/a.png">', 'agent_images': ['https://cdn.example/a.png']}
    client = AsyncMock()
    client.ainvoke.return_value = json.dumps({'images': [{'image': 1, 'controls': [], 'visible_text': [], 'observed_behavior': 'selected control', 'uncertainties': ['interaction unknown']}]})
    context = await describe_bug_images(client, bug)
    state = {'bug_info': bug, 'visual_context': json.loads(json.dumps(context))}
    assert await ensure_visual_context(state, client) == context
    assert client.ainvoke.await_count == 1
    assert 'images' not in context
    bug['description'] += 'updated reproduction'
    assert (await ensure_visual_context(state, client))['source'] != context['source']
    assert client.ainvoke.await_count == 2
    client.ainvoke.return_value = '{"images": []}'
    with pytest.raises(ValueError, match='每张图片'):
        await describe_bug_images(client, bug)


@pytest.mark.asyncio
async def test_reread_requires_observation_before_applying_edit(repository):
    client = AsyncMock()
    client.ainvoke.side_effect = [
        json.dumps({'action': 'read', 'path': 'app.py'}),
        json.dumps({'action': 'view_image', 'image': 1}),
        json.dumps({'action': 'edit', 'edits': [{'path': 'app.py', 'before': 'value = 1', 'after': 'value = 2'}]}),
        json.dumps({'action': 'finish', 'result': {'blocked_reason': 'cannot confirm screenshot'}}),
    ]
    with pytest.raises(ValueError, match='cannot confirm screenshot'):
        await executor.repository_agent(client, str(repository), 'fix', writable=True, image_urls=['https://cdn.example/repro.png'], images_on_demand=True)
    assert (repository / 'app.py').read_text().startswith('value = 1')
    assert any('看图响应必须包含 image_observation' in message['content'] for message in client.ainvoke.await_args_list[-1].args[0])


@pytest.mark.asyncio
async def test_graph_stops_before_analysis_when_repository_is_missing():
    from src.agent.graph import create_bug_fix_graph, compile_graph

    with patch('src.api.bugs.get_bug_details', AsyncMock(return_value={'title': 'bug', 'description': ''})), patch('src.agent.nodes.LLMFactory.create') as factory:
        result = await compile_graph(create_bug_fix_graph()).ainvoke({'bug_id': 'one', 'error': None})
    assert '未配置修复仓库' in result['error']
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_model_finish_without_edits_is_failure(repository):
    from src.agent.nodes import implement_fix_node

    worktree = (await executor.prepare_worktree({'repo_path': str(repository)}))['worktree_path']
    client = AsyncMock()
    client.ainvoke.side_effect = [json.dumps({'action': 'read', 'path': 'app.py'})] + [json.dumps({'action': 'finish', 'result': {'summary': 'done'}})] * executor.MAX_CONSECUTIVE_ERRORS
    state = {'bug_id': 'one', 'worktree_path': worktree, 'bug_info': {'title': 'bug'}, 'root_cause': 'wrong value', 'proposals': [{'name': 'fix'}]}
    with patch('src.agent.nodes.LLMFactory.create', return_value=client):
        result = await implement_fix_node(state)
    assert '没有产生实际代码修改' in result['error']


@pytest.mark.asyncio
async def test_scoped_search_supports_alternatives_and_literal_mode(repository):
    (repository / 'business').mkdir()
    (repository / 'other').mkdir()
    (repository / 'business' / 'input.vue').write_text('questionCode\n题目标识\nA|B\n')
    (repository / 'other' / 'noise.vue').write_text('questionCode\n')
    subprocess.run(['git', '-C', str(repository), 'add', '.'], check=True)
    files = executor.RepositoryFiles(repository)
    await files.initialize()
    result = await files.search('questionCode|题目标识', 'business')
    assert 'business/input.vue:1:' in result
    assert 'business/input.vue:2:' in result
    assert 'other/noise.vue' not in result
    assert 'business/input.vue:3:' in await files.search('A|B', 'business/input.vue', regex=False)
    with pytest.raises(ValueError, match='表达式无效'):
        await files.search('[', 'business')
    with pytest.raises(ValueError, match='没有可用文件'):
        await files.search('questionCode', 'missing')
    assert 'other/noise.vue' not in files.list_files('business')


@pytest.mark.asyncio
async def test_repeated_operations_get_feedback_and_do_not_repeat_search(repository):
    client = AsyncMock()
    client.ainvoke.side_effect = [
        json.dumps({'action': 'search', 'query': 'value', 'path': 'app.py'}),
        json.dumps({'action': 'search', 'query': 'value', 'path': 'app.py'}),
        json.dumps({'action': 'read', 'path': 'app.py'}),
        json.dumps({'action': 'finish', 'result': {'root_cause': 'value incorrect'}}),
    ]
    log = AsyncMock()
    result = await executor.repository_agent(client, str(repository), 'analyze value', on_operation=log)
    assert result['root_cause'] == 'value incorrect'
    assert log.await_count == 3
    assert client.ainvoke.await_args_list[0].kwargs['response_format'] == {'type': 'json_object'}
    # 第二次搜索被拒绝，模型收到具体的收敛指导，而不是重复的无关匹配。
    assert any('此操作已经执行' in message['content'] for message in client.ainvoke.await_args_list[2].args[0])


@pytest.mark.asyncio
async def test_timeout_is_an_explicit_failure_before_proposal(repository):
    from src.agent.nodes import analyze_root_cause_node
    client = AsyncMock()
    client.ainvoke.side_effect = asyncio.TimeoutError()
    state = {'bug_id': 'one', 'worktree_path': str(repository), 'bug_info': {'id': 'one', 'title': 'bug', 'description': ''}}
    with patch('src.agent.nodes.LLMFactory.create', return_value=client):
        result = await analyze_root_cause_node(state)
    assert '模型请求超过 120 秒' in result['error']
    from src.agent.graph import should_add_logging
    from langgraph.graph import END
    assert should_add_logging(result) == END


@pytest.mark.asyncio
async def test_protocol_errors_do_not_spend_source_budget(repository, monkeypatch):
    monkeypatch.setattr(executor, 'MAX_SOURCE_ROUNDS', 2)
    client = AsyncMock()
    client.ainvoke.side_effect = [
        '{"action":"read","path":"app.py"}{"action":"read","path":"AGENTS.md"}',
        json.dumps({'action': 'read', 'path': 'app.py'}),
        json.dumps({'action': 'edit', 'edits': [{'path': 'app.py', 'before': 'value = 1', 'after': 'value = 2'}]}),
        json.dumps({'action': 'finish', 'result': {'summary': 'corrected value'}}),
    ]
    result = await executor.repository_agent(client, repository, 'fix value', writable=True)
    assert result['summary'] == 'corrected value'
    assert (repository / 'app.py').read_text().startswith('value = 2')
    assert any('拼接' in m['content'] and 'batch' in m['content'] for m in client.ainvoke.await_args_list[1].args[0])


@pytest.mark.asyncio
async def test_repeated_invalid_responses_stop_with_specific_reason(repository):
    client = AsyncMock(return_value='not JSON')
    client.ainvoke.return_value = 'not JSON'
    log = AsyncMock()
    with pytest.raises(ValueError, match='连续.*失败.*JSON'):
        await executor.repository_agent(client, repository, 'analyze', on_operation=log)
    assert client.ainvoke.await_count == executor.MAX_CONSECUTIVE_ERRORS
    diagnostics = [call.args[0] for call in log.await_args_list if call.args[0].startswith('模型响应解析失败')]
    assert len(diagnostics) == executor.MAX_CONSECUTIVE_ERRORS
    assert 'not JSON' in diagnostics[0] and 'response_sha256' in diagnostics[0]
    assert '/analyze/1' in diagnostics[0] and '/analyze/3' in diagnostics[-1]


@pytest.mark.asyncio
async def test_read_only_batch_and_context_restore_read_latest_source(repository):
    client = AsyncMock()
    client.ainvoke.side_effect = [
        json.dumps({'action': 'batch', 'operations': [{'action': 'read', 'path': 'app.py'}, {'action': 'search', 'query': 'value', 'path': 'app.py'}]}),
        json.dumps({'action': 'finish', 'result': {'root_cause': 'incorrect assignment'}}),
    ]
    analysis = await executor.repository_agent(client, repository, 'analyze')
    context = json.loads(json.dumps(analysis['source_context']))
    assert 'value = 1' not in json.dumps(context)
    (repository / 'app.py').write_text('value = 3\nassert value == 2\n')
    fix = AsyncMock()
    fix.ainvoke.side_effect = [
        json.dumps({'action': 'edit', 'edits': [{'path': 'app.py', 'before': 'value = 3', 'after': 'value = 2'}]}),
        json.dumps({'action': 'finish', 'result': {'summary': 'corrected latest value'}}),
    ]
    await executor.repository_agent(fix, repository, 'fix', writable=True, source_context=context)
    assert 'value = 3' in fix.ainvoke.await_args_list[0].args[0][1]['content']
    assert 'value = 1' not in fix.ainvoke.await_args_list[0].args[0][1]['content']
    assert (repository / 'app.py').read_text().startswith('value = 2')


@pytest.mark.asyncio
async def test_batch_cannot_hide_write_or_finish(repository):
    client = AsyncMock()
    client.ainvoke.side_effect = [
        json.dumps({'action': 'batch', 'operations': [{'action': 'read', 'path': 'app.py'}, {'action': 'edit', 'edits': [{'path': 'app.py', 'before': 'value = 1', 'after': 'value = 2'}]}]}),
        json.dumps({'action': 'read', 'path': 'app.py'}),
        json.dumps({'action': 'finish', 'result': {'root_cause': 'incorrect assignment'}}),
    ]
    await executor.repository_agent(client, repository, 'analyze')
    assert (repository / 'app.py').read_text().startswith('value = 1')
    assert any('只读' in m['content'] for m in client.ainvoke.await_args_list[1].args[0])


@pytest.mark.asyncio
async def test_exhausted_budget_preserves_edits_but_never_reports_success(repository, monkeypatch):
    monkeypatch.setattr(executor, 'MAX_SOURCE_ROUNDS', 2)
    client = AsyncMock()
    client.ainvoke.side_effect = [
        json.dumps({'action': 'read', 'path': 'app.py'}),
        json.dumps({'action': 'edit', 'edits': [{'path': 'app.py', 'before': 'value = 1', 'after': 'value = 2'}]}),
        json.dumps({'action': 'search', 'query': 'value', 'path': 'app.py'}),
    ]
    with pytest.raises(ValueError, match='上限.*保留'):
        await executor.repository_agent(client, repository, 'fix', writable=True)
    assert (repository / 'app.py').read_text().startswith('value = 2')
    assert any('仅允许 finish' in m['content'] for m in client.ainvoke.await_args_list[-1].args[0])


@pytest.mark.asyncio
async def test_proposal_error_does_not_enter_fix_even_in_legacy_graph(repository):
    from src.agent.graph import create_bug_fix_graph, compile_graph
    client = AsyncMock()
    client.ainvoke.side_effect = [
        json.dumps({'action': 'read', 'path': 'app.py'}),
        json.dumps({'action': 'finish', 'result': {'root_cause': 'incorrect value', 'confidence': 'high'}}),
        '{"proposals":[]}',
    ]
    with patch('src.agent.nodes.LLMFactory.create', return_value=client), patch('src.agent.graph.implement_fix_node', AsyncMock()) as fix:
        result = await compile_graph(create_bug_fix_graph(start_node='analyze_root_cause')).ainvoke({
            'bug_id': 'one', 'bug_info': {'title': 'value'}, 'worktree_path': str(repository), 'error': None,
        })
    assert '修复方案' in result['error']
    fix.assert_not_awaited()


@pytest.mark.asyncio
async def test_early_finish_gets_feedback_then_real_edit(repository):
    client = AsyncMock()
    client.ainvoke.side_effect = [
        json.dumps({'action': 'read', 'path': 'app.py'}),
        json.dumps({'action': 'finish', 'result': {'summary': 'done'}}),
        json.dumps({'action': 'edit', 'edits': [{'path': 'app.py', 'before': 'value = 1', 'after': 'value = 2'}]}),
        json.dumps({'action': 'finish', 'result': {'summary': 'actually done'}}),
    ]
    result = await executor.repository_agent(client, repository, 'fix', writable=True)
    assert result['summary'] == 'actually done'
    assert any('请先edit' in m['content'] for m in client.ainvoke.await_args_list[2].args[0])


@pytest.mark.asyncio
async def test_cumulative_protocol_errors_are_bounded(repository):
    client = AsyncMock()
    client.ainvoke.side_effect = [response for index in range(executor.MAX_OPERATION_ERRORS) for response in (
        json.dumps({'action': 'read', 'path': 'app.py', 'count': index + 1}), 'invalid JSON',
    )]
    with pytest.raises(ValueError, match=f'累计 {executor.MAX_OPERATION_ERRORS} 次操作失败'):
        await executor.repository_agent(client, repository, 'analyze')
    assert client.ainvoke.await_count == 2 * executor.MAX_OPERATION_ERRORS


@pytest.mark.asyncio
async def test_invalid_read_never_grants_edit_permission(repository):
    files = executor.RepositoryFiles(repository)
    with pytest.raises(ValueError):
        files.read('app.py', count=301)
    with pytest.raises(ValueError, match='读取'):
        files.edit([{'path': 'app.py', 'before': 'value = 1', 'after': 'value = 2'}])


@pytest.mark.asyncio
async def test_stage_deadline_cancels_slow_model(repository, monkeypatch):
    monkeypatch.setattr(executor.get_settings(), 'agent_timeout', 0.1)
    cancelled = asyncio.Event()

    async def slow(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    client = AsyncMock()
    client.ainvoke.side_effect = slow
    with pytest.raises(ValueError, match='总执行时限'):
        await executor.repository_agent(client, repository, 'analyze')
    assert cancelled.is_set()
