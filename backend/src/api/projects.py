"""项目仓库绑定管理。"""
import asyncio
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..config import get_settings

router = APIRouter(prefix='/api/projects', tags=['Projects'])


def get_db_path():
    return get_settings().database_url.replace('sqlite+aiosqlite:///', '')


class ProjectConfig(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    repo_path: str = Field(min_length=1, max_length=2000)
    branch: str = Field(min_length=1, max_length=300)
    test_command: str = Field(default='', max_length=2000)


async def git_output(path, *args):
    process = await asyncio.create_subprocess_exec('git', '-C', str(path), *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=10)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        process.kill()
        await process.wait()
        raise
    return process.returncode, output.decode().strip()


@router.get('')
async def list_projects():
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute('''
            WITH known AS (
                SELECT source_project_id AS project_id, MAX(source_project) AS name, COUNT(*) AS bug_count
                FROM bugs WHERE source='yunxiao' AND source_project_id IS NOT NULL
                GROUP BY source_project_id
            ), ids AS (
                SELECT project_id FROM known UNION SELECT project_id FROM projects
            )
            SELECT ids.project_id, COALESCE(known.name, p.name, ids.project_id) AS name,
                   COALESCE(known.bug_count, 0) AS bug_count, p.repo_path, p.branch, p.test_command,
                   p.updated_at
            FROM ids LEFT JOIN known USING(project_id) LEFT JOIN projects p USING(project_id)
            ORDER BY name, ids.project_id
        ''')
        return [dict(row) for row in await cursor.fetchall()]


@router.put('/{project_id}')
async def save_project(project_id: str, config: ProjectConfig):
    if not project_id.strip() or not config.name.strip():
        raise HTTPException(422, '项目 ID 和名称不能为空')
    path = Path(config.repo_path.strip()).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise HTTPException(422, '请填写后端所在机器上存在的仓库绝对路径')
    path = path.resolve()
    try:
        code, root = await git_output(path, 'rev-parse', '--show-toplevel')
        if code or Path(root).resolve() != path:
            raise HTTPException(422, '路径必须是 Git 仓库根目录')
        branch = config.branch.strip()
        code, _ = await git_output(path, 'rev-parse', '--verify', '--end-of-options', f'{branch}^{{commit}}')
        if not branch or code:
            raise HTTPException(422, '分支或 Git 引用不存在，请填写仓库中的有效分支')
    except (OSError, asyncio.TimeoutError):
        raise HTTPException(422, '无法检查 Git 仓库，请检查路径及 Git 安装')
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute('''INSERT INTO projects (project_id, name, repo_path, branch, test_command)
            VALUES (?, ?, ?, ?, ?) ON CONFLICT(project_id) DO UPDATE SET
            name=excluded.name, repo_path=excluded.repo_path, branch=excluded.branch,
            test_command=excluded.test_command, updated_at=CURRENT_TIMESTAMP''',
            (project_id, config.name.strip(), str(path), branch, config.test_command.strip()))
        await db.commit()
    return {'message': '项目配置已保存'}


@router.delete('/{project_id}')
async def unbind_project(project_id: str):
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute('DELETE FROM projects WHERE project_id=?', (project_id,))
        await db.commit()
    return {'message': '仓库绑定已解除，Bug 已保留'}
