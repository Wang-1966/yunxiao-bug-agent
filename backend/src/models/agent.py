"""Agent 数据模型"""
from pydantic import BaseModel, Field, StrictBool
from typing import Optional
from datetime import datetime


class Agent(BaseModel):
    """Agent 信息"""
    agent_id: str = Field(..., description="Agent ID")
    name: str = Field(..., description="Agent 名称")
    status: str = Field(default="idle", description="状态: idle, busy, paused, error")
    current_bug_id: Optional[str] = Field(None, description="当前处理的 Bug ID")
    worktree_path: Optional[str] = Field(None, description="工作树路径")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class AgentStatus(BaseModel):
    """Agent 状态详情"""
    agent_id: str
    name: str
    status: str
    current_bug: Optional[dict] = None
    current_stage: Optional[str] = None
    progress: int = Field(default=0, description="进度百分比 0-100")
    logs: list[str] = Field(default_factory=list, description="最近日志")
    worktree_path: Optional[str] = None
    repair_branch: Optional[str] = None
    base_commit: Optional[str] = None
    git_commit_hash: Optional[str] = None
    modified_files: list[str] = Field(default_factory=list)
    root_cause: Optional[str] = None
    fix_summary: Optional[str] = None
    test_results: dict = Field(default_factory=dict)
    confirmation: Optional[dict] = None
    error: Optional[str] = None


class RepairConfirmation(BaseModel):
    confirmed: StrictBool
    feedback: str = Field(default='', max_length=4000)
    images: list[str] = Field(default_factory=list, max_length=4)
    document: Optional[dict] = None
    git_commit_hash: Optional[str] = Field(default=None, pattern=r'^[0-9a-f]{40,64}$')


class ProposalConfirmation(BaseModel):
    proposal_index: int = Field(..., ge=0)
    feedback: str = Field(default='', max_length=4000)
    images: list[str] = Field(default_factory=list, max_length=4)
    document: Optional[dict] = None
    decision_id: Optional[str] = None


class TaskSubmitRequest(BaseModel):
    """提交任务请求"""
    bug_id: str = Field(..., description="Bug ID")
    priority: int = Field(default=0, description="优先级，数字越大越优先")
    supplement_revision: Optional[int] = Field(default=None, ge=0)


class TaskSubmitResponse(BaseModel):
    """提交任务响应"""
    task_id: str
    bug_id: str
    message: str


class AgentControlRequest(BaseModel):
    """Agent 控制请求"""
    action: str = Field(..., description="操作: pause, resume, stop")


class AgentLog(BaseModel):
    """Agent 日志"""
    agent_id: str
    bug_id: Optional[str] = None
    log_level: str
    message: str
    metadata: Optional[dict] = None
    created_at: Optional[datetime] = None
