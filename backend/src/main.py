"""FastAPI 应用入口"""
import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from contextlib import asynccontextmanager
from pathlib import Path

from .config import get_settings
from .db.database import init_db
from .api import bugs, agents, llm_config, websocket, projects, settings as settings_api, confirmation
from .api.llm_config import load_config
from .llm.factory import LLMFactory

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时执行
    logger.info("Initializing application...")

    # 配置日志
    logger.add(
        "logs/app.log",
        rotation="500 MB",
        retention="10 days",
        level=settings.log_level,
    )

    # 初始化数据库
    await init_db()
    logger.info("Database initialized")

    from .core.repair_merge import sweep_orphan_worktrees
    try:
        await sweep_orphan_worktrees(settings.database_url.replace("sqlite+aiosqlite:///", ""))
    except Exception as e:
        logger.warning(f"Failed to sweep orphan worktrees: {e}")

    # 加载 LLM 配置
    try:
        llm_config = load_config()
        LLMFactory.set_config(llm_config)
        logger.info(f"LLM config loaded: {len(llm_config.providers)} providers")
    except Exception as e:
        logger.warning(f"Failed to load LLM config: {e}")

    # 启动多 Agent 协调器
    from .core.orchestrator import get_orchestrator
    orchestrator = get_orchestrator()
    await orchestrator.start()

    from .core.bug_sync import run_yunxiao_sync_loop
    sync_task = asyncio.create_task(run_yunxiao_sync_loop(settings))
    logger.info(f"云效自动同步已启动，间隔 {settings.yunxiao_sync_interval_seconds} 秒")

    logger.info("Application started successfully")

    try:
        yield
    finally:
        # 关闭时执行
        logger.info("Shutting down application...")
        sync_task.cancel()
        from .core.preview_manager import stop_all
        try:
            await orchestrator.stop()
        finally:
            await stop_all()
            await asyncio.gather(sync_task, return_exceptions=True)
            from .core.observability import shutdown
            await asyncio.to_thread(shutdown)


app = FastAPI(
    title="Self-Healing Agent Yunxiao",
    description="多 Agent 并发 Bug 修复系统",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(bugs.router)
app.include_router(agents.router)
app.include_router(llm_config.router)
app.include_router(websocket.router)
app.include_router(projects.router)
app.include_router(settings_api.router)
app.include_router(confirmation.router)


@app.get("/")
async def root():
    """健康检查"""
    return {"status": "ok", "message": "Self-Healing Agent Yunxiao API"}


@app.get("/health")
async def health():
    """健康检查端点"""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn

    # Uvicorn 仍可能监听 cwd；显式排除工作树，避免依赖安装触发服务重启。
    worktrees = Path(settings.worktrees_dir).expanduser().resolve()
    worktrees.mkdir(parents=True, exist_ok=True)
    uvicorn.run(
        "src.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
        reload_dirs=[str(Path(__file__).resolve().parent)],
        reload_excludes=[str(worktrees)],
    )
