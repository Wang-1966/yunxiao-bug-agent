"""Agent 状态定义"""
from typing import TypedDict, Optional, List, Dict, Any


class BugFixState(TypedDict):
    """Bug 修复状态"""

    # Bug 信息
    bug_id: str
    bug_info: Dict[str, Any]
    input_snapshot: Dict[str, Any]
    worktree_path: Optional[str]
    repair_branch: Optional[str]
    base_commit: Optional[str]
    fix_summary: Optional[str]
    repair_feedback: Optional[str]
    previous_attempt: Optional[Dict[str, Any]]
    visual_context: Optional[Dict[str, Any]]
    source_context: Optional[Dict[str, Any]]

    # 当前阶段
    stage: str  # idle, fetching, analyzing, proposing, fixing, verifying, completed

    # 分析结果
    root_cause: Optional[str]
    confidence: str  # high, medium, low
    need_logging: bool
    analysis: Optional[Dict[str, Any]]  # 症状契约、假设、触发路径
    repro: Optional[Dict[str, Any]]  # 复现命令、测试文件及其哈希、修复前后结果
    review: Optional[Dict[str, Any]]  # 最近一次独立审查结论

    # 方案设计
    proposals: List[Dict[str, Any]]
    selected_proposal: Optional[Dict[str, Any]]

    # 实施结果
    modified_files: List[str]
    git_commit_hash: Optional[str]

    # 验证结果
    test_results: Dict[str, Any]
    verify_rounds: Optional[int]

    # 用户决策
    waiting_for_user: bool
    user_decision: Optional[Dict[str, Any]]
    require_plan_confirmation: bool

    # 错误信息
    error: Optional[str]
    failed_node: Optional[str]
    retry_context: Optional[str]
