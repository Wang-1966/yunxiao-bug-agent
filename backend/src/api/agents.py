"""Agent API 路由"""
from fastapi import APIRouter, HTTPException
from loguru import logger
import aiosqlite
import json
from typing import List

from ..models.agent import (
    Agent,
    AgentStatus,
    TaskSubmitRequest,
    TaskSubmitResponse,
    AgentControlRequest,
    RepairConfirmation,
    ProposalConfirmation,
    ConversationMessageRequest,
)
from ..config import get_settings
from ..core.orchestrator import get_orchestrator
from ..core.agent_stream import live_stream, tool_activity
from ..core.projects import resolve_project_config, project_configuration_lock
from ..core.preview_manager import start_preview, stop_preview, preview_status
from ..core.agent_protocol import repair_unicode_escapes
from ..core.acceptance import evidence_report
from ..core.attention import attention_item
from ..core.repair_summary import repair_summary

router = APIRouter(prefix="/api/agents", tags=["Agents"])
settings = get_settings()


def get_db_path() -> str:
    """获取数据库路径"""
    return settings.database_url.replace("sqlite+aiosqlite:///", "")


@router.get('/{agent_id}/conversation')
async def get_conversation(agent_id: str):
    from ..core import conversation, agent_discussion
    try:
        items = await conversation.messages(agent_id, load_images=False)
        for item in items:
            item['images'] = [f"/api/agents/{agent_id}/conversation/{item['id']}/images/{index}" for index in range(len(item['images']))]
        async with aiosqlite.connect(get_db_path()) as db:
            row = await (await db.execute('''SELECT a.status, b.assigned_agent_id, b.status, h.result
                FROM agents a JOIN bugs b ON a.current_bug_id=b.bug_id
                LEFT JOIN task_history h ON h.id=(SELECT MAX(id) FROM task_history WHERE agent_id=a.agent_id)
                WHERE a.agent_id=?''', (agent_id,))).fetchone()
        saved = json.loads(row[3] or '{}') if row else {}
        runtime = get_orchestrator().get_agent_status(agent_id) or {}
        saved.update(runtime.get('state') or {})
        summary = repair_summary(saved) if saved else None
        outcome = None
        if row:
            if row[0] == 'awaiting_confirmation':
                outcome = '修复完成，等待你的验收'
            elif row[0] == 'error':
                outcome = '本轮执行未完成，可继续对话处理'
            elif row[0] == 'idle':
                outcome = '修复已验收并交付' if row[2] == 'resolved' else '本轮执行已结束'
        return {'messages': items, 'discussing': agent_discussion.busy(agent_id), 'engine': 'pi',
                'status': row[0] if row else 'idle', 'can_send': bool(row and row[1] == agent_id),
                'assigned_agent_id': row[1] if row else None,
                'repair_summary': summary, 'outcome': outcome}
    except KeyError:
        raise HTTPException(404, 'Agent 不存在')


@router.get('/{agent_id}/conversation/{message_id}/images/{index}')
async def conversation_image(agent_id: str, message_id: int, index: int):
    import base64
    from fastapi.responses import Response
    from ..core.conversation import image_data
    try:
        url = await image_data(agent_id, message_id, index)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    header, payload = url.split(',', 1)
    return Response(base64.b64decode(payload), media_type=header[5:].split(';')[0], headers={'Cache-Control': 'private, max-age=3600'})


@router.post('/{agent_id}/conversation')
async def send_conversation(agent_id: str, request: ConversationMessageRequest):
    from ..core import conversation, agent_discussion
    from ..core.repair_review import _delivery_lock
    runner = get_orchestrator()
    # 与停止/删除/审批互斥，消息不会在交付事务中偷偷启动另一个执行器。
    async with runner._agent_control_lock, _delivery_lock, conversation.lock_for(agent_id):
        try:
            async with aiosqlite.connect(get_db_path()) as db:
                row = await (await db.execute('SELECT status FROM agents WHERE agent_id=?', (agent_id,))).fetchone()
                if not row:
                    raise KeyError(agent_id)
            message_id = await conversation.add_user(agent_id, request)
            pending = await conversation.pending(agent_id)
            current_pending = any(item['id'] == message_id for item in pending)
            result = await agent_discussion.dispatch_pending(agent_id, runner, status=row[0], pending=pending)
            if result:
                return {'id': message_id, 'status': 'delivered', 'retry': result}
            return {'id': message_id, 'status': 'queued' if current_pending else 'delivered'}
        except KeyError:
            raise HTTPException(404, 'Agent 不存在')
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        except RuntimeError as exc:
            raise HTTPException(503, str(exc))


