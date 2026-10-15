"""确认策略配置管理"""
import json
from pathlib import Path
from typing import Optional
from loguru import logger

from ..models.confirmation_config import ConfirmationConfig

CONFIRMATION_CONFIG_FILE = Path("data/confirmation_config.json")


def load_confirmation_config() -> ConfirmationConfig:
    """加载确认策略配置"""
    if CONFIRMATION_CONFIG_FILE.exists():
        try:
            config_data = json.loads(CONFIRMATION_CONFIG_FILE.read_text(encoding='utf-8'))
            return ConfirmationConfig.model_validate(config_data)
        except Exception as e:
            logger.warning(f"Failed to load confirmation config: {e}, using default")

    return ConfirmationConfig()


def save_confirmation_config(config: ConfirmationConfig) -> None:
    """保存确认策略配置"""
    CONFIRMATION_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIRMATION_CONFIG_FILE.write_text(
        config.model_dump_json(indent=2, ensure_ascii=False),
        encoding='utf-8'
    )
    logger.info(f"Confirmation config saved: mode={config.mode}")


def get_default_config() -> ConfirmationConfig:
    """获取默认配置"""
    return ConfirmationConfig()
