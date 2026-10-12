"""LLM 配置 API 路由"""
from fastapi import APIRouter, HTTPException
from loguru import logger
import json
import os

from ..models.llm_config import (
    LLMConfig,
    LLMConfigUpdate,
    ConnectionTestResult,
    LLMProvider
)
from ..llm.factory import LLMFactory
from ..llm.unified_client import UnifiedLLMClient

router = APIRouter(prefix="/api/llm-config", tags=["LLM Config"])

CONFIG_FILE = "data/llm_config.json"


def load_config() -> LLMConfig:
    """从文件加载配置"""
    if not os.path.exists(CONFIG_FILE):
        # 返回默认配置
        default_config = LLMConfig(
            providers=[],
            default_provider="",
            default_model=""
        )
        return default_config

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
        return LLMConfig(**data)


def save_config(config: LLMConfig):
    """保存配置到文件"""
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config.model_dump(), f, ensure_ascii=False, indent=2)

    # 更新工厂配置
    LLMFactory.set_config(config)


@router.get("", response_model=LLMConfig)
async def get_config():
    """获取 LLM 配置"""
    try:
        config = load_config()
        return config
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("", response_model=LLMConfig)
async def update_config(update: LLMConfigUpdate):
    """更新 LLM 配置"""
    try:
        config = load_config()

        # 更新字段
        if update.providers is not None:
            config.providers = update.providers
        if update.default_provider is not None:
            config.default_provider = update.default_provider
        if update.default_model is not None:
            config.default_model = update.default_model
        if update.use_stage_config is not None:
            config.use_stage_config = update.use_stage_config
        if update.stage_config is not None:
            config.stage_config = update.stage_config
        if update.response_style is not None:
            config.response_style = update.response_style

        save_config(config)
        logger.info("LLM config updated successfully")

        return config
    except Exception as e:
        logger.error(f"Failed to update config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/test", response_model=ConnectionTestResult)
async def test_connection(provider: LLMProvider):
    """测试 LLM 连接"""
    try:
        client = UnifiedLLMClient(provider)
        success, latency = await client.test_connection()

        if success:
            return ConnectionTestResult(
                success=True,
                provider_id=provider.id,
                message="连接成功",
                latency_ms=latency
            )
        else:
            return ConnectionTestResult(
                success=False,
                provider_id=provider.id,
                message="连接失败"
            )
    except Exception as e:
        logger.error(f"Connection test error: {e}")
        return ConnectionTestResult(
            success=False,
            provider_id=provider.id,
            message=f"测试失败: {str(e)}"
        )


@router.post("/estimate")
async def estimate_cost(bug_count: int = 1):
    """预估成本"""
    # 简单的成本预估逻辑
    # 实际应该根据配置的模型和历史数据来计算

    tokens_per_bug = 50000  # 平均每个 bug 使用的 token 数
    cost_per_1k_tokens = 0.01  # 假设每 1k token 成本

    total_tokens = bug_count * tokens_per_bug
    estimated_cost = (total_tokens / 1000) * cost_per_1k_tokens

    return {
        "bug_count": bug_count,
        "estimated_tokens": total_tokens,
        "estimated_cost_usd": round(estimated_cost, 2),
        "note": "This is a rough estimate based on average usage"
    }
