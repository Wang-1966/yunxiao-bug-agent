"""通用配置 API。云效 Token 仅允许写入，不回传客户端。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Literal
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


class RuntimeUpdate(BaseModel):
    engine: Literal['native', 'pi']


@router.get('/agent-runtime')
async def get_agent_runtime():
    from ..core.pi_agent import BRIDGE
    from ..core.runtime_config import engine
    return {'engine': engine(), 'pi_installed': (BRIDGE.parent / 'node_modules' / '@earendil-works' / 'pi-coding-agent' / 'package.json').exists()}


@router.put('/agent-runtime')
async def update_agent_runtime(update: RuntimeUpdate):
    from ..core.orchestrator import get_orchestrator
    from ..core import conversation, agent_discussion
    from ..core.runtime_config import save_engine
    runner = get_orchestrator()
    async with runner._agent_control_lock:
        if runner.running_agents or not runner.task_queue.empty() or conversation.active or agent_discussion.tasks:
            raise HTTPException(409, '请等待运行中的任务和对话结束后切换执行内核')
        current = await get_agent_runtime()
        if update.engine == 'pi' and not current['pi_installed']:
            raise HTTPException(409, '请先在 backend/pi 执行 npm ci，安装 Pi 依赖')
        save_engine(update.engine)
    return await get_agent_runtime()
