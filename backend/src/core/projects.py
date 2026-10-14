"""按云效项目 ID 解析修复配置，保留 Bug 自身的仓库覆盖配置。"""
from urllib.parse import urlparse, unquote
import json
from pathlib import Path
import asyncio


project_configuration_lock = asyncio.Lock()


def project_id_from_url(url):
    if not url:
        return None
    parsed = urlparse(url)
    parts = parsed.path.split('/')
    if parsed.hostname == 'devops.aliyun.com' and len(parts) >= 6 and parts[1:3] == ['projex', 'project'] and parts[4] == 'bug':
        return unquote(parts[3]) or None
    return None


async def resolve_project_config(db, bug):
    """不改写 Bug 数据；执行时使用最新项目绑定。"""
    result = dict(bug)
    project_id = result.get('source_project_id')
    if result.get('source') == 'yunxiao' and not project_id:
        project_id = project_id_from_url(result.get('source_url'))
    result['source_project_id'] = project_id
    if project_id:
        cursor = await db.execute('SELECT repo_path, branch, test_command, preview_command, preview_env_files, preview_port FROM projects WHERE project_id=?', (project_id,))
        row = await cursor.fetchone()
        if row:
            if not result.get('repo_path'):
                result.update(repo_path=row[0], branch=result.get('branch') or row[1], test_command=row[2])
            if Path(result['repo_path']).expanduser().resolve() == Path(row[0]).expanduser().resolve():
                result.update(preview_command=row[3] or '', preview_env_files=json.loads(row[4] or '[]'), preview_port=row[5])
    return result
