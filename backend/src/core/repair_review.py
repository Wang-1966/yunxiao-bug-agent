"""人工验收：绑定已验证提交，Git 交付与短数据库事务分开执行。"""
import asyncio
import json
import aiosqlite
from .repair_merge import merge_confirmed_repair, cleanup_confirmed_repair
from .projects import resolve_project_config
from .rich_input import save_rich_input
from .repair_attempts import drop_attempt_refs, feedback_window_start, preserve_attempt
from .preview_manager import stop_preview
from .repair_status_sync import sync_confirmed_bug_status

# ponytail: 单进程串行交付，跨进程部署需仓库级外部锁。
_delivery_lock = asyncio.Lock()


async def confirm_repair(db_path, agent_id, confirmation):
    async with _delivery_lock:
        result = await _confirm_repair(db_path, agent_id, confirmation)
        if confirmation.confirmed:
            result['yunxiao_sync'] = await sync_confirmed_bug_status(db_path, agent_id)
        return result


async def _confirm_repair(db_path, agent_id, confirmation):
    feedback, images, document = save_rich_input(db_path, confirmation.feedback, confirmation.images, confirmation.document, max_length=4000)
    if not confirmation.confirmed and not feedback and not images:
        raise ValueError('请填写仍未修复的现象或复现步骤（可粘贴截图），供下一次修复使用')
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
        yunxiao_sync = previous.get('yunxiao_sync')
        if confirmation.confirmed and bug and bug['source'] == 'yunxiao' and not yunxiao_sync:
            yunxiao_sync = {'status': 'pending', 'target': '处理中'}
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
            else:
                config = await resolve_project_config(db, dict(bug))
            await db.execute("UPDATE decisions SET status='delivering' WHERE decision_id=? AND status='pending'", (decision['decision_id'],))
            await db.execute('UPDATE task_history SET result=? WHERE id=?', (json.dumps(saved, ensure_ascii=False), history['id']))
        await db.commit()

    await stop_preview(agent_id)
    merged = previous.get('merge') if cleanup_retry else None
    if not cleanup_retry:
        try:
            if confirmation.confirmed:
                merged = await merge_confirmed_repair(**delivery)
                merged['cleanup_pending'] = True
            elif saved.get('base_commit') and saved.get('git_commit_hash') and config.get('repo_path'):
                try:
                    saved.update(await preserve_attempt(config['repo_path'], bug['bug_id'], history['task_id'], saved['base_commit'], saved['git_commit_hash']))
                except (ValueError, OSError) as exc:
                    saved['attempt_error'] = str(exc)
            choice = {'confirmed': confirmation.confirmed, 'feedback': feedback, 'images': images, 'merge': merged}
            if yunxiao_sync:
                choice['yunxiao_sync'] = yunxiao_sync
            if document is not None:
                choice['document'] = document
            status = 'resolved' if confirmation.confirmed else 'failed'
            stage = 'completed' if confirmation.confirmed else 'rejected'
            message = (f"用户确认 Bug 已修复，已合并到 {merged['target_branch']}（{merged['merge_commit'][:12]}），待清理 Agent 工作树" if confirmation.confirmed else f'用户反馈仍未修复：{feedback or "（见截图）"}{f"（附 {len(images)} 张截图）" if images else ""}')
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

    if not confirmation.confirmed:
        await _cleanup_rejected_repair(db_path, agent, bug['bug_id'], saved, config)
    else:
        try:
            await cleanup_confirmed_repair(delivery['worktree_path'], delivery['repair_branch'], delivery['repo_path'], delivery['git_commit_hash'])
            merged['cleanup_pending'] = False
            merged.pop('cleanup_error', None)
            message = f"代码已合并到 {merged['target_branch']}（{merged['merge_commit'][:12]}），Agent 分支和工作树已清理"
            try:
                await drop_attempt_refs(delivery['repo_path'], bug['bug_id'])
            except (ValueError, OSError) as exc:
                message += f'；历次被驳回尝试的隐藏引用未清理：{exc}'
        except Exception as exc:
            merged['cleanup_pending'] = True
            merged['cleanup_error'] = str(exc)
            message = f'代码已交付，清理未完成；可再次确认重试清理：{exc}'
        saved.update(merge=merged, stage='completed')
        choice = {**previous} if cleanup_retry else {'feedback': feedback, 'images': images}
        choice.update(confirmed=True, merge=merged)
        if yunxiao_sync:
            choice['yunxiao_sync'] = yunxiao_sync
        if not cleanup_retry and document is not None:
            choice['document'] = document
        async with aiosqlite.connect(db_path) as db:
            await db.execute('BEGIN IMMEDIATE')
            await db.execute('UPDATE decisions SET user_choice=? WHERE decision_id=?', (json.dumps(choice, ensure_ascii=False), decision['decision_id']))
            await db.execute('UPDATE task_history SET result=? WHERE id=?', (json.dumps(saved, ensure_ascii=False), history['id']))
            if not merged['cleanup_pending']:
                await db.execute('UPDATE agents SET worktree_path=NULL WHERE agent_id=?', (agent_id,))
            await db.execute("INSERT INTO agent_logs (agent_id, bug_id, log_level, message) VALUES (?, ?, ?, ?)", (agent_id, bug['bug_id'], 'warning' if merged['cleanup_pending'] else 'info', message))
            await db.commit()
    return {'bug_id': bug['bug_id'], 'status': status, 'message': message, 'merge': merged}


async def _cleanup_rejected_repair(db_path, agent, bug_id, saved, config):
    """提交已存入隐藏引用才清理；清理失败只记录，不影响反馈和下一轮。"""
    if not agent['worktree_path'] or not saved.get('repair_branch') or not saved.get('git_commit_hash') or not config.get('repo_path'):
        return
    if not saved.get('attempt_ref'):
        level, message = 'warning', f"未能保存上一轮提交，保留 Agent 分支和工作树：{saved.get('attempt_error') or '缺少提交记录'}"
    else:
        try:
            await cleanup_confirmed_repair(agent['worktree_path'], saved['repair_branch'], config['repo_path'], saved['git_commit_hash'])
            level, message = 'info', f"已清理被驳回的 Agent 分支和工作树，提交保存在 {saved['attempt_ref']} 供下一轮按需查看"
        except Exception as exc:
            level, message = 'warning', f'被驳回的 Agent 工作树未清理，保留供人工审查：{exc}'
    async with aiosqlite.connect(db_path) as db:
        if level == 'info':
            await db.execute('UPDATE agents SET worktree_path=NULL WHERE agent_id=?', (agent['agent_id'],))
        await db.execute('INSERT INTO agent_logs (agent_id, bug_id, log_level, message) VALUES (?, ?, ?, ?)', (agent['agent_id'], bug_id, level, message))
        await db.commit()


async def latest_repair_feedback(db_path, bug_id):
    """方案阶段的理解纠正；验收反馈随上一轮尝试记录注入，不在此重复。"""
    async with aiosqlite.connect(db_path) as db:
        start = await feedback_window_start(db, bug_id)
        cursor = await db.execute("SELECT user_choice FROM decisions WHERE bug_id=? AND stage='proposal' AND status='answered' AND id>=? ORDER BY id", (bug_id, start))
        rows = await cursor.fetchall()
    feedback = []
    for row in rows:
        choice = json.loads(row[0] or '{}')
        text = choice.get('feedback', '').strip()
        if text and text not in feedback:
            feedback.append(text)
    return '\n\n'.join(feedback)
