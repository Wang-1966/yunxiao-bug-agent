import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import llm_config
from src.agent import nodes
from src.llm.factory import LLMFactory
from src.models.llm_config import LLMConfig


def test_response_style_config_round_trip(tmp_path, monkeypatch):
    path = tmp_path / 'llm_config.json'
    path.write_text(json.dumps({
        'providers': [], 'default_provider': 'existing', 'default_model': 'existing-model',
    }))
    monkeypatch.setattr(llm_config, 'CONFIG_FILE', str(path))
    monkeypatch.setattr(LLMFactory, '_config', None)
    app = FastAPI()
    app.include_router(llm_config.router)
    client = TestClient(app)

    assert client.get('/api/llm-config').json()['response_style'] == 'detailed'
    for style in ('concise', 'minimal', 'detailed'):
        response = client.put('/api/llm-config', json={'response_style': style})
        assert response.status_code == 200
        assert response.json()['default_provider'] == 'existing'
        assert llm_config.load_config().response_style == style
        assert LLMFactory.get_response_style() == style
    assert client.put('/api/llm-config', json={'response_style': 'unknown'}).status_code == 422
    assert llm_config.load_config().response_style == 'detailed'


@pytest.mark.asyncio
@pytest.mark.parametrize('style,label', [('detailed', '具体'), ('concise', '较简'), ('minimal', '极简')])
async def test_response_style_reaches_analysis_proposal_and_fix(style, label, tmp_path, monkeypatch):
    monkeypatch.setattr(LLMFactory, '_config', LLMConfig(
        providers=[], default_provider='', default_model='', response_style=style,
    ))
    proposal = {'name': '修正初始化', 'steps': ['保留编辑回显'], 'pros': [], 'cons': [], 'risks': '检查新增入口'}
    llm = AsyncMock()
    llm.ainvoke.return_value = json.dumps({'proposals': [proposal]})
    monkeypatch.setattr(LLMFactory, 'create', lambda **kwargs: llm)
    operation = AsyncMock(side_effect=[
        {'root_cause': 'form.vue 初始化复用编辑数据', 'confidence': 'high', 'need_logging': False},
        {'summary': '已修正新增初始化'},
    ])
    monkeypatch.setattr(nodes, 'repository_agent', operation)
    monkeypatch.setattr(nodes, 'changed_files', AsyncMock(return_value=['form.vue']))
    state = {
        'bug_id': 'one', 'worktree_path': str(tmp_path),
        'bug_info': {'title': '新增异常', 'description': '输入包含 $title 和 {value}', 'agent_images': []},
        'repair_feedback': '保留编辑行为',
        'previous_attempt': {'round': 1, 'root_cause': '旧根因', 'selected_proposal': {'name': '旧方案'}, 'diff_stat': ' a.vue | 2 +- $x',
                             'modified_files': ['a.vue'], 'feedback': '点击后仍报错', 'image_count': 1},
    }
    state.update(await nodes.analyze_root_cause_node(state))
    assert state['stage'] == 'proposing'
    state.update(await nodes.design_proposal_node(state))
    assert state['stage'] == 'awaiting_plan_confirmation'
    assert state['proposals'] == [proposal]
    state['selected_proposal'] = proposal
    result = await nodes.implement_fix_node(state)
    assert result['fix_summary'] == '已修正新增初始化'
    assert result['stage'] == 'verifying'

    proposal_messages = llm.ainvoke.call_args.args[0]
    assert proposal_messages[0]['role'] == 'system'
    assert 'JSON' in proposal_messages[0]['content']
    assert llm.ainvoke.call_args.kwargs['response_format'] == {'type': 'json_object'}
    assert llm.ainvoke.call_args.kwargs['log_context'] == f'{tmp_path.name}/propose/1'
    prompts = [operation.call_args_list[0].args[2], proposal_messages[-1]['content'], operation.call_args_list[1].args[2]]
    for prompt in prompts:
        assert f'回答风格：{label}' in prompt
        assert '输入包含 $title 和 {value}' in prompt
        assert '保留编辑行为' in prompt
        assert '已被验收否定的假设）：旧根因' in prompt and 'a.vue | 2 +- $x' in prompt and '点击后仍报错' in prompt
        assert '不降低源码核对、置信度判断、兼容性或验证要求' in prompt
    assert 'form.vue' in prompts[1]
    assert '修正初始化' in prompts[2]


@pytest.mark.asyncio
async def test_invalid_proposal_response_records_diagnostics(tmp_path, monkeypatch):
    from src.llm.diagnostics import response_summary
    response = '先给出方案。{"proposals":[]} Bearer sensitive-token'
    diagnostics = {'provider': 'relay', 'finish_reasons': ['stop'], **response_summary(response)}

    async def invoke(messages, **kwargs):
        kwargs['on_response'](diagnostics)
        return response

    llm = AsyncMock()
    llm.ainvoke.side_effect = invoke
    monkeypatch.setattr(LLMFactory, 'create', lambda **kwargs: llm)
    operation = AsyncMock()
    monkeypatch.setattr(nodes, 'record_source_operation', operation)
    result = await nodes.design_proposal_node({
        'bug_id': 'one', 'worktree_path': str(tmp_path), 'bug_info': {'title': 'bug'},
        'root_cause': 'wrong value',
    })

    assert result['stage'] == 'completed'
    assert '模型未返回可执行的修复方案' in result['error']
    assert not result.get('waiting_for_user')
    assert llm.ainvoke.await_count == nodes.MAX_PROPOSAL_ATTEMPTS
    message = operation.await_args.args[1]
    assert f'{tmp_path.name}/propose' in message
    assert 'response_sha256' in message and 'relay' in message and '先给出方案' in message
    assert 'sensitive-token' not in message


@pytest.mark.asyncio
async def test_prose_only_proposal_response_is_retried_with_feedback(tmp_path, monkeypatch):
    proposal = {'name': '只改展示', 'steps': ['修正日期分组'], 'pros': [], 'cons': [], 'risks': '低'}
    llm = AsyncMock()
    llm.ainvoke.side_effect = ['我先核对历史分组的日期计算实现，再据此给出修复方案。', json.dumps({'proposals': [proposal]})]
    monkeypatch.setattr(LLMFactory, 'create', lambda **kwargs: llm)
    monkeypatch.setattr(nodes, 'record_source_operation', AsyncMock())
    result = await nodes.design_proposal_node({
        'bug_id': 'one', 'worktree_path': str(tmp_path), 'bug_info': {'title': 'bug'}, 'root_cause': 'wrong value',
    })
    assert result['proposals'] == [proposal]
    retry_messages = llm.ainvoke.await_args_list[1].args[0]
    assert retry_messages[-2] == {'role': 'assistant', 'content': '我先核对历史分组的日期计算实现，再据此给出修复方案。'}
    assert '没有 JSON 对象' in retry_messages[-1]['content']
