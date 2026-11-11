"""应用日志：统一出口、文件轮转和异步请求上下文。"""

import logging
import sys
import threading
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core.config import get_settings


REQUEST_ID: ContextVar[str] = ContextVar("request_id", default="-")
_LOCK = threading.RLock()
_LOGGER_NAME = "ai_write"


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = REQUEST_ID.get()
        return True


class SafeFormatter(logging.Formatter):
    """保留异常堆栈位置，省略可能包含 SQL 参数或模型输入的异常正文。"""

    def formatException(self, exc_info):
        import traceback

        frames = "".join(
            f'  File "{frame.filename}", line {frame.lineno}, in {frame.name}\n'
            for frame in traceback.extract_tb(exc_info[2])
        )
        return f"Traceback (most recent call last):\n{frames}{exc_info[0].__name__}"


def setup_logging(name: str | None = None) -> logging.Logger:
    """各模块共享父记录器的处理器，避免重复输出和重复轮转文件。"""

    with _LOCK:
        parent = logging.getLogger(_LOGGER_NAME)
        if not parent.handlers:
            settings = get_settings()
            parent.setLevel(settings.LOG_LEVEL)
            parent.propagate = False
            formatter = SafeFormatter(settings.LOG_FORMAT + " request_id=%(request_id)s")
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