def require_discussion_idle(agent_id):
    from ..core.agent_discussion import busy
    if busy(agent_id):
        raise HTTPException(409, 'Agent 正在回复，请等待或停止回复后再操作')


@router.get("", response_model=List[AgentStatus])
async def list_agents():
    """获取所有 Agent 状态"""
    return await _read_agent_statuses()


async def _read_agent_statuses(agent_id=None):
    try:
        db_path = get_db_path()
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM agents WHERE agent_id=?" if agent_id else "SELECT * FROM agents ORDER BY agent_id",
                (agent_id,) if agent_id else (),
            )
            rows = await cursor.fetchall()

            agents = []
            for row in rows:
                agent_dict = dict(row)

                # 获取当前 Bug 信息
                current_bug = None
                if agent_dict.get("current_bug_id"):
                    bug_cursor = await db.execute(
                        "SELECT * FROM bugs WHERE bug_id = ?",
                        (agent_dict["current_bug_id"],)
                    )
                    bug_row = await bug_cursor.fetchone()
                    if bug_row:
                        current_bug = dict(bug_row)

                # 获取最近日志
                log_cursor = await db.execute(
                    """
                    SELECT message FROM agent_logs
                    WHERE agent_id = ? AND log_level NOT IN ('thinking', 'assistant')
                    ORDER BY created_at DESC, id DESC
                    LIMIT 10
                    """,
                    (agent_dict["agent_id"],)
                )
                log_rows = await log_cursor.fetchall()
                logs = [row[0] for row in log_rows]

                # 提取阶段耗时统计
                timing_cursor = await db.execute(
                    """
                    SELECT metadata FROM agent_logs
                    WHERE agent_id = ? AND metadata IS NOT NULL
                    ORDER BY id
                    """,
                    (agent_dict["agent_id"],)
                )
                timing_rows = await timing_cursor.fetchall()
                stage_timings = {}
                for timing_row in timing_rows:
                    try:
                        metadata = json.loads(timing_row[0])
                        if isinstance(metadata, dict) and 'stage' in metadata and 'duration_seconds' in metadata:
                            stage = metadata['stage']
                            duration = metadata['duration_seconds']
                            # 保留最新的阶段耗时
                            stage_timings[stage] = duration
                    except (json.JSONDecodeError, KeyError):
                        pass

                # 计算总耗时
                if stage_timings:
                    stage_timings['total'] = sum(stage_timings.get(s, 0) for s in ['analyze', 'fix', 'verify'])
                runtime = get_orchestrator().get_agent_status(agent_dict["agent_id"]) or {}
                history_cursor = await db.execute('SELECT stage, result FROM task_history WHERE agent_id=? ORDER BY id DESC LIMIT 1', (agent_dict['agent_id'],))
                history = await history_cursor.fetchone()
                saved = json.loads(history['result'] or '{}') if history else {}
                state = {**saved, **runtime.get('state', {})}
                decision_cursor = await db.execute("SELECT decision_id, status, user_choice, answered_at FROM decisions WHERE agent_id=? AND stage='verify' ORDER BY id DESC LIMIT 1", (agent_dict['agent_id'],))
                decision = await decision_cursor.fetchone()
                confirmation = dict(decision) if decision else None
                if confirmation:
                    confirmation['user_choice'] = json.loads(confirmation['user_choice']) if confirmation['user_choice'] else None
                pending_cursor = await db.execute("SELECT decision_id, stage, question, status, created_at FROM decisions WHERE agent_id=? AND status='pending' ORDER BY id DESC", (agent_dict['agent_id'],))
                pending_decisions = [dict(item) for item in await pending_cursor.fetchall()]
                report = evidence_report(state)
                if agent_id is None:
                    # 列表仅需条目摘要，逐项命令输出在详情按需读取。
                    report = {**report, 'checks': [], 'reproduction_before': None}

                agents.append(AgentStatus(
                    agent_id=agent_dict["agent_id"],
                    name=agent_dict["name"],
                    status=agent_dict["status"],
                    current_bug=current_bug,
                    current_stage=history['stage'] if history else state.get('stage'),
                    progress=100 if history and history['stage'] == 'completed' else runtime.get('progress', saved.get('progress', 0)),
                    logs=logs,
                    worktree_path=agent_dict.get('worktree_path'),
                    repair_branch=state.get('repair_branch'),
                    base_commit=state.get('base_commit'),
                    git_commit_hash=state.get('git_commit_hash'),
                    modified_files=state.get('modified_files', []),
                    root_cause=repair_unicode_escapes(state.get('root_cause')),
                    confidence=state.get('confidence'),
                    analysis=repair_unicode_escapes(state.get('analysis')),
                    repro=state.get('repro'),
                    review=state.get('review'),
                    fix_summary=state.get('fix_summary'),
                    repair_summary=repair_summary(state),
                    test_results=state.get('test_results') or {},
                    error=state.get('error') or runtime.get('error'),
                    confirmation=confirmation,
                    acceptance=report,
                    attention=attention_item(agent_dict, current_bug, state, pending_decisions, confirmation),
                    stage_timings=stage_timings if stage_timings else None,
                ))

            return agents

    except Exception as e:
        logger.error(f"Failed to list agents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{agent_id}", response_model=AgentStatus)
