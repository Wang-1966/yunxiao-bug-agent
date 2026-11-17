"""Pi 原生工具的业务权限适配；不实现文件编辑或搜索算法。"""
from .agent_tools import (COMMAND_DEFAULT_TIMEOUT, COMMAND_MAX_TIMEOUT,
                          ToolError, command_env, effective_denies, sandbox_profile)

READ_TOOLS = {'read', 'find', 'ls'}


class PiTools:
    def __init__(self, workspace, tools, *, commands=True):
        self.workspace = workspace
        self.names = ['read', 'ls', 'find']
        if workspace.mode != 'read':
            self.names += ['edit', 'write']
            if commands:
                self.names.append('bash')
        self.host_tools = tools


    def config(self):
        workspace = self.workspace
        skill_paths = []
        for name in ('.pi/skills', '.agents/skills'):
            try:
                path = workspace.resolve(name)
            except ToolError:
                continue
            if path.is_dir():
                skill_paths.append(str(path))
        return dict(nativeTools=self.names, commandEnv=command_env(), skillPaths=skill_paths,
                    commandSandbox=sandbox_profile(workspace.root, effective_denies(workspace.root, workspace.deny_read))
                    if 'bash' in self.names else None)

    def permit(self, name, args):
        if name not in self.names:
            raise ToolError(f'当前阶段没有启用 Pi 工具：{name}')
        args = dict(args)
        if name == 'bash':
            if self.workspace.mode == 'read':
                raise ToolError('讨论阶段不能执行命令')
            timeout = args.get('timeout', COMMAND_DEFAULT_TIMEOUT)
            if not isinstance(timeout, (int, float)) or not 0 < timeout <= COMMAND_MAX_TIMEOUT:
                raise ToolError(f'timeout 必须为 1–{COMMAND_MAX_TIMEOUT} 秒')
            args['timeout'] = timeout
        else:
            path = args.get('path') or '.'
            if name in ('edit', 'write'):
                relative = self.workspace.assert_editable(path)
                target = self.workspace.resolve(relative, write=True)
                if target.exists() and relative not in self.workspace.read_paths:
                    raise ToolError('修改已有文件前必须先用 read 读取文件')
            else:
                target = self.workspace.resolve(path)
                if name == 'read' and not target.is_file():
                    # 禁止 SDK 的文件名猜测跨过已经校验的路径（例如猜中另一个符号链接）。
                    raise ToolError(f'文件不存在：{path}')
            args['path'] = str(target)
        return args

    def completed(self, name, args):
        if name == 'read':
            self.workspace.record_read(args['path'], args.get('offset') or 1, args.get('limit') or 2000)
        elif name in ('edit', 'write'):
            self.workspace.touch(self.workspace.rel(self.workspace.resolve(args['path'])))
