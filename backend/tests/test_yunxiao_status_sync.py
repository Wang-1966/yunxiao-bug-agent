import json

import httpx
import pytest

from src.config import Settings
from src.tools.yunxiao.openapi_client import YunxiaoClient, YunxiaoError


def client_with_transport(monkeypatch, handler):
    original = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=transport, **kwargs))
    return YunxiaoClient(Settings(yunxiao_api_base='https://openapi-rdc.aliyuncs.com',
                                  yunxiao_org_id='org', yunxiao_access_token='test-token'))


@pytest.mark.parametrize('already_processing', [False, True])
async def test_updates_only_status_and_verifies_remote_result(monkeypatch, already_processing):
    requests = []
    current = 'progress-custom-id' if already_processing else 'new'

    def handle(request):
        nonlocal current
        requests.append(request)
        assert request.headers['x-yunxiao-token'] == 'test-token'
        if request.url.path.endswith('/workflows'):
            assert request.url.path == '/oapi/v1/projex/organizations/org/projects/project/workitemTypes/bug-type/workflows'
            return httpx.Response(200, json={'statuses': [{'id': 'progress-custom-id', 'displayName': '处理中'}]})
        assert request.url.path == '/oapi/v1/projex/organizations/org/workitems/bug'
        if request.method == 'PUT':
            assert json.loads(request.content) == {'status': 'progress-custom-id'}
            current = 'progress-custom-id'
            return httpx.Response(204)
        return httpx.Response(200, json={'id': 'bug', 'categoryId': 'Bug', 'space': {'id': 'project'},
                                         'workitemType': {'id': 'bug-type'}, 'status': {'id': current}})

    client = client_with_transport(monkeypatch, handle)
    result = await client.set_bug_in_progress('bug')
    assert result == {'status_id': 'progress-custom-id', 'source_status': '处理中'}
    assert [r.method for r in requests].count('PUT') == (0 if already_processing else 1)


@pytest.mark.parametrize('failure', ['missing', 'ambiguous', 'forbidden', 'unconfirmed', 'timeout'])
async def test_status_resolution_and_update_failures_are_explicit(monkeypatch, failure):
    writes = []

    def handle(request):
        if request.url.path.endswith('/workflows'):
            statuses = [{'id': 'progress', 'displayName': '处理中'}]
            if failure == 'missing':
                statuses = []
            elif failure == 'ambiguous':
                statuses += [{'id': 'another', 'displayName': '处理中'}]
            return httpx.Response(200, json={'statuses': statuses})
        if request.method == 'PUT':
            writes.append(request)
            if failure == 'forbidden':
                return httpx.Response(403, json={})
            if failure == 'timeout':
                raise httpx.ReadTimeout('test', request=request)
            return httpx.Response(204)
        return httpx.Response(200, json={'id': 'bug', 'categoryId': 'Bug', 'space': {'id': 'project'},
                                         'workitemType': {'id': 'type'}, 'status': {'id': 'new'}})

    client = client_with_transport(monkeypatch, handle)
    with pytest.raises(YunxiaoError):
        await client.set_bug_in_progress('bug')
    assert len(writes) == (0 if failure in ('missing', 'ambiguous') else 1)
