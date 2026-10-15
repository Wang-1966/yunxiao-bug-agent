"""LLM 客户端工厂"""
from typing import Optional
from loguru import logger

from .unified_client import UnifiedLLMClient
from ..models.llm_config import LLMConfig, LLMProvider


class LLMClientWrapper:
    """LLM 客户端包装器"""

    def __init__(self, client: UnifiedLLMClient, model: str, defaults=None):
        self.client = client
        self.model = model
        self.defaults = defaults or {}

    async def ainvoke(self, messages: list[dict], **kwargs):
        """调用 LLM"""
        return await self.client.ainvoke(
            messages=messages,
            model=self.model,
            **{**self.defaults, **kwargs}
        )

    async def achat(self, messages: list[dict], tools: list[dict], **kwargs):
        """原生工具调用"""
        return await self.client.achat(
            messages=messages,
            model=self.model,
            tools=tools,
            **{**self.defaults, **kwargs}
        )


class LLMFactory:
    """LLM 客户端工厂"""

    _config: Optional[LLMConfig] = None

    @classmethod
    def set_config(cls, config: LLMConfig):
        """设置全局配置"""
        cls._config = config
        logger.info(f"LLM config updated: {len(config.providers)} providers")

    @classmethod
    def get_config(cls) -> LLMConfig:
        """获取全局配置"""
        if cls._config is None:
            raise ValueError("LLM config not initialized")
        return cls._config

    @classmethod
    def get_response_style(cls) -> str:
        """未初始化配置时保持既有表达，独立节点调用也可使用默认模板。"""
        return cls._config.response_style if cls._config is not None else 'detailed'

    @classmethod
    def create(
        cls,
        provider_id: Optional[str] = None,
        model: Optional[str] = None,
        stage: Optional[str] = None
    ) -> LLMClientWrapper:
        """
        创建 LLM 客户端

        优先级：stage > provider_id+model > 默认配置

        Args:
            provider_id: 提供商 ID
            model: 模型名称
            stage: 阶段名称 (analyze, propose, fix, verify)

        Returns:
            LLMClientWrapper 实例
        """
        config = cls.get_config()
        defaults = {}

        # 确定使用哪个 provider 和 model
        if stage and config.use_stage_config and config.stage_config:
            stage_cfg = config.stage_config.get(stage)
            if stage_cfg:
                defaults = {'temperature': stage_cfg.temperature, 'max_tokens': stage_cfg.max_tokens}
                target_provider_id = stage_cfg.provider
                target_model = stage_cfg.model
                logger.debug(f"Using stage config: stage={stage}, provider={target_provider_id}, model={target_model}")
            else:
                target_provider_id = provider_id or config.default_provider
                target_model = model or config.default_model
        else:
            target_provider_id = provider_id or config.default_provider
            target_model = model or config.default_model

        # 找到对应的 provider
        provider = next(
            (p for p in config.providers if p.id == target_provider_id),
            None
        )

        if not provider:
            raise ValueError(f"Provider {target_provider_id} not found")

        if not provider.enabled:
            raise ValueError(f"Provider {target_provider_id} is disabled")

        # 创建客户端
        client = UnifiedLLMClient(provider)
        return LLMClientWrapper(client, target_model, defaults)
