"""In-memory ring buffer of recent log records for the system log screen."""

from __future__ import annotations

import logging
from collections import deque
from datetime import UTC, datetime

_STD = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}


class RingBufferHandler(logging.Handler):
    def __init__(self, capacity: int = 2000) -> None:
        super().__init__(logging.INFO)
        self.records: deque[dict] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            extra = {k: v for k, v in vars(record).items() if k not in _STD and not k.startswith("_")}
            self.records.append({
                "ts": datetime.fromtimestamp(record.created, UTC).isoformat(), "level": record.levelname,
                "logger": record.name, "message": record.getMessage(),
                "extra": {k: (v if isinstance(v, (int, float, str, bool, type(None))) else str(v))
                          for k, v in extra.items()},
            })
        except Exception:  # pragma: no cover
            self.handleError(record)

    def tail(self, limit: int = 200, level: str | None = None) -> list[dict]:
        rows = list(self.records)
        if level:
            min_no = logging.getLevelName(level.upper())
            rows = [r for r in rows if logging.getLevelName(r["level"]) >= min_no]
        return rows[-limit:][::-1]
