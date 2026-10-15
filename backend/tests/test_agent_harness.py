"""原生工具循环：编辑容错、并行只读、复现必须失败、复现测试受保护、审查可打回。"""
import subprocess
import sys
from pathlib import Path

import pytest

from fake_agent import REVIEW_OK, ScriptedLLM, analysis_args, review_refuted, turn
from src.core.agent_tools import ToolError, Workspace, run_in_worktree, sandbox_available
from src.core.repair_agent import run_analysis, run_fix
from src.core.tool_agent import run_tool_agent


@pytest.fixture
def repository(tmp_path, monkeypatch):
    path = tmp_path / 'repo'
    path.mkdir()
    subprocess.run(['git', 'init', '-b', 'main', str(path)], check=True, capture_output=True)
    (path / 'app.py').write_text('label = "确认还原"\nvalue = 1\n')
    subprocess.run(['git', '-C', str(path), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(path), '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-m', 'initial'], check=True, capture_output=True)
    from src.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, 'worktrees_dir', str(tmp_path / 'worktrees'))
    monkeypatch.setattr(settings, 'database_url', f'sqlite+aiosqlite:///{tmp_path / "tasks.db"}')
    return path


def test_edit_matches_confusable_quotes_and_reports_the_nearest_lines(repository):
    files = Workspace(repository, mode='fix')
    files.read_file('app.py')
    files.edit_file('app.py', 'label = “确认还原”', 'label = "确认恢复"')
    assert '确认恢复' in (repository / 'app.py').read_text()
    with pytest.raises(ToolError, match='最接近的原文') as exc:
        files.edit_file('app.py', 'label = "不存在"', 'label = "x"')
    assert '确认恢复' in str(exc.value)
    files.edit_file('app.py', 'value = 1', 'value = 2', replace_all=False)
    assert (repository / 'app.py').read_text().count('value = 2') == 1


@pytest.mark.asyncio
async def test_parallel_reads_run_and_idle_turns_stop(repository):
    llm = ScriptedLLM([
        turn(('read_file', {'path': 'app.py'}), ('grep', {'pattern': 'value', 'path': 'app.py'})),
        turn(content='我先想想'),
        turn(content='还在想'),
        turn(content='仍然没有工具'),
    ])
    from src.core.agent_tools import build_tools
    tools = build_tools(Workspace(repository, mode='read'), commands=False) + []
    from src.core.tool_agent import submit_tool

    async def accept(args):
        return args

    tools.append(submit_tool('submit_fix', '提交', {'type': 'object', 'properties': {'summary': {'type': 'string'}}, 'required': ['summary']}, accept))
    with pytest.raises(ValueError, match='连续 3 次没有调用工具'):
        await run_tool_agent(llm, [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'task'}], tools, max_turns=6)
    second = llm.calls[1]['messages']
    tool_messages = [message['content'] for message in second if message['role'] == 'tool']
    assert len(tool_messages) == 2
    assert any('value = 1' in content for content in tool_messages)
    assert any('app.py:2' in content or 'app.py' in content for content in tool_messages)


