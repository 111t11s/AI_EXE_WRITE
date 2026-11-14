"""Username/password login and persistent, revocable bearer sessions."""

import secrets
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import delete, insert, select
from starlette.concurrency import run_in_threadpool

from api.database import Database, transaction
from core.config import get_settings
from core.identity import AUTH_TOKEN
from db.accounts import account, auth_session, hash_password, replace_password, token_hash, utcnow, verify_password


router = APIRouter(prefix="/auth", tags=["账号"])
bearer = HTTPBearer(auto_error=False)
_DUMMY_HASH = hash_password("not-a-real-password")


class Account(BaseModel):
    id: int
    username: str
    display_name: str


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-z0-9_][a-z0-9_.-]*$")
    password: SecretStr = Field(min_length=8, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class Registration(Credentials):
    display_name: str = Field(default="", max_length=80)


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: SecretStr = Field(min_length=1, max_length=128)
    new_password: SecretStr = Field(min_length=8, max_length=128)


class LoginReceipt(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    account: Account


def unauthorized():
    return HTTPException(401, "请先登录，或重新登录已过期的账号", headers={"WWW-Authenticate": "Bearer"})


def require_token(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer" or not 20 <= len(credentials.credentials) <= 200:
        raise unauthorized()
    return credentials.credentials


BearerToken = Annotated[str, Depends(require_token)]


def authenticate(engine, token):
    with transaction(engine) as connection:
        row = connection.execute(select(account.c.id, account.c.username, account.c.display_name).select_from(
            account.join(auth_session, account.c.id == auth_session.c.account_id)
        ).where(auth_session.c.token_hash == token_hash(token), auth_session.c.expires_at > utcnow(),
                account.c.is_active.is_(True))).mappings().one_or_none()
    if row is None:
        raise unauthorized()
    return Account(**row)


async def get_current_account(token: BearerToken, engine: Database):
    user = await run_in_threadpool(authenticate, engine, token)
    context_token = AUTH_TOKEN.set(token)
    try:
        yield user
    finally:
        AUTH_TOKEN.reset(context_token)


CurrentAccount = Annotated[Account, Depends(get_current_account)]


def start_session(connection, row) -> LoginReceipt:
    token = secrets.token_urlsafe(32)
    lifetime = get_settings().AUTH_SESSION_HOURS * 3600
    connection.execute(delete(auth_session).where(auth_session.c.account_id == row["id"],
                                                 auth_session.c.expires_at <= utcnow()))
    connection.execute(insert(auth_session).values(token_hash=token_hash(token), account_id=row["id"],
                                                   expires_at=utcnow() + timedelta(seconds=lifetime)))
    return LoginReceipt(access_token=token, expires_in=lifetime, account=Account(**row))


@router.post("/register", response_model=LoginReceipt, status_code=201)
def register(payload: Registration, engine: Database, response: Response):
    if not get_settings().AUTH_REGISTRATION_ENABLED:
        raise HTTPException(403, "当前暂未开放注册")
    password_hash = hash_password(payload.password.get_secret_value())
    with transaction(engine) as connection:
        if connection.scalar(select(account.c.id).where(account.c.username == payload.username)) is not None:
            raise HTTPException(409, "用户名已被使用")
        result = connection.execute(insert(account).values(username=payload.username,
            display_name=payload.display_name.strip() or payload.username, password_hash=password_hash))
        row = connection.execute(select(account.c.id, account.c.username, account.c.display_name).where(
            account.c.id == result.lastrowid)).mappings().one()
        receipt = start_session(connection, row)
    response.headers["Cache-Control"] = "no-store"
    return receipt


@router.post("/login", response_model=LoginReceipt)
def login(payload: Credentials, engine: Database, response: Response):
    with transaction(engine) as connection:
        row = connection.execute(select(account).where(account.c.username == payload.username)
                                 .with_for_update()).mappings().one_or_none()
        valid = verify_password(payload.password.get_secret_value(), row["password_hash"] if row else _DUMMY_HASH)
        if row is None or not valid or not row["is_active"]:
            raise HTTPException(401, "用户名或密码错误")
        receipt = start_session(connection, row)
    response.headers["Cache-Control"] = "no-store"
    return receipt


@router.get("/me", response_model=Account)
def me(user: CurrentAccount, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return user


@router.post("/logout", status_code=204)
def logout(user: CurrentAccount, token: BearerToken, engine: Database):
    with transaction(engine) as connection:
        connection.execute(delete(auth_session).where(auth_session.c.token_hash == token_hash(token)))
    return Response(status_code=204)


@router.post("/password", status_code=204)
def change_password(payload: PasswordChange, user: CurrentAccount, token: BearerToken, engine: Database):
    new_hash = hash_password(payload.new_password.get_secret_value())
    with transaction(engine) as connection:
        row = connection.execute(select(account).where(account.c.id == user.id)
                                 .with_for_update()).mappings().one_or_none()
        # Recheck the session after taking the lock in case another reset revoked it.
        valid_session = connection.scalar(select(auth_session.c.account_id).where(
            auth_session.c.token_hash == token_hash(token), auth_session.c.account_id == user.id,
            auth_session.c.expires_at > utcnow()))
        if row is None or not row["is_active"] or valid_session is None:
            raise unauthorized()
        if not verify_password(payload.current_password.get_secret_value(), row["password_hash"]):
            raise HTTPException(400, "当前密码不正确")
        replace_password(connection, user.id, new_hash)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
