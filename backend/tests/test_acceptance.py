import copy
import sys
from pathlib import Path

import aiosqlite
import pytest

from src.core.acceptance import acceptance_plan, evidence_report, validate_criterion_reviews
from src.core.agent_tools import ToolError
from src.core.attention import attention_item
from src.models.agent import RepairConfirmation


def saved_evidence():
    symptom = {'acceptance': ['空内容保存不报错', '窄屏下按钮可见']}
    repro = {'reproduced': True, 'command': 'pytest test_save.py', 'test_files': ['test_save.py'], 'exit_before': 1, 'output_before': 'save failed'}
    plan = acceptance_plan(symptom, [
        {'criterion_index': 0, 'method': 'reproduction', 'detail': '调用真实保存入口并断言成功', 'test_files': ['test_save.py']},
        {'criterion_index': 1, 'method': 'manual', 'detail': '将窗口缩至 375px，确认保存按钮完整显示', 'test_files': []},
    ], repro)
    return {'analysis': {'symptom': symptom, 'acceptance_plan': plan}, 'repro': repro, 'git_commit_hash': 'a' * 40,
            'test_results': {'passed': True, 'acceptance_version': plan['version'], 'verification': {'git_commit_hash': 'a' * 40},
                             'checks': [{'id': 'project', 'kind': 'project', 'command': 'build', 'passed': True},
                                        {'id': 'reproduction', 'kind': 'reproduction', 'command': repro['command'], 'passed': True, 'exit_code': 0}]}}


def test_evidence_keeps_model_coverage_separate_from_system_execution_and_manual_review():
    saved = saved_evidence()
    report = evidence_report(saved)
    assert report['current'] is True
    assert report['criteria'][0]['automatic_status'] == 'passed'
    assert report['criteria'][0]['evidence_ids'] == ['reproduction']
    assert report['criteria'][1]['automatic_status'] == 'manual'
    assert report['criteria'][1]['evidence_ids'] == []
    # 任意绿色项目构建不能替代缺失的复现检查。
    saved['test_results']['checks'].pop()
    assert evidence_report(saved)['criteria'][0]['automatic_status'] == 'not_run'


@pytest.mark.parametrize('change', ['commit', 'requirements', 'command'])
def test_changed_versions_or_commands_cannot_reuse_green_evidence(change):
    saved = saved_evidence()
    if change == 'commit':
        saved['git_commit_hash'] = 'b' * 40
    elif change == 'requirements':
        saved['analysis']['acceptance_plan']['version'] = 'different'
    else:
        saved['repro']['command'] = 'another-test'
    assert evidence_report(saved)['criteria'][0]['automatic_status'] != 'passed'


def test_legacy_green_build_does_not_invent_item_evidence():
    saved = {'analysis': {'symptom': {'acceptance': ['保存不报错']}}, 'test_results': {'passed': True, 'command': 'build', 'exit_code': 0}}
    report = evidence_report(saved)
    assert report['legacy'] is True
    assert report['criteria'][0]['automatic_status'] == 'manual'
    assert report['checks'][0]['command'] == 'build'


def test_coverage_mapping_rejects_unrun_files_duplicate_and_missing_items():
    saved = saved_evidence()
    symptom, repro = saved['analysis']['symptom'], saved['repro']
    checks = [{'criterion_index': 0, 'method': 'reproduction', 'detail': '覆盖', 'test_files': ['not-run.py']}]
    with pytest.raises(ToolError, match='复现'):
        acceptance_plan(symptom, checks, repro)
    checks[0]['test_files'] = ['test_save.py']
    with pytest.raises(ToolError, match='每条'):
        acceptance_plan(symptom, checks, repro)
    with pytest.raises(ToolError, match='重复'):
        acceptance_plan(symptom, checks * 2, repro)


