from __future__ import annotations

import math
import os
import queue
import sqlite3
import threading
import time
from pathlib import Path


class UserAdapter:
    """Non-blocking user preference store backed by sqlite3."""

    def __init__(self, db_path: Path | None = None) -> None:
        data_home = os.environ.get("XDG_DATA_HOME")
        if not data_home:
            data_home = str(Path.home() / ".local" / "share")

        self.db_path = (Path(db_path) if db_path else Path(data_home) / "arabizi_ibus" / "user_preferences.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS user_weights (word TEXT PRIMARY KEY, count INTEGER NOT NULL, updated INTEGER NOT NULL)"
        )
        self._conn.commit()

        self._lock = threading.Lock()
        self._weights: dict[str, int] = {}
        self._load_weights()

        self._queue: queue.Queue[tuple[str, int] | None] = queue.Queue()
        self._worker = threading.Thread(target=self._writer_loop, name="arabizi-user-adapter", daemon=True)
        self._worker.start()

    def _load_weights(self) -> None:
        rows = self._conn.execute("SELECT word, count FROM user_weights").fetchall()
        with self._lock:
            for word, count in rows:
                self._weights[str(word)] = int(count)

    def increment_word(self, word: str) -> None:
        token = word.strip()
        if not token:
            return

        # Bound input size to avoid pathological payloads during stress testing.
        token = token[:128]
        with self._lock:
            self._weights[token] = self._weights.get(token, 0) + 1
        self._queue.put((token, int(time.time())))

    def get_weight(self, word: str) -> float:
        token = word.strip()
        if not token:
            return 0.0
        with self._lock:
            count = self._weights.get(token, 0)
        return math.log1p(count)

    def _writer_loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return

            token, timestamp = item
            self._conn.execute(
                """
                INSERT INTO user_weights(word, count, updated)
                VALUES (?, 1, ?)
                ON CONFLICT(word)
                DO UPDATE SET count = count + 1, updated = excluded.updated
                """,
                (token, timestamp),
            )
            self._conn.commit()

    def close(self) -> None:
        self._queue.put(None)
        self._worker.join(timeout=0.5)
        self._conn.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
