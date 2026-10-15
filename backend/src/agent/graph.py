"""LangGraph 状态图定义"""
from langgraph.graph import StateGraph, END
from loguru import logger

from .state import BugFixState
from .nodes import (
    fetch_bug_node,
    analyze_root_cause_node,
    design_proposal_node,
    implement_fix_node,
    verify_fix_node,
    prepare_repository_node,
)


STAGE_ORDER = (
    'fetch_bug',
    'prepare_repository',
    'analyze_root_cause',
    'design_proposal',
    'implement_fix',
    'verify_fix',
)


def _unless_error(target: str):
    return lambda state: END if state.get('error') else target


def create_bug_fix_graph(start_node: str = "fetch_bug") -> StateGraph:
    """创建 Bug 修复状态图；从 start_node 开始，只包含其后的节点。"""
    if start_node not in STAGE_ORDER:
        raise ValueError(f'不支持从 {start_node} 开始执行')
    nodes = {
        'fetch_bug': fetch_bug_node,
        'prepare_repository': prepare_repository_node,
        'analyze_root_cause': analyze_root_cause_node,
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

    # 分析（含复现与审查）后直接进入方案确认；方案由分析 Agent 一并产出。
    if 'analyze_root_cause' in included:
        workflow.add_conditional_edges('analyze_root_cause', _unless_error('design_proposal'), {END: END, 'design_proposal': 'design_proposal'})
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

    # 验证失败交回修复；通过或连续失败后结束。
    if 'verify_fix' in included and 'implement_fix' in included:
        workflow.add_conditional_edges(
            'verify_fix',
            lambda state: END if state.get('error') or (state.get('test_results') or {}).get('passed') is True else 'implement_fix',
            {END: END, 'implement_fix': 'implement_fix'},
        )
    elif 'verify_fix' in included:
        workflow.add_edge('verify_fix', END)


    logger.info("Bug fix workflow graph created")

    return workflow


def compile_graph(workflow: StateGraph):
    """编译状态图"""
    # TODO: 添加 checkpointer 用于状态持久化
    # from langgraph.checkpoint.sqlite import SqliteSaver
    # checkpointer = SqliteSaver.from_conn_string("checkpoint.db")
    # return workflow.compile(checkpointer=checkpointer)

    return workflow.compile()
