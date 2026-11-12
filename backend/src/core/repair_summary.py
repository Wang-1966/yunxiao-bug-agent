"""面向用户的修复摘要，与完整工程记录分开保存。"""
import re

from pydantic import BaseModel, ConfigDict, Field


class RepairSummary(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    root_cause: str = Field(min_length=1, max_length=180, description='用一两句中文概括问题为什么发生，不列文件行号、命令或调查过程')
    solution: str = Field(min_length=1, max_length=180, description='用一两句中文概括实际修复及效果，不列测试日志；影响使用的兼容性取舍必须简短说明')


def _legacy_brief(text):
    """旧记录仅提取首句，完整内容仍在详情中；不推断未实施的方案。"""
    text = re.sub(r'\s+', ' ', text or '').strip()
    first = re.split(r'(?<=[。！？])|(?<=[.!?])\s', text, maxsplit=1)[0]
    return first if len(first) <= 180 else first[:179].rstrip() + '…'


def repair_summary(saved):
    if saved.get('repair_summary'):
        try:
            return RepairSummary.model_validate(saved['repair_summary']).model_dump()
        except ValueError:
            pass  # 历史记录异常时仍允许查看原始说明。
    return {
        'root_cause': _legacy_brief((saved.get('analysis') or {}).get('root_cause') or saved.get('root_cause')) or '根因尚未确认',
        'solution': _legacy_brief(saved.get('fix_summary')) or '尚未形成修复结果',
    }
