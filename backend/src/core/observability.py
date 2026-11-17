"""Langfuse SDK 接入；监控失败不参与业务控制，也不替代本地验收证据。"""
import hashlib
import re
from functools import lru_cache

from loguru import logger

from ..config import get_settings

_SECRET_FIELD = re.compile(r'api.?key|secret|password|authorization|access.?token', re.I)
_CREDENTIAL = re.compile(r'(?i)(Bearer\s+)[^\s"\']+|\b(?:sk|pk)-[\w-]{12,}')


def sanitize(value, secrets=()):
    """不上传图片载荷；遮蔽凭据并限制单字段大小。"""
    if isinstance(value, dict):
        if value.get('type') in ('image', 'image_url'):
            return {'type': 'image', 'content': '[image omitted]'}
        return {str(key): '[REDACTED]' if _SECRET_FIELD.search(str(key)) else sanitize(item, secrets)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(item, secrets) for item in value[-100:]]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, '[REDACTED]')
        value = _CREDENTIAL.sub('[REDACTED]', value)
        value = re.sub(r'data:[^\s"\']+', '[image omitted]', value)
        return value[:20000] + ('\n[truncated]' if len(value) > 20000 else '')
    return value


@lru_cache(maxsize=1)
def client():
    settings = get_settings()
    if not settings.langfuse_enabled:
        return None
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        logger.warning('Langfuse 未配置项目密钥，监控未启用')
        return None
    try:
        from langfuse import Langfuse
        return Langfuse(public_key=settings.langfuse_public_key, secret_key=settings.langfuse_secret_key,
                        base_url=settings.langfuse_base_url, environment=settings.langfuse_environment,
                        timeout=3, flush_interval=2)
    except Exception as exc:
        logger.warning('Langfuse 初始化失败：{}', type(exc).__name__)
        return None


def shutdown():
    if client.cache_info().currsize:
        try:
            instance = client()
            if instance:
                instance.shutdown()
        except Exception as exc:
            logger.warning('Langfuse 关闭失败：{}', type(exc).__name__)
        finally:
            client.cache_clear()


class AgentTrace:
    def __init__(self, *, run_id, agent_id, phase, model, secret='', metadata=None):
        self.root = None
        self.pending = {}
        self.agent_id, self.phase, self.model = agent_id, phase, model
        self.secrets = (secret, get_settings().langfuse_secret_key)
        self.metadata = {'agent_id': agent_id, 'task_id': agent_id.removeprefix('agent-') if agent_id else None,
                         'run_id': run_id, 'phase': phase, 'engine': 'pi', **(metadata or {})}
        self.trace_id = hashlib.sha256((agent_id or run_id).encode()).hexdigest()[:32]
        self._safe(self._initialize)

    def _safe(self, operation):
        try:
            return operation()
        except Exception as exc:
            # 不记录异常正文，防止提供商凭据或业务输入随异常泄漏。
            logger.warning('Langfuse 记录失败：{}', type(exc).__name__)

    def _initialize(self):
        instance = client()
        if instance:
            from langfuse import propagate_attributes
            with propagate_attributes(session_id=self.agent_id, trace_name='bug-repair', tags=['pi'],
                                      metadata={key: str(value) for key, value in sanitize(self.metadata, self.secrets).items() if value is not None}):
                self.root = instance.start_observation(name=self.phase, as_type='agent',
                    trace_context={'trace_id': self.trace_id}, metadata=sanitize(self.metadata, self.secrets))

    def start(self, key, *, name, kind, input=None, metadata=None):
        def record():
            if self.root is None:
                return
            self.pending[key] = self.root.start_observation(name=name, as_type=kind,
                model=self.model if kind == 'generation' else None,
                input=sanitize(input, self.secrets), metadata=metadata)
        self._safe(record)

    def end(self, key, *, output=None, usage=None, error=None, metadata=None):
        def record():
            span = self.pending.pop(key, None)
            if span is None:
                return
            usage_details = {key: value for key, value in (usage or {}).items() if isinstance(value, int) and value >= 0}
            span.update(output=sanitize(output, self.secrets), usage_details=usage_details or None,
                        level='ERROR' if error else 'DEFAULT', status_message=sanitize(error, self.secrets), metadata=metadata)
            span.end()
        self._safe(record)

    def finish(self, status):
        for key in list(self.pending):
            self.end(key, error=f'阶段结束，调用未正常完成：{status}')
        def record():
            if self.root:
                self.root.update(output={'status': status}, level='DEFAULT' if status == 'submitted' else 'ERROR')
                self.root.end()
        self._safe(record)
