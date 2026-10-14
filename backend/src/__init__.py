"""后端运行环境要求；在导入应用和第三方依赖之前检查。"""
import sys


if sys.version_info < (3, 11):
    version = '.'.join(map(str, sys.version_info[:3]))
    raise RuntimeError(
        f'后端要求 Python 3.11+，当前为 {version}（{sys.executable}）。'
        '请使用 Python 3.11+ 创建 backend/venv，'
        '并在 backend 目录执行 venv/bin/python -m src.main。'
    )
