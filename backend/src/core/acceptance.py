"""验收要求、覆盖说明与系统执行记录；模型说明不能代替执行证据。"""
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .agent_tools import ToolError


class AcceptanceCheck(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    criterion_index: int = Field(ge=0, description='symptom.acceptance 的下标，从 0 开始')
    method: Literal['reproduction', 'manual']
    detail: str = Field(min_length=1, max_length=2000, description='测试的具体断言如何覆盖此要求；人工验证则写操作步骤和预期结果')
    test_files: list[str] = Field(default_factory=list, description='覆盖此项的复现测试文件；人工验证时留空')


class CriterionReview(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    criterion_id: str = Field(min_length=1, max_length=80)
    status: Literal['passed', 'failed']
    feedback: str = Field(default='', max_length=1000)


def acceptance_plan(symptom, checks=None, repro=None):
    texts = (symptom or {}).get('acceptance') or []
    by_index = {}
    if checks is not None:
        if not isinstance(checks, list):
            raise ToolError('acceptance_checks 必须是数组')
        for raw in checks:
            try:
                item = AcceptanceCheck.model_validate(raw).model_dump()
            except ValueError as exc:
                raise ToolError(f'验收覆盖说明无效：{exc}') from exc
            index = item['criterion_index']
            if index >= len(texts) or index in by_index:
                raise ToolError('每条验收要求必须且只能提供一份覆盖说明，criterion_index 不能重复或越界')
            files = item['test_files']
            if item['method'] == 'reproduction':
                if not (repro or {}).get('reproduced') or not files or not set(files) <= set(repro.get('test_files') or []):
                    raise ToolError('自动覆盖必须引用本次已成功复现的测试文件；无法自动验证的要求应标为 manual')
            elif files:
                raise ToolError('人工验收条目的 test_files 应为空')
            by_index[index] = item
        if len(by_index) != len(texts):
            raise ToolError('acceptance_checks 必须覆盖每条验收要求；无法自动化的条目请提供人工检查步骤')
    criteria = []
    for index, text in enumerate(texts):
        digest = hashlib.sha256(f'{index}:{text}'.encode()).hexdigest()[:16]
        check = by_index.get(index) or {'method': 'manual', 'detail': '按原始复现步骤核对该项预期行为。历史分析未提供逐项覆盖说明。', 'test_files': []}
        criteria.append({'id': f'criterion-{digest}', 'text': text, **{key: check[key] for key in ('method', 'detail', 'test_files')}})
    version = hashlib.sha256(json.dumps(criteria, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]
    return {'version': version, 'criteria': criteria, 'legacy': checks is None}


def evidence_report(saved):
    analysis = saved.get('analysis') or {}
    plan = analysis.get('acceptance_plan') or acceptance_plan(analysis.get('symptom'))
    tests = saved.get('test_results') or {}
    commit = saved.get('git_commit_hash')
    verification = tests.get('verification') or {}
    current = bool(commit and verification.get('git_commit_hash') == commit and tests.get('acceptance_version') == plan['version'])
    checks = tests.get('checks') or []
    # 老记录仍可查看原始命令，但不推断逐项通过。
    if not checks and tests.get('command'):
        checks = [{'id': 'legacy-project', 'kind': 'project', 'label': '历史项目检查',
                   **{key: tests.get(key) for key in ('command', 'exit_code', 'passed', 'output')}}]
    repro = saved.get('repro') or {}
    reproduction = next((check for check in checks if check.get('kind') == 'reproduction' and check.get('command') == repro.get('command')), None)
    criteria = []
    for item in plan['criteria']:
        status, evidence_ids = 'manual', []
        if item['method'] == 'reproduction':
            status = 'not_run'
            if reproduction:
                evidence_ids = [reproduction['id']]
                status = ('passed' if reproduction.get('passed') is True else 'failed') if current else 'stale'
        criteria.append({**item, 'automatic_status': status, 'evidence_ids': evidence_ids})
    return {'version': plan['version'], 'git_commit_hash': commit, 'current': current,
            'legacy': plan.get('legacy', True), 'criteria': criteria, 'checks': checks,
            'verification_limits': analysis.get('verification_limits') or [],
            'reproduction_before': {key: repro.get(key) for key in ('command', 'reproduced', 'exit_before', 'output_before', 'not_reproducible_reason')}}


def validate_criterion_reviews(saved, confirmation):
    """新页面逐项验收；旧调用方未提供条目时保留原接口行为。"""
    if confirmation.acceptance_results is None:
        return None
    report = evidence_report(saved)
    if confirmation.acceptance_version != report['version'] or confirmation.git_commit_hash != report['git_commit_hash']:
        raise ValueError('验收要求或提交版本已变化，请刷新证据后重新验收')
    criteria = {item['id']: item for item in report['criteria']}
    reviews = [item.model_dump() for item in confirmation.acceptance_results]
    ids = [item['criterion_id'] for item in reviews]
    if len(ids) != len(set(ids)) or not set(ids) <= criteria.keys():
        raise ValueError('验收条目重复或不属于当前版本，请刷新页面')
    if confirmation.confirmed and (set(ids) != criteria.keys() or any(item['status'] != 'passed' for item in reviews)):
        raise ValueError('请逐项确认验收要求；存在未检查或未通过的条目，不能合并')
    return {'version': report['version'], 'git_commit_hash': report['git_commit_hash'],
            'criteria': [{**item, 'text': criteria[item['criterion_id']]['text']} for item in reviews]}
