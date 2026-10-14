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
)
from ..config import get_settings
from ..core.orchestrator import get_orchestrator
from ..core.agent_stream import live_stream
from ..core.projects import resolve_project_config, project_configuration_lock
from ..core.preview_manager import start_preview, stop_preview, preview_status

router = APIRouter(prefix="/api/agents", tags=["Agents"])
settings = get_settings()


def get_db_path() -> str:
    """获取数据库路径"""
    return settings.database_url.replace("sqlite+aiosqlite:///", "")


@router.get("", response_model=List[AgentStatus])
async def list_agents():
    """获取所有 Agent 状态"""
    try:
        db_path = get_db_path()
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM agents ORDER BY agent_id"
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
                    root_cause=state.get('root_cause'),
                    fix_summary=state.get('fix_summary'),
                    test_results=state.get('test_results') or {},
                    error=state.get('error') or runtime.get('error'),
                    confirmation=confirmation,
                ))

            return agents

    except Exception as e:
        logger.error(f"Failed to list agents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{agent_id}", response_model=AgentStatus)
async def get_agent(agent_id: str):
    """获取 Agent 详情"""
    try:
        agents = await list_agents()
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
    return {'entries': entries, 'live': live_stream(agent_id)}


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
    """从失败阶段继续执行；原任务无法续跑时新建任务。"""
    try:
        return await get_orchestrator().retry_agent(agent_id)
    except KeyError:
        raise HTTPException(status_code=404, detail='Agent 不存在')
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


async def _restart_rejected_repair(bug_id):
    """反馈已落盘；自动启动失败或达到上限时只提示手动重新执行，不让验收提交失败。"""
    from ..core.repair_attempts import MAX_AUTO_REPAIR_ROUNDS, rejected_rounds
    rounds = await rejected_rounds(get_db_path(), bug_id)
    if rounds >= MAX_AUTO_REPAIR_ROUNDS:
        return {'started': False, 'reason': f'已连续 {rounds} 轮验收未修复，停止自动重新修复；请人工介入，或补充信息后手动重新执行'}
    try:
        task_id = await get_orchestrator().submit_task(bug_id, priority=5)
    except (RuntimeError, ValueError) as exc:
        return {'started': False, 'reason': f'自动重新修复未启动：{exc}；反馈已保存，可手动重新执行'}
    return {'started': True, 'round': rounds + 1, 'task_id': task_id, 'agent_id': f'agent-{task_id}'}


@router.post('/{agent_id}/confirmation')
async def confirm_agent_repair(agent_id: str, request: RepairConfirmation):
    from ..core.repair_review import confirm_repair
    try:
        result = await confirm_repair(get_db_path(), agent_id, request)
        runtime = get_orchestrator().get_agent_status(agent_id)
        if runtime:
            runtime.update(status='completed' if request.confirmed else 'rejected', progress=100 if request.confirmed else 90)
            runtime['state'].update(stage='completed' if request.confirmed else 'rejected', waiting_for_user=False)
        if not request.confirmed:
            result['restart'] = await _restart_rejected_repair(result['bug_id'])
        return result
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
    return {'status': row['status'], 'previous_attempt': attempt, 'decision_id': row['decision_id'], 'decision_status': row['decision_status'], 'root_cause': result.get('root_cause'), 'proposals': result.get('proposals', []), 'feedback': result.get('repair_feedback', ''), 'user_choice': json.loads(row['user_choice']) if row['user_choice'] else None}


@router.post('/{agent_id}/proposal-confirmation')
async def confirm_agent_proposal(agent_id: str, request: ProposalConfirmation):
    try:
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