@pytest.mark.asyncio
async def test_analysis_requires_a_failing_reproduction_and_keeps_source_clean(repository):
    passing = f'{sys.executable} -c "print(1)"'
    failing = f'{sys.executable} -c "raise SystemExit(\'toolbar missing\')"'
    llm = ScriptedLLM([
        turn(('edit_file', {'path': 'app.py', 'old_string': 'value = 1', 'new_string': 'value = 2'})),
        turn(('read_file', {'path': 'app.py'})),
        turn(('edit_file', {'path': 'app.py', 'old_string': 'value = 1', 'new_string': 'value = 2'})),
        turn(('edit_file', {'path': 'test_repro.py', 'old_string': '', 'new_string': 'def test_bug():\n    assert False, "toolbar missing"\n'})),
        turn(analysis_args(reproduction={'command': passing, 'test_files': ['test_repro.py'], 'not_reproducible_reason': ''})),
        turn(analysis_args(reproduction={'command': failing, 'test_files': ['test_repro.py'], 'not_reproducible_reason': ''})),
    ])
    reviewer = ScriptedLLM([turn(REVIEW_OK)])
    bug = {'title': '工具栏消失', 'description': '缩进后工具栏消失', 'feedback': '无', 'previous_attempt': '无'}
    result = await run_analysis(llm, reviewer, repository, '分析这个 bug', bug=bug)
    assert (repository / 'app.py').read_text().startswith('label')
    assert 'value = 1' in (repository / 'app.py').read_text()
    assert result['repro']['reproduced'] is True
    assert result['repro']['exit_before'] != 0
    assert result['confidence'] == 'high'
    assert result['proposals'][0]['name'] == 'correct value'
    assert 'toolbar missing' in result['root_cause'] or '复现' in result['root_cause']
    # 先改业务源码被拒绝，复现命令通过也被拒绝，最后一次才提交成功。
    errors = [message['content'] for call in llm.calls for message in call['messages'] if message['role'] == 'tool' and message['content'].startswith('错误')]
    assert any('业务源码' in item or 'read_file' in item for item in errors)
    assert any('通过了' in item for item in errors)


@pytest.mark.asyncio
async def test_fix_protects_reproduction_test_and_reruns_it(repository):
    (repository / 'test_repro.py').write_text('raise SystemExit(1)\n')
    subprocess.run(['git', '-C', str(repository), 'add', 'test_repro.py'], check=True)
    subprocess.run(['git', '-C', str(repository), '-c', 'user.name=T', '-c', 'user.email=t@e', 'commit', '-qam', 'test'], check=True)
    command = f'''{sys.executable} -c "import pathlib; assert 'value = 9' in pathlib.Path('app.py').read_text()"'''
    repro = {'command': command, 'test_files': ['test_repro.py'], 'hashes': {}, 'reproduced': True, 'exit_before': 1, 'output_before': 'fail'}
    from src.core.repair_agent import _sha
    repro['hashes'] = {'test_repro.py': _sha(repository / 'test_repro.py')}
    llm = ScriptedLLM([
        turn(('edit_file', {'path': 'test_repro.py', 'old_string': 'raise SystemExit(1)\n', 'new_string': 'raise SystemExit(0)\n'})),
        turn(('read_file', {'path': 'app.py'})),
        turn(('edit_file', {'path': 'app.py', 'old_string': 'value = 1', 'new_string': 'value = 8'})),
        turn(('submit_fix', {'summary': '还没改完'})),
        turn(('edit_file', {'path': 'app.py', 'old_string': 'value = 8', 'new_string': 'value = 9'})),
        turn(('submit_fix', {'summary': '已把 value 改为 9，复现测试通过'})),
        turn(('submit_fix', {'summary': '已把 value 改为 9，复现测试通过'})),
    ])
    reviewer = ScriptedLLM([turn(review_refuted('app.py:2', '没有覆盖另一条路径')), turn(REVIEW_OK)])
    bug = {'title': '值错误', 'description': 'value 应为 9', 'feedback': '无', 'previous_attempt': '无'}
    analysis = {'symptom': {'expected': '9', 'actual': '1', 'steps': ['运行'], 'acceptance': ['value 为 9']}, 'root_cause': '赋值', 'trigger_paths': [], 'proposals': []}
    result = await run_fix(llm, reviewer, repository, '按方案修复', bug=bug, analysis=analysis, repro=repro, instructions='无')
    assert (repository / 'test_repro.py').read_text().startswith('raise SystemExit(1)')
    assert (repository / 'app.py').read_text().splitlines()[1] == 'value = 9'
    assert result['repro']['exit_after'] == 0
    assert '已把 value 改为 9' in result['summary']
    errors = [message['content'] for call in llm.calls for message in call['messages'] if message['role'] == 'tool' and str(message['content']).startswith('错误')]
    assert any('复现测试' in item and '不能修改' in item for item in errors)
    assert any('仍然失败' in item for item in errors)


