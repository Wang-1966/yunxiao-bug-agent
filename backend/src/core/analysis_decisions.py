"""分析阶段的决策边界；旧报告无版本字段时沿用原确认规则。"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .agent_tools import ToolError


class DecisionQuestion(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    question: str = Field(min_length=1)
    options: list[str] = Field(min_length=2)
    recommended_option: int = Field(ge=0)
    reason: str = Field(min_length=1, description='不同答案为什么会改变修复方向')
    investigated: str = Field(min_length=1, description='已查阅什么，为什么仍无法判断')


class ChangeImpact(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    categories: list[Literal['data_deletion', 'permission_change', 'breaking_api', 'schema_migration', 'external_side_effect', 'product_behavior_change']]
    evidence: str = Field(min_length=1, description='引用具体文件和拟执行操作；无高风险也需解释依据')


def normalize_decisions(args):
    """新工具调用须提供完整分类；老会话的旧工具参数仍可提交。"""
    fields = ('decision_questions', 'assumptions', 'verification_limits')
    if not any(key in args for key in fields):
        return {}
    try:
        if any(key not in args for key in fields):
            raise ValueError('必须同时提供 decision_questions、assumptions、verification_limits')
        raw = args['decision_questions']
        if not isinstance(raw, list):
            raise ValueError('decision_questions 必须是数组')
        questions = []
        for item in raw:
            question = DecisionQuestion.model_validate(item)
            if any(not option.strip() for option in question.options) or question.recommended_option >= len(question.options):
                raise ValueError('问题选项不能为空，推荐索引必须指向有效选项')
            questions.append(question.model_dump())
        result = {'decision_schema_version': 1, 'decision_questions': questions}
        for key in fields[1:]:
            values = args[key]
            if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError(f'{key} 必须是非空字符串组成的数组（允许空数组）')
            result[key] = values
        # 兼容所有现有报告及调用方；这里只有真正的决策问题。
        result['open_questions'] = [item['question'] for item in questions]
        return result
    except (ValidationError, ValueError) as exc:
        raise ToolError(f'决策分类无效：{exc}') from exc
