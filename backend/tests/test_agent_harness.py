"""原生工具循环：编辑容错、并行只读、复现必须失败、复现测试受保护与证据来源。"""
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from fake_agent import REVIEW_OK, ScriptedLLM, analysis_args, review_refuted, turn
from src.core.agent_tools import ToolError, Workspace, run_in_worktree, sandbox_available
from src.core.repair_agent import run_analysis, run_fix


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['analysis', 'fix'])
async def test_missing_dependency_is_not_business_reproduction(repository, monkeypatch, phase):
    from src.core import repair_agent
    (repository / 'test_repro.py').write_text('assert False')
    output = "Error [ERR_MODULE_NOT_FOUND]: Cannot find package '@fangtian/components'"
    monkeypatch.setattr(repair_agent, 'run_in_worktree', AsyncMock(return_value=(1, output)))
    holder = {}
    workspace = Workspace(repository, mode='analyze' if phase == 'analysis' else 'fix')
    if phase == 'analysis':
        tool = repair_agent._analysis_tool(workspace, repository, holder)
        args = analysis_args(reproduction={'command': 'node test.mjs', 'test_files': ['test_repro.py']})[1]
    else:
        (repository / 'app.py').write_text('value = 2')
        tool = repair_agent._fix_tool(workspace, repository, {'command': 'node test.mjs'}, holder)
        args = {'summary': '修复'}
    with pytest.raises(ToolError, match='依赖加载失败'):
        await tool.handler(args)
    assert not holder


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


@pytest.mark.asyncio
async def test_submit_fix_preserves_brief_and_detailed_results_separately(repository):
    from src.core.repair_agent import _fix_tool
    (repository / 'app.py').write_text('value = 9\n')
    holder = {}
    tool = _fix_tool(Workspace(repository, mode='fix'), repository, {}, holder)
    brief = {'root_cause': '初始值设置错误。', 'solution': '已修正初始值。'}
    await tool.handler({'summary': '完整记录：app.py 修改；测试命令和限制。', 'repair_summary': brief})
    assert holder['repair_summary'] == brief
    assert holder['summary'].startswith('完整记录')
    with pytest.raises(ToolError, match='修复摘要无效'):
        await tool.handler({'summary': '记录', 'repair_summary': {**brief, 'solution': '长' * 181}})


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


def test_double_escaped_tool_text_is_restored():
    from src.core.agent_protocol import repair_unicode_escapes
    raw = '\\u5386\\u53f2列表 HandoutHistoryPanel.vue:121'
    assert repair_unicode_escapes(raw).startswith('历史列表')
    assert repair_unicode_escapes('讲义历史') == '讲义历史'
    assert repair_unicode_escapes({'root_cause': raw})['root_cause'].startswith('历史列表')


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
