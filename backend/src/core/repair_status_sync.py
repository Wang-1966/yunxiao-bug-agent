"""代码交付后的云效状态同步；独立重试不重复合并代码。"""
import json

import aiosqlite

from ..config import get_settings
from ..tools.yunxiao.openapi_client import YunxiaoClient, YunxiaoError


async def sync_confirmed_bug_status(db_path, agent_id):
    # 调用方持有交付锁，禁止验收、删除和同步重试相互穿插。
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        decision = await (await db.execute("SELECT * FROM decisions WHERE agent_id=? AND stage='verify' ORDER BY id DESC LIMIT 1", (agent_id,))).fetchone()
        if not decision:
            raise ValueError('没有该任务的修复验收记录')
        choice = json.loads(decision['user_choice'] or '{}')
        sync = choice.get('yunxiao_sync')
        if decision['status'] != 'answered' or not choice.get('confirmed') or not choice.get('merge'):
            raise ValueError('只有确认修复且代码交付成功后才能更新云效状态')
        if sync is None or sync.get('status') == 'succeeded':
            return sync
        bug = await (await db.execute('SELECT source, assigned_agent_id FROM bugs WHERE bug_id=?', (decision['bug_id'],))).fetchone()
        if not bug or bug['source'] != 'yunxiao' or bug['assigned_agent_id'] != agent_id:
            raise ValueError('该任务已不是当前云效 Bug 的修复任务，不能重试状态更新')
    try:
        remote = await YunxiaoClient(get_settings()).set_bug_in_progress(decision['bug_id'])
        sync = {'status': 'succeeded', 'target': '处理中', **remote}
        message = '云效 Bug 状态已更新为处理中'
    except YunxiaoError as exc:
        sync = {'status': 'failed', 'target': '处理中', 'error': str(exc)}
        message = f'代码已交付，但云效状态更新失败，可重试同步：{exc}'
    choice['yunxiao_sync'] = sync
    async with aiosqlite.connect(db_path) as db:
        await db.execute('BEGIN IMMEDIATE')
        await db.execute('UPDATE decisions SET user_choice=? WHERE decision_id=?',
                         (json.dumps(choice, ensure_ascii=False), decision['decision_id']))
        if sync['status'] == 'succeeded':
            await db.execute('UPDATE bugs SET source_status=?, updated_at=CURRENT_TIMESTAMP WHERE bug_id=?',
                             (sync['source_status'], decision['bug_id']))
        await db.execute('INSERT INTO agent_logs (agent_id, bug_id, log_level, message) VALUES (?, ?, ?, ?)',
                         (agent_id, decision['bug_id'], 'info' if sync['status'] == 'succeeded' else 'warning', message))
        await db.commit()
    return sync
