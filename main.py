import os
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

from core.logging import get_logger
from core.config import get_settings
from core.middleware import RequestLoggingMiddleware
from core.static import FrontendFiles

logger = get_logger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("服务启动：名称=%s，版本=%s", app.title, app.version)
    try:
        yield
    finally:
        logger.info("服务关闭")

from api.novel_assistant_api import router as assistant_router
from api.novel_contracts import CRUD_PREFIX
from api.novel_crud_api import router as crud_router
from api.novel_writing_api import router as writing_router
from api.account_api import CurrentAccount, router as account_router

app = FastAPI(
    title="AI 小说工程 API",
    description="账号登录、个人小说书架、小说设定助手、写作智能体与 15 类小说数据的 CRUD 接口",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if get_settings().ENABLE_API_DOCS else None,
    redoc_url=None,
    openapi_url="/openapi.json" if get_settings().ENABLE_API_DOCS else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in get_settings().CORS_ORIGINS.split(",") if origin.strip()],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggingMiddleware)


@app.get("/healthz", include_in_schema=False)
def health():
    return {"status": "ok"}


# Older Android builds check this endpoint before opening the library.
@app.get("/api/v1/access", include_in_schema=False)
def access(user: CurrentAccount):
    return {"authorized": True, "account": user}


# 注册小说设定助手 API，并提供一个可直接操作的浏览器页面。
app.include_router(assistant_router, prefix="/api/v1", tags=["小说设定助手"])
app.include_router(account_router, prefix="/api/v1")
app.include_router(crud_router, prefix=CRUD_PREFIX)
app.include_router(writing_router, prefix=CRUD_PREFIX)
app.mount("/", FrontendFiles(directory=PROJECT_ROOT / "frontend", html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    from core.logging import configure_external_logging, uvicorn_log_config

    configure_external_logging()

    settings = get_settings()
    browser_host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(settings.HOST, settings.HOST)
    if ":" in browser_host:
        browser_host = f"[{browser_host}]"
    browser_origin = f"http://{browser_host}:{settings.PORT}"
    os.environ.setdefault("AITXT_API_BASE_URL", f"{browser_origin}{CRUD_PREFIX}")
    print(f"服务启动后，请在浏览器打开工作台：{browser_origin}/", flush=True)
    uvicorn.run(app, host=settings.HOST, port=settings.PORT, log_config=uvicorn_log_config())
