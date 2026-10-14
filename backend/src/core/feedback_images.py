"""验收反馈截图：校验后按内容哈希落盘，数据库只记录文件名。"""
import json

import aiosqlite

from .image_storage import save_images, validate_images, _directory, _read_data_url

MAX_IMAGES_PER_FEEDBACK = 4
MAX_IMAGES_PER_TASK = 8
MAX_IMAGE_BYTES = 5_000_000


def validate_feedback_images(images):
    return validate_images(images, max_images=MAX_IMAGES_PER_FEEDBACK, max_bytes=MAX_IMAGE_BYTES)


def save_feedback_images(db_path, images):
    return save_images(db_path, images, max_images=MAX_IMAGES_PER_FEEDBACK, max_bytes=MAX_IMAGE_BYTES)


async def latest_feedback_images(db_path, bug_id):
    """从上一轮被驳回起已提交反馈的截图（去重，保留最近 MAX_IMAGES_PER_TASK 张），返回 (文件名, data URL)。"""
    from .repair_attempts import feedback_window_start
    async with aiosqlite.connect(db_path) as db:
        start = await feedback_window_start(db, bug_id)
        cursor = await db.execute("SELECT user_choice FROM decisions WHERE bug_id=? AND stage IN ('proposal', 'verify') AND status='answered' AND id>=? ORDER BY id", (bug_id, start))
        rows = await cursor.fetchall()
    names = []
    for row in rows:
        for name in json.loads(row[0] or '{}').get('images') or []:
            if name in names:
                names.remove(name)
            names.append(name)
    directory = _directory(db_path)
    loaded = [(name, _read_data_url(directory, name)) for name in names[-MAX_IMAGES_PER_TASK:]]
    return [(name, url) for name, url in loaded if url]
