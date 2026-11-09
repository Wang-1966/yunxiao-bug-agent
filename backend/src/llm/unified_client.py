"""统一 LLM 客户端"""
from openai import AsyncOpenAI
from typing import AsyncIterator, List, Dict, Any, Optional
from loguru import logger
import httpx
import json
import time

from ..models.llm_config import LLMProvider
from .diagnostics import response_summary

# SDK 默认 connect=5s。握手超过 5 秒会抛出 Request timed out，并中断整次任务。
# 读超时保持 600 秒，由调用方的 120 秒上限负责取消长时间无输出的请求。
LLM_TIMEOUT = httpx.Timeout(600.0, connect=30.0)


class UnifiedLLMClient:
    """统一的 LLM 客户端（所有 LLM 都用 OpenAI 兼容格式）"""

    def __init__(self, provider: LLMProvider):
        self.provider = provider
        self.client = AsyncOpenAI(
            api_key=provider.api_key,
            base_url=provider.base_url,
            timeout=LLM_TIMEOUT,
        )

    async def ainvoke(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: Optional[float] = None,
        max_tokens: int = 8000,
        on_chunk=None,
        on_response=None,
        log_context=None,
        on_reasoning=None,
        **kwargs
    ) -> str:
        """
        调用 LLM 生成响应

        Args:
            messages: 消息列表，格式 [{"role": "user", "content": "..."}]
            model: 模型名称
            temperature: 温度参数
            max_tokens: 最大 token 数
            **kwargs: 其他参数

        Returns:
            生成的文本内容
        """
        chunks, diagnostics = [], {}
        try:
            async for chunk in self.astream(messages, model, temperature, max_tokens, diagnostics=diagnostics, log_context=log_context, on_reasoning=on_reasoning, **kwargs):
                chunks.append(chunk)
                if on_chunk:
                    await on_chunk(chunk)
            return ''.join(chunks)

        except Exception as e:
            logger.error(
                f"LLM call failed: provider={self.provider.id}, "
                f"model={model}, error={str(e)}"
            )
            raise
        finally:
            if on_response:
                on_response({**diagnostics, **response_summary(''.join(chunks), (self.provider.api_key,))})

    async def achat(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        tools: List[Dict[str, Any]],
        temperature: Optional[float] = None,
        max_tokens: int = 16000,
        on_chunk=None,
        on_reasoning=None,
        on_response=None,
        log_context=None,
        **kwargs,
    ) -> Dict[str, Any]:
        """原生工具调用：返回 {'content', 'tool_calls'}，tool_calls 为 OpenAI 格式。"""
        diagnostics, content, calls = {}, [], {}

        def on_tool_delta(items):
            for item in items:
                index = item.get('index', len(calls))
                call = calls.setdefault(index, {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                call['id'] = item.get('id') or call['id']
                function = item.get('function') or {}
                # 部分中转在一个片段里给出完整名称，部分逐字流式下发；名称只取首次完整值。
                if function.get('name') and not call['function']['name']:
                    call['function']['name'] = function['name']
                call['function']['arguments'] += function.get('arguments') or ''

        try:
            async for chunk in self.astream(messages, model, temperature, max_tokens, diagnostics=diagnostics, log_context=log_context,
                                            on_reasoning=on_reasoning, on_tool_delta=on_tool_delta, tools=tools, **kwargs):
                content.append(chunk)
                if on_chunk:
                    await on_chunk(chunk)
        finally:
            if on_response:
                on_response({**diagnostics, 'tool_calls': len(calls), **response_summary(''.join(content), (self.provider.api_key,))})
        tool_calls = []
        for index in sorted(calls):
            call = calls[index]
            call['id'] = call['id'] or f'call_{index}'
            tool_calls.append(call)
        return {'content': ''.join(content), 'tool_calls': tool_calls}

    async def astream(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: Optional[float] = None,
        max_tokens: int = 8000,
        diagnostics=None,
        log_context=None,
        on_reasoning=None,
        on_tool_delta=None,
        **kwargs,
    ) -> AsyncIterator[str]:
        """以 OpenAI 兼容的 SSE 流读取文本片段；调用方可自行累积成 JSON。"""
        start_time = time.time()
        diagnostics = diagnostics if diagnostics is not None else {}
        diagnostics.update(provider=self.provider.id, model=model, context=log_context,
                           stream_chunks=0, content_chars=0, reasoning_chars=0, finish_reasons=[])
        delta_fields = set()
        if temperature is not None:
            kwargs['temperature'] = temperature
        if getattr(self.provider, 'stream_usage', False):
            kwargs.setdefault('stream_options', {'include_usage': True})
        response = await self.client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            stream=True,
            **kwargs,
        )
        try:
            async for chunk in response:
                diagnostics['stream_chunks'] += 1
                usage = getattr(chunk, 'usage', None)
                if usage:
                    diagnostics['usage'] = {
                        'input_tokens': usage.prompt_tokens,
                        'output_tokens': usage.completion_tokens,
                        'total_tokens': usage.total_tokens,
                        'cached_tokens': getattr(getattr(usage, 'prompt_tokens_details', None), 'cached_tokens', None),
                    }
                if getattr(chunk, 'id', None):
                    diagnostics['response_id'] = chunk.id
                if getattr(chunk, 'model', None):
                    diagnostics['response_model'] = chunk.model
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta:
                    values = delta.model_dump(exclude_none=True) if hasattr(delta, 'model_dump') else vars(delta)
                    delta_fields.update(key for key, value in values.items() if value is not None)
                    # DeepSeek/Grok 中转使用 reasoning_content，OpenRouter 使用 reasoning。
                    reasoning = getattr(delta, 'reasoning_content', None) or getattr(delta, 'reasoning', None) or values.get('reasoning_content') or values.get('reasoning')
                    if isinstance(reasoning, str) and reasoning:
                        diagnostics['reasoning_chars'] += len(reasoning)
                        if on_reasoning:
                            await on_reasoning(reasoning)
                    if on_tool_delta and values.get('tool_calls'):
                        on_tool_delta(values['tool_calls'])
                reason = getattr(chunk.choices[0], 'finish_reason', None) if chunk.choices else None
                if reason:
                    diagnostics['finish_reasons'].append(reason)
                if chunk.choices and getattr(chunk.choices[0], 'finish_reason', None) == 'length':
                    raise ValueError('模型响应达到输出长度上限，返回内容不完整')
                content = getattr(delta, 'content', None) if delta else None
                if content:
                    diagnostics['content_chars'] += len(content)
                    yield content
            latency = (time.time() - start_time) * 1000
            logger.info(f'LLM stream succeeded: provider={self.provider.id}, model={model}, latency={latency:.0f}ms')
        except Exception as e:
            diagnostics['error_type'] = type(e).__name__
            logger.error(
                f"LLM stream failed: provider={self.provider.id}, model={model}, error={e}"
            )
            raise
        finally:
            diagnostics['delta_fields'] = sorted(delta_fields)
            diagnostics['latency_ms'] = round((time.time() - start_time) * 1000)
            logger.info('LLM response diagnostics: {}', json.dumps(diagnostics, ensure_ascii=False))
            await response.close()

    async def test_connection(self) -> tuple[bool, Optional[float]]:
        """
        测试 API 连接

        Returns:
            (是否成功, 延迟毫秒数)
        """
        try:
            start_time = time.time()

            await self.ainvoke(
                messages=[{"role": "user", "content": "test"}],
                model=self.provider.default_model,
                max_tokens=10
            )

            latency = (time.time() - start_time) * 1000
            return True, latency

        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False, None
