"""LLM 配置 API 路由"""
from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel
import httpx
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
    os.makedirs(os.path.dirname(CONFIG_FILE) or ".", exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config.model_dump(), f, ensure_ascii=False, indent=2)

    # 更新工厂配置
    LLMFactory.set_config(config)


def redact_config(config: LLMConfig) -> LLMConfig:
    """API Key 只保存在服务端，响应里不回传。"""
    public = config.model_copy(deep=True)
    for provider in public.providers:
        provider.api_key = ""
    return public


def merge_providers(current: list[LLMProvider], incoming: list[LLMProvider]) -> list[LLMProvider]:
    """空白 API Key 表示保留已保存的 Key。"""
    stored = {provider.id: provider for provider in current}
    seen: set[str] = set()
    merged: list[LLMProvider] = []
    for provider in incoming:
        provider_id = provider.id.strip()
        if not provider_id or provider_id in seen:
            raise ValueError("提供商 ID 不能为空，且不能重复")
        if not provider.name.strip() or not provider.base_url.strip() or not provider.default_model.strip():
            raise ValueError("请填写提供商名称、Base URL 和默认模型")
        seen.add(provider_id)
        api_key = provider.api_key.strip()
        existing = stored.get(provider_id)
        if not api_key:
            if existing is None or not existing.api_key.strip():
                raise ValueError(f"请为提供商「{provider.name.strip()}」填写 API Key")
            api_key = existing.api_key
        models = [model.strip() for model in provider.available_models if model.strip()]
        default_model = provider.default_model.strip()
        if default_model not in models:
            models.append(default_model)
        merged.append(provider.model_copy(update={
            "id": provider_id,
            "name": provider.name.strip(),
            "base_url": provider.base_url.strip(),
            "api_key": api_key,
            "default_model": default_model,
            "available_models": models,
        }))
    return merged


def validate_provider_refs(config: LLMConfig):
    ids = {provider.id for provider in config.providers}
    if config.default_provider and config.default_provider not in ids:
        raise ValueError("默认提供商不在列表中，请先修改默认配置")
    for stage, stage_cfg in (config.stage_config or {}).items():
        if stage_cfg.provider not in ids:
            raise ValueError(f"{stage} 阶段仍在使用列表中不存在的提供商")


@router.get("", response_model=LLMConfig)
async def get_config():
    """获取 LLM 配置"""
    try:
        return redact_config(load_config())
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("", response_model=LLMConfig)
async def update_config(update: LLMConfigUpdate):
    """更新 LLM 配置"""
    try:
        config = load_config()

        if update.providers is not None:
            config.providers = merge_providers(config.providers, update.providers)
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
        if update.providers is not None or update.default_provider is not None or update.stage_config is not None:
            validate_provider_refs(config)

        save_config(config)
        logger.info("LLM config updated successfully")
        return redact_config(config)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to update config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/test", response_model=ConnectionTestResult)
async def test_connection(provider: LLMProvider):
    """测试 LLM 连接。未提交 Key 时使用已保存的 Key。"""
    try:
        if not provider.api_key.strip():
            stored = next((item for item in load_config().providers if item.id == provider.id), None)
            if stored is None or not stored.api_key.strip():
                return ConnectionTestResult(
                    success=False,
                    provider_id=provider.id,
                    message="请先填写 API Key",
                )
            provider = provider.model_copy(update={"api_key": stored.api_key})
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


class ModelListRequest(BaseModel):
    provider_id: str = ""
    base_url: str
    api_key: str = ""


def parse_model_ids(payload) -> list[str]:
    """兼容 OpenAI `{data:[{id}]}`、`{models:[...]}` 和纯数组返回。"""
    items = payload.get("data") or payload.get("models") or [] if isinstance(payload, dict) else payload
    ids = []
    for item in items if isinstance(items, list) else []:
        raw = item.get("id") or item.get("name") if isinstance(item, dict) else item
        if isinstance(raw, str) and raw.strip():
            ids.append(raw.strip().removeprefix("models/"))
    return sorted(set(ids), key=str.lower)


@router.post("/models")
async def fetch_models(request: ModelListRequest):
    """向提供商的 `/models` 接口查询可用模型。未提交 Key 时使用已保存的 Key。"""
    base_url = request.base_url.strip().rstrip("/")
    if not base_url:
        raise HTTPException(status_code=400, detail="请先填写 Base URL")
    api_key = request.api_key.strip()
    if not api_key:
        stored = next((item for item in load_config().providers if item.id == request.provider_id), None)
        api_key = stored.api_key.strip() if stored else ""
    if not api_key:
        raise HTTPException(status_code=400, detail="请先填写 API Key")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0), follow_redirects=True) as client:
            response = await client.get(f"{base_url}/models", headers={"Authorization": f"Bearer {api_key}"})
        response.raise_for_status()
        models = parse_model_ids(response.json())
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"获取模型列表失败：HTTP {e.response.status_code}")
    except (httpx.HTTPError, ValueError) as e:
        logger.error(f"Fetch models failed: {e}")
        raise HTTPException(status_code=502, detail="获取模型列表失败：无法连接或返回格式不正确")
    if not models:
        raise HTTPException(status_code=502, detail="该提供商没有返回任何模型")
    return {"models": models}


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
