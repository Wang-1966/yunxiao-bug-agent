"""Pi 宿主协议：阶段提交工具、参数归一化和会话路径。"""
import re
from pathlib import Path
from ..config import get_settings
from .agent_tools import Tool

_UNICODE_ESCAPE = re.compile(r'\\u([0-9a-fA-F]{4})')

def repair_unicode_escapes(value):
    """把工具参数里被多转义一次的 \\uXXXX 还原成字符。正常中文不含这个序列。"""
    if isinstance(value, str):
        return _UNICODE_ESCAPE.sub(lambda match: chr(int(match.group(1), 16)), value) if '\\u' in value else value
    if isinstance(value, list):
        return [repair_unicode_escapes(item) for item in value]
    if isinstance(value, dict):
        return {key: repair_unicode_escapes(item) for key, item in value.items()}
    return value

def submit_tool(name: str, description: str, parameters: dict, validator) -> Tool:
    """提交工具：validator(args) 抛 ToolError 表示拒绝并把原因返回给模型，否则返回结果对象。"""
    return Tool(name, description, parameters, validator, readonly=False, terminal=True)

def session_path(worktree) -> Path:
    base = Path(get_settings().database_url.replace('sqlite+aiosqlite:///', '')).parent / 'agent_sessions'
    return base / f'{Path(worktree).name}.json'

