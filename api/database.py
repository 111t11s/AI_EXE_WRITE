"""Shared database dependency and transaction error handling."""

from contextlib import contextmanager
from sqlite3 import IntegrityError as SQLiteIntegrityError
from typing import Annotated

from fastapi import Depends, HTTPException
from sqlalchemy import Engine
from sqlalchemy.exc import DBAPIError, TimeoutError as PoolTimeout

from core.logging import get_logger
from db.connection import DatabaseNotConfigured, get_engine


logger = get_logger("database")


def get_database() -> Engine:
    try:
        return get_engine()
    except DatabaseNotConfigured:
        raise HTTPException(503, "数据库连接未配置") from None


Database = Annotated[Engine, Depends(get_database)]


@contextmanager
def transaction(engine):
    try:
        with engine.begin() as connection:
            yield connection
    except PoolTimeout:
        raise HTTPException(503, "数据库连接池暂时繁忙") from None
    except DBAPIError as exc:
        code = next(iter(getattr(exc.orig, "args", ())), None)
        if code == 1062 or isinstance(exc.orig, SQLiteIntegrityError):
            raise HTTPException(409, "记录与现有数据冲突") from None
        if code in {1451, 1452}:
            raise HTTPException(409, "关联记录不存在或仍被引用") from None
        if code in {1048, 1264, 1366, 1406, 3140, 3141}:
            raise HTTPException(422, "字段值超出数据库允许的范围") from None
        logger.error("数据操作失败：错误码=%s", code if isinstance(code, int) else "未知")
        if exc.connection_invalidated or code in {1044, 1045, 1049, 1054, 1146, 1205, 1213, 2002, 2003, 2006, 2013, 2026}:
            raise HTTPException(503, "数据库暂时不可用，请确认已执行数据库迁移") from None
        raise HTTPException(500, "数据库操作失败") from None
