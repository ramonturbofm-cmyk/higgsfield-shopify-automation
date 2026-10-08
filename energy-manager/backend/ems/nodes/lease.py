"""Control lease with fencing epoch, held by exactly one controller per device-owner node.

The node that owns devices is the authority: it grants the lease to one controller
at a time (TTL, renewed by heartbeats). Every hand-over increments the epoch; commands
must carry the current holder id and epoch, so a controller that lost the lease (network
partition, second controller) can never drive the devices again with a stale epoch.
When the lease expires the owner node releases all remotely controlled devices.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class LeaseState:
    holder: str | None
    epoch: int
    expires_at: float

    def to_dict(self, now: float) -> dict:
        return {"holder": self.holder, "epoch": self.epoch, "expires_in_s": max(0.0, round(self.expires_at - now, 1))}


class LeaseManager:
    def __init__(self, ttl_s: float = 30.0, *, load_epoch: Callable[[], int] | None = None,
                 save_epoch: Callable[[int], None] | None = None, clock: Callable[[], float] = time.time) -> None:
        self.ttl = ttl_s
        self.clock = clock
        self._save = save_epoch
        self._lock = threading.Lock()
        self.state = LeaseState(None, int(load_epoch() if load_epoch else 0), 0.0)

    def acquire(self, node_id: str) -> tuple[bool, LeaseState]:
        """Grant or renew. Another holder with a valid lease keeps it (no take-over)."""
        with self._lock:
            now = self.clock()
            st = self.state
            if st.holder not in (None, node_id) and st.expires_at > now:
                return False, LeaseState(st.holder, st.epoch, st.expires_at)
            if st.holder != node_id:
                st.epoch += 1
                st.holder = node_id
                if self._save:
                    self._save(st.epoch)
            st.expires_at = now + self.ttl
            return True, LeaseState(st.holder, st.epoch, st.expires_at)

    def check(self, node_id: str, epoch: int) -> str | None:
        """None if ``node_id`` currently holds the lease with ``epoch``; otherwise the reason."""
        with self._lock:
            st, now = self.state, self.clock()
            if st.holder != node_id:
                return "deze controller heeft geen regelrecht (lease) voor dit apparaat"
            if epoch != st.epoch:
                return "verouderd regelrecht (epoch) — opdracht genegeerd"
            if st.expires_at <= now:
                return "regelrecht verlopen"
            return None

    def release(self, node_id: str) -> None:
        with self._lock:
            if self.state.holder == node_id:
                self.state.expires_at = 0.0

    def expired_holder(self) -> str | None:
        """Holder whose lease ran out (once): the caller releases the devices it controlled."""
        with self._lock:
            st = self.state
            if st.holder is not None and st.expires_at <= self.clock():
                holder, st.holder = st.holder, None
                return holder
            return None
