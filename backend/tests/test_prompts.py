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
async def test_response_style_reaches_analysis_and_fix(style, label, tmp_path, monkeypatch):
    monkeypatch.setattr(LLMFactory, '_config', LLMConfig(
        providers=[], default_provider='', default_model='', response_style=style,
    ))
    proposal = {'name': '修正初始化', 'steps': ['保留编辑回显'], 'pros': [], 'cons': [], 'risks': '检查新增入口'}
    prompts = []

    async def analysis(llm, reviewer, path, prompt, **kwargs):
        prompts.append(prompt)
        return {'root_cause': 'form.vue 初始化复用编辑数据', 'analysis': {'root_cause': 'form.vue'}, 'repro': {},
                'review': {}, 'confidence': 'high', 'proposals': [proposal], 'source_context': {'reads': []}}

    async def fix(llm, reviewer, path, prompt, **kwargs):
        prompts.append(prompt)
        return {'summary': '已修正新增初始化', 'review': {}, 'repro': {}, 'source_context': {'reads': []}}

    monkeypatch.setattr(LLMFactory, 'create', lambda **kwargs: AsyncMock())
    monkeypatch.setattr(nodes, 'repository_context', AsyncMock(return_value={'instructions': '无', 'inventory': 'src'}))
    monkeypatch.setattr(nodes, 'run_analysis', analysis)
    monkeypatch.setattr(nodes, 'run_fix', fix)
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
    for prompt in prompts:
        assert f'回答风格：{label}' in prompt
        assert '输入包含 $title 和 {value}' in prompt
        assert '保留编辑行为' in prompt
        assert '已被验收否定的假设）：旧根因' in prompt and 'a.vue | 2 +- $x' in prompt and '点击后仍报错' in prompt
        assert '不降低源码核对、置信度判断、兼容性或验证要求' in prompt
    assert '修正初始化' in prompts[1]


@pytest.mark.asyncio
async def test_missing_proposals_stop_before_confirmation():
    result = await nodes.design_proposal_node({'bug_id': 'one', 'proposals': []})
    assert result['stage'] == 'completed'
    assert '修复方案' in result['error']
    assert not result.get('waiting_for_user')
