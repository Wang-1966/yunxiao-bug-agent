import runpy
from pathlib import Path
from unittest.mock import patch

from uvicorn import Config
from uvicorn.supervisors.watchfilesreload import FileFilter

from src.config import get_settings


def test_server_reload_excludes_new_worktree_dependencies(tmp_path, monkeypatch):
    worktrees = tmp_path / 'custom-worktrees'
    monkeypatch.setattr(get_settings(), 'worktrees_dir', str(worktrees))
    with patch('uvicorn.run') as run:
        runpy.run_module('src.main', run_name='__main__')
    config = Config(run.call_args.args[0], **run.call_args.kwargs)
    # 排除目录在启动时就必须存在，否则 Uvicorn 将它当作单文件模式。
    assert worktrees.is_dir()
    include = FileFilter(config)
    assert not include(worktrees / 'repair-one/node_modules/.pnpm/katex/src/metrics/parse_tfm.py')
    assert include(Path(__file__).resolve().parents[1] / 'src/main.py')
    assert config.reload_dirs == [Path(__file__).resolve().parents[1] / 'src']
