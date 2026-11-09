"""LLM 配置数据模型"""
from pydantic import BaseModel, Field, field_validator
from typing import Literal, Optional

ResponseStyle = Literal['detailed', 'concise', 'minimal']


class LLMProvider(BaseModel):
    """LLM 提供商配置"""
    id: str = Field(..., description="提供商 ID")
    name: str = Field(..., description="显示名称")
    type: str = Field(default="openai-compatible", description="类型")
    base_url: str = Field(..., description="API Base URL")
    api_key: str = Field(..., description="API Key")
    available_models: list[str] = Field(..., description="可用模型列表")
    default_model: str = Field(..., description="默认模型")
    enabled: bool = Field(default=True, description="是否启用")
    context_windows: dict[str, int] = Field(default_factory=dict, description="模型上下文窗口 tokens；未填写使用兼容估算")
    stream_usage: bool = Field(default=False, description='提供商支持在流式响应返回 usage 时启用')

    @field_validator('context_windows')
    @classmethod
    def validate_context_windows(cls, value):
        if any(not name.strip() or window < 8192 or window > 10_000_000 for name, window in value.items()):
            raise ValueError('模型上下文窗口必须在 8192–10000000 tokens 之间')
        return value


class StageConfig(BaseModel):
    """阶段配置"""
    provider: str = Field(..., description="Provider ID")
    model: str = Field(..., description="模型名称")
    temperature: float = Field(default=0.0, description="温度参数")
    max_tokens: int = Field(default=8000, description="最大 token 数")


class LLMConfig(BaseModel):
    """LLM 总配置"""
    providers: list[LLMProvider] = Field(..., description="提供商列表")
    default_provider: str = Field(..., description="默认提供商 ID")
    default_model: str = Field(..., description="默认模型")
    use_stage_config: bool = Field(default=False, description="是否使用阶段配置")
    response_style: ResponseStyle = Field(default='detailed', description="Agent 回答风格")
    stage_config: Optional[dict[str, StageConfig]] = Field(
        default=None,
        description="阶段配置映射"
    )


class LLMConfigUpdate(BaseModel):
    """LLM 配置更新请求"""
    providers: Optional[list[LLMProvider]] = None
    default_provider: Optional[str] = None
    default_model: Optional[str] = None
    use_stage_config: Optional[bool] = None
    response_style: Optional[ResponseStyle] = None
    stage_config: Optional[dict[str, StageConfig]] = None


class ConnectionTestResult(BaseModel):
    """连接测试结果"""
    success: bool
    provider_id: str
    message: str
    latency_ms: Optional[float] = None
