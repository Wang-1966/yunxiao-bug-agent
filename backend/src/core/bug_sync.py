"""Import source fields without overwriting local repair state."""
import asyncio

import aiosqlite
from loguru import logger
from urllib.parse import quote

from ..tools.yunxiao.openapi_client import YunxiaoClient, YunxiaoError

# ponytail: 单进程锁；多个 API 进程同时同步时再拆开
_sync_lock = asyncio.Lock()


def source_priority(item):
    fields = sorted(item.get("customFieldValues") or [], key=lambda field: field.get("fieldName") != "优先级")
    for field in fields:
        if field.get("fieldName") in ("优先级", "严重程度", "Priority", "Severity"):
            values = field.get("values") or []
            if values:
                value = values[0].get("displayValue", "").lower().split("-")[-1]
                if value in ("紧急", "致命", "严重", "最高", "p0", "p1", "critical", "urgent"):
                    return "critical"
                if value in ("高", "重要", "p2", "high", "major"):
                    return "important"
                return "normal"
    return "normal"


def _yunxiao_configured(settings):
    return bool(settings.yunxiao_access_token and settings.yunxiao_org_id and settings.yunxiao_project_id)


async def sync_my_bugs(settings):
    async with _sync_lock:
        return await _write_synced_bugs(settings)


async def run_yunxiao_sync_loop(settings, sleep=asyncio.sleep):
    if settings.yunxiao_sync_interval_seconds <= 0:
        logger.info("云效自动同步已关闭")
        return
    missing_logged = False
    while True:
        interval = settings.yunxiao_sync_interval_seconds
        if interval <= 0:
            return
        if not _yunxiao_configured(settings):
            if not missing_logged:
                logger.warning("云效未配置，自动同步暂停")
                missing_logged = True
        else:
            missing_logged = False
            try:
                counts = await sync_my_bugs(settings)
                logger.info(
                    f"云效自动同步完成: total={counts['total']} created={counts['created']} updated={counts['updated']} unchanged={counts['unchanged']}"
                )
            except asyncio.CancelledError:
                raise
            except YunxiaoError as exc:
                logger.warning(f"云效自动同步失败: {exc}")
            except Exception:
                logger.exception("云效自动同步失败")
        await sleep(interval)


async def _write_synced_bugs(settings):
    client = YunxiaoClient(settings)
    items = await client.list_my_bugs()
    db_path = settings.database_url.replace("sqlite+aiosqlite:///", "")
    listed_ids = {item["id"] for item in items}
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT bug_id FROM bugs WHERE source = 'yunxiao'")
        tracked_ids = [row[0] for row in await cursor.fetchall()]
    # 负责人变更后会移出“我的 Bug”，已入库记录仍需从详情刷新，不能视为无变化。
    for bug_id in tracked_ids:
        if bug_id not in listed_ids:
            try:
                items.append(await client.get_bug(bug_id))
            except YunxiaoError as exc:
                raise YunxiaoError(f"刷新已入库 Bug {bug_id} 失败，本次同步未写入：{exc}") from exc
    counts = {"total": len(items), "created": 0, "updated": 0, "unchanged": 0}
    async with aiosqlite.connect(db_path) as db:
        await db.execute("BEGIN IMMEDIATE")
        for item in items:
            bug_id = item["id"]
            source_status = (item.get("status") or {}).get("displayName") or (item.get("status") or {}).get("name")
            source_project = (item.get("space") or {}).get("name")
            project_id = (item.get("space") or {}).get("id")
            source_url = f"https://devops.aliyun.com/projex/project/{quote(project_id, safe='')}/bug/{quote(bug_id, safe='')}" if project_id else None
            fields = (item["subject"], item.get("description") or "", source_priority(item), source_status, source_project, source_url, project_id)
            cursor = await db.execute("SELECT title, description, priority, source_status, source_project, source_url, source_project_id, source FROM bugs WHERE bug_id = ?", (bug_id,))
            existing = await cursor.fetchone()
            if existing:
                if existing[7] != "yunxiao":
                    raise YunxiaoError("云效 ID 与手动 Bug 冲突，本次同步已取消")
                if tuple(existing[:7]) == fields:
                    counts["unchanged"] += 1
                else:
                    await db.execute("UPDATE bugs SET title = ?, description = ?, priority = ?, source_status = ?, source_project = ?, source_url = ?, source_project_id = ?, updated_at = CURRENT_TIMESTAMP WHERE bug_id = ?", (*fields, bug_id))
                    counts["updated"] += 1
            else:
                status = "resolved" if (item.get("status") or {}).get("nameEn") in ("Fixed", "Closed", "Won't Fix") else "pending"
                await db.execute("INSERT INTO bugs (bug_id, title, description, priority, source_status, source_project, source_url, source_project_id, status, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'yunxiao')", (bug_id, *fields, status))
                counts["created"] += 1
        await db.commit()
    return counts
