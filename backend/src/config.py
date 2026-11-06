"""配置管理模块"""
from pydantic_settings import BaseSettings
from functools import lru_cache
import json
import os
from pathlib import Path
import tempfile
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


YUNXIAO_CONFIG_FILE = Path("data/yunxiao_config.json")


class YunxiaoConfigUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    api_base: Literal["https://openapi-rdc.aliyuncs.com"]
    org_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    access_token: str = Field(default="", repr=False)


class Settings(BaseSettings):
    """应用配置"""

    # API 配置
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # CORS
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "http://localhost:5173",
    ]

    # 数据库
    database_url: str = "sqlite+aiosqlite:///./data/yunxiao_agent.db"

    # Agent 配置
    max_concurrent_agents: int = 3
    agent_timeout: int = 1800  # 30分钟

    # 工作目录
    worktrees_dir: str = ".worktrees"

    # 日志
    log_level: str = "INFO"

    yunxiao_api_base: str = "https://openapi-rdc.aliyuncs.com"
    yunxiao_access_token: str = ""
    yunxiao_org_id: str = ""
    yunxiao_project_id: str = ""
    yunxiao_sync_interval_seconds: int = 300

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    """获取配置单例"""
    settings = Settings()
    if YUNXIAO_CONFIG_FILE.exists():
        config = YunxiaoConfigUpdate.model_validate_json(YUNXIAO_CONFIG_FILE.read_text(encoding="utf-8"))
        for field, value in config.model_dump().items():
            setattr(settings, f"yunxiao_{field}", value)
    return settings


def save_yunxiao_config(update: YunxiaoConfigUpdate) -> None:
    settings = get_settings()
    config = update.model_copy(update={
        "access_token": update.access_token or settings.yunxiao_access_token,
    })
    if not config.access_token:
        raise ValueError("请填写云效 Access Token")

    YUNXIAO_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    # 原子替换且仅当前用户可读写，写入成功后才更新运行时配置。
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=YUNXIAO_CONFIG_FILE.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            json.dump(config.model_dump(), file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
            os.replace(temporary, YUNXIAO_CONFIG_FILE)
        finally:
            temporary.unlink(missing_ok=True)
    for field, value in config.model_dump().items():
        setattr(settings, f"yunxiao_{field}", value)
