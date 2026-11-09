"""修复 Agent 的工具集：读文件、搜索、列目录、精确编辑、受限命令执行。

设计参考 Grok Build / Claude Code 的公开设计：行号前缀仅供定位、编辑对易混字符归一化后匹配、
命令在平台沙箱内执行。所有路径限制在工作树内，密钥与 Git 元数据不可访问。
"""
import asyncio
import difflib
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from .repair_executor import git, run_process

READ_MAX_LINES = 1000
READ_DEFAULT_LINES = 500
READ_MAX_CHARS = 100_000
READ_MAX_BYTES = 2_000_000
GREP_DEFAULT_LINES = 200
GREP_MAX_LINES = 1000
LIST_MAX_ENTRIES = 300
COMMAND_DEFAULT_TIMEOUT = 120
COMMAND_MAX_TIMEOUT = 900
COMMAND_OUTPUT_CHARS = 20_000
MAX_EDIT_CHARS = 200_000

SECRET_DIRS = {'.git', '.ssh'}
SECRET_FILES = {'.npmrc', '.pypirc', '.netrc'}
GENERATED_DIRS = {'dist', '.venv', 'venv', '__pycache__'}
LISTING_SKIPPED = SECRET_DIRS | GENERATED_DIRS | {'node_modules'}


class ToolError(ValueError):
    """可由模型修正的工具调用错误，原样返回给模型。"""


def is_test_path(name: str) -> bool:
    path = Path(name)
    parts = {part.lower() for part in path.parts[:-1]}
    base = path.name.lower()
    return bool(parts & {'test', 'tests', '__tests__', 'spec', 'specs', 'e2e', '__mocks__'}
                or re.search(r'(\.|_)(test|spec)\.[a-z0-9]+$', base) or base.startswith('test_'))


# ---- 编辑时的易混字符归一化（弯引号、破折号、不间断空格等）----
CONFUSABLES = {
    '\u2018': "'", '\u2019': "'", '\u201a': "'", '\u201b': "'", '\u201c': '"', '\u201d': '"', '\u201e': '"',
    '\u2013': '-', '\u2014': '-', '\u2212': '-', '\u00a0': ' ', '\u3000': ' ', '\u2009': ' ', '\u202f': ' ',
    '\u2026': '...', '\u200b': '',
}


def normalize_with_map(text: str):
    """返回 (归一化文本, 每个归一化字符对应的原始下标)。"""
    out, mapping = [], []
    for index, char in enumerate(text):
        replacement = CONFUSABLES.get(char, char)
        for item in replacement:
            out.append(item)
            mapping.append(index)
    return ''.join(out), mapping


def locate(text: str, needle: str):
    """精确匹配优先；没有精确匹配时用归一化匹配。返回原文中的 (start, end) 列表。"""
    spans, start = [], 0
    while (found := text.find(needle, start)) != -1:
        spans.append((found, found + len(needle)))
        start = found + len(needle)
    if spans:
        return spans
    norm_text, mapping = normalize_with_map(text)
    norm_needle, _ = normalize_with_map(needle)
    if not norm_needle:
        return []
    start = 0
    while (found := norm_text.find(norm_needle, start)) != -1:
        end = found + len(norm_needle)
        spans.append((mapping[found], mapping[end - 1] + 1))
        start = end
    return spans


