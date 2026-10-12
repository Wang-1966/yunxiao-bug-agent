from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.llm.unified_client import UnifiedLLMClient
from src.llm.diagnostics import response_summary


class FakeStream:
    def __init__(self, values):
        self.values = values
        self.close = AsyncMock()

    def __aiter__(self):
        async def values():
            for value in self.values:
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=value))])
        return values()


@pytest.mark.asyncio
async def test_ainvoke_consumes_openai_stream_and_keeps_full_text():
    client = UnifiedLLMClient(SimpleNamespace(id='test', api_key='key', base_url='http://example'))
    client.client.chat.completions.create = AsyncMock(return_value=FakeStream(['{"action":', '"finish"}']))

    result = await client.ainvoke([{'role': 'user', 'content': 'test'}], 'model', response_format={'type': 'json_object'})

    assert result == '{"action":"finish"}'
    client.client.chat.completions.create.assert_awaited_once()
    assert client.client.chat.completions.create.await_args.kwargs['stream'] is True
    assert client.client.chat.completions.create.await_args.kwargs['response_format'] == {'type': 'json_object'}
    client.client.chat.completions.create.return_value.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_error_closes_connection_and_does_not_return_partial_json():
    class BrokenStream(FakeStream):
        def __aiter__(self):
            async def chunks():
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='{"action":'))])
                raise ConnectionError('stream disconnected')
            return chunks()
    client = UnifiedLLMClient(SimpleNamespace(id='test', api_key='key', base_url='http://example'))
    stream = BrokenStream([])
    client.client.chat.completions.create = AsyncMock(return_value=stream)
    progress = AsyncMock()
    with pytest.raises(ConnectionError):
        await client.ainvoke([{'role': 'user', 'content': 'test'}], 'model', on_chunk=progress)
    progress.assert_awaited_once_with('{"action":')
    stream.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_response_diagnostics_capture_stream_metadata_without_forwarding_callbacks():
    class DiagnosticStream(FakeStream):
        def __aiter__(self):
            async def chunks():
                yield SimpleNamespace(id='response-one', model='actual-model', choices=[SimpleNamespace(
                    delta=SimpleNamespace(content=None, reasoning_content='private reasoning'), finish_reason=None)])
                yield SimpleNamespace(choices=[SimpleNamespace(
                    delta=SimpleNamespace(content='说明 api_key="provider-secret" https://cdn.example/image?signature=secret'), finish_reason='stop')])
            return chunks()
    client = UnifiedLLMClient(SimpleNamespace(id='test', api_key='provider-secret', base_url='http://example'))
    stream = DiagnosticStream([])
    client.client.chat.completions.create = AsyncMock(return_value=stream)
    diagnostics = {}
    response = await client.ainvoke([], 'requested-model', on_response=diagnostics.update, log_context='worktree/fix/3')
    assert response.startswith('说明')
    assert diagnostics['response_id'] == 'response-one'
    assert diagnostics['response_model'] == 'actual-model'
    assert diagnostics['context'] == 'worktree/fix/3'
    assert diagnostics['finish_reasons'] == ['stop']
    assert diagnostics['reasoning_chars'] == len('private reasoning')
    assert diagnostics['content_chars'] == diagnostics['response_chars'] == len(response)
    assert diagnostics['delta_fields'] == ['content', 'reasoning_content']
    assert 'provider-secret' not in diagnostics['response_prefix']
    assert 'signature=secret' not in diagnostics['response_prefix']
    sent = client.client.chat.completions.create.await_args.kwargs
    assert 'on_response' not in sent and 'log_context' not in sent and 'diagnostics' not in sent


@pytest.mark.asyncio
async def test_empty_and_truncated_streams_leave_response_diagnostics():
    client = UnifiedLLMClient(SimpleNamespace(id='test', api_key='key', base_url='http://example'))
    client.client.chat.completions.create = AsyncMock(return_value=FakeStream([]))
    diagnostics = {}
    assert await client.ainvoke([], 'model', on_response=diagnostics.update) == ''
    assert diagnostics['response_chars'] == 0 and diagnostics['finish_reasons'] == []

    class TruncatedStream(FakeStream):
        def __aiter__(self):
            async def chunks():
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='{'), finish_reason=None)])
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None), finish_reason='length')])
            return chunks()
    stream = TruncatedStream([])
    client.client.chat.completions.create.return_value = stream
    with pytest.raises(ValueError, match='输出长度上限'):
        await client.ainvoke([], 'model', on_response=diagnostics.update)
    assert diagnostics['response_chars'] == 1
    assert diagnostics['finish_reasons'] == ['length']
    assert diagnostics['error_type'] == 'ValueError'
    stream.close.assert_awaited_once()


def test_response_summary_redacts_before_truncating_and_bounds_excerpts():
    secret = 'unique-provider-key'
    response = '说明 Bearer credential sk-test-key ' + 'x' * 950 + secret + 'y' * 1500 + '\npassword=hidden data:image/png;base64,private'
    summary = response_summary(response, (secret,))
    assert summary['response_chars'] == len(response)
    assert len(summary['response_sha256']) == 64
    assert len(summary['response_prefix']) <= 1000 and len(summary['response_suffix']) <= 500
    excerpts = summary['response_prefix'] + summary['response_suffix']
    for value in (secret, 'credential', 'sk-test-key', 'hidden', 'base64,private'):
        assert value not in excerpts
