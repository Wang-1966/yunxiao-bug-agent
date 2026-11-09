"""执行内核配置；只允许无运行任务时切换，旧会话文件分别保留。"""
import json
import os
import tempfile
from pathlib import Path

from ..config import get_settings


def config_path():
    return Path(get_settings().database_url.replace('sqlite+aiosqlite:///', '')).parent / 'agent_runtime.json'


def engine():
    path = config_path()
    if path.exists():
        value = json.loads(path.read_text())['engine']
        if value not in ('native', 'pi'):
            raise ValueError('无效的执行内核配置')
        return value
    return get_settings().agent_engine


def save_engine(value):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            json.dump({'engine': value}, file)
            file.flush()
            os.fsync(file.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
