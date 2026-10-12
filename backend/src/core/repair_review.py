"""人工验收：绑定已验证提交，Git 交付与短数据库事务分开执行。"""
import asyncio
import json
import aiosqlite
from .repair_merge import merge_confirmed_repair, cleanup_confirmed_repair
from .projects import resolve_project_config

# ponytail: 单进程串行交付，跨进程部署需仓库级外部锁。
_delivery_lock = asyncio.Lock()


async def confirm_repair(db_path, agent_id, confirmation):
    async with _delivery_lock:
        return await _confirm_repair(db_path, agent_id, confirmation)


async def _confirm_repair(db_path, agent_id, confirmation):
    feedback = confirmation.feedback.strip()
    if not confirmation.confirmed and not feedback:
        raise ValueError('请填写仍未修复的现象或复现步骤，供下一次修复使用')
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        agent = await (await db.execute('SELECT * FROM agents WHERE agent_id=?', (agent_id,))).fetchone()
        if not agent:
            raise KeyError(agent_id)
        decision = await (await db.execute("SELECT * FROM decisions WHERE agent_id=? AND stage='verify' ORDER BY id DESC LIMIT 1", (agent_id,))).fetchone()
        bug = await (await db.execute('SELECT * FROM bugs WHERE bug_id=?', (agent['current_bug_id'],))).fetchone()
        history = await (await db.execute('SELECT * FROM task_history WHERE agent_id=? ORDER BY id DESC LIMIT 1', (agent_id,))).fetchone()
        saved = json.loads(history['result'] or '{}') if history else {}
        previous = json.loads(decision['user_choice'] or '{}') if decision else {}
        # 已交付但清理未完成的同一验收请求可重试清理，绝不再次合并。
        cleanup_retry = bool(confirmation.confirmed and decision and decision['status'] == 'answered' and previous.get('confirmed') and previous.get('merge', {}).get('cleanup_pending'))
        if cleanup_retry:
            delivery = saved['delivery']
        else:
            if not decision or decision['status'] != 'pending' or agent['status'] != 'awaiting_confirmation' or not bug or bug['assigned_agent_id'] != agent_id or bug['status'] != 'awaiting_confirmation':
                raise ValueError('该修复结果已处理或不是当前待确认任务，请刷新页面')
            delivery = saved.get('delivery')
            if confirmation.confirmed:
                commit = saved.get('git_commit_hash')
                if not commit or not saved.get('base_commit') or saved.get('test_results', {}).get('passed') is not True:
                    raise ValueError('缺少已验证提交记录，请重新执行修复后验收')
                if confirmation.git_commit_hash and confirmation.git_commit_hash != commit:
                    raise ValueError('页面审查的提交与当前结果不一致，请刷新后重新审查')
                config = await resolve_project_config(db, dict(bug))
                if not config.get('repo_path') or not config.get('branch'):
                    raise ValueError('项目未配置目标仓库或修复分支')
                if not delivery:
                    delivery = {'worktree_path': agent['worktree_path'], 'repair_branch': saved.get('repair_branch', ''),
                                'repo_path': config['repo_path'], 'target_branch': config['branch'],
                                'git_commit_hash': commit, 'base_commit': saved['base_commit']}
                if delivery['repo_path'] != config['repo_path'] or delivery['target_branch'] != config['branch']:
                    raise ValueError('交付目标配置已变化，请恢复原配置后重试')
                saved['delivery'] = delivery
            await db.execute("UPDATE decisions SET status='delivering' WHERE decision_id=? AND status='pending'", (decision['decision_id'],))
            await db.execute('UPDATE task_history SET result=? WHERE id=?', (json.dumps(saved, ensure_ascii=False), history['id']))
        await db.commit()

    merged = previous.get('merge') if cleanup_retry else None
    if not cleanup_retry:
        try:
            if confirmation.confirmed:
                merged = await merge_confirmed_repair(**delivery)
                merged['cleanup_pending'] = True
            choice = {'confirmed': confirmation.confirmed, 'feedback': feedback, 'merge': merged}
            status = 'resolved' if confirmation.confirmed else 'failed'
            stage = 'completed' if confirmation.confirmed else 'rejected'
            message = (f"用户确认 Bug 已修复，已合并到 {merged['target_branch']}（{merged['merge_commit'][:12]}），待清理 Agent 工作树" if confirmation.confirmed else f'用户反馈仍未修复：{feedback}；可重新执行')
            async with aiosqlite.connect(db_path) as db:
                await db.execute('BEGIN IMMEDIATE')
                cursor = await db.execute("UPDATE decisions SET status='answered', user_choice=?, answered_at=CURRENT_TIMESTAMP WHERE decision_id=? AND status='delivering'", (json.dumps(choice, ensure_ascii=False), decision['decision_id']))
                if cursor.rowcount != 1:
                    raise ValueError('验收记录已变化；已有交付保留，请检查任务状态')
                await db.execute('UPDATE bugs SET status=?, resolved_at=CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END, updated_at=CURRENT_TIMESTAMP WHERE bug_id=? AND assigned_agent_id=?', (status, confirmation.confirmed, bug['bug_id'], agent_id))
                await db.execute('UPDATE agents SET status=?, updated_at=CURRENT_TIMESTAMP WHERE agent_id=?', ('idle' if confirmation.confirmed else 'error', agent_id))
                saved.update(stage=stage, merge=merged)
                await db.execute('UPDATE task_history SET stage=?, status=?, result=?, end_time=CURRENT_TIMESTAMP WHERE id=?', (stage, 'completed' if confirmation.confirmed else 'rejected', json.dumps(saved, ensure_ascii=False), history['id']))
                await db.execute("INSERT INTO agent_logs (agent_id, bug_id, log_level, message) VALUES (?, ?, 'info', ?)", (agent_id, bug['bug_id'], message))
                await db.commit()
        except BaseException:
            # 合并可能已经成功；恢复待确认后，重试用提交祖先关系识别已合并结果。
            async with aiosqlite.connect(db_path) as db:
                await db.execute("UPDATE decisions SET status='pending' WHERE decision_id=? AND status='delivering'", (decision['decision_id'],))
                await db.commit()
            raise
    else:
        status = 'resolved'
        message = '代码已交付，重试清理 Agent 工作树'

    if confirmation.confirmed:
        try:
            await cleanup_confirmed_repair(delivery['worktree_path'], delivery['repair_branch'], delivery['repo_path'], delivery['git_commit_hash'])
            merged['cleanup_pending'] = False
            merged.pop('cleanup_error', None)
            message = f"代码已合并到 {merged['target_branch']}（{merged['merge_commit'][:12]}），Agent 分支和工作树已清理"
        except Exception as exc:
            merged['cleanup_pending'] = True
            merged['cleanup_error'] = str(exc)
            message = f'代码已交付，清理未完成；可再次确认重试清理：{exc}'
        saved.update(merge=merged, stage='completed')
        choice = {'confirmed': True, 'feedback': feedback, 'merge': merged}
        async with aiosqlite.connect(db_path) as db:
            await db.execute('BEGIN IMMEDIATE')
            await db.execute('UPDATE decisions SET user_choice=? WHERE decision_id=?', (json.dumps(choice, ensure_ascii=False), decision['decision_id']))
            await db.execute('UPDATE task_history SET result=? WHERE id=?', (json.dumps(saved, ensure_ascii=False), history['id']))
            if not merged['cleanup_pending']:
                await db.execute('UPDATE agents SET worktree_path=NULL WHERE agent_id=?', (agent_id,))
            await db.execute("INSERT INTO agent_logs (agent_id, bug_id, log_level, message) VALUES (?, ?, ?, ?)", (agent_id, bug['bug_id'], 'warning' if merged['cleanup_pending'] else 'info', message))
            await db.commit()
    return {'bug_id': bug['bug_id'], 'status': status, 'message': message, 'merge': merged}


async def latest_repair_feedback(db_path, bug_id):
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT user_choice FROM decisions WHERE bug_id=? AND stage IN ('proposal', 'verify') AND status='answered' ORDER BY id", (bug_id,))
        rows = await cursor.fetchall()
    feedback = []
    for row in rows:
        choice = json.loads(row[0] or '{}')
        text = choice.get('feedback', '').strip()
        if text and text not in feedback:
            feedback.append(text)
    return '\n\n'.join(feedback)
