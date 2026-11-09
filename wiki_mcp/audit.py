"""Access log (JSONL, append-only) and per-user rate limiting."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("wiki_mcp.access")


class AccessLog:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, **fields: Any) -> None:
        record = {"ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"), "event": event}
        record.update({k: v for k, v in fields.items() if v is not None})
        line = json.dumps(record, ensure_ascii=False)
        with self._lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        logger.info(line)                 # also on the console / `docker logs`


def summarize_args(args: dict[str, Any], max_len: int = 200) -> dict[str, Any]:
    out = {}
    for k, v in args.items():
        if isinstance(v, str) and len(v) > max_len:
            v = v[:max_len] + "…"
        out[k] = v
    return out


class RateLimiter:
    """Sliding one-minute window per key."""

    def __init__(self, per_minute: int, clock=time.monotonic):
        self.per_minute = per_minute
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self.clock()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] >= 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return False
            q.append(now)
            return True
