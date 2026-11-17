import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.core import observability


def test_redacts_nested_credentials_images_and_bounds_content():
    value = {'messages': [{'content': [{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,PRIVATE'}}]}],
             'api_key': 'PRIVATE', 'text': 'provider-secret Bearer abcdef ' + 'x' * 21000}
    result = observability.sanitize(value, ('provider-secret',))
    text = json.dumps(result)
    assert 'PRIVATE' not in text and 'provider-secret' not in text and 'abcdef' not in text
    assert '[truncated]' in result['text'] and len(result['text']) < 20100


def test_trace_links_phases_requests_and_tools_and_closes_pending(monkeypatch):
    sdk = MagicMock()
    root = sdk.start_observation.return_value
    child = root.start_observation.return_value
    monkeypatch.setattr(observability, 'client', lambda: sdk)
    trace = observability.AgentTrace(run_id='run', agent_id='agent-task-1', phase='analyze', model='test', secret='SECRET')
    trace.start('request', name='model', kind='generation', input={'text': 'SECRET'})
    trace.end('request', output='done', usage={'input': 10, 'output': 4, 'input_cache_read': None})
    assert root.start_observation.call_args.kwargs['input']['text'] == '[REDACTED]'
    assert child.update.call_args.kwargs['usage_details'] == {'input': 10, 'output': 4}
    trace.start('tool', name='read', kind='tool', input={'path': 'app.py'})
    trace.finish('cancelled')
    assert not trace.pending and child.end.call_count == 2
    assert root.update.call_args.kwargs['level'] == 'ERROR'
    other = observability.AgentTrace(run_id='another-run', agent_id='agent-task-1', phase='fix', model='test')
    assert other.trace_id == trace.trace_id


def test_export_failures_never_interrupt_business(monkeypatch):
    monkeypatch.setattr(observability, 'client', MagicMock(side_effect=RuntimeError('not reachable')))
    trace = observability.AgentTrace(run_id='run', agent_id=None, phase='fix', model='test')
    trace.start('one', name='model', kind='generation')
    trace.end('one', error='failed')
    trace.finish('failed')
    assert trace.root is None


@pytest.mark.asyncio
async def test_auxiliary_request_consumes_trace_context_before_provider_call(monkeypatch):
    from src.llm.unified_client import UnifiedLLMClient

    trace_factory = MagicMock()
    monkeypatch.setattr(observability, 'AgentTrace', trace_factory)
    llm = object.__new__(UnifiedLLMClient)
    llm.provider = SimpleNamespace(id='test', api_key='SECRET')
    received = {}

    async def stream(*args, **kwargs):
        received.update(kwargs)
        kwargs['diagnostics']['usage'] = {'input_tokens': 12, 'output_tokens': 3}
        yield 'answer'

    monkeypatch.setattr(llm, '_astream', stream)
    result = await llm.ainvoke([], model='test', trace_agent_id='agent-1', trace_phase='vision',
                              trace_metadata={'bug_id': 'bug-1'}, response_format={'type': 'json_object'})
    assert result == 'answer'
    assert not any(key.startswith('trace_') for key in received)
    assert received['response_format'] == {'type': 'json_object'}
    context = trace_factory.call_args.kwargs
    assert context['agent_id'] == 'agent-1' and context['phase'] == 'vision'
    assert context['metadata']['bug_id'] == 'bug-1'
    assert trace_factory.return_value.end.call_args.kwargs['usage']['input'] == 12


def test_real_sdk_exports_parent_child_and_session_without_network(monkeypatch):
    from langfuse import Langfuse
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    sdk = Langfuse(public_key='pk-test-offline', secret_key='sk-test-offline',
                   base_url='http://127.0.0.1:1', tracer_provider=provider, span_exporter=exporter)
    monkeypatch.setattr(observability, 'client', lambda: sdk)
    trace = observability.AgentTrace(run_id='offline', agent_id='agent-offline', phase='analyze', model='test')
    trace.start('request', name='model', kind='generation', input='prompt')
    trace.end('request', output='answer', usage={'input': 10, 'output': 5})
    trace.finish('submitted')
    spans = exporter.get_finished_spans()
    root = next(span for span in spans if span.name == 'analyze')
    child = next(span for span in spans if span.name == 'model')
    assert child.parent.span_id == root.context.span_id
    assert child.context.trace_id == int(trace.trace_id, 16)
    assert root.attributes['session.id'] == 'agent-offline'
    assert child.attributes['langfuse.observation.usage_details'] == '{"input": 10, "output": 5}'
    provider.shutdown()


def test_setup_preserves_other_config_and_is_idempotent(tmp_path):
    path = Path(__file__).resolve().parents[2] / 'infra/langfuse/setup.py'
    module = importlib.util.spec_from_file_location('langfuse_setup', path)
    setup = importlib.util.module_from_spec(module)
    module.loader.exec_module(setup)
    directory = tmp_path / 'monitor'
    directory.mkdir()
    backend_env = tmp_path / 'backend.env'
    backend_env.write_text('EXISTING=value\nLANGFUSE_ENABLED=false\n')
    setup.setup(directory, backend_env)
    first = (directory / '.env').read_text()
    setup.setup(directory, backend_env)
    assert (directory / '.env').read_text() == first
    assert 'EXISTING=value' in backend_env.read_text()
    assert backend_env.read_text().count('LANGFUSE_ENABLED=') == 1
    assert backend_env.stat().st_mode & 0o077 == 0