def closest_lines(text: str, needle: str, limit: int = 5) -> str:
    """匹配失败时给出最接近的原文片段，帮助模型修正 old_string。"""
    lines = text.splitlines()
    probe = next((line.strip() for line in needle.splitlines() if line.strip()), '')
    if not probe or not lines:
        return ''
    stripped = [line.strip() for line in lines]
    hits = [i for i, line in enumerate(stripped) if probe in line]
    if not hits:
        close = difflib.get_close_matches(probe, stripped, n=1, cutoff=0.5)
        hits = [stripped.index(close[0])] if close else []
    if not hits:
        return ''
    i = hits[0]
    lo, hi = max(0, i - 1), min(len(lines), i + limit)
    return '最接近的原文（行号仅供定位）：\n' + '\n'.join(f'{n + 1}→{lines[n]}' for n in range(lo, hi))


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
    def read_file(self, path, offset=1, limit=READ_DEFAULT_LINES):
        target = self.resolve(path)
        if not target.is_file():
            raise ToolError(f'文件不存在：{path}（可用 list_dir / find_files 查找）')
        name = self.rel(target)
        whole = target.name in ('AGENTS.md', 'CLAUDE.md')
        if target.stat().st_size > READ_MAX_BYTES and not whole and offset == 1 and limit > 200:
            limit = 200
        try:
            text = target.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            raise ToolError('不能读取二进制或非 UTF-8 文件')
        if '\0' in text:
            raise ToolError('不能读取二进制文件')
        lines = text.splitlines()
        offset, limit = int(offset or 1), int(limit or READ_DEFAULT_LINES)
        if whole:
            offset, limit = 1, len(lines) or 1
        if offset < 1 or limit < 1:
            raise ToolError('offset 和 limit 必须大于 0')
        limit = min(limit, READ_MAX_LINES) if not whole else limit
        if lines and offset > len(lines):
            raise ToolError(f'offset 超过文件总行数 {len(lines)}')
        end = min(len(lines), offset - 1 + limit)
        body = '\n'.join(f'{i + 1}→{lines[i]}' for i in range(offset - 1, end))
        truncated = len(body) > READ_MAX_CHARS
        body = body[:READ_MAX_CHARS]
        self.read_paths.add(name)
        self.touch(name)
        if not whole:
            request = {'path': name, 'start_line': offset, 'count': limit}
            if request in self.read_requests:
                self.read_requests.remove(request)
            self.read_requests.append(request)
        footer = ''
        if truncated:
            footer = '\n（输出过长已截断，请缩小 limit）'
        elif end < len(lines):
            footer = f'\n（还有 {len(lines) - end} 行，使用 offset={end + 1} 继续读取）'
        return f'{name}（共 {len(lines)} 行；行号前缀 N→ 不属于文件内容）\n{body}{footer}'

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

    async def find_files(self, pattern, path=''):
        scope = '.'
        if path and path not in ('.', ''):
            scope = self.rel(self.resolve(path))
        rg = shutil.which('rg')
        if rg:
            args = [rg, '--files', '-g', str(pattern), '--', scope]
            code, output = await run_process(args, self.root, timeout=30)
            names = [line for line in output.splitlines() if line]
        else:
            tracked = (await git(self.root, 'ls-files', '-z')).split('\0')
            import fnmatch
            names = [n for n in tracked if n and fnmatch.fnmatch(n, str(pattern)) and (scope == '.' or n.startswith(scope + '/'))]
        safe = []
        for name in sorted(names):
            try:
                self.resolve(name)
            except ToolError:
                continue
            safe.append(name)
        if not safe:
            return '没有匹配的文件'
        extra = f'\n（共 {len(safe)} 个，显示前 {LIST_MAX_ENTRIES} 个）' if len(safe) > LIST_MAX_ENTRIES else ''
        return '\n'.join(safe[:LIST_MAX_ENTRIES]) + extra

    def list_dir(self, path='', depth=1):
        target = self.resolve(path) if path and path != '.' else self.root
        if not target.is_dir():
            raise ToolError(f'目录不存在：{path}')
        depth = max(1, min(int(depth or 1), 3))
        inside_modules = 'node_modules' in target.relative_to(self.root).parts
        entries = []

        def walk(directory: Path, level: int):
            try:
                children = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            except OSError:
                return
            for child in children:
                if len(entries) >= LIST_MAX_ENTRIES:
                    return
                if child.name in SECRET_DIRS or child.name in SECRET_FILES or child.name == '.env' or child.name.startswith('.env.'):
                    continue
                if child.name in LISTING_SKIPPED and not inside_modules:
                    continue
                if child.is_symlink():
                    continue
                entries.append(('  ' * (level - 1)) + child.name + ('/' if child.is_dir() else ''))
                if child.is_dir() and level < depth:
                    walk(child, level + 1)

        walk(target, 1)
        header = f'{self.rel(target) if target != self.root else "."}/'
        more = f'\n（已达 {LIST_MAX_ENTRIES} 条上限，请指定子目录）' if len(entries) >= LIST_MAX_ENTRIES else ''
        return header + '\n' + '\n'.join(entries) + more

    def instructions(self) -> str:
        """仓库及子目录的 AGENTS.md 全文（有上限），随任务一并提供。"""
        sections = []
        for path in sorted(self.root.rglob('AGENTS.md')):
            relative = path.relative_to(self.root)
            if any(part in LISTING_SKIPPED for part in relative.parts) or path.is_symlink():
                continue
            try:
                sections.append(f'## {relative}\n' + path.read_text(encoding='utf-8')[:20000])
            except (OSError, UnicodeDecodeError):
                continue
        return '\n\n'.join(sections)[:40000]

    # ---- 编辑 ----
    def edit_file(self, path, old_string, new_string, replace_all=False):
        if not isinstance(new_string, str) or len(new_string) > MAX_EDIT_CHARS:
            raise ToolError('new_string 必须是文本且小于 200KB')
        name = self.assert_editable(path)
        target = self.resolve(name, write=True)
        if not isinstance(old_string, str):
            raise ToolError('old_string 必须是字符串')
        if old_string == '':
            if target.exists() and target.read_text(encoding='utf-8'):
                raise ToolError('old_string 为空只能创建新文件，不能覆盖已有内容；请提供要替换的原文')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(new_string, encoding='utf-8')
            self.touch(name)
            return f'已创建 {name}（{len(new_string.splitlines())} 行）'
        if not target.is_file():
            raise ToolError(f'文件不存在：{name}；新建文件请将 old_string 设为空字符串')
        if name not in self.read_paths:
            raise ToolError(f'修改前必须先用 read_file 读取 {name}')
        if target.stat().st_size > READ_MAX_BYTES:
            raise ToolError('不支持修改超过 2MB 的文件')
        text = target.read_text(encoding='utf-8')
        if old_string == new_string:
            raise ToolError('old_string 与 new_string 相同，没有修改')
        spans = locate(text, old_string)
        if not spans:
            hint = closest_lines(text, old_string)
            raise ToolError(f'{name} 中找不到 old_string。请重新 read_file 后按原文逐字复制（注意缩进）。' + (f'\n{hint}' if hint else ''))
        if len(spans) > 1 and not replace_all:
            raise ToolError(f'old_string 在 {name} 中匹配了 {len(spans)} 处；请加入更多上下文使其唯一，或设置 replace_all=true')
        for start, end in reversed(spans if replace_all else spans[:1]):
            text = text[:start] + new_string + text[end:]
        target.write_text(text, encoding='utf-8')
        self.touch(name)
        count = len(spans) if replace_all else 1
        return f'已修改 {name}（替换 {count} 处）'

    async def apply_patch(self, patch: str):
        """应用 unified diff。路径先按阶段规则检查，应用后再核对没有落到清单之外的文件。"""
        if not isinstance(patch, str) or not patch.strip():
            raise ToolError('patch 不能为空')
        if len(patch) > MAX_EDIT_CHARS:
            raise ToolError('patch 超过 200KB')
        if 'GIT binary patch' in patch:
            raise ToolError('不支持二进制补丁')
        paths = diff_paths(patch)
        if not paths:
            raise ToolError('patch 里没有文件路径。请使用 unified diff，并写明 --- a/相对路径 与 +++ b/相对路径')
        for name in paths:
            self.assert_editable(name)
        payload = patch.encode('utf-8')
        before = _status_paths(await _git_status(self.root))
        code, output = await run_process(['git', '-C', str(self.root), 'apply', '--check', '--whitespace=nowarn', '-'], self.root, input=payload)
        if code:
            raise ToolError('patch 无法应用，请对照当前文件重新生成 diff：\n' + output[-2000:])
        code, output = await run_process(['git', '-C', str(self.root), 'apply', '--whitespace=nowarn', '-'], self.root, input=payload)
        if code:
            raise ToolError('patch 应用失败：\n' + output[-2000:])
        extra = _status_paths(await _git_status(self.root)) - before - set(paths)
        if extra:
            await run_process(['git', '-C', str(self.root), 'apply', '-R', '--whitespace=nowarn', '-'], self.root, input=payload)
            raise ToolError('patch 改到了声明之外的文件：' + '、'.join(sorted(extra)[:10]))
        for name in paths:
            self.touch(name)
        return '已应用 patch：' + '、'.join(paths)


