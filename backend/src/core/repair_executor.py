"""真实代码修复：Git 隔离、受限文件工具、明确的本地验证结果。"""
import asyncio
import json
import os
import signal
import uuid
import time
from collections import Counter
from pathlib import Path

from ..config import get_settings
from ..prompts import render_prompt
from ..llm.diagnostics import response_summary
from loguru import logger

MAX_SOURCE_ROUNDS = 30
MAX_OPERATION_ERRORS = 10
MAX_CONSECUTIVE_ERRORS = 3
MAX_FINISH_ATTEMPTS = 3
MAX_BATCH_OPERATIONS = 5
MAX_CONTEXT_READS = 12
MAX_CONTEXT_CHARS = 48000


async def run_process(args, cwd, timeout=60):
    """停止/超时同时终止子进程组，避免后台安装或构建继续执行。"""
    process = await asyncio.create_subprocess_exec(
        *args, cwd=str(cwd), stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT, start_new_session=True,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
        raise
    return process.returncode, output.decode('utf-8', errors='replace')


async def git(path, *args):
    code, output = await run_process(['git', '-C', str(path), *args], path)
    if code:
        raise ValueError(f'Git 操作失败：{output[-3000:]}')
    return output.strip()


async def prepare_worktree(bug):
    if not bug.get('repo_path'):
        raise ValueError('未配置修复仓库，请先到项目管理绑定此云效项目')
    repo = Path(bug['repo_path']).expanduser().resolve()
    if not repo.is_dir() or Path(await git(repo, 'rev-parse', '--show-toplevel')).resolve() != repo:
        raise ValueError('修复仓库路径必须是现有 Git 仓库的根目录')
    ref = bug.get('branch') or 'HEAD'
    base = await git(repo, 'rev-parse', '--verify', '--end-of-options', f'{ref}^{{commit}}')
    # 每次执行独立路径，失败或重试不覆盖上一轮修改。
    run_id = uuid.uuid4().hex
    root = Path(get_settings().worktrees_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    worktree = root / f'repair-{run_id}'
    branch = f'bug-agent/{run_id}'
    await git(repo, 'worktree', 'add', '-b', branch, str(worktree), base)
    return {'worktree_path': str(worktree), 'repair_branch': branch, 'base_commit': base}


def parse_json(response):
    if not isinstance(response, str):
        raise ValueError('模型没有返回文本')
    response = response.strip()
    if response.startswith('```'):
        response = '\n'.join(response.splitlines()[1:-1])
    try:
        value = json.loads(response)
    except json.JSONDecodeError as exc:
        if exc.msg == 'Extra data':
            raise ValueError('JSON 后存在额外内容，禁止拼接多个 JSON 对象；多个只读操作请使用一个 batch 对象') from exc
        raise ValueError(f'无效 JSON：{exc.msg}（行 {exc.lineno}，列 {exc.colno}）') from exc
    if not isinstance(value, dict):
        raise ValueError('模型必须返回 JSON 对象')
    return value


class RepositoryFiles:
    """只操作 worktree 内的源码；拒绝目录穿越、符号链接、Git 元数据和密钥文件。"""
    def __init__(self, worktree):
        self.root = Path(worktree).resolve()
        self.read_paths = set()
        self.read_requests = []
        self.files = []

    def path(self, name):
        if not isinstance(name, str) or not name:
            raise ValueError('文件路径不能为空')
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('只能操作仓库内的相对路径')
        if any(p in ('.git', 'node_modules', '.venv', 'venv', 'dist', '.npmrc', '.pypirc') or p == '.env' or p.startswith('.env.') or p.endswith(('.pem', '.key', '.p12')) for p in relative.parts):
            raise ValueError('禁止读取或修改依赖、构建产物、Git 元数据或密钥文件')
        path = self.root
        for part in relative.parts:
            path = path / part
            if path.is_symlink():
                raise ValueError('禁止通过符号链接读写文件')
        if not path.resolve().is_relative_to(self.root):
            raise ValueError('文件路径越过仓库边界')
        return path

    async def initialize(self):
        output = await git(self.root, 'ls-files', '-z')
        for name in output.split('\0'):
            if not name:
                continue
            try:
                self.path(name)
            except ValueError:
                continue
            self.files.append(name)

    def read(self, name, start=1, count=200):
        path = self.path(name)
        if not path.is_file() or path.stat().st_size > 2_000_000:
            raise ValueError('文件不存在或超过 2MB，请选择源码文件')
        text = path.read_text(encoding='utf-8')
        if '\0' in text:
            raise ValueError('不能读取二进制文件')
        start, count = int(start), int(count)
        if start < 1 or count < 1 or count > 300:
            raise ValueError('读取起始行必须大于 0，每次最多 300 行')
        lines = text.splitlines()
        if start > len(lines) and lines:
            raise ValueError(f'读取起始行超过文件总行数 {len(lines)}')
        excerpt = '\n'.join(f'{i + 1}: {lines[i]}' for i in range(start - 1, min(len(lines), start - 1 + count)))
        self.read_paths.add(name)
        if Path(name).name != 'AGENTS.md':
            request = {'action': 'read', 'path': name, 'start_line': start, 'count': count}
            if request in self.read_requests:
                self.read_requests.remove(request)
            self.read_requests.append(request)
        return f'{name}（共 {len(lines)} 行）\n{excerpt[:24000]}'

    def scoped_files(self, scope=''):
        if not isinstance(scope, str):
            raise ValueError('搜索范围必须是相对路径')
        scope = scope.rstrip('/')
        if scope in ('', '.'):
            return self.files
        self.path(scope)
        candidates = [name for name in self.files if name == scope or name.startswith(scope + '/')]
        if not candidates:
            raise ValueError(f'范围 {scope} 中没有可用文件，请先使用 list 浏览目录')
        return candidates

    def list_files(self, scope='', offset=0):
        names = self.scoped_files(scope)
        offset = max(0, int(offset))
        result = '\n'.join(names[offset:offset + 150])
        return f'共 {len(names)} 个文件；本页 offset={offset}\n{result}\n' + (f'下一页 offset={offset + 150}' if offset + 150 < len(names) else '已列完')

    async def search(self, query, scope='', regex=True):
        if not isinstance(query, str) or not query or len(query) > 200:
            raise ValueError('搜索文本长度必须为 1–200')
        candidates = self.scoped_files(scope)
        # Git grep 原生支持范围及扩展正则，避免在线程中执行无界 Python 正则。
        code, output = await run_process(
            ['git', 'grep', '--no-index', '-n', '-I', '-i', '-E' if regex else '-F', '-e', query, '--', *candidates],
            self.root, timeout=20,
        )
        if code == 1:
            return f'范围 {scope or "整个仓库"} 内没有匹配结果'
        if code:
            raise ValueError(f'搜索表达式无效：{output[-1000:]}')
        lines = output.splitlines()
        result = '\n'.join(line[:500] for line in lines[:60])
        if len(lines) > 60:
            result += f'\n（共 {len(lines)} 条，显示前 60 条，请缩小 path 范围）'
        return result

    def instructions(self):
        sections = []
        # 将仓库及各子目录的约束交给模型；实际路径检查由工具执行。
        for name in self.files:
            if Path(name).name == 'AGENTS.md':
                sections.append(self.read(name, count=300))
        return '\n\n'.join(sections)[:40000]

    def edit(self, edits):
        if not isinstance(edits, list) or not edits or len(edits) > 20:
            raise ValueError('每次需提交 1–20 处精确修改')
        pending = {}
        originals = {}
        for item in edits:
            name = item['path']
            path = self.path(name)
            before, after = item.get('before'), item.get('after')
            if not isinstance(after, str) or len(after) > 200000:
                raise ValueError('修改内容必须是有效文本且小于 200KB')
            if name not in pending:
                if path.exists():
                    if name not in self.read_paths:
                        raise ValueError(f'修改前必须读取 {name}')
                    if path.stat().st_size > 2_000_000:
                        raise ValueError('不支持修改超过 2MB 的文件')
                    originals[name] = path.read_text(encoding='utf-8')
                    pending[name] = originals[name]
                else:
                    if before is not None:
                        raise ValueError('新文件必须使用 before=null')
                    originals[name] = None
                    pending[name] = ''
            if before is None:
                if originals[name] is not None or pending[name]:
                    raise ValueError('不能使用新文件操作覆盖现有文件')
                pending[name] = after
            elif not isinstance(before, str) or not before or pending[name].count(before) != 1:
                raise ValueError(f'{name} 的 before 必须在当前文件中精确匹配一次')
            else:
                pending[name] = pending[name].replace(before, after, 1)
        # 所有替换先校验；写入失败恢复本批次文件，保留此前已完成的修改。
        written = []
        try:
            for name, content in pending.items():
                path = self.path(name)
                path.parent.mkdir(parents=True, exist_ok=True)
                written.append(name)
                path.write_text(content, encoding='utf-8')
        except OSError:
            for name in written:
                path = self.path(name)
                if originals[name] is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(originals[name], encoding='utf-8')
            raise
        for name in pending:
            if name not in self.files:
                self.files.append(name)
        return '已应用修改：' + ', '.join(pending)


async def repository_agent(llm, worktree, prompt, writable=False, on_operation=None, image_urls=None, images_on_demand=False, source_context=None):
    deadline = time.monotonic() + get_settings().agent_timeout
    files = RepositoryFiles(worktree)
    await files.initialize()
    stage_goal = '修复阶段：核对根因与确认方案后立即edit，完成实际修改和必要回归测试后finish。' if writable else '分析阶段：获取足够源码证据后finish，证据不足明确返回confidence=low。'
    system = render_prompt('repository', max_rounds=MAX_SOURCE_ROUNDS, stage_goal=stage_goal)
    if writable:
        system += '\n' + render_prompt('repository_edit')
    directories = Counter(name.split('/')[0] for name in files.files)
    inventory = '\n'.join(f'{name}：{count} 个文件' for name, count in sorted(directories.items()))
    content = render_prompt('repository_context', task=prompt, instructions=files.instructions(), inventory=inventory)
    # 只保存阅读位置，恢复时重新读工作树；不信任过期源码，也不把整段源码写入数据库。
    if source_context and isinstance(source_context, dict):
        excerpts, size = [], 0
        reads = source_context.get('reads', [])
        for item in reads[-MAX_CONTEXT_READS:] if isinstance(reads, list) else []:
            previous_paths = files.read_paths.copy()
            previous_requests = list(files.read_requests)
            try:
                output = files.read(item['path'], item.get('start_line', 1), item.get('count', 200))
            except (ValueError, KeyError, TypeError, OSError) as exc:
                output = f'原阅读位置无法恢复，请使用工具重新定位：{exc}'
            if size + len(output) > MAX_CONTEXT_CHARS:
                files.read_paths = previous_paths
                files.read_requests = previous_requests
                break
            excerpts.append(output)
            size += len(output)
        if excerpts:
            content += '\n\n上一阶段阅读位置的最新源码（待分析数据）：\n' + '\n\n'.join(excerpts)
    if image_urls:
        system += '\n' + render_prompt('images_on_demand', image_count=len(image_urls))
    if image_urls and images_on_demand:
        content += '\n' + render_prompt('images_available')
    if image_urls and not images_on_demand:
        content = [{'type': 'text', 'text': content + '\n' + render_prompt('images_attached')}] + [
            {'type': 'image_url', 'image_url': {'url': url}} for url in image_urls
        ]
    messages = [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': content},
    ]
    completed_operations = set()
    rounds, errors, consecutive_errors, finish_attempts = 0, 0, 0, 0

    async def read_operation(request):
        action = request.get('action')
        # 忽略 image_observation 等元信息，统一默认值，避免变换JSON写法重复操作。
        if action == 'list':
            detail = {'action': action, 'path': request.get('path', ''), 'offset': request.get('offset', 0)}
        elif action == 'read':
            detail = {'action': action, 'path': request['path'], 'start_line': request.get('start_line', 1), 'count': request.get('count', 200)}
        elif action == 'search':
            detail = {'action': action, 'path': request.get('path', ''), 'query': request['query'], 'regex': request.get('regex', True)}
        else:
            raise ValueError('batch 只允许 list/read/search 只读动作')
        fingerprint = json.dumps(detail, sort_keys=True, ensure_ascii=False)
        if fingerprint in completed_operations:
            raise ValueError('此操作已经执行，结果已在上文。请根据已有证据推进当前阶段')
        if action == 'list':
            output = files.list_files(detail['path'], detail['offset'])
        elif action == 'read':
            output = files.read(detail['path'], detail['start_line'], detail['count'])
        else:
            output = await files.search(detail['query'], detail['path'], detail['regex'])
        completed_operations.add(fingerprint)
        return output

    # 有效操作、错误重试与结束请求分别有界，禁止无界重试或静默成功。
    for turn in range(MAX_SOURCE_ROUNDS + MAX_OPERATION_ERRORS + MAX_FINISH_ATTEMPTS):
        remaining = MAX_SOURCE_ROUNDS - rounds
        if remaining == 0:
            finish_attempts += 1
            if finish_attempts > MAX_FINISH_ATTEMPTS:
                break
        guidance = render_prompt('finish_reminder', remaining=remaining, stage_goal=stage_goal)
        if remaining == 0:
            guidance += '\n有效源码操作额度已耗尽，仅允许 finish；不能再读取或修改。未完成请返回blocked_reason。'
        messages.append({'role': 'user', 'content': guidance})
        received, last_progress = 0, 0.0
        response_diagnostics = {}
        log_context = f'{Path(worktree).name}/{"fix" if writable else "analyze"}/{turn + 1}'
        async def report_chunk(chunk):
            nonlocal received, last_progress
            received += len(chunk)
            if on_operation and time.monotonic() - last_progress >= 4:
                last_progress = time.monotonic()
                await on_operation(f'模型请求 {turn + 1}：正在流式生成，已接收 {received} 字符；有效源码操作 {rounds}/{MAX_SOURCE_ROUNDS}，失败 {errors}/{MAX_OPERATION_ERRORS}')
        try:
            # 每次传入独立消息快照；看图响应之后移除原图，保留文字观察。
            time_left = deadline - time.monotonic()
            if time_left <= 0:
                raise ValueError('当前 Agent 源码阶段超过总执行时限；已有修改保留在工作树')
            response = await asyncio.wait_for(llm.ainvoke(list(messages), temperature=0.0, response_format={'type': 'json_object'}, on_chunk=report_chunk, on_response=response_diagnostics.update, log_context=log_context), timeout=min(120, time_left))
        except asyncio.TimeoutError as exc:
            if time_left < 120:
                raise ValueError('当前 Agent 源码阶段超过总执行时限；已有修改保留在工作树') from exc
            raise ValueError(f'模型请求超过 120 秒，当前提供商未及时响应；有效源码操作 {rounds}/{MAX_SOURCE_ROUNDS}，已有修改保留在工作树') from exc
        had_images = any(isinstance(message['content'], list) for message in messages)
        messages.append({'role': 'assistant', 'content': response})
        if had_images:
            messages = [
                {**message, 'content': '\n'.join(part['text'] if part['type'] == 'text' else '原图已从请求卸载；需要核对时使用 view_image。' for part in message['content'])}
                if isinstance(message['content'], list) else message for message in messages
            ]
        request = {}
        parsed = False
        terminal_error = None
        try:
            request = parse_json(response)
            parsed = True
            if had_images and (not isinstance(request.get('image_observation'), str) or not request['image_observation'].strip()):
                raise ValueError('看图响应必须包含 image_observation；请再次使用 view_image 核对并记录可见事实')
            action = request.get('action')
            if action == 'finish':
                result = request.get('result')
                if not isinstance(result, dict):
                    raise ValueError('result 必须是 JSON 对象')
                if result.get('blocked_reason'):
                    terminal_error = f"Agent 无法完成当前阶段：{str(result['blocked_reason'])[:2000]}；已有修改保留在工作树"
                    raise ValueError(terminal_error)
                if not files.read_paths - {name for name in files.read_paths if Path(name).name == 'AGENTS.md'}:
                    raise ValueError('必须先读取相关源码再结束')
                if writable and not await changed_files(worktree):
                    raise ValueError('没有产生实际代码修改，不能finish宣称完成；请先edit，无法修复返回blocked_reason')
                return {**result, 'source_context': {'reads': files.read_requests[-MAX_CONTEXT_READS:]}}
            if remaining == 0:
                terminal_error = f'已达到 {MAX_SOURCE_ROUNDS} 轮有效源码操作上限，未完成结束确认；已有修改保留在工作树'
                raise ValueError(terminal_error)
            if action == 'view_image':
                index = request.get('image')
                if type(index) is not int or not image_urls or not 1 <= index <= len(image_urls):
                    raise ValueError('图片编号无效，请使用已有原图的编号')
                output = [
                    {'type': 'text', 'text': render_prompt('image_caption', image=index)},
                    {'type': 'image_url', 'image_url': {'url': image_urls[index - 1]}},
                ]
            elif action in ('list', 'read', 'search'):
                output = await read_operation(request)
            elif action == 'batch':
                operations = request.get('operations')
                if not isinstance(operations, list) or not 1 <= len(operations) <= MAX_BATCH_OPERATIONS:
                    raise ValueError(f'batch 需要 1–{MAX_BATCH_OPERATIONS} 个独立只读操作')
                if any(not isinstance(item, dict) or item.get('action') not in ('list', 'read', 'search') for item in operations):
                    raise ValueError('batch 只允许 list/read/search 只读动作，禁止写入、嵌套或结束')
                outputs = []
                succeeded = 0
                for index, item in enumerate(operations, 1):
                    try:
                        part = await read_operation(item)
                        succeeded += 1
                    except (ValueError, KeyError, TypeError, OSError) as exc:
                        part = f'操作失败：{exc}'
                    outputs.append(f'操作 {index}：{json.dumps(item, ensure_ascii=False)}\n{part}')
                output = '\n\n'.join(outputs)
                if not succeeded:
                    raise ValueError(output)
            elif action == 'edit' and writable:
                output = files.edit(request['edits'])
            else:
                raise ValueError('不支持的动作')
            if action == 'edit':
                completed_operations.clear()
            rounds += 1
            consecutive_errors = 0
        except (ValueError, KeyError, TypeError, OSError) as exc:
            if not parsed:
                diagnostic = f'模型响应解析失败：{log_context}；{json.dumps(response_diagnostics or response_summary(response), ensure_ascii=False)}'
                logger.warning(diagnostic)
                if on_operation:
                    await on_operation(diagnostic)
            errors += 1
            consecutive_errors += 1
            output = f'操作失败：{exc}。请修正后重试。'
        detail = {key: request[key] for key in ('action', 'image', 'path', 'query', 'regex', 'start_line', 'count', 'offset') if key in request}
        status = '返回所选原图，仅下一次请求携带' if isinstance(output, list) else output[:500] if output.startswith('操作失败') else f'返回 {len(output)} 字符'
        if request.get('action') == 'batch' and not output.startswith('操作失败'):
            status += f'，批量操作 {succeeded}/{len(operations)} 成功'
        if request.get('action') == 'batch':
            detail['operations'] = request.get('operations')
        operation = f'源码操作 {rounds}/{MAX_SOURCE_ROUNDS}（请求 {turn + 1}，失败 {errors}/{MAX_OPERATION_ERRORS}）：{json.dumps(detail, ensure_ascii=False)}；{status}'
        logger.info(operation)
        if on_operation:
            await on_operation(operation)
        messages.append({'role': 'user', 'content': output})
        if terminal_error:
            raise ValueError(terminal_error)
        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS or errors >= MAX_OPERATION_ERRORS:
            raise ValueError(f'连续 {consecutive_errors} 次或累计 {errors} 次操作失败，已停止；最后错误：{output}；已有修改保留在工作树')
    raise ValueError(f'已达到 {MAX_SOURCE_ROUNDS} 轮有效源码操作上限，未完成结束确认；已有修改保留在工作树，请查看源码操作日志')


async def changed_files(worktree):
    tracked = await git(worktree, 'diff', '--name-only', '-z', 'HEAD')
    new = await git(worktree, 'ls-files', '--others', '--exclude-standard', '-z')
    return sorted({name for name in (tracked + '\0' + new).split('\0') if name})


async def verify_worktree(worktree, command):
    if not command or not command.strip():
        raise ValueError('代码修改已保留，但未配置验证命令；请在项目管理填写后重新执行')
    code, output = await run_process(['git', 'diff', '--check', 'HEAD'], worktree)
    if code:
        return {'passed': False, 'command': 'git diff --check', 'exit_code': code, 'output': output[-20000:]}
    # 只执行用户保存的命令；模型不具备 shell 工具。
    code, output = await run_process(['/bin/sh', '-c', command], worktree, get_settings().agent_timeout)
    return {'passed': code == 0, 'command': command, 'exit_code': code, 'output': output[-20000:]}


async def verify_and_commit_repair(worktree, command, bug_id, repair_branch, base_commit):
    """系统提交与验证完全相同的树，模型不能提交或变更交付对象。"""
    if not repair_branch or not base_commit:
        raise ValueError('修复分支或基础提交记录缺失，请重新执行')
    if await git(worktree, 'branch', '--show-current') != repair_branch:
        raise ValueError('修复工作树分支已变化，请重新执行')
    head = await git(worktree, 'rev-parse', 'HEAD')
    if head != base_commit:
        raise ValueError('修复工作树基础提交已变化，请重新执行')

    async def stage_tree():
        names = await changed_files(worktree)
        if not names:
            raise ValueError('没有实际修改，无法生成已验证提交')
        files = RepositoryFiles(worktree)
        for name in names:
            files.path(name)
        await git(worktree, '--literal-pathspecs', 'add', '-A', '--', *names)
        return names, await git(worktree, 'write-tree')

    names, tree = await stage_tree()
    result = await verify_worktree(worktree, command)
    if not result['passed']:
        return {'test_results': result}
    current_names, current_tree = await stage_tree()
    if current_tree != tree or current_names != names or await git(worktree, 'rev-parse', 'HEAD') != head:
        raise ValueError('验证期间源码或提交发生变化，不能交付；请重新执行验证')
    # commit-tree 固定已验证的树，不运行可能修改内容的提交钩子，也不依赖用户全局身份。
    commit = await git(worktree, '-c', 'user.name=Bug Repair Agent', '-c', 'user.email=bug-agent@localhost',
                       'commit-tree', tree, '-p', head, '-m', f'fix: repair bug {bug_id}')
    await git(worktree, 'update-ref', f'refs/heads/{repair_branch}', commit, head)
    if await git(worktree, 'status', '--porcelain'):
        raise ValueError('生成提交后工作树发生变化，已保留提交但不能进入验收')
    return {'test_results': result, 'git_commit_hash': commit, 'modified_files': names}