async def get_agent(agent_id: str):
    """获取 Agent 详情"""
    try:
        agents = await _read_agent_statuses(agent_id)
        agent = next((a for a in agents if a.agent_id == agent_id), None)

        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        return agent

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get agent: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/{agent_id}/transcript')
async def agent_transcript(agent_id: str, after: int = 0, limit: int = 500):
    """按时间顺序返回 after 之后的日志，以及正在生成中的思考过程。"""
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            'SELECT id, log_level, message, metadata, created_at FROM agent_logs WHERE agent_id=? AND id>? ORDER BY id LIMIT ?',
            (agent_id, after, max(1, min(limit, 1000))),
        )).fetchall()
    entries = [{
        'id': row['id'], 'level': row['log_level'], 'message': row['message'],
        'metadata': json.loads(row['metadata']) if row['metadata'] else None, 'created_at': row['created_at'],
    } for row in rows]
    return {'entries': entries, 'live': live_stream(agent_id), 'activity': tool_activity(agent_id)}


@router.post("/submit-task", response_model=TaskSubmitResponse)
async def submit_task(request: TaskSubmitRequest):
    """提交修复任务"""
    try:
        # 验证 Bug 存在
        db_path = get_db_path()
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT bug_id FROM bugs WHERE bug_id = ?",
                (request.bug_id,)
            )
            row = await cursor.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail="Bug not found")

        task_id = await get_orchestrator().submit_task(request.bug_id, request.priority, supplement_revision=request.supplement_revision)

        logger.info(f"Task submitted: {task_id} for bug {request.bug_id}")

        return TaskSubmitResponse(
            task_id=task_id,
            bug_id=request.bug_id,
            message=f"Task submitted successfully with priority {request.priority}"
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to submit task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{agent_id}/pause")
async def pause_agent(agent_id: str):
    """暂停 Agent"""
    try:
        # TODO: 实现暂停逻辑
        logger.info(f"Agent pause requested: {agent_id}")
        return {"message": f"Agent {agent_id} paused"}

    except Exception as e:
        logger.error(f"Failed to pause agent: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{agent_id}/resume")
async def resume_agent(agent_id: str):
    """恢复 Agent"""
    try:
        # TODO: 实现恢复逻辑
        logger.info(f"Agent resume requested: {agent_id}")
        return {"message": f"Agent {agent_id} resumed"}

    except Exception as e:
        logger.error(f"Failed to resume agent: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{agent_id}/stop")
async def stop_agent(agent_id: str):
    """停止 Agent"""
    try:
        await get_orchestrator().stop_agent(agent_id)
        logger.info(f"Agent stop requested: {agent_id}")
        return {"message": f"Agent {agent_id} stopped"}

    except KeyError:
        raise HTTPException(status_code=404, detail="Agent not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as e:
        logger.error(f"Failed to stop agent: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post('/{agent_id}/retry')
async def retry_agent(agent_id: str):
    require_discussion_idle(agent_id)
    """从失败阶段继续执行；原任务无法续跑时新建任务。"""
    try:
        return await get_orchestrator().retry_agent(agent_id)
    except KeyError:
        raise HTTPException(status_code=404, detail='Agent 不存在')
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post('/{agent_id}/confirmation')
async def confirm_agent_repair(agent_id: str, request: RepairConfirmation):
    from ..core.repair_review import settle_repair
    try:
        return await settle_repair(get_db_path(), get_orchestrator(), agent_id, request, before=lambda: require_discussion_idle(agent_id))
    except KeyError:
        raise HTTPException(status_code=404, detail='Agent 不存在')
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post('/{agent_id}/yunxiao-status-sync')
async def retry_yunxiao_status_sync(agent_id: str):
    from ..core.repair_review import _delivery_lock
    from ..core.repair_status_sync import sync_confirmed_bug_status
    await get_agent(agent_id)
    async with _delivery_lock:
        try:
            result = await sync_confirmed_bug_status(get_db_path(), agent_id)
            if result is None:
                raise ValueError('该任务没有待同步的云效状态')
            return result
        except ValueError as exc:
            raise HTTPException(409, str(exc))


@router.get('/{agent_id}/proposal')
async def get_agent_proposal(agent_id: str):
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT a.status, h.result, d.decision_id, d.status AS decision_status, d.user_choice FROM agents a JOIN task_history h ON h.agent_id=a.agent_id LEFT JOIN decisions d ON d.agent_id=a.agent_id AND d.stage='proposal' WHERE a.agent_id=? ORDER BY h.id DESC, d.id DESC LIMIT 1", (agent_id,))
        row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail='Agent 不存在')
    result = json.loads(row['result'] or '{}')
    attempt = result.get('previous_attempt')
    if attempt:
        attempt = {**attempt, 'diff': ''}
        if attempt.get('attempt_ref') and result.get('worktree_path'):
            from ..core.repair_executor import git
            try:
                diff = await git(result['worktree_path'], 'diff', '--no-ext-diff', '--no-textconv', attempt['base_commit'], attempt['git_commit_hash'])
                attempt['diff'] = diff[:100000] + ('\n…（差异超过展示上限，已截断）' if len(diff) > 100000 else '')
            except (ValueError, OSError):
                attempt['diff'] = '上一轮提交已不可用，仅能查看改动统计'
        attempt['diff'] = attempt['diff'] or attempt.get('diff_stat') or ''
    result = repair_unicode_escapes(result)
    return {'status': row['status'], 'previous_attempt': attempt, 'decision_id': row['decision_id'], 'decision_status': row['decision_status'], 'root_cause': result.get('root_cause'), 'confidence': result.get('confidence'), 'analysis': result.get('analysis'), 'repro': result.get('repro'), 'review': result.get('review'), 'proposals': result.get('proposals', []), 'feedback': result.get('repair_feedback', ''), 'user_choice': json.loads(row['user_choice']) if row['user_choice'] else None}


@router.post('/{agent_id}/proposal-confirmation')
async def confirm_agent_proposal(agent_id: str, request: ProposalConfirmation):
    try:
        async with get_orchestrator()._agent_control_lock:
            require_discussion_idle(agent_id)
            return await get_orchestrator().confirm_proposal(agent_id, request.proposal_index, request.feedback, request.decision_id, request.images, request.document)
    except KeyError:
        raise HTTPException(status_code=404, detail='Agent 不存在')
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get('/{agent_id}/diff')
async def agent_diff(agent_id: str):
    from pathlib import Path
    import difflib
    from ..core.repair_executor import RepositoryFiles, changed_files, git
    agent = await get_agent(agent_id)
    if not agent.worktree_path:
        raise HTTPException(status_code=409, detail='该任务尚未创建修复工作树')
    root = Path(agent.worktree_path).resolve()
    if not root.is_dir() or not root.is_relative_to(Path(settings.worktrees_dir).resolve()):
        raise HTTPException(status_code=409, detail='修复工作树已移除或不在配置的工作树目录中')
    if agent.git_commit_hash:
        if not agent.base_commit or await git(root, 'rev-parse', 'HEAD') != agent.git_commit_hash or await git(root, 'status', '--porcelain'):
            raise HTTPException(status_code=409, detail='工作树与已验证提交不一致，请重新执行验证')
        diff = await git(root, 'diff', '--no-ext-diff', '--no-textconv', agent.base_commit, agent.git_commit_hash)
        return {'diff': diff[:100000], 'truncated': len(diff) > 100000}
    files = RepositoryFiles(root)
    names = await changed_files(root)
    safe_names = []
    for name in names:
        try:
            files.path(name)
            safe_names.append(name)
        except ValueError:
            continue
    diff = await git(root, '--literal-pathspecs', 'diff', '--no-ext-diff', '--no-textconv', 'HEAD', '--', *safe_names) if safe_names else ''
    tracked = set((await git(root, 'ls-files', '-z')).split('\0'))
    for name in safe_names:
        path = files.path(name)
        if name not in tracked and path.is_file() and path.stat().st_size <= 2_000_000:
            try:
                text = path.read_text()
            except UnicodeError:
                continue
            diff += '\n' + ''.join(difflib.unified_diff([], text.splitlines(keepends=True), fromfile='/dev/null', tofile=f'b/{name}'))
    return {'diff': diff[:100000], 'truncated': len(diff) > 100000}


@router.get('/{agent_id}/preview')
async def get_preview(agent_id: str):
    await get_agent(agent_id)
    return await preview_status(agent_id)


@router.post('/{agent_id}/preview')
async def start_agent_preview(agent_id: str):
    from ..core.repair_review import _delivery_lock
    # 与验收和删除共用锁，防止读取旧状态后在已清理工作树中启动服务。
    async with _delivery_lock, project_configuration_lock:
        return await _start_agent_preview(agent_id)


async def _start_agent_preview(agent_id: str):
    agent = await get_agent(agent_id)
    if agent.status != 'awaiting_confirmation' or not agent.worktree_path or not agent.current_bug or agent.current_bug.get('assigned_agent_id') != agent_id or not agent.confirmation or agent.confirmation['status'] != 'pending' or agent.test_results.get('passed') is not True:
        raise HTTPException(status_code=409, detail='只有等待人工验收且已有工作树的任务可以启动预览')
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        bug = await (await db.execute('SELECT * FROM bugs WHERE bug_id=?', (agent.current_bug['bug_id'],))).fetchone()
        if not bug:
            raise HTTPException(status_code=404, detail='当前 Bug 不存在')
        config = await resolve_project_config(db, dict(bug))
    if not config.get('repo_path'):
        raise HTTPException(status_code=409, detail='项目未配置目标仓库')
    try:
        return await start_preview(agent_id, worktree_path=agent.worktree_path, repo_path=config['repo_path'],
                                   command=config.get('preview_command') or '', env_files=config.get('preview_env_files') or [],
                                   commit=agent.git_commit_hash, branch=agent.repair_branch,
                                   public_port=config.get('preview_port'))
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.delete('/{agent_id}/preview')
async def stop_agent_preview(agent_id: str):
    await get_agent(agent_id)
    return await stop_preview(agent_id)


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str):
    """停止并删除 Agent 及其关联记录，保留 Bug。"""
    try:
        await get_orchestrator().delete_agent(agent_id)
        return {"message": f"Agent {agent_id} deleted"}
    except KeyError:
        raise HTTPException(status_code=404, detail="Agent not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as e:
        logger.error(f"Failed to delete agent: {e}")
        raise HTTPException(status_code=500, detail=str(e))
