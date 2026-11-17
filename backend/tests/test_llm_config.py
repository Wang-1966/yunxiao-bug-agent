import json

import httpx
import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import llm_config
from src.llm.factory import LLMFactory


def _client(tmp_path, monkeypatch, payload):
    path = tmp_path / 'llm_config.json'
    path.write_text(json.dumps(payload))
    monkeypatch.setattr(llm_config, 'CONFIG_FILE', str(path))
    monkeypatch.setattr(LLMFactory, '_config', None)
    app = FastAPI()
    app.include_router(llm_config.router)
    return TestClient(app), path


def _provider(**overrides):
    provider = {
        'id': 'relay',
        'name': 'Relay',
        'type': 'openai-compatible',
        'base_url': 'https://example.com/v1',
        'api_key': 'secret',
        'available_models': ['model-a'],
        'default_model': 'model-a',
        'enabled': True,
    }
    provider.update(overrides)
    return provider


def test_context_capabilities_roundtrip_and_old_client_preservation(tmp_path, monkeypatch):
    client, path = _client(tmp_path, monkeypatch, {
        'providers': [_provider(context_windows={'model-a': 64000}, stream_usage=True)],
        'default_provider': 'relay', 'default_model': 'model-a',
    })
    response = client.put('/api/llm-config', json={'providers': [_provider(api_key='')]})
    assert response.status_code == 200
    assert response.json()['providers'][0]['context_windows'] == {'model-a': 64000}
    assert response.json()['providers'][0]['stream_usage'] is True
    invalid = client.put('/api/llm-config', json={'providers': [_provider(context_windows={'model-a': -1})]})
    assert invalid.status_code == 422
    cleared = client.put('/api/llm-config', json={'providers': [_provider(context_windows={}, stream_usage=False)]})
    assert cleared.status_code == 200
    assert json.loads(path.read_text())['providers'][0]['context_windows'] == {}


def test_provider_can_be_added_and_key_stays_on_server(tmp_path, monkeypatch):
    client, path = _client(tmp_path, monkeypatch, {
        'providers': [_provider()],
        'default_provider': 'relay',
        'default_model': 'model-a',
    })

    listed = client.get('/api/llm-config')
    assert listed.status_code == 200
    assert listed.json()['providers'][0]['api_key'] == ''

    created = _provider(id='openai', name='OpenAI', api_key='new-secret', default_model='gpt-4.1', available_models=[])
    response = client.put('/api/llm-config', json={'providers': [_provider(api_key=''), created]})
    assert response.status_code == 200
    assert [item['id'] for item in response.json()['providers']] == ['relay', 'openai']
    assert all(item['api_key'] == '' for item in response.json()['providers'])

    stored = json.loads(path.read_text())
    assert stored['providers'][0]['api_key'] == 'secret'
    assert stored['providers'][1]['api_key'] == 'new-secret'
    assert stored['providers'][1]['available_models'] == ['gpt-4.1']
    assert LLMFactory.get_config().providers[1].api_key == 'new-secret'


def test_provider_update_rejects_missing_key_duplicates_and_dangling_default(tmp_path, monkeypatch):
    client, path = _client(tmp_path, monkeypatch, {
        'providers': [_provider()],
        'default_provider': 'relay',
        'default_model': 'model-a',
        'stage_config': {'fix': {'provider': 'relay', 'model': 'model-a', 'temperature': 0, 'max_tokens': 8000}},
    })

    missing_key = client.put('/api/llm-config', json={'providers': [_provider(id='new', api_key='')]})
    assert missing_key.status_code == 400
    duplicate = client.put('/api/llm-config', json={'providers': [_provider(), _provider()]})
    assert duplicate.status_code == 400
    removed = client.put('/api/llm-config', json={'providers': []})
    assert removed.status_code == 400
    assert '默认提供商' in removed.json()['detail']
    assert json.loads(path.read_text())['providers'][0]['api_key'] == 'secret'


@pytest.mark.parametrize('with_windows', [False, True])
def test_fetch_models_queries_provider_and_uses_stored_key(tmp_path, monkeypatch, with_windows):
    client, _ = _client(tmp_path, monkeypatch, {'providers': [_provider()], 'default_provider': 'relay', 'default_model': 'model-a'})
    seen = {}

    class FakeClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, headers):
            seen.update(url=url, headers=headers)
            return httpx.Response(200, json={'data': [{'id': 'b-model', **({'context_length': 1000000} if with_windows else {})},
                {'id': 'A-model'}, {'id': 'b-model'}]}, request=httpx.Request('GET', url))

    monkeypatch.setattr(llm_config.httpx, 'AsyncClient', FakeClient)
    response = client.post('/api/llm-config/models', json={'provider_id': 'relay', 'base_url': 'https://example.com/v1/'})
    assert response.status_code == 200
    assert response.json() == {'models': ['A-model', 'b-model'], **({'context_windows': {'b-model': 1000000}} if with_windows else {})}
    assert seen == {'url': 'https://example.com/v1/models', 'headers': {'Authorization': 'Bearer secret'}}

    missing = client.post('/api/llm-config/models', json={'base_url': 'https://example.com/v1'})
    assert missing.status_code == 400


def test_model_windows_only_accept_explicit_valid_unambiguous_capabilities():
    payload = {'data': [
        {'id': 'models/a', 'context_window': 64000},
        {'id': 'b', 'context_length': 200000},
        {'id': 'ambiguous', 'context_window': 64000},
        {'id': 'ambiguous', 'context_window': 1000000},
        {'id': 'output-only', 'max_tokens': 64000},
        {'id': 'invalid', 'context_length': -1},
        {'id': 'text', 'context_length': '128K'},
    ]}
    assert llm_config.parse_model_windows(payload) == {'a': 64000, 'b': 200000}


def test_stage_selection_keeps_provider_specific_model_windows(monkeypatch):
    from types import SimpleNamespace
    from src.llm import factory
    from src.models.llm_config import LLMConfig

    config = LLMConfig(providers=[
        _provider(id='a', context_windows={'model-a': 64000}),
        _provider(id='b', context_windows={'model-a': 1000000}),
    ], default_provider='a', default_model='model-a', use_stage_config=True,
        stage_config={'analyze': {'provider': 'a', 'model': 'model-a'}, 'fix': {'provider': 'b', 'model': 'model-a'}})
    monkeypatch.setattr(LLMFactory, '_config', config)
    monkeypatch.setattr(factory, 'UnifiedLLMClient', lambda provider: SimpleNamespace(provider=provider))
    for stage, expected in [('analyze', 64000), ('fix', 1000000)]:
        llm = LLMFactory.create(stage=stage)
        assert llm.client.provider.context_windows[llm.model] == expected
