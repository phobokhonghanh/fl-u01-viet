"""
Context variable to hold the current Cloudflare Worker 'env' object.
"""

from contextvars import ContextVar
from typing import Any, Optional

_active_env: ContextVar[Optional[Any]] = ContextVar("_active_env", default=None)


def set_current_env(env: Any):
    return _active_env.set(env)


def reset_current_env(token):
    _active_env.reset(token)


def get_current_env() -> Optional[Any]:
    return _active_env.get()
