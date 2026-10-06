"""Decision journal: every EMS action with its explanation.

Fields follow the logging spec: timestamp, device, action, old_value,
new_value, reason, price, expected_profit, run_id. Stored as JSONL (one
file per day) and kept in a ring buffer for the API/system log screen.
Phase 2 adds a database sink.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class JournalEntry:
    timestamp: datetime
    site_id: str
    run_id: str
    device: str
    action: str
    outcome: str            # sent | dry_run | skipped | blocked | failed | released
    old_value: Any
    new_value: Any
    summary: str
    reasons: list[str] = field(default_factory=list)
    source: str = "controller"
    price: float | None = None
    expected_profit: float | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return json.dumps(d, default=str, ensure_ascii=False)

    def render_nl(self, tz: tzinfo | None = None) -> str:
        """Human readable entry; pass the site timezone for local times."""
        prefix = "EMS zou" if self.outcome == "dry_run" else "EMS"
        ts = self.timestamp.astimezone(tz) if tz else self.timestamp
        lines = [f"{ts:%Y-%m-%d %H:%M:%S} {prefix}: {self.summary} [{self.outcome}]"]
        if self.reasons:
            lines.append("Reden:")
            lines.extend(f"  - {r}" for r in self.reasons)
        return "\n".join(lines)


class DecisionJournal:
    def __init__(self, directory: Path | None = None, buffer_size: int = 2000) -> None:
        self.directory = directory
        self.recent: deque[JournalEntry] = deque(maxlen=buffer_size)
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)

    def record(self, entry: JournalEntry) -> None:
        self.recent.append(entry)
        if self.directory is not None:
            path = self.directory / f"decisions-{entry.timestamp:%Y-%m-%d}.jsonl"
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(entry.to_json() + "\n")