def prefers_apply_patch(model: str) -> bool:
    """GPT 系列用 unified diff 比逐段替换更稳；其余模型继续用 edit_file。"""
    name = (model or '').lower().rsplit('/', 1)[-1]
    return name.startswith(('gpt-', 'gpt', 'o1', 'o3', 'o4', 'chatgpt'))


def diff_paths(patch: str) -> list[str]:
    paths = []
    for line in patch.splitlines():
        if line.startswith('diff --git '):
            for part in line.split(' ')[2:]:
                if part.startswith(('a/', 'b/')):
                    paths.append(part[2:])
        elif line.startswith(('+++ ', '--- ')):
            name = line[4:].split('\t', 1)[0].strip().strip('"')
            if name.startswith(('a/', 'b/')):
                name = name[2:]
            if name and name != '/dev/null':
                paths.append(name)
    seen = []
    for name in paths:
        if name not in seen:
            seen.append(name)
    return seen


def _status_paths(output: str) -> set:
    names = set()
    for line in output.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].split(' -> ')[-1].strip().strip('"')
        if path:
            names.add(path)
    return names


async def _git_status(root: Path) -> str:
    code, output = await run_process(['git', '-C', str(root), 'status', '--porcelain'], root)
    return output if code == 0 else ''


def _schema(properties: dict, required: list) -> dict:
    return {'type': 'object', 'properties': properties, 'required': required}


