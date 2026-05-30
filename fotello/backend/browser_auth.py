from __future__ import annotations

import time
from typing import Any

from .auth import FOTELLO_STATE, detect_team_id, fotello_get_tokens, save_fotello_tokens
from .client import LogFn, noop_log, print_system_exception


def fotello_grab_tokens_from_browser(driver: Any, log: LogFn = None) -> bool:
    log = log or noop_log
    current_url = getattr(driver, "current_url", "") or ""
    if "fotello.co" not in current_url:
        log("Đang mở app.fotello.co...", "info")
        driver.get("https://app.fotello.co")
        time.sleep(3)

    log("Đang tìm Firebase token...", "info")
    script = r"""
        const done = arguments[arguments.length - 1];
        function pick(v) {
          try {
            const data = typeof v === 'string' ? JSON.parse(v) : v;
            const token = data?.stsTokenManager || data?.value?.stsTokenManager;
            if (token?.refreshToken) return {
              refresh_token: token.refreshToken,
              id_token: token.accessToken || '',
              uid: data.uid || data?.value?.uid || '',
              email: data.email || data?.value?.email || ''
            };
          } catch(e) {}
          return null;
        }
        try {
          for (let i = 0; i < localStorage.length; i++) {
            const key = localStorage.key(i);
            if (key && key.startsWith('firebase:authUser')) {
              const found = pick(localStorage.getItem(key));
              if (found) return done(found);
            }
          }
        } catch(e) {}
        try {
          const req = indexedDB.open('firebaseLocalStorageDb');
          req.onsuccess = function(ev) {
            try {
              const db = ev.target.result;
              const tx = db.transaction('firebaseLocalStorage', 'readonly');
              const store = tx.objectStore('firebaseLocalStorage');
              const getAllReq = store.getAll();
              getAllReq.onsuccess = function() {
                for (const item of getAllReq.result || []) {
                  const found = pick(item.value);
                  if (found) return done(found);
                }
                done(null);
              };
              getAllReq.onerror = () => done(null);
            } catch(e) { done(null); }
          };
          req.onerror = () => done(null);
        } catch(e) { done(null); }
    """
    try:
        driver.set_script_timeout(15)
        token_data = driver.execute_async_script(script)
    except Exception as exc:
        print_system_exception("browser_auth.fotello_grab_tokens_from_browser extract token", exc)
        log(f"Token extract error: {exc}", "error")
        return False

    if not isinstance(token_data, dict) or not token_data.get("refresh_token"):
        log("Không tìm thấy Firebase token. Hãy login Fotello trên Chrome.", "warn")
        return False

    FOTELLO_STATE["refresh_token"] = token_data["refresh_token"]
    FOTELLO_STATE["id_token"] = token_data.get("id_token", "")
    FOTELLO_STATE["connected"] = True
    try:
        tokens = fotello_get_tokens()
        FOTELLO_STATE["team_id"] = detect_team_id(tokens["id_token"], tokens["access_token"])
    except Exception as exc:
        print_system_exception("browser_auth.fotello_grab_tokens_from_browser detect_team_id", exc)
        log(f"Team detect warn: {exc}", "warn")
    save_fotello_tokens()
    log("✔ Đã kết nối Fotello", "success")
    return True