@pytest.mark.asyncio
async def test_review_rejection_is_shown_to_the_implementer(repository):
    llm = ScriptedLLM([
        turn(analysis_args()),
        turn(analysis_args(root_cause='app.py:2 赋值为 1，缩进后无条件关栏')),
    ])
    reviewer = ScriptedLLM([
        turn(review_refuted('app.py:2', '没有枚举 dismiss 的其他调用点')),
        turn(REVIEW_OK),
    ])
    bug = {'title': '工具栏', 'description': '消失', 'feedback': '无', 'previous_attempt': '无'}
    result = await run_analysis(llm, reviewer, repository, '分析', bug=bug)
    assert result['confidence'] == 'medium'  # 审查通过，但没有复现
    feedback = llm.calls[1]['messages'][-1]['content']
    assert 'dismiss' in feedback


@pytest.mark.asyncio
async def test_unresolved_review_lowers_confidence_instead_of_stopping(repository):
    llm = ScriptedLLM([turn(analysis_args()), turn(analysis_args()), turn(analysis_args())])
    reviewer = ScriptedLLM([turn(review_refuted()), turn(review_refuted()), turn(review_refuted())])
    bug = {'title': '工具栏', 'description': '消失', 'feedback': '无', 'previous_attempt': '无'}
    result = await run_analysis(llm, reviewer, repository, '分析', bug=bug)
    assert result['confidence'] == 'low'
    assert result['review']['refuted'] is True
    assert '审查员仍不认可' in result['root_cause']


@pytest.mark.asyncio
async def test_sandbox_cannot_write_outside_or_read_secrets(repository, tmp_path):
    if not sandbox_available():
        pytest.skip('当前系统没有 sandbox-exec')
    secret = tmp_path / 'secret.txt'
    secret.write_text('token')
    outside = Path.home() / '.harness-sandbox-probe'
    outside.unlink(missing_ok=True)
    code, output = await run_in_worktree(repository, f'echo pwned > {outside}; echo wrote', deny_read=[secret])
    assert not outside.exists()
    assert 'Operation not permitted' in output or code != 0
    # 工作树内可以写。
    code, _ = await run_in_worktree(repository, 'echo sandbox > sandbox.txt')
    assert code == 0
    assert (repository / 'sandbox.txt').read_text().strip() == 'sandbox'
    code, output = await run_in_worktree(repository, f'cat {secret}', deny_read=[secret])
    assert 'token' not in output


@pytest.mark.asyncio
async def test_gpt_models_apply_a_unified_diff(repository):
    import difflib
    from src.core.agent_tools import build_tools, prefers_apply_patch
    assert prefers_apply_patch('gpt-4.1') and prefers_apply_patch('openai/o3-mini')
    assert not prefers_apply_patch('grok-4.7')
    fixing = Workspace(repository, mode='fix')
    names = [tool.name for tool in build_tools(fixing, commands=False, editor='apply_patch')]
    assert 'apply_patch' in names and 'edit_file' not in names
    old = (repository / 'app.py').read_text()
    patch = ''.join(difflib.unified_diff(old.splitlines(True), old.replace('value = 1', 'value = 9').splitlines(True), fromfile='a/app.py', tofile='b/app.py'))
    analyzing = Workspace(repository, mode='analyze')
    with pytest.raises(ToolError, match='业务源码'):
        await analyzing.apply_patch(patch)
    assert 'value = 1' in (repository / 'app.py').read_text()
    await fixing.apply_patch(patch)
    assert 'value = 9' in (repository / 'app.py').read_text()


