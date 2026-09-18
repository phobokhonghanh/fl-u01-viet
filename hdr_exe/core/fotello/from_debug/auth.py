"""Authentication and Token Management for Fotello (from_debug)."""
from __future__ import annotations

import base64
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from core.fotello.from_debug.constants import FIREBASE_AUTH_URL, TOKENS_FILE

_fotello_state: dict[str, Any] = {
    "connected": False,
    "user_id": None,
    "email": None,
    "tokens": None,
    "team_id": None,
}


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        rem = len(parts[1]) % 4
        padded = parts[1] + "=" * ((4 - rem) % 4)
        raw = base64.urlsafe_b64decode(padded)
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def load_fotello_tokens() -> dict[str, Any]:
    if not TOKENS_FILE.exists():
        return {}
    try:
        with open(TOKENS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                _fotello_state["tokens"] = data
                return data
    except Exception:
        pass
    return {}


def save_fotello_tokens(tokens: dict[str, Any]) -> None:
    TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(tokens, f, indent=2)
    _fotello_state["tokens"] = tokens


def refresh_firebase_token(refresh_token: str) -> dict[str, str]:
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }).encode("utf-8")
    req = urllib.request.Request(
        FIREBASE_AUTH_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return {
            "access_token": data.get("access_token", ""),
            "id_token": data.get("id_token", ""),
            "refresh_token": data.get("refresh_token", refresh_token),
            "user_id": data.get("user_id", ""),
        }


def get_tokens() -> dict[str, Any] | None:
    tokens = load_fotello_tokens()
    if not tokens:
        return None
    id_tok = tokens.get("id_token", "")
    payload = _decode_jwt_payload(id_tok)
    exp = payload.get("exp", 0)
    now = int(time.time())
    if exp and exp - now < 300:
        ref = tokens.get("refresh_token")
        if ref:
            try:
                new_tok = refresh_firebase_token(ref)
                if new_tok:
                    tokens.update(new_tok)
                    save_fotello_tokens(tokens)
            except Exception:
                pass
    return tokens


def validate_session(log_fn: Callable[[str, str], None] | None = None) -> bool:
    tokens = get_tokens()
    if not tokens:
        if log_fn:
            log_fn("[Fotello][Auth] Chưa tìm thấy token đăng nhập.", "warn")
        return False
    valid = bool(tokens.get("access_token") or tokens.get("id_token"))
    if log_fn:
        if valid:
            log_fn("[Fotello][Auth] Phiên làm việc hợp lệ.", "success")
        else:
            log_fn("[Fotello][Auth] Phiên làm việc không hợp lệ hoặc đã hết hạn.", "error")
    return valid


def get_status() -> dict[str, Any]:
    valid = validate_session()
    tokens = load_fotello_tokens()
    email = _decode_jwt_payload(tokens.get("id_token", "")).get("email", "")
    return {
        "connected": valid,
        "service": "fotello.from_debug",
        "email": email,
        "team_id": _fotello_state.get("team_id", ""),
    }
