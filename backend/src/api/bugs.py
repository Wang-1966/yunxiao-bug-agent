"""Bug API 路由"""
from fastapi import APIRouter, HTTPException, Query
from loguru import logger
import aiosqlite
import json
from typing import Optional

from ..models.bug import Bug, BugCreate, BugUpdate, BugListResponse, BugSupplementUpdate
from ..config import get_settings

router = APIRouter(prefix="/api/bugs", tags=["Bugs"])
settings = get_settings()
HIDDEN_YUNXIAO_STATUSES = ("已关闭", "暂不修复", "已修复")
VISIBLE_BUGS_CONDITION = "(source != 'yunxiao' OR source_status IS NULL OR source_status NOT IN (?, ?, ?))"


def get_db_path() -> str:
    """获取数据库路径"""
    return settings.database_url.replace("sqlite+aiosqlite:///", "")


@router.get("", response_model=BugListResponse)
async def list_bugs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    priority: Optional[str] = None,
    search: Optional[str] = None,
    exclude_closed_yunxiao: bool = False,
    source_status: Optional[str] = None,
):
    """获取 Bug 列表"""
    try:
        db_path = get_db_path()
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row

            # 构建查询条件
            conditions = []
            params = []

            if exclude_closed_yunxiao:
                conditions.append(VISIBLE_BUGS_CONDITION)
                params.extend(HIDDEN_YUNXIAO_STATUSES)

            if source_status:
                conditions.append("source = 'yunxiao' AND source_status = ?")
                params.append(source_status)

            if status:
                conditions.append("status = ?")
                params.append(status)
            if priority:
                conditions.append("priority = ?")
                params.append(priority)
            if search:
                conditions.append("(title LIKE ? OR description LIKE ?)")
                params.extend([f"%{search}%", f"%{search}%"])

            where_clause = " AND ".join(conditions) if conditions else "1=1"

            # 获取总数
            count_query = f"SELECT COUNT(*) as total FROM bugs WHERE {where_clause}"
            cursor = await db.execute(count_query, params)
            row = await cursor.fetchone()
            total = row[0]

            # 获取分页数据
            offset = (page - 1) * page_size
            query = f"""
                SELECT * FROM bugs
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """
            cursor = await db.execute(query, params + [page_size, offset])
            rows = await cursor.fetchall()

            bugs = [Bug(**dict(row)) for row in rows]

            return BugListResponse(
                total=total,
                items=bugs,
                page=page,
                page_size=page_size
            )

    except Exception as e:
        logger.error(f"Failed to list bugs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats/dashboard")
async def dashboard_stats():
    try:
        async with aiosqlite.connect(get_db_path()) as db:
            cursor = await db.execute(f"SELECT COUNT(*), COUNT(CASE WHEN priority='critical' THEN 1 END) FROM bugs WHERE {VISIBLE_BUGS_CONDITION}", HIDDEN_YUNXIAO_STATUSES)
            total, critical = await cursor.fetchone()
            cursor = await db.execute("SELECT COUNT(*) FROM bugs WHERE status='resolved' AND resolved_at IS NOT NULL")
            resolved = (await cursor.fetchone())[0]
            return {"total": total, "critical": critical, "resolved": resolved}
    except Exception:
        logger.error("Failed to load dashboard statistics")
        raise HTTPException(status_code=500, detail="加载首页统计失败")


@router.get("/filters/source-statuses", response_model=list[str])
async def list_source_statuses():
    try:
        async with aiosqlite.connect(get_db_path()) as db:
            cursor = await db.execute(
                "SELECT DISTINCT source_status FROM bugs WHERE source = 'yunxiao' "
                "AND source_status IS NOT NULL AND source_status != '' "
                "AND source_status NOT IN (?, ?, ?) ORDER BY source_status",
                HIDDEN_YUNXIAO_STATUSES,
            )
            return [row[0] for row in await cursor.fetchall()]
    except Exception:
        logger.error("Failed to load Yunxiao status options")
        raise HTTPException(status_code=500, detail="加载云效状态失败")


@router.get("/{bug_id}", response_model=Bug)
async def get_bug(bug_id: str):
    """获取 Bug 详情"""
    try:
        db_path = get_db_path()
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM bugs WHERE bug_id = ?",
                (bug_id,)
            )
            row = await cursor.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail="Bug not found")

            from ..core.projects import resolve_project_config
            return Bug(**await resolve_project_config(db, dict(row)))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get bug: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{bug_id}/details")
async def get_bug_details(bug_id: str):
    bug = await get_bug(bug_id)
    result = bug.model_dump()
    if bug.source == "yunxiao":
        from ..tools.yunxiao.openapi_client import YunxiaoClient, YunxiaoError
        from ..core.bug_media import load_bug_media
        try:
            client = YunxiaoClient(settings)
            remote = await client.get_bug(bug_id)
            result["description"] = remote.get("description") or ""
            result["description_format"] = remote.get("formatType", "RICHTEXT")
            if result["description_format"] == "RICHTEXT":
                try:
                    rich_text = json.loads(result["description"])
                except (ValueError, TypeError):
                    rich_text = None
                if isinstance(rich_text, dict) and isinstance(rich_text.get("htmlValue"), str):
                    result["description"] = rich_text["htmlValue"]
            result["source_status"] = (remote.get("status") or {}).get("displayName") or (remote.get("status") or {}).get("name")
            result.update(await load_bug_media(client, bug_id, result["description"]))
        except YunxiaoError as exc:
            result["detail_warning"] = f"云效正文加载失败：{exc}。当前显示本地信息。"
    return result


