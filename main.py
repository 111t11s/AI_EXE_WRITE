import os
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

from core.logging import get_logger
from core.config import get_settings
from core.middleware import RequestLoggingMiddleware
from core.access import AccessKeyMiddleware

logger = get_logger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.ACCESS_KEY_REQUIRED and not settings.APP_ACCESS_KEY.get_secret_value():
        raise RuntimeError("请在服务器配置 APP_ACCESS_KEY 后启动服务")
    logger.info("服务启动 name=%s version=%s", app.title, app.version)
    try:
        yield
    finally:
        logger.info("服务关闭")

from api.novel_assistant_api import router as assistant_router
from api.novel_contracts import CRUD_PREFIX
from api.novel_crud_api import router as crud_router
from api.novel_writing_api import router as writing_router

app = FastAPI(
    title="AI 小说工程 API",
    description="小说设定助手、写作智能体与 15 张小说数据表的 CRUD 接口",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if get_settings().ENABLE_API_DOCS else None,
    redoc_url=None,
    openapi_url="/openapi.json" if get_settings().ENABLE_API_DOCS else None,
)

app.add_middleware(AccessKeyMiddleware)

# CORS must wrap access checks so Android also receives CORS headers on a 401.
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


@app.get("/api/v1/access", include_in_schema=False)
def access():
    return {"authorized": True}


# 注册小说设定助手 API，并提供一个可直接操作的浏览器页面。
app.include_router(assistant_router, prefix="/api/v1", tags=["小说设定助手"])
app.include_router(crud_router, prefix=CRUD_PREFIX)
app.include_router(writing_router, prefix=CRUD_PREFIX)
app.mount("/", StaticFiles(directory=PROJECT_ROOT / "frontend", html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    browser_host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(settings.HOST, settings.HOST)
    if ":" in browser_host:
        browser_host = f"[{browser_host}]"
    browser_origin = f"http://{browser_host}:{settings.PORT}"
    os.environ.setdefault("AITXT_API_BASE_URL", f"{browser_origin}{CRUD_PREFIX}")
    print(f"服务启动后，请在浏览器打开工作台：{browser_origin}/", flush=True)
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
