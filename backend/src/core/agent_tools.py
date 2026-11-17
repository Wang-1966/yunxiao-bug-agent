"""Pi 宿主权限、受控搜索、复现命令与业务工具；通用编码工具由 Pi SDK 提供。"""
import asyncio
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from .repair_executor import run_process

READ_DEFAULT_LINES = 500
GREP_DEFAULT_LINES = 200
GREP_MAX_LINES = 1000
COMMAND_DEFAULT_TIMEOUT = 120
COMMAND_MAX_TIMEOUT = 900
COMMAND_OUTPUT_CHARS = 20_000

SECRET_DIRS = {'.git', '.ssh'}
SECRET_FILES = {'.npmrc', '.pypirc', '.netrc'}
GENERATED_DIRS = {'dist', '.venv', 'venv', '__pycache__'}


class ToolError(ValueError):
    """可由模型修正的工具调用错误，原样返回给模型。"""


def is_test_path(name: str) -> bool:
    path = Path(name)
    parts = {part.lower() for part in path.parts[:-1]}
    base = path.name.lower()
    return bool(parts & {'test', 'tests', '__tests__', 'spec', 'specs', 'e2e', '__mocks__'}
                or re.search(r'(\.|_)(test|spec)\.[a-z0-9]+$', base) or base.startswith('test_'))


# ---- 命令沙箱（macOS sandbox-exec）----
def sandbox_available() -> bool:
    return sys.platform == 'darwin' and shutil.which('sandbox-exec') is not None


def sandbox_profile(worktree: Path, deny_read: list[Path]) -> str:
    home = Path.home()
    writable = [worktree, Path('/private/tmp'), Path('/private/var/folders'), home / '.npm', home / '.cache',
                home / 'Library' / 'Caches', home / 'Library' / 'pnpm', home / '.local' / 'share' / 'pnpm',
                home / '.pnpm-store', home / '.yarn', home / '.bun']
    secret = [home / name for name in ('.ssh', '.aws', '.gnupg', '.kube', '.docker', '.config/gh', '.netrc', '.npmrc', '.cursor', '.codex', '.claude')]
    quote = lambda p: json.dumps(str(p))
    rules = ['(version 1)', '(allow default)', '(deny file-write*)',
             f"(allow file-write* {' '.join(f'(subpath {quote(p)})' for p in writable)} (literal \"/dev/null\") (literal \"/dev/tty\") (regex #\"^/dev/(fd|pty|tty)\"))"]
    rules.append(f"(deny file-read* {' '.join(f'(subpath {quote(p)})' for p in secret + deny_read)})")
    # 工作树的 Git 元数据位于主仓库内；禁止写入，模型无法提交、切换分支或改写引用。
    return ''.join(rules)


def command_env() -> dict:
    """只保留运行构建工具所需的变量，不继承 API Key 等敏感环境变量。"""
    keep = ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR', 'SHELL', 'USER', 'NVM_DIR', 'PNPM_HOME', 'COREPACK_HOME', 'npm_config_registry')
    env = {key: os.environ[key] for key in keep if key in os.environ}
    env.update(CI='1', TERM='dumb', NO_COLOR='1')
    return env


