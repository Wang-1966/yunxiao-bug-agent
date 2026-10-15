"""确认策略配置 API"""
from fastapi import APIRouter, HTTPException
from loguru import logger

from ..models.confirmation_config import ConfirmationConfig
from ..core.confirmation_manager import (
    load_confirmation_config,
    save_confirmation_config,
    get_default_config
)

router = APIRouter(prefix="/api/confirmation", tags=["Confirmation"])


@router.get("/config", response_model=ConfirmationConfig)
async def get_confirmation_config():
    """获取当前确认策略配置"""
    try:
        config = load_confirmation_config()
        return config
    except Exception as e:
        logger.error(f"Failed to load confirmation config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/config", response_model=ConfirmationConfig)
async def update_confirmation_config(config: ConfirmationConfig):
    """更新确认策略配置"""
    try:
        save_confirmation_config(config)
        logger.info(f"Confirmation config updated: mode={config.mode}")
        return config
    except Exception as e:
        logger.error(f"Failed to save confirmation config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/config/reset", response_model=ConfirmationConfig)
async def reset_confirmation_config():
    """重置为默认配置"""
    try:
        default_config = get_default_config()
        save_confirmation_config(default_config)
        logger.info("Confirmation config reset to default")
        return default_config
    except Exception as e:
        logger.error(f"Failed to reset confirmation config: {e}")
        raise HTTPException(status_code=500, detail=str(e))
