from __future__ import annotations

import base64
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

from .client import LogFn, json_request, noop_log, print_system_exception
from .constants import FIREBASE_AUTH_URL, FLD_SV


FOTELLO_TOKEN_FILE = Path.home() / ".fotello_tokens_autohdr.json"
FOTELLO_STATE: dict[str, Any] = {
    "refresh_token": "",
    "id_token": "",
    "access_token": "",
    "team_id": "",
    "connected": False,
}

TEAM_ID_CLAIM_KEYS = ("teamId", "team_id", "team", "defaultTeamId", "teamID")
TEAM_ID_USER_DOC_PATHS = (
    "users/{uid}",
    "users_public/{uid}",
    "user_profiles/{uid}",
    "profiles/{uid}",
    "memberships/{uid}",
    "team_members/{uid}",
)


def save_fotello_tokens() -> None:
    data = {
        "refresh_token": FOTELLO_STATE.get("refresh_token", ""),
        "id_token": FOTELLO_STATE.get("id_token", ""),
        "access_token": FOTELLO_STATE.get("access_token", ""),
        "team_id": FOTELLO_STATE.get("team_id", ""),
        "connected": FOTELLO_STATE.get("connected", False),
    }
    FOTELLO_TOKEN_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_fotello_tokens() -> dict[str, Any]:
    if not FOTELLO_TOKEN_FILE.exists():
        return {}
    try:
        data = json.loads(FOTELLO_TOKEN_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        print_system_exception(f"auth.load_fotello_tokens: {FOTELLO_TOKEN_FILE}", exc)
        return {}
    if isinstance(data, dict):
        FOTELLO_STATE.update({k: data.get(k, v) for k, v in FOTELLO_STATE.items()})
    return data if isinstance(data, dict) else {}


def refresh_firebase_token(refresh_token: str) -> dict[str, str]:
    body = f"grant_type=refresh_token&refresh_token={refresh_token}".encode()
    req = urllib.request.Request(
        FIREBASE_AUTH_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    data = json_request(req, 15)
    return {
        "id_token": data["id_token"],
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", refresh_token),
    }


def decode_jwt_payload(id_token: str) -> dict[str, Any]:
    try:
        payload_b64 = id_token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64.encode()))
    except Exception as exc:
        print_system_exception("auth.decode_jwt_payload", exc)
        return {}


def detect_team_id(id_token: str, access_token: str) -> str:
    from .firestore import firestore_get

    payload = decode_jwt_payload(id_token)
    for key in TEAM_ID_CLAIM_KEYS:
        val = payload.get(key)
        if isinstance(val, str) and len(val) >= 16:
            return val.strip()
    teams_dict = payload.get("teams")
    if isinstance(teams_dict, dict) and teams_dict:
        for team_id in teams_dict:
            if len(team_id) >= 16:
                return str(team_id).strip()
    uid = str(payload.get("user_id") or payload.get("sub") or payload.get("uid") or "").strip()
    if uid:
        for template in TEAM_ID_USER_DOC_PATHS:
            try:
                doc = firestore_get(template.format(uid=uid), access_token)
            except Exception as exc:
                print_system_exception(f"auth.detect_team_id firestore_get template={template}", exc)
                continue
            fields = doc.get("fields", {})
            for key in TEAM_ID_CLAIM_KEYS:
                val = fields.get(key, {}).get(FLD_SV)
                if isinstance(val, str) and len(val) >= 16:
                    return val.strip()
    raise RuntimeError("Không tìm thấy team_id")


def fotello_get_tokens() -> dict[str, str]:
    if not FOTELLO_STATE.get("refresh_token"):
        load_fotello_tokens()
    if not FOTELLO_STATE.get("refresh_token"):
        raise RuntimeError('Chưa kết nối Fotello. Bấm "Kết nối Fotello" trước.')
    tokens = refresh_firebase_token(FOTELLO_STATE["refresh_token"])
    FOTELLO_STATE["id_token"] = tokens["id_token"]
    FOTELLO_STATE["access_token"] = tokens["access_token"]
    FOTELLO_STATE["refresh_token"] = tokens["refresh_token"]
    FOTELLO_STATE["connected"] = True
    if not FOTELLO_STATE.get("team_id"):
        try:
            FOTELLO_STATE["team_id"] = detect_team_id(tokens["id_token"], tokens["access_token"])
        except Exception as exc:
            print_system_exception("auth.fotello_get_tokens detect_team_id", exc)
            pass
    save_fotello_tokens()
    return tokens


def fotello_reconnect_saved(log: LogFn = None) -> bool:
    log = log or noop_log
    load_fotello_tokens()
    if not FOTELLO_STATE.get("refresh_token"):
        return False
    try:
        fotello_get_tokens()
        FOTELLO_STATE["connected"] = True
        save_fotello_tokens()
        log("✔ Fotello reconnect OK", "success")
        return True
    except Exception as exc:
        print_system_exception("auth.fotello_reconnect_saved", exc)
        FOTELLO_STATE["connected"] = False
        log(f"Fotello reconnect lỗi: {exc}", "error")
        return False


def fotello_is_connected() -> bool:
    return bool(FOTELLO_STATE.get("connected", False))


def fotello_get_status() -> dict[str, Any]:
    if not FOTELLO_STATE.get("refresh_token"):
        load_fotello_tokens()
    return {
        "connected": FOTELLO_STATE.get("connected", False),
        "team_id": str(FOTELLO_STATE.get("team_id", ""))[:12],
        "has_saved_token": bool(FOTELLO_STATE.get("refresh_token")),
    }


load_fotello_tokens()
