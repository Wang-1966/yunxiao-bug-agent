import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize('version', [(3, 9, 6), (3, 10, 16), (3, 11, 16), (3, 12, 13)])
def test_backend_checks_python_version_before_importing_dependencies(version):
    package = Path(__file__).resolve().parents[1] / 'src' / '__init__.py'
    # 独立进程模拟版本，避免改变测试进程及第三方依赖看到的 sys.version_info。
    result = subprocess.run(
        [sys.executable, '-c', f'import sys, runpy; sys.version_info = {version!r}; runpy.run_path({str(package)!r})'],
        capture_output=True, text=True,
    )
    if version < (3, 11):
        assert result.returncode != 0
        assert 'Python 3.11+' in result.stderr
        assert '.'.join(map(str, version)) in result.stderr
        assert 'venv' in result.stderr
    else:
        assert result.returncode == 0, result.stderr
