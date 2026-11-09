from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from src.agent.nodes import _should_require_user_confirmation
from src.core.analysis_decisions import normalize_decisions
from src.core.agent_tools import ToolError, Workspace
from src.core.repair_agent import _analysis_tool
from src.models.confirmation_config import ConfirmationConfig


def report():
    return {
        'bug_info': {'title': 'MathLive API 空字段删除', 'priority': 'normal'},
        'root_cause': 'NodeView model 字段没有空值处理', 'confidence': 'high',
        'repro': {'reproduced': True},
        'analysis': {'decision_schema_version': 1, 'decision_questions': [], 'open_questions': [],
                     'assumptions': ['保留非空公式的原有删除行为'], 'verification_limits': ['未覆盖整页布局']},
        'proposals': [{'name': '修复键盘路由', 'files': ['src/math.ts'],
                       'impact': {'categories': [], 'evidence': 'src/math.ts 仅补齐空节点删除，不修改接口和持久化结构'}}],
    }


def requires(state, config=None):
    return _should_require_user_confirmation(state, state['proposals'], config or ConfirmationConfig())


def test_technical_notes_and_keyword_mentions_do_not_block():
    state = report()
    state['proposals'] *= 3
    assert not requires(state)


@pytest.mark.parametrize('category', ['data_deletion', 'permission_change', 'breaking_api', 'schema_migration', 'external_side_effect', 'product_behavior_change'])
def test_actual_high_impact_requires_confirmation(category):
    state = report()
    state['proposals'][0]['impact']['categories'] = [category]
    assert requires(state)
    # 标题豁免不能覆盖新报告已经识别的实际风险。
    assert requires(state, ConfirmationConfig(auto_approve_patterns=['.*']))


def test_user_decision_still_blocks():
    state = report()
    state['analysis']['decision_questions'] = [{'question': 'Delete 是否沿用原行为？'}]
    assert requires(state)


@pytest.mark.parametrize('change', ['no_repro', 'low_confidence', 'large', 'critical', 'missing_impact'])
def test_existing_safety_checks_remain(change):
    state = report()
    if change == 'no_repro': state['repro']['reproduced'] = False
    if change == 'low_confidence': state['confidence'] = 'low'
    if change == 'large': state['proposals'][0]['files'] = list('abcdef')
    if change == 'critical': state['bug_info']['priority'] = 'critical'
    if change == 'missing_impact': state['proposals'][0].pop('impact')
    assert requires(state)


def test_old_reports_keep_old_keyword_policy():
    state = report()
    state['analysis'].pop('decision_schema_version')
    assert requires(state)
    assert requires(report(), ConfirmationConfig(force_confirm_patterns=['MathLive']))


def decisions():
    return {'decision_questions': [{'question': 'Delete 要删除空公式吗？', 'options': ['保留原行为', '统一删除'],
            'recommended_option': 0, 'reason': '两种答案对应不同交互', 'investigated': '已查键盘映射及测试，没有产品约定'}],
            'assumptions': ['保留已有行为'], 'verification_limits': ['整页排版未覆盖']}


def test_classification_keeps_legacy_question_projection():
    data = decisions()
    result = normalize_decisions(data)
    assert result['open_questions'] == ['Delete 要删除空公式吗？']
    assert result['verification_limits'] == data['verification_limits']
    assert normalize_decisions({'open_questions': ['旧问题']}) == {}


@pytest.mark.parametrize('change', ['missing', 'invalid_index', 'blank_option', 'blank_reason'])
def test_invalid_decisions_rejected(change):
    data = decisions()
    if change == 'missing': data.pop('assumptions')
    if change == 'invalid_index': data['decision_questions'][0]['recommended_option'] = 9
    if change == 'blank_option': data['decision_questions'][0]['options'][0] = ' '
    if change == 'blank_reason': data['decision_questions'][0]['reason'] = ' '
    with pytest.raises(ToolError): normalize_decisions(data)


@pytest.mark.asyncio
async def test_analysis_tool_preserves_classification_and_impact(tmp_path, monkeypatch):
    monkeypatch.setattr('src.core.repair_agent.changed_files', AsyncMock(return_value=[]))
    holder = {}
    tool = _analysis_tool(Workspace(tmp_path), tmp_path, holder)
    args = {**decisions(), 'open_questions': ['不该进入待决策的环境说明'],
            'symptom': {'expected': '删除', 'actual': '未删除', 'steps': ['清空公式'], 'acceptance': ['节点删除']},
            'root_cause': '键盘路由缺失', 'trigger_paths': [],
            'hypotheses': [{'description': '路由缺失', 'evidence': 'math.ts:1', 'status': 'confirmed'},
                           {'description': '保存延迟', 'evidence': 'math.ts:2', 'status': 'rejected'}],
            'proposals': [{**report()['proposals'][0], 'steps': ['补充路由']}],
            'reproduction': {'not_reproducible_reason': '运行环境缺失'}}
    result = await tool.handler(args)
    assert result['open_questions'] == ['Delete 要删除空公式吗？']
    assert result['proposals'][0]['files'] == ['src/math.ts']
    assert result['proposals'][0]['impact']['categories'] == []
    broken = deepcopy(args)
    broken['proposals'][0].pop('impact')
    with pytest.raises(ToolError): await tool.handler(broken)
    legacy = deepcopy(args)
    for key in decisions(): legacy.pop(key)
    assert 'decision_schema_version' not in await tool.handler(legacy)


def test_unused_risky_alternative_does_not_block_recommended_safe_fix():
    state = report()
    alternative = deepcopy(state['proposals'][0])
    alternative['impact']['categories'] = ['breaking_api']
    state['proposals'].append(alternative)
    assert not requires(state)
