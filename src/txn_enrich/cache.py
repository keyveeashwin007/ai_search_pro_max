"""SQLite cache of merchant enrichments keyed by normalized signature."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional


class EnrichmentCache:
    def __init__(self, path: str | Path = "cache.db", ttl_days: int = 30):
        self.path = str(path)
        self.ttl_seconds = int(ttl_days * 86400)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS enrichment ("
            " key TEXT PRIMARY KEY, value TEXT NOT NULL, created_at REAL NOT NULL)"
        )
        self._conn.commit()

    def get(self, key: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value, created_at FROM enrichment WHERE key = ?", (key,)
            ).fetchone()
        if not row:
            return None
        value, created_at = row
        if self.ttl_seconds and time.time() - created_at > self.ttl_seconds:
            self.delete(key)
            return None
        return json.loads(value)

    def set(self, key: str, value: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO enrichment (key, value, created_at) VALUES (?, ?, ?)",
                (key, json.dumps(value), time.time()),
            )
            self._conn.commit()

    def delete(self, key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM enrichment WHERE key = ?", (key,))
            self._conn.commit()

    def clear(self) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM enrichment")
            self._conn.commit()
            return cur.rowcount

    def __len__(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM enrichment").fetchone()[0]
