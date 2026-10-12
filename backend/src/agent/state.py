"""Agent 状态定义"""
from typing import TypedDict, Optional, List, Dict, Any


class BugFixState(TypedDict):
    """Bug 修复状态"""

    # Bug 信息
    bug_id: str
    bug_info: Dict[str, Any]
    worktree_path: Optional[str]
    repair_branch: Optional[str]
    base_commit: Optional[str]
    fix_summary: Optional[str]
    repair_feedback: Optional[str]
    visual_context: Optional[Dict[str, Any]]
    source_context: Optional[Dict[str, Any]]

    # 当前阶段
    stage: str  # idle, fetching, analyzing, proposing, fixing, verifying, completed

    # 分析结果
    root_cause: Optional[str]
    confidence: str  # high, medium, low
    need_logging: bool

    # 方案设计
    proposals: List[Dict[str, Any]]
    selected_proposal: Optional[Dict[str, Any]]

    # 实施结果
    modified_files: List[str]
    git_commit_hash: Optional[str]

    # 验证结果
    test_results: Dict[str, Any]

    # 用户决策
    waiting_for_user: bool
    user_decision: Optional[Dict[str, Any]]
    require_plan_confirmation: bool

    # 错误信息
    error: Optional[str]
