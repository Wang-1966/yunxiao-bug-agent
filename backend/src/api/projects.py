"""项目仓库绑定管理。"""
import asyncio
import json
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..config import get_settings
from ..core.preview_manager import validate_preview_config, start_project, stop_project, project_status, check_project_change
from ..core.projects import project_configuration_lock

router = APIRouter(prefix='/api/projects', tags=['Projects'])


def get_db_path():
    return get_settings().database_url.replace('sqlite+aiosqlite:///', '')


class ProjectConfig(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    repo_path: str = Field(min_length=1, max_length=2000)
    branch: str = Field(min_length=1, max_length=300)
    test_command: str = Field(default='', max_length=2000)
    preview_command: str = Field(default='', max_length=2000)
    preview_env_files: list[str] = Field(default_factory=list, max_length=20)
    preview_port: int | None = Field(default=None, ge=1, le=65535)


async def git_output(path, *args):
    process = await asyncio.create_subprocess_exec('git', '-C', str(path), *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=10)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()
        raise
    return process.returncode, output.decode().strip()


async def validate_repository(repo_path: str) -> Path:
    try:
        path = Path(repo_path.strip()).expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise HTTPException(422, '请填写后端所在机器上存在的仓库绝对路径')
        path = path.resolve()
        code, root = await git_output(path, 'rev-parse', '--show-toplevel')
        if code or Path(root).resolve() != path:
            raise HTTPException(422, '路径必须是 Git 仓库根目录')
    except (OSError, ValueError, asyncio.TimeoutError):
        raise HTTPException(422, '无法检查 Git 仓库，请检查路径及 Git 安装')
    return path


@router.get('/branches')
async def list_branches(repo_path: str = Query(min_length=1, max_length=2000)):
    path = await validate_repository(repo_path)
    try:
        code, output = await git_output(path, 'for-each-ref', '--sort=refname', '--format=%(refname:lstrip=2)', 'refs/heads/')
        if code:
            raise HTTPException(422, '无法读取本地分支，请检查仓库后重试')
        branches = output.splitlines() if output else []
        code, current = await git_output(path, 'symbolic-ref', '--quiet', 'HEAD')
        if code not in (0, 1):
            raise HTTPException(422, '无法读取当前分支，请检查仓库后重试')
        current = current.removeprefix('refs/heads/')
        current_branch = current if code == 0 and current in branches else None
        if current_branch:
            branches.remove(current_branch)
            branches.insert(0, current_branch)
        return {'repo_path': str(path), 'current_branch': current_branch, 'branches': branches}
    except (OSError, asyncio.TimeoutError):
        raise HTTPException(422, '无法读取本地分支，请检查 Git 安装或稍后重试')


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
                   COALESCE(known.bug_count, 0) AS bug_count, p.repo_path, p.branch, p.test_command, p.preview_command, p.preview_env_files, p.preview_port,
                   p.updated_at
            FROM ids LEFT JOIN known USING(project_id) LEFT JOIN projects p USING(project_id)
            ORDER BY name, ids.project_id
        ''')
        results = [dict(row) for row in await cursor.fetchall()]
        for project in results:
            project['preview_env_files'] = json.loads(project['preview_env_files'] or '[]')
        return results


@router.put('/{project_id}')
async def save_project(project_id: str, config: ProjectConfig):
    if not project_id.strip() or not config.name.strip():
        raise HTTPException(422, '项目 ID 和名称不能为空')
    path = await validate_repository(config.repo_path)
    try:
        validate_preview_config(config.preview_command, config.preview_env_files)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    try:
        branch = config.branch.strip()
        code, _ = await git_output(path, 'rev-parse', '--verify', '--end-of-options', f'{branch}^{{commit}}')
        if not branch or code:
            raise HTTPException(422, '分支或 Git 引用不存在，请填写仓库中的有效分支')
    except (OSError, asyncio.TimeoutError):
        raise HTTPException(422, '无法检查 Git 仓库，请检查路径及 Git 安装')
    async with project_configuration_lock, aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute('SELECT * FROM projects WHERE project_id=?', (project_id,))).fetchone()
        previous = dict(row) if row else None
        updated = {**config.model_dump(), 'repo_path': str(path)}
        for field in ('preview_port', 'preview_command', 'preview_env_files'):
            if previous and field not in config.model_fields_set:
                updated[field] = previous[field]
        try:
            await check_project_change(previous, updated)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        await db.execute('''INSERT INTO projects (project_id, name, repo_path, branch, test_command, preview_command, preview_env_files, preview_port)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(project_id) DO UPDATE SET
            name=excluded.name, repo_path=excluded.repo_path, branch=excluded.branch,
            test_command=excluded.test_command,
            preview_command=CASE WHEN ? THEN excluded.preview_command ELSE projects.preview_command END,
            preview_env_files=CASE WHEN ? THEN excluded.preview_env_files ELSE projects.preview_env_files END,
            preview_port=CASE WHEN ? THEN excluded.preview_port ELSE projects.preview_port END,
            updated_at=CURRENT_TIMESTAMP''',
            (project_id, config.name.strip(), str(path), branch, config.test_command.strip(), config.preview_command.strip(), json.dumps(config.preview_env_files), config.preview_port,
             'preview_command' in config.model_fields_set, 'preview_env_files' in config.model_fields_set, 'preview_port' in config.model_fields_set))
        await db.commit()
    return {'message': '项目配置已保存'}


@router.delete('/{project_id}')
async def unbind_project(project_id: str):
    async with project_configuration_lock, aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute('SELECT * FROM projects WHERE project_id=?', (project_id,))).fetchone()
        try:
            await check_project_change(dict(row) if row else None, None)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        await db.execute('DELETE FROM projects WHERE project_id=?', (project_id,))
        await db.commit()
    return {'message': '仓库绑定已解除，Bug 已保留'}


async def development_config(project_id):
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute('SELECT * FROM projects WHERE project_id=?', (project_id,))).fetchone()
    if not row:
        raise HTTPException(404, '项目尚未绑定仓库')
    return dict(row)


@router.get('/{project_id}/development')
async def get_development(project_id: str):
    async with project_configuration_lock:
        config = await development_config(project_id)
        return await project_status(config['repo_path'])


@router.post('/{project_id}/development')
async def start_development(project_id: str):
    async with project_configuration_lock:
        config = await development_config(project_id)
        path = await validate_repository(config['repo_path'])
        try:
            return await start_project(str(path), config['preview_command'], config['preview_port'])
        except (ValueError, OSError) as exc:
            raise HTTPException(409, str(exc))


@router.delete('/{project_id}/development')
async def stop_development(project_id: str):
    async with project_configuration_lock:
        config = await development_config(project_id)
        return await stop_project(config['repo_path'])
