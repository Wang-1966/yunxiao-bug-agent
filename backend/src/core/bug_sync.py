"""Import source fields without overwriting local repair state."""
import aiosqlite
from urllib.parse import quote

from ..tools.yunxiao.openapi_client import YunxiaoClient, YunxiaoError


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


async def sync_my_bugs(settings):
    items = await YunxiaoClient(settings).list_my_bugs()
    counts = {"total": len(items), "created": 0, "updated": 0, "unchanged": 0}
    async with aiosqlite.connect(settings.database_url.replace("sqlite+aiosqlite:///", "")) as db:
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