def clip_output(text: str, limit: int = COMMAND_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    head = limit // 4
    return f'{text[:head]}\n…（输出过长，省略 {len(text) - limit} 字符）…\n{text[-(limit - head):]}'


def effective_denies(worktree, denies) -> list:
    """禁读不能覆盖工作树或其父目录，否则进程无法定位当前目录。"""
    root = Path(worktree).resolve()
    kept = []
    for raw in denies:
        path = Path(raw).resolve()
        if path == root or root.is_relative_to(path) or path.is_relative_to(root):
            continue
        kept.append(path)
    return kept


async def run_in_worktree(worktree, command: str, timeout: int = COMMAND_DEFAULT_TIMEOUT, *, sandbox: bool = True, deny_read=()):
    """在工作树内执行命令；返回 (退出码, 输出)。超时返回退出码 124。"""
    root = Path(worktree).resolve()
    args = ['/bin/sh', '-c', command]
    if sandbox and sandbox_available():
        args = ['sandbox-exec', '-p', sandbox_profile(root, effective_denies(root, deny_read)), *args]
    process = await asyncio.create_subprocess_exec(
        *args, cwd=str(root), env=command_env(), stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT, stdin=asyncio.subprocess.DEVNULL, start_new_session=True,
    )
    # Bound memory while draining output, preserving both startup and final diagnostics.
    output_limit = 8 * 1024 * 1024
    head_limit = output_limit // 4
    head, tail, total = bytearray(), bytearray(), 0

    async def drain():
        nonlocal total
        while chunk := await process.stdout.read(65536):
            total += len(chunk)
            needed = max(0, head_limit - len(head))
            head.extend(chunk[:needed])
            tail.extend(chunk[needed:])
            if len(tail) > output_limit - head_limit:
                del tail[:len(tail) - (output_limit - head_limit)]

    reader = asyncio.create_task(drain())
    timed_out = False
    try:
        await asyncio.wait_for(asyncio.gather(process.wait(), asyncio.shield(reader)), timeout)
    except asyncio.TimeoutError:
        timed_out = True
        _kill(process)
        await process.wait()
    except asyncio.CancelledError:
        _kill(process)
        await asyncio.gather(process.wait(), reader)
        raise
    await reader
    omitted = f'\n[命令输出超过保留上限，省略 {total - output_limit} 字节]\n'.encode() if total > output_limit else b''
    output = (head + omitted + tail).decode('utf-8', errors='replace')
    if timed_out:
        return 124, f'命令超过 {timeout} 秒被终止\n{output}'
    return process.returncode, output


def _kill(process):
    import signal
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[[dict], Awaitable[Any]]
    readonly: bool = True
    terminal: bool = False  # submit_* 工具：校验通过即结束本次循环

    def schema(self):
        return {'type': 'function', 'function': {'name': self.name, 'description': self.description, 'parameters': self.parameters}}


@dataclass
class Workspace:
    """工作树文件操作；写入策略由 mode 与 protected 决定。"""
    root: Path
    mode: str = 'read'  # read | analyze | fix
    protected: set = field(default_factory=set)
    deny_read: list = field(default_factory=list)
    read_paths: set = field(default_factory=set)
    read_requests: list = field(default_factory=list)
    touched: list = field(default_factory=list)

    def __post_init__(self):
        self.root = Path(self.root).resolve()

    def resolve(self, name, *, write=False) -> Path:
        if not isinstance(name, str) or not name.strip():
            raise ToolError('文件路径不能为空')
        relative = Path(name)
        if relative.is_absolute():
            try:
                relative = relative.resolve().relative_to(self.root)
            except ValueError:
                raise ToolError('只能操作工作树内的路径')
        if '..' in relative.parts:
            raise ToolError('路径不能包含 ..')
        for part in relative.parts:
            if part in SECRET_DIRS or part in SECRET_FILES or part == '.env' or part.startswith('.env.') or part.endswith(('.pem', '.key', '.p12')):
                raise ToolError('禁止访问 Git 元数据或密钥文件')
            if write and (part in GENERATED_DIRS or part == 'node_modules'):
                raise ToolError('禁止修改依赖或构建产物')
        path = self.root
        for part in relative.parts:
            path = path / part
            if path.is_symlink():
                raise ToolError('禁止通过符号链接读写文件')
        if not path.resolve().is_relative_to(self.root):
            raise ToolError('路径越过工作树边界')
        return path

    def rel(self, path: Path) -> str:
        return str(path.relative_to(self.root))

    def touch(self, name: str):
        if name in self.touched:
            self.touched.remove(name)
        self.touched.append(name)

    def assert_editable(self, name: str) -> str:
        """写入前的路径与阶段检查，返回仓库内相对路径。"""
        if self.mode == 'read':
            raise ToolError('当前阶段只读，不能修改文件')
        target = self.resolve(name, write=True)
        relative = self.rel(target)
        if relative in self.protected:
            raise ToolError(f'{relative} 是已确认的复现测试，修复阶段不能修改；请修改业务源码使其通过')
        if self.mode == 'analyze' and not is_test_path(relative):
            raise ToolError(f'分析阶段只能新增或修改测试文件（用于复现）；{relative} 是业务源码，请留到修复阶段')
        return relative

    # ---- 读取 ----
    def record_read(self, path, offset=1, limit=READ_DEFAULT_LINES):
        """记录实际成功的读取，供后续修复轮次定位源码。"""
        target = self.resolve(path)
        name = self.rel(target)
        self.read_paths.add(name)
        self.touch(name)
        if target.name not in ('AGENTS.md', 'CLAUDE.md'):
            request = {'path': name, 'start_line': offset, 'count': limit}
            if request in self.read_requests:
                self.read_requests.remove(request)
            self.read_requests.append(request)


    async def grep(self, pattern, path='', glob=None, ignore_case=True, include_ignored=False, max_lines=GREP_DEFAULT_LINES, context=0):
        if not isinstance(pattern, str) or not pattern or len(pattern) > 500:
            raise ToolError('pattern 长度必须为 1–500')
        scope = '.'
        if path and path not in ('.', ''):
            scope = self.rel(self.resolve(path))
        max_lines = max(1, min(int(max_lines or GREP_DEFAULT_LINES), GREP_MAX_LINES))
        rg = shutil.which('rg')
        if rg:
            args = [rg, '--no-heading', '-n', '--max-columns', '500', '--max-filesize', '5M', '-e', pattern]
            if ignore_case:
                args.append('-i')
            if include_ignored:
                args += ['--no-ignore', '--hidden']
            for secret in ('!**/.git/**', '!**/.env', '!**/.env.*', '!**/*.pem', '!**/*.key', '!**/*.p12', '!**/.npmrc', '!**/.pypirc'):
                args += ['-g', secret]
            if glob:
                args += ['-g', str(glob)]
            if context:
                args += ['-C', str(min(int(context), 5))]
            args += ['--', scope]
        else:
            args = ['git', 'grep', '--no-index', '-n', '-I', '-E', '-e', pattern, '--', scope]
            if ignore_case:
                args.insert(2, '-i')
        code, output = await run_process(args, self.root, timeout=30)
        if code == 1:
            return f'没有匹配结果（范围：{scope}）'
        if code not in (0, 1) and not output.strip():
            raise ToolError('搜索失败')
        if code > 1:
            raise ToolError(f'搜索表达式无效：{output[-500:]}')
        kept = []
        for line in output.splitlines():
            if not context:
                head = line.split(':', 1)[0]
                try:
                    if rg is None and not include_ignored and 'node_modules/' in head:
                        continue
                    self.resolve(head)
                except ToolError:
                    continue
            kept.append(line)
        if not kept:
            return f'没有匹配结果（范围：{scope}）'
        shown = kept[:max_lines]
        result = '\n'.join(shown)
        if len(kept) > max_lines:
            result += f'\n（共 {len(kept)} 行，显示前 {max_lines} 行；请缩小 path 或 glob 范围）'
        return result


    # ---- 编辑 ----


def _schema(properties: dict, required: list) -> dict:
    return {'type': 'object', 'properties': properties, 'required': required}


def build_tools(workspace: Workspace, *, images: Optional[list] = None,
                previous: Optional[Callable] = None, preview: Optional[dict] = None) -> list[Tool]:
    """构造本阶段可用工具；image/previous 由调用方注入。"""

    async def grep(a):
        return await workspace.grep(a.get('pattern'), a.get('path', ''), a.get('glob'), a.get('ignore_case', True),
                                    a.get('include_ignored', False), a.get('max_lines', GREP_DEFAULT_LINES), a.get('context', 0))

    tools = [Tool('grep', '搜索工作树内容，过滤密钥文件及越界路径。',
                  _schema({'pattern': {'type': 'string'}, 'path': {'type': 'string'}, 'glob': {'type': 'string'},
                           'ignore_case': {'type': 'boolean'}, 'include_ignored': {'type': 'boolean'},
                           'max_lines': {'type': 'integer'}, 'context': {'type': 'integer'}}, ['pattern']), grep)]
    if images:
        async def view_image(a):
            index = a.get('image')
            if type(index) is not int or not 1 <= index <= len(images):
                raise ToolError(f'图片编号无效，可用编号 1–{len(images)}')
            return {'text': f'图片 {index} 已附在下一条消息中。请在回复里写下你看到的控件、文字和现象。', 'images': [images[index - 1]]}
        tools.append(Tool('view_image', f'查看 Bug 附带的原图（共 {len(images)} 张，编号从 1 开始）。需要核对界面细节时使用。',
                          _schema({'image': {'type': 'integer'}}, ['image']), view_image))
    if preview:
        from .browser_tool import browser_action

        async def browser(a):
            return await browser_action(preview, a.get('action') or 'text', a.get('path') or '/', a.get('selector') or '', a.get('text') or '')

        tools.append(Tool('browser', '打开项目预览页核对界面。action：goto 打开路径，text 读取当前页文字，click 点击，fill 输入，screenshot 截图，reload 改完代码后重启预览。path 只能是站内路径，如 /。',
                          _schema({'action': {'type': 'string', 'enum': ['goto', 'text', 'click', 'fill', 'screenshot', 'reload']},
                                   'path': {'type': 'string'}, 'selector': {'type': 'string'}, 'text': {'type': 'string'}}, ['action']), browser, readonly=False))
    if previous:
        async def previous_diff(a):
            return await previous(a.get('path', ''), a.get('offset', 0))
        tools.append(Tool('previous_diff', '查看上一轮被驳回修复的代码差异（分页）。仅用于理解上一轮为何没修好，结论必须基于当前源码。',
                          _schema({'path': {'type': 'string'}, 'offset': {'type': 'integer'}}, []), previous_diff))
    return tools
