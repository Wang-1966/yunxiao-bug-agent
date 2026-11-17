"""通用配置 API。云效 Token 仅允许写入，不回传客户端。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from loguru import logger

from ..config import YunxiaoConfigUpdate, get_settings, save_yunxiao_config

router = APIRouter(prefix="/api/settings", tags=["Settings"])


class YunxiaoConfigResponse(BaseModel):
    api_base: str
    org_id: str
    project_id: str
    token_configured: bool


@router.get("/yunxiao", response_model=YunxiaoConfigResponse)
async def get_yunxiao_config():
    settings = get_settings()
    return YunxiaoConfigResponse(
        api_base=settings.yunxiao_api_base,
        org_id=settings.yunxiao_org_id,
        project_id=settings.yunxiao_project_id,
        token_configured=bool(settings.yunxiao_access_token),
    )


@router.put("/yunxiao", response_model=YunxiaoConfigResponse)
async def update_yunxiao_config(update: YunxiaoConfigUpdate):
    try:
        save_yunxiao_config(update)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OSError:
        logger.error("Failed to persist Yunxiao configuration")
        raise HTTPException(status_code=500, detail="保存云效配置失败，请检查配置目录权限")
    return await get_yunxiao_config()


@router.get('/agent-runtime')
async def get_agent_runtime():
    from ..core.pi_agent import BRIDGE
    return {'engine': 'pi', 'pi_installed': (BRIDGE.parent / 'node_modules' / '@earendil-works' / 'pi-coding-agent' / 'package.json').exists()}
