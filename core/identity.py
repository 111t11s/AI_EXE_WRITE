"""Propagate the authenticated request's token to its MCP subprocess only."""

from contextvars import ContextVar


AUTH_TOKEN: ContextVar[str | None] = ContextVar("auth_token", default=None)
