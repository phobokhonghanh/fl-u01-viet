"""Authentication and Token Management for Fotello Engine."""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from core.fotello.config import load_fotello_config
from core.fotello.constants import get_fotello_tokens_file


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    """Giải mã payload JWT mà không in token ra ngoài."""
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


def load_tokens() -> dict[str, Any]:
    """Tải token từ storage riêng của Fotello (~/.hdr_exe/fotello/tokens.json)."""
    tokens_file = get_fotello_tokens_file()
    if not tokens_file.is_file():
        return {}
    try:
        with open(tokens_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_tokens(tokens: dict[str, Any]) -> None:
    """Ghi token nguyên tử (atomic write) với quyền POSIX riêng tư 0700/0600."""
    tokens_file = get_fotello_tokens_file()
    tokens_dir = tokens_file.parent
    tokens_dir.mkdir(parents=True, exist_ok=True)
    if hasattr(os, "chmod"):
        try:
            os.chmod(tokens_dir, 0o700)
        except OSError:
            pass

    temp_file = tokens_dir / f".tmp_{os.getpid()}_{time.time_ns()}.json"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(tokens, f, indent=2)
        if hasattr(os, "chmod"):
            try:
                os.chmod(temp_file, 0o600)
            except OSError:
                pass
        os.replace(temp_file, tokens_file)
    finally:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except OSError:
                pass


def refresh_firebase_token(refresh_token: str) -> dict[str, str]:
    """Làm mới Firebase id_token và access_token qua securetoken endpoint."""
    config = load_fotello_config()
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }).encode("utf-8")
    req = urllib.request.Request(
        config.endpoints.auth_url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=config.network.request_timeout_seconds) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return {
            "access_token": data.get("access_token", ""),
            "id_token": data.get("id_token", ""),
            "refresh_token": data.get("refresh_token", refresh_token),
            "user_id": data.get("user_id", ""),
        }


def get_tokens() -> dict[str, Any] | None:
    """Lấy token hiện tại, tự động refresh nếu gần hết hạn (dưới 300s)."""
    tokens = load_tokens()
    if not tokens:
        return None
    id_tok = tokens.get("id_token", "")
    payload = _decode_jwt_payload(id_tok)
    exp = payload.get("exp", 0)
    now = int(time.time())
    if exp and exp - now < load_fotello_config().auth.refresh_margin_seconds:
        ref = tokens.get("refresh_token")
        if ref:
            try:
                new_tok = refresh_firebase_token(ref)
                if new_tok and new_tok.get("id_token"):
                    tokens.update(new_tok)
                    save_tokens(tokens)
            except Exception:
                pass
    return tokens


def check_auth_session() -> tuple[str, str, dict[str, Any] | None]:
    """Kiểm tra chi tiết trạng thái phiên làm việc.
    
    Trả về: (state, message, tokens)
    Các trạng thái:
    - 'valid': Token hợp lệ
    - 'missing_token': Chưa có token
    - 'expired': Token hết hạn và không có refresh token
    - 'refresh_failed': Refresh thất bại do credential bị từ chối
    - 'network_error': Lỗi mạng khi kiểm tra/refresh
    """
    tokens = load_tokens()
    if not tokens or not (tokens.get("access_token") or tokens.get("id_token")):
        return "missing_token", "Chưa tìm thấy token đăng nhập.", None

    id_tok = tokens.get("id_token", "") or tokens.get("access_token", "")
    payload = _decode_jwt_payload(id_tok)
    exp = payload.get("exp", 0)
    now = int(time.time())

    # Token còn hạn trên 300 giây
    if exp and (exp - now) >= load_fotello_config().auth.refresh_margin_seconds:
        return "valid", "Phiên làm việc hợp lệ.", tokens

    # Token hết hạn hoặc sắp hết hạn (< 300s): thử refresh
    ref = tokens.get("refresh_token")
    if not ref:
        return "expired", "Token đã hết hạn và không có refresh token.", tokens

    try:
        new_tok = refresh_firebase_token(ref)
        if new_tok and (new_tok.get("id_token") or new_tok.get("access_token")):
            tokens.update(new_tok)
            save_tokens(tokens)
            return "valid", "Làm mới phiên làm việc thành công.", tokens
        return "refresh_failed", "Làm mới phiên thất bại: phản hồi không chứa token mới.", tokens
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 401, 403):
            return "refresh_failed", f"Phiên làm việc đã hết hạn hoặc bị hủy (HTTP {exc.code}). Cần đăng nhập lại.", tokens
        return "network_error", f"Lỗi máy chủ khi làm mới phiên (HTTP {exc.code}).", tokens
    except (urllib.error.URLError, TimeoutError) as exc:
        return "network_error", f"Lỗi kết nối mạng khi kiểm tra phiên: {exc}", tokens
    except Exception as exc:
        return "refresh_failed", f"Lỗi không xác định khi làm mới phiên: {exc}", tokens


def validate_session(log_fn: Callable[[str, str], None] | None = None) -> bool:
    """Kiểm tra tính hợp lệ của phiên đăng nhập hiện tại."""
    state, msg, _ = check_auth_session()
    is_valid = (state == "valid")
    if log_fn:
        lvl = "success" if is_valid else ("warn" if state == "missing_token" else "error")
        log_fn(f"[Fotello][Auth] {msg}", lvl)
    return is_valid


def get_status() -> dict[str, Any]:
    """Trả về trạng thái kết nối chi tiết của Fotello."""
    state, msg, tokens = check_auth_session()
    email = ""
    if tokens:
        email = _decode_jwt_payload(tokens.get("id_token", "")).get("email", "")
    return {
        "connected": (state == "valid"),
        "state": state,
        "service": "fotello",
        "email": email,
        "message": msg,
        "tokens_path": str(get_fotello_tokens_file()),
    }