@pytest.mark.asyncio
async def test_compaction_summarizes_and_rereads_recent_files(repository):
    from src.core.tool_agent import COMPACT_TRIGGER_CHARS, compact_history
    files = Workspace(repository, mode='fix')
    files.read_file('app.py')

    class Summarizer:
        async def ainvoke(self, messages, **kwargs):
            assert 'app.py:2' in messages[-1]['content']
            return '摘要：app.py:2 的 value 仍为 1'

    messages = [{'role': 'system', 'content': 'sys'}]
    for index in range(7):
        messages.append({'role': 'assistant', 'content': None, 'tool_calls': [{'id': f'c{index}', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{}'}}]})
        messages.append({'role': 'tool', 'tool_call_id': f'c{index}', 'content': '事实 app.py:2\n' + 'x' * (COMPACT_TRIGGER_CHARS // 6)})
    removed, _, _ = await compact_history(messages, Summarizer(), files)
    assert removed >= 1
    note = messages[-1]['content']
    assert '摘要：app.py:2' in note and 'value = 1' in note
    assert messages[2]['content'].startswith('[较早的工具结果已压缩')


def test_unexpected_eof_is_retried():
    from src.core.tool_agent import retryable_request_error
    assert retryable_request_error(RuntimeError('unexpected EOF'))
    assert not retryable_request_error(ValueError('参数必须是 JSON 对象'))


def test_double_escaped_tool_text_is_restored():
    from src.core.tool_agent import repair_unicode_escapes
    raw = '\\u5386\\u53f2列表 HandoutHistoryPanel.vue:121'
    assert repair_unicode_escapes(raw).startswith('历史列表')
    assert repair_unicode_escapes('讲义历史') == '讲义历史'
    assert repair_unicode_escapes({'root_cause': raw})['root_cause'].startswith('历史列表')


@pytest.mark.asyncio
async def test_dropped_model_stream_is_retried(repository):
    from src.core.agent_tools import build_tools
    from src.core.tool_agent import run_tool_agent, submit_tool

    def drop(_messages):
        raise RuntimeError('peer closed connection without sending complete message body (incomplete chunked read)')

    llm = ScriptedLLM([drop, turn(('submit_fix', {'summary': 'done'}))])

    async def accept(args):
        return args

    tools = build_tools(Workspace(repository, mode='read'), commands=False)
    tools.append(submit_tool('submit_fix', '提交', {'type': 'object', 'properties': {'summary': {'type': 'string'}}, 'required': ['summary']}, accept))
    name, value = await run_tool_agent(llm, [{'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'go'}], tools, max_turns=4)
    assert name == 'submit_fix' and value['summary'] == 'done'
    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_browser_tool_stays_on_the_preview_origin():
    from src.core.browser_tool import browser_action
    seen = {}

    async def preview(spec, *, restart=False):
        seen['restart'] = restart
        return {'status': 'running', 'url': 'http://127.0.0.1:9'}

    class Page:
        url = 'http://127.0.0.1:9/'

        async def goto(self, url, **kwargs):
            self.url = url

        async def inner_text(self, selector):
            return '工具栏消失'

        async def title(self):
            return '预览'

        async def screenshot(self, **kwargs):
            return b'\x89PNG'

    page = Page()
    spec = {'agent_id': 'a', 'worktree_path': '/tmp/repair-1', 'repo_path': '/tmp/repo', 'command': 'serve'}
    text = await browser_action(spec, 'goto', '/editor', page=page, preview=preview)
    assert page.url == 'http://127.0.0.1:9/editor' and '工具栏消失' in text
    shot = await browser_action(spec, 'screenshot', page=page, preview=preview)
    assert shot['images'][0].startswith('data:image/png;base64,')
    await browser_action(spec, 'reload', page=page, preview=preview)
    assert seen['restart'] is True
    with pytest.raises(ToolError, match='站内路径'):
        await browser_action(spec, 'goto', 'https://evil.example', page=page, preview=preview)
