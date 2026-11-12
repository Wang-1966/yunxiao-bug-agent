import pytest
from pydantic import ValidationError

from src.core.attention import attention_item
from src.core.repair_summary import RepairSummary, repair_summary


def test_structured_summary_is_used_instead_of_full_engineering_notes():
    brief = {'root_cause': '连续数字无法在行内换行。', 'solution': '调整普通段落的换行规则，保留公式行为。'}
    saved = {'repair_summary': brief, 'root_cause': '详细分析' * 500, 'fix_summary': '运行命令' * 500}
    assert repair_summary(saved) == brief
    item = attention_item({'agent_id': 'one', 'status': 'awaiting_confirmation'}, {'assigned_agent_id': 'one'}, saved,
                          [{'stage': 'verify', 'status': 'pending', 'question': '验收', 'decision_id': 'v', 'created_at': 'now'}], None)
    assert item['summary'] == f"根因：{brief['root_cause']}\n修复方案：{brief['solution']}"
    assert '运行命令' not in item['summary']


def test_legacy_summary_keeps_first_sentence_without_changing_saved_details():
    saved = {'analysis': {'root_cause': '连续数字换行时被整体移到下一行。详细证据 src/editor.ts:30。'},
             'fix_summary': '已调整普通段落的换行规则。验证：node test.mjs 通过。'}
    assert repair_summary(saved) == {'root_cause': '连续数字换行时被整体移到下一行。', 'solution': '已调整普通段落的换行规则。'}
    assert 'node test.mjs' in saved['fix_summary']
    saved['fix_summary'] = '未分句的历史说明' * 500
    assert len(repair_summary(saved)['solution']) == 180
    assert repair_summary({})['solution'] == '尚未形成修复结果'


@pytest.mark.parametrize('changes', [{'solution': ''}, {'root_cause': '长' * 181}, {'solution': '长' * 181}])
def test_summary_rejects_empty_or_verbose_conclusions(changes):
    with pytest.raises(ValidationError):
        RepairSummary.model_validate({'root_cause': '原因', 'solution': '方案', **changes})
