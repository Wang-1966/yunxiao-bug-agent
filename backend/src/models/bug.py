"""Bug 数据模型"""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class Bug(BaseModel):
    """Bug 信息"""
    bug_id: str = Field(..., description="Bug ID")
    title: str = Field(..., description="标题")
    description: Optional[str] = Field(None, description="描述")
    priority: str = Field(..., description="优先级: critical, important, normal")
    status: str = Field(default="pending", description="状态")
    source: str = Field(..., description="来源: yunxiao")
    source_url: Optional[str] = Field(None, description="来源 URL")
    source_status: Optional[str] = None
    source_project: Optional[str] = None
    source_project_id: Optional[str] = None
    test_command: Optional[str] = None
    repo_path: Optional[str] = Field(None, description="仓库路径")
    branch: Optional[str] = Field(None, description="分支")
    assigned_agent_id: Optional[str] = Field(None, description="分配的 Agent ID")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None


class BugCreate(BaseModel):
    """创建 Bug 请求"""
    bug_id: str
    title: str
    description: Optional[str] = None
    priority: str
    source: str = "yunxiao"
    source_url: Optional[str] = None
    repo_path: Optional[str] = None
    branch: Optional[str] = None
    test_command: Optional[str] = None


class BugUpdate(BaseModel):
    """更新 Bug 请求"""
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    assigned_agent_id: Optional[str] = None
    test_command: Optional[str] = None


class BugListResponse(BaseModel):
    """Bug 列表响应"""
    total: int
    items: list[Bug]
    page: int = 1
    page_size: int = 20
