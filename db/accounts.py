"""Account persistence and password hashing, without external crypto packages."""

import hashlib
import hmac
import secrets
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, MetaData, String, Table, delete, func, update
from sqlalchemy.dialects.mysql import BIGINT


metadata = MetaData()
account_id_type = BigInteger().with_variant(BIGINT(unsigned=True), "mysql").with_variant(Integer, "sqlite")
account = Table(
    "app_account", metadata,
    Column("id", account_id_type, primary_key=True, autoincrement=True),
    Column("username", String(32), nullable=False, unique=True),
    Column("display_name", String(80), nullable=False),
    Column("password_hash", String(255), nullable=False),
    Column("is_active", Boolean, nullable=False, server_default="1"),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
)
auth_session = Table(
    "app_auth_session", metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("account_id", account_id_type, ForeignKey("app_account.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("expires_at", DateTime, nullable=False, index=True),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
)


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f"scrypt${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, salt, expected = stored.split("$")
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def replace_password(connection, account_id: int, password_hash: str) -> int:
    """Caller locks the account row; password and session revocation commit together."""
    connection.execute(update(account).where(account.c.id == account_id).values(password_hash=password_hash))
    result = connection.execute(delete(auth_session).where(auth_session.c.account_id == account_id))
    return result.rowcount
