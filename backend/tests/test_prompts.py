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

    prompts = [operation.call_args_list[0].args[2], llm.ainvoke.call_args.args[0][0]['content'], operation.call_args_list[1].args[2]]
    for prompt in prompts:
        assert f'回答风格：{label}' in prompt
        assert '输入包含 $title 和 {value}' in prompt
        assert '保留编辑行为' in prompt
        assert '不降低源码核对、置信度判断、兼容性或验证要求' in prompt
    assert 'form.vue' in prompts[1]
    assert '修正初始化' in prompts[2]