def build_tools(workspace: Workspace, *, commands: bool = True, images: Optional[list] = None,
                previous: Optional[Callable] = None, command_timeout_cap: int = COMMAND_MAX_TIMEOUT,
                editor: str = 'edit_file', preview: Optional[dict] = None) -> list[Tool]:
    """构造本阶段可用工具；image/previous 由调用方注入。"""

    async def read_file(a):
        return workspace.read_file(a.get('path'), a.get('offset', 1), a.get('limit', READ_DEFAULT_LINES))

    async def grep(a):
        return await workspace.grep(a.get('pattern'), a.get('path', ''), a.get('glob'), a.get('ignore_case', True),
                                    a.get('include_ignored', False), a.get('max_lines', GREP_DEFAULT_LINES), a.get('context', 0))

    async def list_dir(a):
        return workspace.list_dir(a.get('path', ''), a.get('depth', 1))

    async def find_files(a):
        return await workspace.find_files(a.get('pattern'), a.get('path', ''))

    async def edit_file(a):
        return workspace.edit_file(a.get('path'), a.get('old_string'), a.get('new_string'), bool(a.get('replace_all', False)))

    async def apply_patch(a):
        return await workspace.apply_patch(a.get('patch'))

    tools = [
        Tool('read_file', f'读取工作树内的文件。每行带 "行号→" 前缀，该前缀不属于文件内容，编辑时不要带上。默认读取 {READ_DEFAULT_LINES} 行，最多 {READ_MAX_LINES} 行；用 offset 继续读取。可读取 node_modules 下的依赖源码。AGENTS.md 总是整篇返回。',
             _schema({'path': {'type': 'string', 'description': '相对工作树根目录的路径'},
                      'offset': {'type': 'integer', 'description': '起始行号，从 1 开始'},
                      'limit': {'type': 'integer', 'description': '读取行数'}}, ['path']), read_file),
        Tool('grep', '用 ripgrep 在工作树中搜索（扩展正则，默认忽略大小写，默认遵守 .gitignore）。搜索 node_modules 等被忽略目录时设置 include_ignored=true 并指定 path。',
             _schema({'pattern': {'type': 'string'}, 'path': {'type': 'string', 'description': '搜索范围（目录或文件），默认整个仓库'},
                      'glob': {'type': 'string', 'description': '文件名过滤，如 *.vue'}, 'ignore_case': {'type': 'boolean'},
                      'include_ignored': {'type': 'boolean'}, 'max_lines': {'type': 'integer'}, 'context': {'type': 'integer', 'description': '上下文行数，最多 5'}}, ['pattern']), grep),
        Tool('list_dir', '列出目录内容（目录以 / 结尾），默认一层，depth 最大 3。', _schema({'path': {'type': 'string'}, 'depth': {'type': 'integer'}}, []), list_dir),
        Tool('find_files', '按 glob 查找文件名，例如 **/*Bubble*.vue。', _schema({'pattern': {'type': 'string'}, 'path': {'type': 'string'}}, ['pattern']), find_files),
    ]
    if workspace.mode != 'read' and editor == 'apply_patch':
        tools.append(Tool('apply_patch', '用 unified diff 修改一个或多个文件。必须包含 --- a/相对路径 与 +++ b/相对路径（新建文件用 --- /dev/null）。不要带行号前缀。',
                          _schema({'patch': {'type': 'string', 'description': 'unified diff 全文'}}, ['patch']), apply_patch, readonly=False))
    elif workspace.mode != 'read':
        tools.append(Tool('edit_file', '精确替换文件中的字符串。old_string 必须在文件中唯一匹配（含缩进），不带 "行号→" 前缀；多处相同时加入上下文或设 replace_all=true。old_string 为空字符串表示创建新文件（不能覆盖已有文件）。修改已有文件前必须先 read_file。',
                          _schema({'path': {'type': 'string'}, 'old_string': {'type': 'string'}, 'new_string': {'type': 'string'},
                                   'replace_all': {'type': 'boolean'}}, ['path', 'old_string', 'new_string']), edit_file, readonly=False))
    if commands and workspace.mode != 'read':
        async def run_command(a):
            command = a.get('command')
            if not isinstance(command, str) or not command.strip():
                raise ToolError('command 不能为空')
            timeout = max(1, min(int(a.get('timeout') or COMMAND_DEFAULT_TIMEOUT), command_timeout_cap))
            code, output = await run_in_worktree(workspace.root, command, timeout, deny_read=workspace.deny_read)
            # Shared execution layer archives the result before producing a bounded preview.
            return f'退出码：{code}\n{output}'
        tools.append(Tool('run_command', f'在工作树根目录执行 shell 命令（沙箱：只能写入工作树和包管理器缓存，不能读取密钥目录，不能提交或改写 Git 引用）。用于运行测试、类型检查、构建、安装依赖。系统会在依赖配置一致且可安全隔离时从源仓库复制 node_modules；运行前检查依赖是否就绪，缺失时按 package.json 与 AGENTS.md 安装（较慢，请设置足够的 timeout）。私有包鉴权或环境缺失应明确报告，不要作为业务 Bug 的复现证据。默认超时 {COMMAND_DEFAULT_TIMEOUT} 秒，最长 {command_timeout_cap} 秒；输出过长会被截断。',
                          _schema({'command': {'type': 'string'}, 'timeout': {'type': 'integer', 'description': '超时秒数'}}, ['command']), run_command, readonly=False))
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
