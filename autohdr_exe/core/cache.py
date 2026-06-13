"""
Local JSON cache for persisting user settings between sessions.
Stored in the app data directory so it persists across runs.
"""

import base64
import json
import os
import itertools
import threading
from core.utils import get_app_data_dir


CACHE_FILE = os.path.join(get_app_data_dir(), "cache.json")
LICENSE_CACHE_KEYS = {"active_key", "license_last_check", "license_machine_id"}
LICENSE_SECRET = "admin"


class AppCache:
    def __init__(self):
        self._lock = threading.RLock()
        self.data = self._load()

    def _load(self):
        if not os.path.exists(CACHE_FILE):
            return {}
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self):
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def _encode_license_value(self, value):
        raw = str(value).encode("utf-8")
        secret = LICENSE_SECRET.encode("utf-8")
        encoded = bytes(b ^ s for b, s in zip(raw, itertools.cycle(secret)))
        return base64.urlsafe_b64encode(encoded).decode("ascii")

    def _decode_license_value(self, value):
        if not isinstance(value, str):
            raise ValueError("Encoded license value must be a string")

        payload = base64.urlsafe_b64decode(value.encode("ascii"))
        secret = LICENSE_SECRET.encode("utf-8")
        decoded = bytes(b ^ s for b, s in zip(payload, itertools.cycle(secret)))
        return decoded.decode("utf-8")

    def get(self, key, default=None):
        with self._lock:
            value = self.data.get(key, default)
            if key not in LICENSE_CACHE_KEYS or value == default:
                return value

            try:
                decoded = self._decode_license_value(value)
                if key == "license_last_check":
                    return float(decoded)
                return decoded
            except Exception:
                return default

    def set(self, key, value):
        with self._lock:
            if key in LICENSE_CACHE_KEYS:
                value = self._encode_license_value(value)
            self.data[key] = value
            self._save()

    def delete(self, key):
        with self._lock:
            if key in self.data:
                del self.data[key]
                self._save()


cache = AppCache()
