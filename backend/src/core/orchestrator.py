"""多 Agent 协调器"""
import asyncio
import json
import random
import uuid
import aiosqlite
from itertools import count
from pathlib import Path
from typing import Dict, Optional, List
from loguru import logger
from dataclasses import dataclass, field
from datetime import datetime

from ..agent.graph import create_bug_fix_graph, compile_graph
from ..agent.state import BugFixState
from ..config import get_settings
from .projects import resolve_project_config
from .repair_merge import discard_worktree
from .bug_inputs import read_supplement

settings = get_settings()

AGENT_NICKNAMES = (
    "夜猫子修修",
    "咖啡因大夫",
    "通宵啄木鸟",
    "爱较真的补丁匠",
    "戴眼镜的捉虫侠",
    "零警告强迫症",
    "断点旅行者",
    "日志考古家",
)


def pick_agent_name(taken: set[str]) -> str:
    free = [name for name in AGENT_NICKNAMES if name not in taken]
    if free:
        return random.choice(free)
    base = random.choice(AGENT_NICKNAMES)
    number = 2
    while f"{base} {number}" in taken:
        number += 1
    return f"{base} {number}"


async def rename_stiff_agent_names(db):
    rows = await (await db.execute("SELECT agent_id, name FROM agents")).fetchall()
    taken = {name for _, name in rows if not name.startswith("修复 Agent task-")}
    for agent_id, name in rows:
        if not name.startswith("修复 Agent task-"):
            continue
        nickname = pick_agent_name(taken)
        taken.add(nickname)
        await db.execute("UPDATE agents SET name=? WHERE agent_id=?", (nickname, agent_id))


STAGE_PROGRESS = {"analyzing": 10, "proposing": 30, "awaiting_plan_confirmation": 35, "fixing": 50, "verifying": 75, "awaiting_confirmation": 90, "completed": 90}
# 续跑起点 -> (阶段, Bug 状态, 进度, 页面说明)
RESUME_POINTS = {
    'fetch_bug': ('analyzing', 'analyzing', 0, '获取 Bug 详情'),
    'analyze_root_cause': ('analyzing', 'analyzing', 10, '根因分析'),
    'design_proposal': ('proposing', 'analyzing', 30, '方案生成'),
    'implement_fix': ('fixing', 'fixing', 45, '代码修复'),
    'verify_fix': ('verifying', 'testing', 75, '验证'),
}


@dataclass
class Task:
    """任务定义"""
    task_id: str
    bug_id: str
    priority: int
    created_at: datetime
    status: str = "pending"  # pending, running, completed, failed
    start_node: str = "fetch_bug"
    resume_state: Optional[dict] = None
    input_snapshot: dict = field(default_factory=dict)


