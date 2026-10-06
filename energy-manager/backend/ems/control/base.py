"""Controller interface.

A controller turns a SiteSnapshot into Decisions. Phase 1 ships rule-based
controllers; the rolling-horizon optimizer (phase 6+) becomes another
controller that executes the first interval of its plan.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from ems.core.config import EMSConfig
from ems.core.models import Capability, Decision
from ems.core.snapshot import SiteSnapshot


@dataclass
class ControlContext:
    config: EMSConfig
    now: datetime
    capabilities: dict[str, frozenset[Capability]] = field(default_factory=dict)
    run_id: str = ""

    def can(self, device_id: str, capability: Capability) -> bool:
        return capability in self.capabilities.get(device_id, frozenset())


class Controller(ABC):
    name: str = "controller"

    @abstractmethod
    def decide(self, snap: SiteSnapshot, ctx: ControlContext) -> list[Decision]: ...


class NativeController(Controller):
    """Never intervenes: every device runs its own built-in logic.

    This is the "zonder EMS" reference for comparisons and equals the
    behaviour the installation falls back to in fail-safe.
    """

    name = "native"

    def decide(self, snap: SiteSnapshot, ctx: ControlContext) -> list[Decision]:
        return []
