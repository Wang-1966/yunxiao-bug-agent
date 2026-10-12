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


STAGE_ORDER = (
    'fetch_bug',
    'prepare_repository',
    'analyze_root_cause',
    'add_logging',
    'design_proposal',
    'implement_fix',
    'verify_fix',
)


def _unless_error(target: str):
    return lambda state: END if state.get('error') else target


def create_bug_fix_graph(start_node: str = "fetch_bug") -> StateGraph:
    """创建 Bug 修复状态图；从 start_node 开始，只包含其后的节点。"""
    if start_node not in STAGE_ORDER or start_node == 'add_logging':
        raise ValueError(f'不支持从 {start_node} 开始执行')
    nodes = {
        'fetch_bug': fetch_bug_node,
        'prepare_repository': prepare_repository_node,
        'analyze_root_cause': analyze_root_cause_node,
        'add_logging': add_logging_node,
        'design_proposal': design_proposal_node,
        'implement_fix': implement_fix_node,
        'verify_fix': verify_fix_node,
    }
    included = STAGE_ORDER[STAGE_ORDER.index(start_node):]

    workflow = StateGraph(BugFixState)
    for name in included:
        workflow.add_node(name, nodes[name])
    workflow.set_entry_point(start_node)

    if 'fetch_bug' in included:
        workflow.add_conditional_edges('fetch_bug', _unless_error('prepare_repository'), {END: END, 'prepare_repository': 'prepare_repository'})
        workflow.add_conditional_edges('prepare_repository', _unless_error('analyze_root_cause'), {END: END, 'analyze_root_cause': 'analyze_root_cause'})

    # 分析后：需要日志 -> 添加日志 -> 设计方案
    #         不需要日志 -> 设计方案
    if 'analyze_root_cause' in included:
        workflow.add_conditional_edges(
            "analyze_root_cause",
            should_add_logging,
            {"add_logging": "add_logging", "design_proposal": "design_proposal", END: END},
        )
        workflow.add_conditional_edges('add_logging', _unless_error('design_proposal'), {END: END, 'design_proposal': 'design_proposal'})
    if 'design_proposal' in included:
        # 调度器传入 require_plan_confirmation 时暂停；底层图的历史直接调用保持兼容。
        workflow.add_conditional_edges(
            'design_proposal',
            lambda state: END if state.get('error') or state.get('require_plan_confirmation') else 'implement_fix',
            {END: END, 'implement_fix': 'implement_fix'},
        )

    # 实施修复后 -> 验证
    if 'implement_fix' in included:
        workflow.add_conditional_edges('implement_fix', _unless_error('verify_fix'), {END: END, 'verify_fix': 'verify_fix'})

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