class MultiAgentOrchestrator:
    """多 Agent 协调器"""

    def __init__(self):
        self.task_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self.running_agents: Dict[str, asyncio.Task] = {}
        self.agent_states: Dict[str, Dict] = {}
        self.max_concurrent = settings.max_concurrent_agents
        self._running = False
        self._schedule_task = None
        self._sequence = count()
        self._agent_control_lock = asyncio.Lock()

    async def start(self):
        """启动协调器"""
        if self._running:
            logger.warning("Orchestrator already running")
            return

        # 上次进程中断的任务无法继续使用内存中的图；释放 Bug 供重新执行。
        # 待人工确认结果已经落盘，不属于运行中任务，必须保留。
        db_path = settings.database_url.replace('sqlite+aiosqlite:///', '')
        async with aiosqlite.connect(db_path) as db:
            await db.execute('BEGIN IMMEDIATE')
            # 合并已开始但进程中断时，允许再次验收；提交祖先检查避免重复合并。
            await db.execute("UPDATE decisions SET status='pending' WHERE stage='verify' AND status='delivering'")
            await rename_stiff_agent_names(db)
            cursor = await db.execute("SELECT agent_id, current_bug_id FROM agents WHERE status IN ('busy', 'paused')")
            for agent_id, bug_id in await cursor.fetchall():
                await db.execute("UPDATE agents SET status='error', updated_at=CURRENT_TIMESTAMP WHERE agent_id=?", (agent_id,))
                await db.execute("UPDATE bugs SET status='failed', updated_at=CURRENT_TIMESTAMP WHERE assigned_agent_id=? AND status IN ('analyzing', 'fixing', 'testing')", (agent_id,))
                await db.execute("UPDATE task_history SET stage='failed', status='failed', error='服务中断，请重新执行', end_time=CURRENT_TIMESTAMP WHERE agent_id=? AND status='running'", (agent_id,))
                await db.execute("INSERT INTO agent_logs (agent_id, bug_id, log_level, message) VALUES (?, ?, 'error', '服务中断，任务已停止；已有修改保留，可重新执行')", (agent_id, bug_id))
            await db.commit()

        self._running = True
        logger.info(f"Orchestrator started with max {self.max_concurrent} concurrent agents")

        # 启动调度循环
        self._schedule_task = asyncio.create_task(self._schedule_loop())

    async def stop(self):
        """停止协调器"""
        self._running = False
        if self._schedule_task:
            self._schedule_task.cancel()
            await asyncio.gather(self._schedule_task, return_exceptions=True)

        # 取消所有运行中的 Agent
        for agent_id, task in list(self.running_agents.items()):
            logger.info(f"Cancelling agent: {agent_id}")
            runtime = self.agent_states.get(agent_id)
            if runtime:
                runtime.setdefault('cancellation_reason', '服务停止，任务执行中断；已有修改保留在工作树，请重新执行')
            task.cancel()

        # 等待所有任务结束
        await asyncio.gather(*self.running_agents.values(), return_exceptions=True)

        self.running_agents.clear()
        while not self.task_queue.empty():
            _, _, queued = self.task_queue.get_nowait()
            if queued.status == "cancelled":
                continue
            await self._save_status(f"agent-{queued.task_id}", queued, "error", "failed", "服务停止，排队任务已取消")
        logger.info("Orchestrator stopped")

    async def submit_task(self, bug_id: str, priority: int = 0, *, supplement_revision: Optional[int] = None) -> str:
        """
        提交修复任务

        Args:
            bug_id: Bug ID
            priority: 优先级（数字越大越优先）

        Returns:
            任务 ID
        """
        if not self._running:
            raise RuntimeError("任务调度器未启动")

        import uuid
        task_id = f"task-{uuid.uuid4().hex[:8]}"

        task = Task(
            task_id=task_id,
            bug_id=bug_id,
            priority=priority,
            created_at=datetime.now()
        )

        agent_id = f"agent-{task_id}"
        db_path = settings.database_url.replace("sqlite+aiosqlite:///", "")
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "UPDATE bugs SET status='analyzing', assigned_agent_id=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE bug_id=? AND status IN ('pending', 'failed') "
                "AND COALESCE(source_status, '') NOT IN ('已修复', '已关闭', '暂不修复')",
                (agent_id, bug_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("该 Bug 不可修复或已有排队/运行任务，请勿重复提交")
            supplement = await read_supplement(db, bug_id)
            if supplement_revision is not None and supplement['revision'] != supplement_revision:
                raise ValueError('补充信息已更新，请重新加载后再开始修复')
            task.input_snapshot = {'supplement': supplement}
            taken = {row[0] for row in await (await db.execute("SELECT name FROM agents")).fetchall()}
            await db.execute(
                "INSERT INTO agents (agent_id, name, status, current_bug_id) VALUES (?, ?, 'busy', ?)",
                (agent_id, pick_agent_name(taken), bug_id),
            )
            await db.execute(
                "INSERT INTO agent_logs (agent_id, bug_id, log_level, message) VALUES (?, ?, 'info', '任务已入队，等待执行')",
                (agent_id, bug_id),
            )
            await db.execute(
                "INSERT INTO task_history (task_id, bug_id, agent_id, stage, status, result) VALUES (?, ?, ?, 'queued', 'running', ?)",
                (task_id, bug_id, agent_id, json.dumps({'input_snapshot': task.input_snapshot}, ensure_ascii=False)),
            )
            await db.commit()
        self.agent_states[agent_id] = {"task": task, "state": {"stage": "queued", "input_snapshot": task.input_snapshot}, "status": "queued", "progress": 0}
        # 同优先级按提交顺序排列，避免比较不可排序的 Task 对象。
        self.task_queue.put_nowait((-priority, next(self._sequence), task))

        logger.info(f"Task submitted: {task_id} for bug {bug_id} with priority {priority}")
        return task_id

    async def _schedule_loop(self):
        """调度循环"""
        while self._running:
            try:
                # 如果有空闲槽位且队列中有任务
                if len(self.running_agents) < self.max_concurrent:
                    if not self.task_queue.empty():
                        _, _, task = await asyncio.wait_for(
                            self.task_queue.get(),
                            timeout=1.0
                        )

                        # 启动 Agent 处理任务
                        await self._start_agent(task)
                    else:
                        # 队列为空，等待
                        await asyncio.sleep(1.0)
                else:
                    # 所有槽位都满，等待
                    await asyncio.sleep(1.0)

            except asyncio.TimeoutError:
                # 队列为空超时，继续循环
                continue
            except Exception as e:
                logger.error(f"Schedule loop error: {e}")
                await asyncio.sleep(1.0)

    async def _start_agent(self, task: Task):
        """
        启动 Agent 处理任务

        Args:
            task: 任务对象
        """
        agent_id = f"agent-{task.task_id}"

        if task.status != "pending":
            return

        logger.info(f"Starting agent {agent_id} for bug {task.bug_id}")

        if task.resume_state is not None:
            agent_task = asyncio.create_task(self._resume_from(agent_id, task, task.resume_state, task.start_node))
        else:
            agent_task = asyncio.create_task(self._run_agent(agent_id, task))

        self.running_agents[agent_id] = agent_task

        # 任务完成后清理
        agent_task.add_done_callback(
            lambda t: self._on_agent_complete(agent_id, t)
        )

    async def _run_agent(self, agent_id: str, task: Task):
        """
        运行 Agent

        Args:
            agent_id: Agent ID
            task: 任务对象
        """
        try:
            task.status = "running"
            await self._save_status(agent_id, task, "busy", "analyzing", "Agent 开始执行")
            # 创建状态图
            workflow = create_bug_fix_graph()
            app = compile_graph(workflow)

            # 初始化状态
            initial_state: BugFixState = {
                "bug_id": task.bug_id,
                "bug_info": {},
                "input_snapshot": task.input_snapshot,
                "stage": "idle",
                "root_cause": None,
                "confidence": "medium",
                "need_logging": False,
                "proposals": [],
                "selected_proposal": None,
                "modified_files": [],
                "git_commit_hash": None,
                "test_results": {},
                "waiting_for_user": False,
                "user_decision": None,
                "require_plan_confirmation": True,
                "error": None,
            }
            from .repair_review import latest_repair_feedback
            from .repair_attempts import latest_rejected_attempt
            initial_state['repair_feedback'] = await latest_repair_feedback(settings.database_url.replace('sqlite+aiosqlite:///', ''), task.bug_id)
            initial_state['previous_attempt'] = await latest_rejected_attempt(settings.database_url.replace('sqlite+aiosqlite:///', ''), task.bug_id)
            initial_state['require_plan_confirmation'] = True
            # Project binding is authoritative for repository and target branch.
            from ..core.projects import resolve_project_config
            async with aiosqlite.connect(settings.database_url.replace('sqlite+aiosqlite:///', '')) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute('SELECT * FROM bugs WHERE bug_id=?', (task.bug_id,))
                row = await cursor.fetchone()
                resolved_bug = await resolve_project_config(db, dict(row))
            initial_state['bug_config'] = resolved_bug

            # 保存状态
            self.agent_states[agent_id] = {
                "agent_id": agent_id,
                "task": task,
                "state": initial_state,
                "status": "running"
            }

            # 运行状态图
            logger.info(f"Agent {agent_id} starting execution")

            async for output in app.astream(initial_state):
                logger.debug(f"Agent {agent_id} completed nodes: {list(output)}")

                # 更新状态
                if agent_id in self.agent_states:
                    for value in output.values():
                        if isinstance(value, dict):
                            self.agent_states[agent_id]["state"].update(value)
                    state = self.agent_states[agent_id]["state"]
                    if state.get('worktree_path'):
                        db_path = settings.database_url.replace("sqlite+aiosqlite:///", "")
                        async with aiosqlite.connect(db_path) as db:
                            await db.execute('UPDATE agents SET worktree_path=? WHERE agent_id=?', (state['worktree_path'], agent_id))
                            await db.commit()
                    if state.get("error"):
                        state['failed_node'] = next(iter(output), None)
                        raise RuntimeError(state["error"])
                    stage = state.get("stage", "analyzing")
                    bug_status = {"proposing": "analyzing", "awaiting_plan_confirmation": "awaiting_plan_confirmation", "verifying": "testing", "awaiting_confirmation": "awaiting_confirmation", "completed": "testing"}.get(stage, stage)
                    self.agent_states[agent_id]["progress"] = STAGE_PROGRESS.get(stage, 0)
                    message = f"当前阶段：{stage}"
                    if 'prepare_repository' in output:
                        message = f"已创建隔离工作树：{state['worktree_path']}，分支：{state['repair_branch']}"
                    elif 'implement_fix' in output:
                        message = f"已修改文件：{', '.join(state.get('modified_files', []))}；开始验证"
                    elif 'verify_fix' in output:
                        message = f"验证命令通过：{state.get('test_results', {}).get('command')}"
                    await self._save_status(agent_id, task, "busy", bug_status, message)

            state = self.agent_states[agent_id]['state']
            if state.get('stage') == 'awaiting_plan_confirmation':
                task.status = 'awaiting_plan_confirmation'
                self.agent_states[agent_id].update(status='awaiting_plan_confirmation', progress=35)
                await self._save_status(agent_id, task, 'awaiting_plan_confirmation', 'awaiting_plan_confirmation', '修复方案已生成，请确认方案后继续修改代码')
                return
            if not state.get('modified_files') or state.get('test_results', {}).get('passed') is not True:
                raise ValueError('缺少实际修改或成功验证结果，不能进入人工确认')
            task.status = "awaiting_confirmation"
            state.update(stage='awaiting_confirmation', waiting_for_user=True)
            self.agent_states[agent_id].update(status='awaiting_confirmation', progress=90)
            await self._save_status(agent_id, task, "awaiting_confirmation", "awaiting_confirmation", "验证通过，请审查修改并确认 Bug 是否已修复")

        except asyncio.CancelledError:
            logger.info(f"Agent {agent_id} cancelled")
            await self._record_cancellation(agent_id, task)
            raise

        except Exception as e:
            logger.error(f"Agent {agent_id} failed: {e}")
            task.status = "failed"
            if agent_id in self.agent_states:
                self.agent_states[agent_id]["status"] = "failed"
                self.agent_states[agent_id]["error"] = str(e)
                self.agent_states[agent_id]["state"]["stage"] = "failed"
                self.agent_states[agent_id]["state"]["error"] = str(e) or type(e).__name__
            await self._save_status(agent_id, task, "error", "failed", f"任务执行失败：{e}")

    async def _save_status(self, agent_id: str, task: Task, agent_status: str, bug_status: str, message: str):
        """将执行状态和日志写入页面使用的数据库。"""
        db_path = settings.database_url.replace("sqlite+aiosqlite:///", "")
        async with aiosqlite.connect(db_path) as db:
            await db.execute('BEGIN IMMEDIATE')
            cursor = await db.execute('SELECT 1 FROM agents WHERE agent_id=?', (agent_id,))
            if await cursor.fetchone() is None:
                return
            await db.execute("UPDATE agents SET status=?, updated_at=CURRENT_TIMESTAMP WHERE agent_id=?", (agent_status, agent_id))
            await db.execute("UPDATE bugs SET status=?, updated_at=CURRENT_TIMESTAMP WHERE bug_id=? AND assigned_agent_id=?", (bug_status, task.bug_id, agent_id))
            runtime = self.agent_states.get(agent_id, {})
            state = runtime.get('state', {})
            # 输入快照保留原始正文和图片文件引用，不持久化图片 base64 或临时签名地址。
            result = {key: state[key] for key in ('worktree_path', 'repair_branch', 'base_commit', 'git_commit_hash', 'root_cause', 'confidence', 'need_logging', 'analysis', 'repro', 'review', 'proposals', 'selected_proposal', 'fix_summary', 'modified_files', 'test_results', 'error', 'failed_node', 'repair_feedback', 'previous_attempt') if key in state}
            result['progress'] = runtime.get('progress', 0)
            result['input_snapshot'] = state.get('input_snapshot', task.input_snapshot)
            # 文字观察和正文指纹单独保存；续跑按快照文件引用读取原图。
            result['visual_context'] = state.get('visual_context')
            result['source_context'] = state.get('source_context')
            stage = 'failed' if agent_status == 'error' else state.get('stage', 'queued')
            history_status = 'failed' if agent_status == 'error' else 'awaiting_confirmation' if bug_status == 'awaiting_confirmation' else 'awaiting_plan_confirmation' if bug_status == 'awaiting_plan_confirmation' else 'running'
            await db.execute(
                "UPDATE task_history SET stage=?, status=?, result=?, error=?, end_time=CASE WHEN ?='running' THEN NULL ELSE CURRENT_TIMESTAMP END WHERE task_id=?",
                (stage, history_status, json.dumps(result, ensure_ascii=False), state.get('error'), history_status, task.task_id),
            )
            if bug_status == 'awaiting_confirmation':
                await db.execute(
                    "INSERT OR IGNORE INTO decisions (decision_id, bug_id, agent_id, stage, question, options) SELECT ?, ?, ?, 'verify', '请确认此 Bug 是否已修复', ? WHERE EXISTS (SELECT 1 FROM bugs WHERE bug_id=? AND assigned_agent_id=? AND status='awaiting_confirmation')",
                    (f'review-{task.task_id}', task.bug_id, agent_id, json.dumps(['已修复', '未修复'], ensure_ascii=False), task.bug_id, agent_id),
                )
            if bug_status == 'awaiting_plan_confirmation':
                await db.execute(
                    "INSERT OR IGNORE INTO decisions (decision_id, bug_id, agent_id, stage, question, options) SELECT ?, ?, ?, 'proposal', '请选择修复方案并补充要求', ? WHERE EXISTS (SELECT 1 FROM bugs WHERE bug_id=? AND assigned_agent_id=? AND status='awaiting_plan_confirmation') AND NOT EXISTS (SELECT 1 FROM decisions WHERE agent_id=? AND stage='proposal' AND status='pending')",
                    (f"proposal-{task.task_id}-{uuid.uuid4().hex[:8]}", task.bug_id, agent_id, json.dumps(state.get('proposals', []), ensure_ascii=False), task.bug_id, agent_id, agent_id),
                )
            await db.execute(
                "INSERT INTO agent_logs (agent_id, bug_id, log_level, message) VALUES (?, ?, ?, ?)",
                (agent_id, task.bug_id, "error" if agent_status == "error" else "info", message),
            )
            await db.commit()

    async def confirm_proposal(self, agent_id: str, proposal_index: int, feedback: str = '', decision_id: str = None, images: list = None, document: dict = None):
        """有文字或截图反馈则重新分析并等待确认；无反馈才实施当前方案。"""
        from .rich_input import save_rich_input
        db_path = settings.database_url.replace('sqlite+aiosqlite:///', '')
        feedback, image_names, document = save_rich_input(db_path, feedback, images or [], document, max_length=4000)
        has_feedback = bool(feedback or image_names)
        stage = 'analyzing' if has_feedback else 'fixing'
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute('BEGIN IMMEDIATE')
            agent = await (await db.execute('SELECT * FROM agents WHERE agent_id=?', (agent_id,))).fetchone()
            history = await (await db.execute('SELECT * FROM task_history WHERE agent_id=? ORDER BY id DESC LIMIT 1', (agent_id,))).fetchone()
            decision = await (await db.execute("SELECT * FROM decisions WHERE agent_id=? AND stage='proposal' ORDER BY id DESC LIMIT 1", (agent_id,))).fetchone()
            bug = await (await db.execute('SELECT * FROM bugs WHERE assigned_agent_id=?', (agent_id,))).fetchone()
            if not agent or not history or not decision or decision['status'] != 'pending' or agent['status'] != 'awaiting_plan_confirmation' or not bug or bug['status'] != 'awaiting_plan_confirmation':
                raise ValueError('该修复方案已处理或不是当前待确认任务，请刷新页面')
            if decision_id and decision_id != decision['decision_id']:
                raise ValueError('修复方案已更新，请刷新页面后审查新方案')
            saved = json.loads(history['result'] or '{}')
            proposals = saved.get('proposals') or []
            if proposal_index < 0 or proposal_index >= len(proposals):
                raise ValueError('选择的修复方案不存在')
            if not saved.get('worktree_path'):
                raise ValueError('修复工作树记录缺失，请重新执行任务')
            choice = {'proposal_index': proposal_index, 'feedback': feedback, 'images': image_names, 'proposal': proposals[proposal_index]}
            if document is not None:
                choice['document'] = document
            if feedback:
                saved['repair_feedback'] = '\n\n'.join(filter(None, [saved.get('repair_feedback'), feedback]))
            await db.execute("UPDATE decisions SET status='answered', user_choice=?, answered_at=CURRENT_TIMESTAMP WHERE decision_id=?", (json.dumps(choice, ensure_ascii=False), decision['decision_id']))
            await db.execute("UPDATE agents SET status='busy', updated_at=CURRENT_TIMESTAMP WHERE agent_id=?", (agent_id,))
            await db.execute('UPDATE bugs SET status=?, updated_at=CURRENT_TIMESTAMP WHERE bug_id=? AND assigned_agent_id=?', (stage, bug['bug_id'], agent_id))
            await db.execute("UPDATE task_history SET stage=?, status='running', result=?, error=NULL, end_time=NULL WHERE id=?", (stage, json.dumps(saved, ensure_ascii=False), history['id']))
            await db.commit()
        task = Task(task_id=history['task_id'], bug_id=bug['bug_id'], priority=0, created_at=datetime.now(), status='running')
        state = {**saved, 'bug_id': task.bug_id, 'bug_info': {}, 'stage': stage,
                 'root_cause': None if has_feedback else saved.get('root_cause'),
                 'analysis': None if has_feedback else saved.get('analysis'), 'repro': None if has_feedback else saved.get('repro'),
                 'proposals': [] if has_feedback else proposals,
                 'selected_proposal': None if has_feedback else proposals[proposal_index],
                 'modified_files': [], 'test_results': {}, 'waiting_for_user': False,
                 'user_decision': choice, 'error': None, 'require_plan_confirmation': True}
        self.agent_states[agent_id] = {'agent_id': agent_id, 'task': task, 'state': state, 'status': 'running', 'progress': 10 if has_feedback else 45}
        task_handle = asyncio.create_task(self._resume_proposal(agent_id, task, state, has_feedback))
        self.running_agents[agent_id] = task_handle
        task_handle.add_done_callback(lambda t: self._on_agent_complete(agent_id, t))
        return {'agent_id': agent_id, 'status': stage}

    async def _resume_proposal(self, agent_id, task, state, reanalyze):
        await self._resume_from(agent_id, task, state, 'analyze_root_cause' if reanalyze else 'implement_fix')

    async def _resume_from(self, agent_id, task, state, start_node):
        # 详情加载与图执行都属于后台任务，异常由统一任务状态记录。
        task.status = 'running'
        try:
            from ..agent.nodes import fetch_bug_node
            result = await fetch_bug_node(state)
            if result.get('error'):
                raise RuntimeError(result['error'])
            state['bug_info'] = result['bug_info']
            state['input_snapshot'] = result.get('input_snapshot', state.get('input_snapshot', task.input_snapshot))
            async with aiosqlite.connect(settings.database_url.replace('sqlite+aiosqlite:///', '')) as db:
                db.row_factory = aiosqlite.Row
                row = await (await db.execute('SELECT * FROM bugs WHERE bug_id=?', (task.bug_id,))).fetchone()
                config = await resolve_project_config(db, dict(row))
            state['bug_info'].update({key: config.get(key) for key in ('repo_path', 'branch', 'test_command', 'preview_command', 'preview_env_files', 'preview_port')})
            workflow = compile_graph(create_bug_fix_graph(start_node=start_node))
            await self._run_agent_from_state(agent_id, task, state, workflow)
        except asyncio.CancelledError:
            await self._record_cancellation(agent_id, task)
            raise
        except Exception as exc:
            task.status = 'failed'
            self.agent_states[agent_id].update(status='failed', error=str(exc))
            state.update(stage='failed', error=str(exc))
            await self._save_status(agent_id, task, 'error', 'failed', f'任务执行失败：{exc}')

    async def _run_agent_from_state(self, agent_id, task, state, app):
        try:
            async for output in app.astream(state):
                for value in output.values():
                    if isinstance(value, dict):
                        self.agent_states[agent_id]['state'].update(value)
                current = self.agent_states[agent_id]['state']
                if current.get('error'):
                    current['failed_node'] = next(iter(output), None)
                    raise RuntimeError(current['error'])
                stage = current.get('stage', 'fixing')
                self.agent_states[agent_id]['progress'] = STAGE_PROGRESS.get(stage, self.agent_states[agent_id].get('progress', 0))
                bug_status = {'analyzing': 'analyzing', 'proposing': 'analyzing', 'awaiting_plan_confirmation': 'awaiting_plan_confirmation', 'verifying': 'testing', 'awaiting_confirmation': 'awaiting_confirmation'}.get(stage, 'fixing')
                await self._save_status(agent_id, task, 'busy', bug_status, f'当前阶段：{stage}')
            current = self.agent_states[agent_id]['state']
            if current.get('stage') == 'awaiting_plan_confirmation':
                task.status = 'awaiting_plan_confirmation'
                self.agent_states[agent_id].update(status='awaiting_plan_confirmation', progress=35)
                await self._save_status(agent_id, task, 'awaiting_plan_confirmation', 'awaiting_plan_confirmation', '已根据用户反馈重新分析并生成方案，请再次确认后继续修改代码')
                return
            if not current.get('modified_files') or current.get('test_results', {}).get('passed') is not True:
                raise ValueError('缺少实际修改或成功验证结果，不能进入人工确认')
            task.status = 'awaiting_confirmation'
            current.update(stage='awaiting_confirmation', waiting_for_user=True)
            self.agent_states[agent_id].update(status='awaiting_confirmation', progress=90)
            await self._save_status(agent_id, task, 'awaiting_confirmation', 'awaiting_confirmation', '验证通过，请审查修改并确认 Bug 是否已修复')
        except Exception as exc:
            task.status = 'failed'
            self.agent_states[agent_id].update(status='failed', error=str(exc))
            self.agent_states[agent_id]['state'].update(stage='failed', error=str(exc))
            await self._save_status(agent_id, task, 'error', 'failed', f'任务执行失败：{exc}')

    @staticmethod
    async def _resume_point(saved: dict) -> tuple:
        """返回 (起点节点, 原因)；起点为 None 表示原任务无法续跑，需新建任务。"""
        from .repair_executor import git
        worktree = saved.get('worktree_path')
        if not worktree:
            return 'fetch_bug', '尚未创建修复工作树'
        if not Path(worktree).is_dir():
            return 'fetch_bug', '原修复工作树已不存在'
        try:
            head = await git(worktree, 'rev-parse', 'HEAD')
            branch = await git(worktree, 'branch', '--show-current')
        except ValueError:
            return 'fetch_bug', '原修复工作树已损坏'
        if head != saved.get('base_commit') or branch != saved.get('repair_branch'):
            return None, '原修复工作树已生成提交或切换分支'
        if saved.get('failed_node') == 'verify_fix' and not saved.get('test_results'):
            return 'verify_fix', '验证命令未能执行'
        if saved.get('selected_proposal'):
            return 'implement_fix', '已确认修复方案'
        if saved.get('root_cause') and saved.get('proposals'):
            return 'design_proposal', '已完成根因分析'
        return 'analyze_root_cause', '已创建修复工作树'

    async def retry_agent(self, agent_id: str, priority: int = 5) -> dict:
        """失败任务从失败阶段继续执行，复用原工作树与已完成阶段的结果。

        已提交验收（存在 verify 决策）的任务交付记录与 task_id 绑定，按反馈新建任务重新修复。
        """
        if not self._running:
            raise RuntimeError("任务调度器未启动")
        async with self._agent_control_lock:
            db_path = settings.database_url.replace('sqlite+aiosqlite:///', '')
            async with aiosqlite.connect(db_path) as db:
                db.row_factory = aiosqlite.Row
                agent = await (await db.execute('SELECT * FROM agents WHERE agent_id=?', (agent_id,))).fetchone()
                if agent is None:
                    raise KeyError(agent_id)
                history = await (await db.execute('SELECT * FROM task_history WHERE agent_id=? ORDER BY id DESC LIMIT 1', (agent_id,))).fetchone()
                bug = await (await db.execute('SELECT * FROM bugs WHERE bug_id=?', (agent['current_bug_id'],))).fetchone()
                reviewed = await (await db.execute("SELECT 1 FROM decisions WHERE agent_id=? AND stage='verify'", (agent_id,))).fetchone()
                supplement = await read_supplement(db, agent['current_bug_id'])
            if agent['status'] != 'error' or agent_id in self.running_agents or not history:
                raise ValueError('只有已失败或已停止的任务可以重新执行')
            # Bug 被其他任务删除释放后，原任务可重新认领并续跑。
            claimable = bug and (bug['assigned_agent_id'] == agent_id and bug['status'] == 'failed' or bug['assigned_agent_id'] is None and bug['status'] in ('pending', 'failed'))
            if not claimable or (bug['source_status'] or '') in ('已修复', '已关闭', '暂不修复'):
                raise ValueError('该 Bug 已由其他任务处理或不可修复，请刷新页面')
            saved = json.loads(history['result'] or '{}')
            previous_supplement = saved.get('input_snapshot', {}).get('supplement', {'notes': '', 'image_names': [], 'revision': 0})
            if supplement != previous_supplement:
                start_node, reason = None, '补充修复信息已更新，需要重新分析根因'
            elif reviewed:
                start_node, reason = None, '上次修复已提交验收'
            else:
                start_node, reason = await self._resume_point(saved)
            if start_node is None:
                bug_id = bug['bug_id']
            else:
                stage, bug_status, progress, label = RESUME_POINTS[start_node]
                message = f'重新执行：{reason}，从「{label}」继续'
                if start_node != 'fetch_bug':
                    message += f"，复用工作树 {saved['worktree_path']}"
                async with aiosqlite.connect(db_path) as db:
                    await db.execute('BEGIN IMMEDIATE')
                    cursor = await db.execute(
                        "UPDATE bugs SET status=?, assigned_agent_id=?, updated_at=CURRENT_TIMESTAMP WHERE bug_id=? "
                        "AND (assigned_agent_id=? AND status='failed' OR assigned_agent_id IS NULL AND status IN ('pending', 'failed'))",
                        (bug_status, agent_id, bug['bug_id'], agent_id),
                    )
                    if cursor.rowcount != 1:
                        raise ValueError('该 Bug 已由其他任务处理，请刷新页面')
                    await db.execute("UPDATE agents SET status='busy', updated_at=CURRENT_TIMESTAMP WHERE agent_id=?", (agent_id,))
                    await db.execute("UPDATE task_history SET stage='queued', status='running', error=NULL, end_time=NULL WHERE id=?", (history['id'],))
                    await db.execute("INSERT INTO agent_logs (agent_id, bug_id, log_level, message) VALUES (?, ?, 'info', ?)", (agent_id, bug['bug_id'], message))
                    await db.commit()
        if start_node is None:
            task_id = await self.submit_task(bug_id, priority)
            return {'agent_id': f'agent-{task_id}', 'task_id': task_id, 'mode': 'restart', 'reason': reason}

        task = Task(task_id=history['task_id'], bug_id=bug['bug_id'], priority=priority, created_at=datetime.now(), start_node=start_node,
                    input_snapshot=saved.get('input_snapshot') or {'supplement': supplement})
        if start_node != 'fetch_bug':
            cleared = {
                'analyze_root_cause': {'root_cause': None, 'analysis': None, 'repro': None, 'review': None, 'proposals': [], 'selected_proposal': None, 'modified_files': []},
                'design_proposal': {'selected_proposal': None, 'modified_files': []},
            }.get(start_node, {})
            previous_error = history['error'] or saved.get('error')
            task.resume_state = {
                **saved, **cleared,
                'bug_id': bug['bug_id'], 'bug_info': {}, 'stage': stage, 'test_results': {},
                'waiting_for_user': False, 'user_decision': None, 'require_plan_confirmation': True,
                'error': None, 'failed_node': None,
                'retry_context': f'上次执行失败：{previous_error}。请针对该失败继续，不要重复同样的问题。' if previous_error else None,
            }
        self.agent_states[agent_id] = {'agent_id': agent_id, 'task': task, 'state': task.resume_state or {'stage': 'queued'}, 'status': 'queued', 'progress': progress}
        self.task_queue.put_nowait((-priority, next(self._sequence), task))
        logger.info(f'Agent {agent_id} retry queued from {start_node}')
        return {'agent_id': agent_id, 'task_id': task.task_id, 'mode': 'resume', 'start_node': start_node, 'stage_label': label, 'reason': reason}

    async def _record_cancellation(self, agent_id: str, task: Task):
        runtime = self.agent_states.get(agent_id, {})
        reason = runtime.get('cancellation_reason', '任务已取消')
        task.status = 'cancelled'
        runtime['status'] = 'cancelled'
        runtime.setdefault('state', {}).update(stage='cancelled', error=reason)
        await self._save_status(agent_id, task, 'error', 'failed', reason)

    async def stop_agent(self, agent_id: str):
        """停止排队或执行中的任务，等待退出后才返回。"""
        async with self._agent_control_lock:
            await self._stop_agent(agent_id)

    async def _stop_agent(self, agent_id: str):
        db_path = settings.database_url.replace("sqlite+aiosqlite:///", "")
        async with aiosqlite.connect(db_path) as db:
            delivering = await (await db.execute("SELECT 1 FROM decisions WHERE agent_id=? AND status='delivering'", (agent_id,))).fetchone()
            if delivering:
                raise ValueError('代码正在交付，请等待完成后再停止或删除任务')
            cursor = await db.execute("SELECT status, current_bug_id FROM agents WHERE agent_id=?", (agent_id,))
            row = await cursor.fetchone()
        if row is None:
            raise KeyError(agent_id)

        runtime = self.agent_states.get(agent_id)
        running = self.running_agents.get(agent_id)
        if row[0] not in ("busy", "paused") and not running:
            return
        if runtime:
            runtime['cancellation_reason'] = '任务已取消'
            runtime["task"].status = "cancelled"
            runtime["status"] = "cancelled"
            runtime["state"]["stage"] = "cancelled"
        if running:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

        # 也处理服务重启后留下的 busy 记录，以及尚未开始执行的排队任务。
        async with aiosqlite.connect(db_path) as db:
            await db.execute("UPDATE agents SET status='error', updated_at=CURRENT_TIMESTAMP WHERE agent_id=?", (agent_id,))
            await db.execute(
                "UPDATE bugs SET status='failed', updated_at=CURRENT_TIMESTAMP "
                "WHERE assigned_agent_id=? AND status IN ('analyzing', 'fixing', 'testing')",
                (agent_id,),
            )
            await db.execute(
                "INSERT INTO agent_logs (agent_id, bug_id, log_level, message) VALUES (?, ?, 'info', '任务已停止，可重新执行')",
                (agent_id, row[1]),
            )
            await db.commit()

    async def delete_agent(self, agent_id: str):
        """先停止执行，再在事务中删除 Agent；保留 Bug 及其他 Agent。"""
        from .repair_review import _delivery_lock
        # 删除不能穿插在交付或清理的数据库事务之间。
        async with self._agent_control_lock, _delivery_lock:
            await self._stop_agent(agent_id)
            from .preview_manager import stop_preview
            await stop_preview(agent_id, forget=True)
            db_path = settings.database_url.replace("sqlite+aiosqlite:///", "")
            async with aiosqlite.connect(db_path) as db:
                await db.execute("PRAGMA foreign_keys=ON")
                await db.execute("BEGIN IMMEDIATE")
                row = await (await db.execute('''SELECT a.worktree_path, h.result FROM agents a
                    LEFT JOIN task_history h ON h.id=(SELECT MAX(id) FROM task_history WHERE agent_id=a.agent_id)
                    WHERE a.agent_id=?''', (agent_id,))).fetchone()
                saved = json.loads(row[1] or '{}') if row else {}
                await db.execute(
                    "UPDATE bugs SET assigned_agent_id=NULL, "
                    "status=CASE WHEN status IN ('analyzing', 'fixing', 'testing', 'awaiting_plan_confirmation', 'awaiting_confirmation') THEN 'failed' ELSE status END, "
                    "updated_at=CURRENT_TIMESTAMP WHERE assigned_agent_id=?",
                    (agent_id,),
                )
                for table in ("decisions", "task_history", "agent_logs"):
                    await db.execute(f"DELETE FROM {table} WHERE agent_id=?", (agent_id,))
                await db.execute("DELETE FROM agents WHERE agent_id=?", (agent_id,))
                await db.commit()
            state = self.agent_states.pop(agent_id, None) or {}
            worktree = (row and row[0]) or state.get("state", {}).get("worktree_path")
            if worktree:
                try:
                    await discard_worktree(worktree, saved.get('repair_branch') or state.get('state', {}).get('repair_branch'))
                except (ValueError, OSError) as exc:
                    logger.warning(f"Agent {agent_id} 已删除，工作树未清理（下次启动会重试）：{exc}")

    def _on_agent_complete(self, agent_id: str, task: asyncio.Task):
        """
        Agent 完成回调

        Args:
            agent_id: Agent ID
            task: asyncio Task
        """
        # 从运行列表中移除
        if agent_id in self.running_agents:
            del self.running_agents[agent_id]

        logger.info(f"Agent {agent_id} removed from running list. Active: {len(self.running_agents)}")

    def get_agent_status(self, agent_id: str) -> Optional[Dict]:
        """
        获取 Agent 状态

        Args:
            agent_id: Agent ID

        Returns:
            Agent 状态
        """
        return self.agent_states.get(agent_id)

    def list_agents(self) -> List[Dict]:
        """
        列出所有 Agent 状态

        Returns:
            Agent 状态列表
        """
        return list(self.agent_states.values())


# 全局单例
_orchestrator: Optional[MultiAgentOrchestrator] = None


def get_orchestrator() -> MultiAgentOrchestrator:
    """获取全局协调器实例"""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = MultiAgentOrchestrator()
    return _orchestrator
