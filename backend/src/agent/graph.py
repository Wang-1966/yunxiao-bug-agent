"""LangGraph 状态图定义"""
from langgraph.graph import StateGraph, END
from loguru import logger

from .state import BugFixState
from .nodes import (
    fetch_bug_node,
    analyze_root_cause_node,
    add_logging_node,
    design_proposal_node,
    implement_fix_node,
    verify_fix_node,
    prepare_repository_node,
)


def should_add_logging(state: BugFixState) -> str:
    """判断是否需要添加日志"""
    if state.get('error'):
        return END
    if state.get("need_logging"):
        return "add_logging"
    return "design_proposal"


def should_wait_user(state: BugFixState) -> str:
    """判断是否需要等待用户决策"""
    confidence = state.get("confidence", "medium")
    if confidence == "low":
        return "wait_user"
    return "design_proposal"


def create_bug_fix_graph(start_node: str = "fetch_bug") -> StateGraph:
    """创建 Bug 修复状态图"""

    # 创建状态图
    workflow = StateGraph(BugFixState)

    # 添加节点
    if start_node == 'fetch_bug':
        workflow.add_node("fetch_bug", fetch_bug_node)
        workflow.add_node("prepare_repository", prepare_repository_node)
    if start_node != 'implement_fix':
        workflow.add_node("analyze_root_cause", analyze_root_cause_node)
        workflow.add_node("add_logging", add_logging_node)
        workflow.add_node("design_proposal", design_proposal_node)
    workflow.add_node("implement_fix", implement_fix_node)
    workflow.add_node("verify_fix", verify_fix_node)

    # 设置入口点
    workflow.set_entry_point(start_node)

    # 添加边（定义流程）
    if start_node == 'fetch_bug':
        workflow.add_conditional_edges('fetch_bug', lambda state: END if state.get('error') else 'prepare_repository', {END: END, 'prepare_repository': 'prepare_repository'})
        workflow.add_conditional_edges('prepare_repository', lambda state: END if state.get('error') else 'analyze_root_cause', {END: END, 'analyze_root_cause': 'analyze_root_cause'})

    # 分析后：需要日志 -> 添加日志 -> 设计方案
    #         不需要日志 -> 设计方案
    if start_node != 'implement_fix':
        workflow.add_conditional_edges(
            "analyze_root_cause",
            should_add_logging,
            {"add_logging": "add_logging", "design_proposal": "design_proposal", END: END},
        )
        workflow.add_conditional_edges('add_logging', lambda state: END if state.get('error') else 'design_proposal', {END: END, 'design_proposal': 'design_proposal'})
        # 调度器传入 require_plan_confirmation 时暂停；底层图的历史直接调用保持兼容。
        workflow.add_conditional_edges(
            'design_proposal',
            lambda state: END if state.get('error') or state.get('require_plan_confirmation') else 'implement_fix',
            {END: END, 'implement_fix': 'implement_fix'},
        )

    # 实施修复后 -> 验证
    workflow.add_conditional_edges('implement_fix', lambda state: END if state.get('error') else 'verify_fix', {END: END, 'verify_fix': 'verify_fix'})

    # 验证完成 -> 结束
    workflow.add_edge("verify_fix", END)


    logger.info("Bug fix workflow graph created")

    return workflow


def compile_graph(workflow: StateGraph):
    """编译状态图"""
    # TODO: 添加 checkpointer 用于状态持久化
    # from langgraph.checkpoint.sqlite import SqliteSaver
    # checkpointer = SqliteSaver.from_conn_string("checkpoint.db")
    # return workflow.compile(checkpointer=checkpointer)

    return workflow.compile()