@router.get('/{bug_id}/supplement')
async def get_bug_supplement(bug_id: str):
    from ..core.bug_inputs import read_supplement
    from ..core.image_storage import load_images
    async with aiosqlite.connect(get_db_path()) as db:
        if not await (await db.execute('SELECT 1 FROM bugs WHERE bug_id=?', (bug_id,))).fetchone():
            raise HTTPException(status_code=404, detail='Bug 不存在')
        supplement = await read_supplement(db, bug_id)
    images, warning, image_map = [], None, {}
    for index, name in enumerate(supplement['image_names']):
        try:
            image_map[index] = len(images)
            images.extend(load_images(get_db_path(), [name]))
        except ValueError:
            warning = '部分已保存的截图文件缺失，请重新上传后保存。补充说明和其他截图仍可编辑。'
            image_map[index] = None
    document = supplement.get('document')
    if document and warning:
        from ..core.rich_input import remap_document_images
        document = remap_document_images(document, image_map)
    return {'notes': supplement['notes'], 'images': images, 'revision': supplement['revision'],
            **({'document': document} if document else {}),
            **({'image_warning': warning} if warning else {})}


@router.put('/{bug_id}/supplement')
async def save_bug_supplement(bug_id: str, request: BugSupplementUpdate):
    from ..core.bug_inputs import read_supplement
    from ..core.image_storage import load_images
    from ..core.rich_input import save_rich_input
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute('BEGIN IMMEDIATE')
        if not await (await db.execute('SELECT 1 FROM bugs WHERE bug_id=?', (bug_id,))).fetchone():
            raise HTTPException(status_code=404, detail='Bug 不存在')
        current = await read_supplement(db, bug_id)
        if current['revision'] != request.revision:
            raise HTTPException(status_code=409, detail='补充信息已在其他页面更新，请重新加载后编辑')
        try:
            notes, names, document = save_rich_input(get_db_path(), request.notes, request.images, request.document, max_length=12000)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        revision = current['revision']
        if notes != current['notes'] or names != current['image_names'] or document != current.get('document'):
            revision += 1
            await db.execute(
                'INSERT INTO bug_supplements (bug_id, notes, image_names, revision, document) VALUES (?, ?, ?, ?, ?) '
                'ON CONFLICT(bug_id) DO UPDATE SET notes=excluded.notes, image_names=excluded.image_names, '
                'revision=excluded.revision, document=excluded.document, updated_at=CURRENT_TIMESTAMP',
                (bug_id, notes, json.dumps(names), revision, json.dumps(document, ensure_ascii=False) if document else None),
            )
        await db.commit()
    return {'notes': notes, 'images': load_images(get_db_path(), names), 'revision': revision,
            **({'document': document} if document else {})}


@router.post("", response_model=Bug)
async def create_bug(bug: BugCreate):
    """创建 Bug"""
    try:
        db_path = get_db_path()
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                """
                INSERT INTO bugs (bug_id, title, description, priority, status,
                                 source, source_url, repo_path, branch, test_command)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bug.bug_id, bug.title, bug.description, bug.priority,
                    "pending", bug.source, bug.source_url, bug.repo_path, bug.branch, bug.test_command
                )
            )
            await db.commit()

            logger.info(f"Bug created: {bug.bug_id}")
            return await get_bug(bug.bug_id)

    except Exception as e:
        logger.error(f"Failed to create bug: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{bug_id}", response_model=Bug)
async def update_bug(bug_id: str, update: BugUpdate):
    """更新 Bug"""
    try:
        db_path = get_db_path()
        async with aiosqlite.connect(db_path) as db:
            # 构建更新语句
            updates = []
            params = []

            if update.title is not None:
                updates.append("title = ?")
                params.append(update.title)
            if update.description is not None:
                updates.append("description = ?")
                params.append(update.description)
            if update.priority is not None:
                updates.append("priority = ?")
                params.append(update.priority)
            if update.status is not None:
                updates.append("status = ?")
                params.append(update.status)
            if update.assigned_agent_id is not None:
                updates.append("assigned_agent_id = ?")
                params.append(update.assigned_agent_id)
            if update.test_command is not None:
                updates.append('test_command = ?')
                params.append(update.test_command)

            if not updates:
                return await get_bug(bug_id)

            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.append(bug_id)

            query = f"UPDATE bugs SET {', '.join(updates)} WHERE bug_id = ?"
            await db.execute(query, params)
            await db.commit()

            logger.info(f"Bug updated: {bug_id}")
            return await get_bug(bug_id)

    except Exception as e:
        logger.error(f"Failed to update bug: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sync")
async def sync_bugs():
    """从云效同步 Bug"""
    from ..core.bug_sync import sync_my_bugs
    from ..tools.yunxiao.openapi_client import YunxiaoError

    try:
        return await sync_my_bugs(settings)
    except YunxiaoError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        logger.error("Failed to persist Yunxiao bugs")
        raise HTTPException(status_code=500, detail="同步写入失败，请查看后端日志")
