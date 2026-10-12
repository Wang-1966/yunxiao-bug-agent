"""决策数据模型"""
from pydantic import BaseModel, Field
from typing import Optional, List, Any
from datetime import datetime


class Decision(BaseModel):
    """决策信息"""
    decision_id: str = Field(..., description="决策 ID")
    bug_id: str = Field(..., description="Bug ID")
    agent_id: str = Field(..., description="Agent ID")
    stage: str = Field(..., description="阶段")
    question: str = Field(..., description="问题")
    options: List[Any] = Field(..., description="选项列表")
    user_choice: Optional[Any] = Field(None, description="用户选择")
    status: str = Field(default="pending", description="状态: pending, answered, timeout")
    created_at: Optional[datetime] = None
    answered_at: Optional[datetime] = None


class DecisionRequest(BaseModel):
    """决策请求"""
    agent_id: str
    bug_id: str
    stage: str
    question: str
    options: List[Any]


class DecisionResponse(BaseModel):
    """决策响应"""
    decision_id: str
    user_choice: Any
