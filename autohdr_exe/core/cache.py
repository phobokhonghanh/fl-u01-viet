"""
Local JSON cache for persisting user settings between sessions.
Stored in the app data directory so it persists across runs.
"""

import json
import os
from core.utils import get_app_data_dir


CACHE_FILE = os.path.join(get_app_data_dir(), "cache.json")


class AppCache:
    def __init__(self):
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

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self._save()

    def delete(self, key):
        if key in self.data:
            del self.data[key]
            self._save()


cache = AppCache()
