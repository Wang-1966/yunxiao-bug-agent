"""用项目预览核对界面。预览进程走 preview_manager，页面操作用本机 Chrome（Playwright）。"""
import asyncio
import base64
import time

from .agent_tools import ToolError, clip_output
from .preview_manager import START_TIMEOUT, preview_status, start_preview

_sessions: dict[str, '_Session'] = {}
_lock = asyncio.Lock()


class _Session:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.page = None

    async def open_page(self):
        if self.page:
            return self.page
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise ToolError('未安装 Playwright，无法打开预览。请在后端环境执行 pip install playwright') from exc
        self.playwright = await async_playwright().start()
        last = None
        for kwargs in ({'channel': 'chrome', 'headless': True}, {'headless': True}):
            try:
                self.browser = await self.playwright.chromium.launch(**kwargs)
                break
            except Exception as exc:
                last = exc
        if not self.browser:
            await self.close()
            raise ToolError(f'无法启动浏览器：{last}')
        self.page = await self.browser.new_page()
        return self.page

    async def close(self):
        for closer in (getattr(self.page, 'close', None), getattr(self.browser, 'close', None)):
            if closer:
                try:
                    await closer()
                except Exception:
                    pass
        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass
        self.page = self.browser = self.playwright = None


async def close_browser(worktree):
    async with _lock:
        session = _sessions.pop(str(worktree), None)
    if session:
        await session.close()


def _path(path: str) -> str:
    if not isinstance(path, str) or not path.startswith('/') or path.startswith('//') or '..' in path.split('/'):
        raise ToolError('path 只能是预览站内路径，例如 /')
    return path


async def _preview(spec, *, restart=False):
    agent_id = spec['agent_id']
    if not restart:
        current = await preview_status(agent_id)
        if current.get('status') == 'running' and current.get('url'):
            return current
    try:
        snapshot = await start_preview(
            agent_id, worktree_path=spec['worktree_path'], repo_path=spec['repo_path'], command=spec['command'],
            env_files=spec.get('env_files') or [], commit='', branch=spec.get('branch') or '',
            public_port=spec.get('public_port'), allow_dirty=True)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    deadline = time.monotonic() + START_TIMEOUT
    while snapshot.get('status') == 'starting' and time.monotonic() < deadline:
        await asyncio.sleep(0.5)
        snapshot = await preview_status(agent_id)
    if snapshot.get('status') != 'running' or not snapshot.get('url'):
        raise ToolError(snapshot.get('error') or '预览未就绪')
    return snapshot


async def browser_action(spec, action, path='/', selector='', text='', *, page=None, preview=None):
    """page / preview 仅测试注入。截图返回 {text, images}，其余返回页面文字。"""
    action = action if action in ('goto', 'text', 'click', 'fill', 'screenshot', 'reload') else ''
    if not action:
        raise ToolError('action 必须是 goto、text、click、fill、screenshot 或 reload')
    if action in ('click', 'fill') and (not isinstance(selector, str) or not selector.strip()):
        raise ToolError('click 和 fill 需要 selector')
    if action == 'fill' and not isinstance(text, str):
        raise ToolError('fill 需要 text')
    snapshot = await (preview or _preview)(spec, restart=action == 'reload')
    url = snapshot['url'].rstrip('/') + (_path(path) if action == 'goto' else '')
    if page is None:
        async with _lock:
            session = _sessions.setdefault(str(spec['worktree_path']), _Session())
        page = await session.open_page()
    timeout = 30_000
    try:
        if action == 'goto':
            await page.goto(url, wait_until='domcontentloaded', timeout=timeout)
        elif action == 'click':
            await page.click(selector, timeout=timeout)
        elif action == 'fill':
            await page.fill(selector, text, timeout=timeout)
        body = clip_output(await page.inner_text('body'), 8000)
        title = await page.title()
        shown = f'{title}\n{getattr(page, "url", "") or url}\n{body}'
        if action == 'screenshot':
            png = await page.screenshot(full_page=False)
            return {'text': shown, 'images': ['data:image/png;base64,' + base64.b64encode(png).decode('ascii')]}
        return shown
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f'浏览器操作失败：{exc}') from exc
