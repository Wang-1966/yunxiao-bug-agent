"""Bug 本地补充及任务输入快照，与云效同步数据和验收反馈分别管理。"""
import json

from .bug_media import agent_bug_images
from .image_storage import load_images, save_images


async def read_supplement(db, bug_id):
    row = await (await db.execute(
        'SELECT notes, image_names, revision, document FROM bug_supplements WHERE bug_id=?', (bug_id,),
    )).fetchone()
    return {'notes': row[0], 'image_names': json.loads(row[1]), 'revision': row[2],
            **({'document': json.loads(row[3])} if row[3] else {})} if row else {
        'notes': '', 'image_names': [], 'revision': 0,
    }


async def capture_bug(db_path, bug):
    """正文首次加载后冻结；原图落盘，避免续跑时重新依赖签名地址。"""
    images = await agent_bug_images(bug, materialize=True)
    # 按图片原有顺序保留编号（即使不同地址返回相同内容）。
    names = [save_images(db_path, [image], max_bytes=20_000_000)[0] for image in images]
    return {
        **{key: bug.get(key) for key in ('repo_path', 'branch', 'test_command', 'assigned_agent_id')},
        'title': bug['title'], 'description': bug.get('description') or '',
        'description_format': bug.get('description_format'),
        'detail_warning': bug.get('detail_warning'),
        'source': bug.get('source'), 'image_names': names,
    }


def apply_input_snapshot(db_path, snapshot):
    bug = dict(snapshot['bug'])
    bug['agent_images'] = load_images(db_path, bug.pop('image_names'))
    supplement = snapshot['supplement']
    bug['supplement_notes'] = supplement['notes']
    bug['supplement_image_names'] = supplement['image_names']
    bug['supplement_image_start'] = len(bug['agent_images']) + 1
    bug['agent_images'] += load_images(db_path, supplement['image_names'])
    return bug
