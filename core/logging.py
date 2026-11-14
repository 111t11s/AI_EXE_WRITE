"""应用日志：统一出口、文件轮转和异步请求上下文。"""

import logging
import json
import copy
import re
import sys
import threading
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core.config import get_settings


REQUEST_ID: ContextVar[str] = ContextVar("request_id", default="-")
_LOCK = threading.RLock()
_LOGGER_NAME = "ai_write"
LEVEL_NAMES = {"DEBUG": "调试", "INFO": "信息", "WARNING": "警告", "ERROR": "错误", "CRITICAL": "严重错误"}


def chinese_record(record):
    record = copy.copy(record)
    record.levelname = LEVEL_NAMES.get(record.levelname, record.levelname)
    message = record.getMessage()
    if record.name.startswith("mcp") and message.startswith("Processing request of type "):
        kind = message.rsplit(" ", 1)[-1]
        names = {"ListToolsRequest": "查询工具列表", "CallToolRequest": "调用工具",
                 "ListResourcesRequest": "查询资源列表", "ReadResourceRequest": "读取资源"}
        record.msg, record.args = "MCP 收到请求：%s", (names.get(kind, kind),)
    elif "OpenAI API call failed:" in message or "Error executing listener call_llm_native_tools:" in message:
        code = re.search(r"Error code:\s*(\d+)", message)
        record.msg, record.args = "模型接口调用失败：状态码=%s", (code[1] if code else "未知",)
    elif message.startswith("Using an approximate input token count"):
        record.msg, record.args = "模型未提供原生令牌计数，当前按每四个字符约一个令牌估算输入长度。", ()
    elif record.name.startswith("uvicorn"):
        translations = {
            "Waiting for application startup.": "正在启动应用…",
            "Application startup complete.": "应用启动完成。",
            "Waiting for application shutdown.": "正在关闭应用…",
            "Application shutdown complete.": "应用已关闭。",
            "Shutting down": "正在停止服务…",
        }
        message = translations.get(message, message)
        message = re.sub(r"Started server process \[(\d+)\]", r"服务进程已启动：进程号=\1", message)
        message = re.sub(r"Finished server process \[(\d+)\]", r"服务进程已退出：进程号=\1", message)
        message = message.replace("Uvicorn running on ", "服务监听地址：").replace("(Press CTRL+C to quit)", "（按 Ctrl+C 停止）")
        if record.name == "uvicorn.access" and isinstance(record.args, tuple) and len(record.args) == 5:
            client, method, path, protocol, status = record.args
            message = f"访问请求：客户端={client}，方法={method}，路径={path.split('?', 1)[0]}，协议=HTTP/{protocol}，状态码={status}"
        record.msg, record.args = message, ()
    return record


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = REQUEST_ID.get()
        return True


class SafeFormatter(logging.Formatter):
    """保留异常堆栈位置，省略可能包含 SQL 参数或模型输入的异常正文。"""

    def format(self, record):
        return super().format(chinese_record(record))

    def formatException(self, exc_info):
        import traceback

        frames = "".join(
            f'  File "{frame.filename}", line {frame.lineno}, in {frame.name}\n'
            for frame in traceback.extract_tb(exc_info[2])
        )
        return f"异常调用堆栈：\n{frames}{exc_info[0].__name__}"


class SafeJsonFormatter(SafeFormatter):
    def format(self, record: logging.LogRecord) -> str:
        record = chinese_record(record)
        values = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            values["exception"] = self.formatException(record.exc_info)
        return json.dumps(values, ensure_ascii=False)


def setup_logging(name: str | None = None) -> logging.Logger:
    """各模块共享父记录器的处理器，避免重复输出和重复轮转文件。"""

    with _LOCK:
        parent = logging.getLogger(_LOGGER_NAME)
        if not parent.handlers:
            settings = get_settings()
            parent.setLevel(settings.LOG_LEVEL)
            parent.propagate = False
            formatter = (SafeJsonFormatter() if settings.LOG_FORMAT.strip().lower() == "json"
                         else SafeFormatter(settings.LOG_FORMAT + " 请求标识=%(request_id)s"))
            console = logging.StreamHandler(sys.stdout)
            handlers: list[logging.Handler] = [console]
            if settings.LOG_FILE:
                path = Path(settings.LOG_FILE)
                if not path.is_absolute():
                    path = Path(__file__).resolve().parent.parent / path
                path.parent.mkdir(parents=True, exist_ok=True)
                handlers.append(RotatingFileHandler(
                    path, maxBytes=settings.LOG_MAX_BYTES,
                    backupCount=settings.LOG_BACKUP_COUNT, encoding="utf-8",
                ))
            for handler in handlers:
                handler.setFormatter(formatter)
                handler.addFilter(RequestIdFilter())
                parent.addHandler(handler)
    if not name or name == _LOGGER_NAME:
        return parent
    return logging.getLogger(name if name.startswith(_LOGGER_NAME + ".") else f"{_LOGGER_NAME}.{name}")


def get_logger(name: str) -> logging.Logger:
    return setup_logging(name)

def configure_external_logging() -> None:
    """独立入口使用中文标准日志，MCP 日志仅写 stderr，保护 stdio 协议。"""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(SafeFormatter("%(asctime)s [%(levelname)s] %(message)s"))
    handler.addFilter(RequestIdFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(get_settings().LOG_LEVEL)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def uvicorn_log_config() -> dict:
    return {
        "version": 1, "disable_existing_loggers": False,
        "formatters": {"chinese": {"()": SafeFormatter, "format": "%(asctime)s [%(levelname)s] %(message)s"}},
        "handlers": {"console": {"class": "logging.StreamHandler", "stream": "ext://sys.stderr", "formatter": "chinese"}},
        "loggers": {
            "uvicorn": {"handlers": ["console"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"level": "INFO"},
            "uvicorn.access": {"handlers": ["console"], "level": "INFO", "propagate": False},
        },
    }
