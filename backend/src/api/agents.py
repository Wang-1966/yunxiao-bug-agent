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
                    WHERE agent_id = ?
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

        task_id = await get_orchestrator().submit_task(request.bug_id, request.priority)

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


@router.post('/{agent_id}/confirmation')
async def confirm_agent_repair(agent_id: str, request: RepairConfirmation):
    from ..core.repair_review import confirm_repair
    try:
        result = await confirm_repair(get_db_path(), agent_id, request)
        runtime = get_orchestrator().get_agent_status(agent_id)
        if runtime:
            runtime.update(status='completed' if request.confirmed else 'rejected', progress=100 if request.confirmed else 90)
            runtime['state'].update(stage='completed' if request.confirmed else 'rejected', waiting_for_user=False)
        return result
    except KeyError:
        raise HTTPException(status_code=404, detail='Agent 不存在')
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get('/{agent_id}/proposal')
async def get_agent_proposal(agent_id: str):
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT a.status, h.result, d.decision_id, d.status AS decision_status, d.user_choice FROM agents a JOIN task_history h ON h.agent_id=a.agent_id LEFT JOIN decisions d ON d.agent_id=a.agent_id AND d.stage='proposal' WHERE a.agent_id=? ORDER BY h.id DESC, d.id DESC LIMIT 1", (agent_id,))
        row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail='Agent 不存在')
    result = json.loads(row['result'] or '{}')
    return {'status': row['status'], 'decision_id': row['decision_id'], 'decision_status': row['decision_status'], 'root_cause': result.get('root_cause'), 'proposals': result.get('proposals', []), 'feedback': result.get('repair_feedback', ''), 'user_choice': json.loads(row['user_choice']) if row['user_choice'] else None}


@router.post('/{agent_id}/proposal-confirmation')
async def confirm_agent_proposal(agent_id: str, request: ProposalConfirmation):
    try:
        return await get_orchestrator().confirm_proposal(agent_id, request.proposal_index, request.feedback, request.decision_id)
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
