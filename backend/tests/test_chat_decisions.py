"""对话驱动的验收与方案确认：模型判断意图，harness 复核后执行对应步骤。"""
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from src.core import agent_discussion, conversation
from src.core.repair_attempts import attempt_prompt
from src.llm.factory import LLMFactory
from src.models.agent import ConversationMessageRequest
from fake_agent import ScriptedLLM, turn
from test_conversation import chat_db  # noqa: F401


@pytest.mark.parametrize('text', ['没问题', '已修复', '可以合并', '很好，继续下一步吧', '修好了，合吧！', '没有问题了', '方案 2 可以，开始吧'])
def test_clear_approvals_pass_the_guard(text):
    assert agent_discussion.approval_blocker(text) is None


@pytest.mark.parametrize('text', ['可以合并了吗？', '可以合并了吗', '基本没问题，但是按钮有点歪', '修好了，不过另一个页面还有问题',
                                  '如果测试过了就合并', '先不合，我再看看', '还是有空白', '还有个问题', '这里有问题', '没修好'])
def test_questions_reservations_and_conditions_are_not_approval(text):
    assert agent_discussion.approval_blocker(text)


async def _awaiting(path, status, result):
    async with aiosqlite.connect(path) as db:
        await db.execute('UPDATE agents SET status=? WHERE agent_id=?', (status, 'agent'))
        await db.execute('UPDATE bugs SET status=? WHERE bug_id=?', (status, 'bug'))
        await db.execute("INSERT INTO task_history (task_id, bug_id, agent_id, stage, status, result) VALUES ('task', 'bug', 'agent', ?, ?, ?)",
                         (status, status, json.dumps(result)))
        await db.commit()


@pytest.mark.asyncio
async def test_chat_approval_executes_next_step(chat_db, monkeypatch):
    path, _ = chat_db
    await _awaiting(path, 'awaiting_confirmation', {'git_commit_hash': 'a' * 40})
    await conversation.add_user('agent', ConversationMessageRequest(text='很好，继续下一步吧', client_id='ok'))
    llm = ScriptedLLM([turn(('submit_reply', {'reply': '判断为验收通过，开始合并到修复分支并清理工作树。', 'action': 'accept', 'evidence': '很好，继续下一步吧'}))])
    monkeypatch.setattr(LLMFactory, 'create', lambda **kwargs: llm)
    execute = AsyncMock(return_value='代码已合并到 fix（abc），Agent 分支和工作树已清理')
    monkeypatch.setattr(agent_discussion, 'execute_action', execute)
    assert await agent_discussion.discuss('agent') is True
    answer = execute.await_args.args[1]
    assert answer['action'] == 'accept'
    assert execute.await_args.kwargs['user_text'] == '很好，继续下一步吧'
    texts = [m['text'] for m in await conversation.messages('agent') if m['role'] == 'assistant']
    assert texts[-2].startswith('判断为验收通过') and '已清理' in texts[-1]
    assert not await conversation.pending('agent')


@pytest.mark.asyncio
async def test_question_cannot_be_turned_into_merge(chat_db, monkeypatch):
    path, _ = chat_db
    await _awaiting(path, 'awaiting_confirmation', {'git_commit_hash': 'a' * 40})
    await conversation.add_user('agent', ConversationMessageRequest(text='可以合并了吗？', client_id='q'))
    llm = ScriptedLLM([
        turn(('submit_reply', {'reply': '开始合并。', 'action': 'accept', 'evidence': '可以合并'})),
        turn(('submit_reply', {'reply': '可以，确认没问题请直接回复“可以合并”。', 'action': 'none'})),
    ])
    monkeypatch.setattr(LLMFactory, 'create', lambda **kwargs: llm)
    execute = AsyncMock()
    monkeypatch.setattr(agent_discussion, 'execute_action', execute)
    assert await agent_discussion.discuss('agent') is True
    execute.assert_not_awaited()
    assert '问句' in json.dumps(llm.calls[1]['messages'], ensure_ascii=False)


@pytest.mark.asyncio
async def test_rework_feedback_routes_to_requested_step(chat_db, monkeypatch):
    path, _ = chat_db
    await _awaiting(path, 'awaiting_confirmation', {'git_commit_hash': 'a' * 40})
    await conversation.add_user('agent', ConversationMessageRequest(text='根因对，但这个方案改动太大，换个更小的', client_id='p'))
    llm = ScriptedLLM([turn(('submit_reply', {'reply': '理解为根因认可、方案需要更换，开始重新制定方案。', 'action': 'replan', 'feedback': '方案改动过大'}))])
    monkeypatch.setattr(LLMFactory, 'create', lambda **kwargs: llm)
    from src.core import repair_review, orchestrator
    settle = AsyncMock(return_value={'restart': {'started': True, 'round': 2}})
    monkeypatch.setattr(repair_review, 'settle_repair', settle)
    monkeypatch.setattr(orchestrator, 'get_orchestrator', lambda: SimpleNamespace())
    assert await agent_discussion.discuss('agent') is True
    request = settle.await_args.args[3]
    assert request.confirmed is False and request.rework == 'proposal'
    assert '方案改动过大' in request.feedback and '换个更小的' in request.feedback
    assert '第 2 轮' in (await conversation.messages('agent'))[-1]['text']