def test_item_review_rejects_incomplete_failed_and_stale_acceptance_but_keeps_old_clients():
    saved = saved_evidence()
    plan = saved['analysis']['acceptance_plan']
    request = {'confirmed': True, 'git_commit_hash': saved['git_commit_hash'], 'acceptance_version': plan['version'],
               'acceptance_results': [{'criterion_id': item['id'], 'status': 'passed'} for item in plan['criteria']]}
    assert len(validate_criterion_reviews(saved, RepairConfirmation(**request))['criteria']) == 2
    for mutation in ['missing', 'failed', 'version', 'unknown', 'duplicate']:
        changed = copy.deepcopy(request)
        if mutation == 'missing': changed['acceptance_results'].pop()
        if mutation == 'failed': changed['acceptance_results'][0]['status'] = 'failed'
        if mutation == 'version': changed['acceptance_version'] = 'old'
        if mutation == 'unknown': changed['acceptance_results'][0]['criterion_id'] = 'other'
        if mutation == 'duplicate': changed['acceptance_results'][1] = changed['acceptance_results'][0]
        with pytest.raises(ValueError):
            validate_criterion_reviews(saved, RepairConfirmation(**changed))
    assert validate_criterion_reviews(saved, RepairConfirmation(confirmed=True)) is None


def test_attention_requires_current_ownership_and_pending_decision():
    agent = {'agent_id': 'one', 'status': 'busy', 'updated_at': '2026-11-11 01:00:00'}
    bug = {'assigned_agent_id': 'one'}
    pending = [{'decision_id': 'q', 'stage': 'question', 'question': '应该保留历史数据吗？', 'status': 'pending', 'created_at': '2026-11-11 00:00:00'}]
    assert attention_item(agent, bug, {}, pending, None)['kind'] == 'question'
    assert attention_item(agent, bug, {}, [], None) is None
    assert attention_item(agent, {'assigned_agent_id': 'new'}, {}, pending, None) is None
    agent['status'] = 'error'
    assert attention_item(agent, bug, {'error': '超时'}, pending, None)['kind'] == 'error'


@pytest.mark.asyncio
async def test_system_records_actual_check_failures_and_commit_binding(tmp_path):
    from src.core.repair_executor import git, run_process, verify_and_commit_repair, verify_worktree
    await run_process(['git', 'init', '-b', 'main'], tmp_path)
    (tmp_path / 'app.py').write_text('value = 1\n')
    await git(tmp_path, 'add', '.')
    await git(tmp_path, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-m', 'base')
    base = await git(tmp_path, 'rev-parse', 'HEAD')
    (tmp_path / 'app.py').write_text('value = 2\n')
    success = f'{sys.executable} -c "print(\'project passed\')"'
    failure = f'{sys.executable} -c "raise SystemExit(7)"'
    failed = await verify_worktree(tmp_path, success, failure)
    assert failed['passed'] is False
    assert failed['checks'][-1]['exit_code'] == 7
    result = await verify_and_commit_repair(tmp_path, success, 'one', 'main', base, repro_command=success)
    assert result['test_results']['verification']['git_commit_hash'] == result['git_commit_hash']
    assert result['test_results']['checks'][-1]['output'].strip() == 'project passed'


@pytest.mark.asyncio
async def test_question_records_close_on_answer_and_timeout(tmp_path, monkeypatch):
    from src.core import conversation
    path = tmp_path / 'questions.db'
    async with aiosqlite.connect(path) as db:
        await db.executescript(Path('src/db/init.sql').read_text())
        await db.execute("INSERT INTO bugs (bug_id, title, priority, source) VALUES ('one', 'bug', 'normal', 'manual')")
        await db.execute("INSERT INTO agents (agent_id, name, current_bug_id) VALUES ('one', 'agent', 'one')")
        await db.commit()
    monkeypatch.setattr(conversation, 'db_path', lambda: str(path))
    answered = await conversation.begin_question('one', 'question')
    await conversation.finish_question(answered, answered=True)
    stopped = await conversation.begin_question('one', 'another')
    await conversation.finish_question(stopped)
    async with aiosqlite.connect(path) as db:
        assert await (await db.execute('SELECT status FROM decisions ORDER BY id')).fetchall() == [('answered',), ('timeout',)]