@pytest.mark.asyncio
async def test_plan_stage_approval_confirms_named_proposal(chat_db, monkeypatch):
    path, runner = chat_db
    await _awaiting(path, 'awaiting_plan_confirmation', {'proposals': [{'name': '方案一'}, {'name': '方案二'}]})
    await conversation.add_user('agent', ConversationMessageRequest(text='方案 2 可以，开始吧', client_id='plan'))
    llm = ScriptedLLM([turn(('submit_reply', {'reply': '按方案二开始修复。', 'action': 'approve_plan', 'proposal_index': 1, 'evidence': '方案 2 可以'}))])
    monkeypatch.setattr(LLMFactory, 'create', lambda **kwargs: llm)
    from src.core import orchestrator
    monkeypatch.setattr(orchestrator, 'get_orchestrator', lambda: runner)
    confirm = AsyncMock()
    monkeypatch.setattr(runner, 'confirm_proposal', confirm)
    assert await agent_discussion.discuss('agent') is True
    confirm.assert_awaited_once_with('agent', 1)
    assert '方案二' in (await conversation.messages('agent'))[-1]['text']


def _git(cwd, *args):
    return subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.mark.asyncio
async def test_refix_keeps_previous_changes_in_original_worktree(chat_db, tmp_path, monkeypatch):
    path, runner = chat_db
    repo = tmp_path / 'repo'
    repo.mkdir()
    _git(repo, 'init', '-q', '-b', 'repair')
    (repo / 'app.py').write_text('value = 1\n')
    _git(repo, 'add', '.')
    _git(repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'base')
    base = _git(repo, 'rev-parse', 'HEAD')
    (repo / 'app.py').write_text('value = 2\n')
    _git(repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qam', 'fix')
    commit = _git(repo, 'rev-parse', 'HEAD')
    await _awaiting(path, 'awaiting_confirmation', {'base_commit': base, 'git_commit_hash': commit, 'repair_branch': 'repair',
                                                    'root_cause': '初始值错误', 'selected_proposal': {'name': '改初始值'}})
    async with aiosqlite.connect(path) as db:
        await db.execute('UPDATE agents SET worktree_path=? WHERE agent_id=?', (str(repo), 'agent'))
        await db.execute("INSERT INTO decisions (decision_id, bug_id, agent_id, stage, question, options) VALUES ('review-1', 'bug', 'agent', 'verify', '?', '[]')")
        await db.commit()
    resume = AsyncMock()
    monkeypatch.setattr(runner, '_resume_from', resume)
    result = await runner.continue_fix('agent', '空公式时仍然多出一行')
    await runner.running_agents['agent']
    assert result['round'] == 1
    assert _git(repo, 'rev-parse', 'HEAD') == base and (repo / 'app.py').read_text() == 'value = 2\n'
    state, start = resume.await_args.args[2], resume.await_args.args[3]
    assert start == 'implement_fix' and state['root_cause'] == '初始值错误' and state['selected_proposal'] == {'name': '改初始值'}
    assert '空公式时仍然多出一行' in state['retry_context'] and state['git_commit_hash'] is None
    async with aiosqlite.connect(path) as db:
        choice = json.loads((await (await db.execute("SELECT user_choice FROM decisions WHERE decision_id='review-1'")).fetchone())[0])
        status = (await (await db.execute("SELECT status FROM agents WHERE agent_id='agent'")).fetchone())[0]
    assert choice['confirmed'] is False and choice['rework'] == 'fix' and status == 'busy'
    with pytest.raises(ValueError):
        await runner.continue_fix('agent', '再试一次')


@pytest.mark.asyncio
async def test_each_acceptance_round_gets_its_own_decision(chat_db):
    path, runner = chat_db
    await _awaiting(path, 'awaiting_confirmation', {})
    task = SimpleNamespace(task_id='task', bug_id='bug', input_snapshot={})
    runner.agent_states['agent'] = {'state': {'stage': 'awaiting_confirmation'}, 'progress': 90}
    await runner._save_status('agent', task, 'awaiting_confirmation', 'awaiting_confirmation', '等待验收')
    await runner._save_status('agent', task, 'awaiting_confirmation', 'awaiting_confirmation', '重复保存')
    async with aiosqlite.connect(path) as db:
        await db.execute("UPDATE decisions SET status='answered' WHERE stage='verify'")
        await db.commit()
    await runner._save_status('agent', task, 'awaiting_confirmation', 'awaiting_confirmation', '第二轮验收')
    async with aiosqlite.connect(path) as db:
        rows = await (await db.execute("SELECT status FROM decisions WHERE stage='verify' ORDER BY id")).fetchall()
    assert [row[0] for row in rows] == ['answered', 'pending']


def test_next_round_prompt_follows_requested_step():
    attempt = {'round': 1, 'feedback': '方案太重', 'modified_files': []}
    assert '认可上一轮根因' in attempt_prompt({**attempt, 'rework': 'proposal'})
    assert '不得沿用上一轮根因' in attempt_prompt({**attempt, 'rework': 'analysis'})
    assert '不得原样重复上一轮方案' in attempt_prompt(attempt)
