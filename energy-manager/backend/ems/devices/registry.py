"""Driver plugin registry.

Drivers register themselves with ``@register_driver``. ``discover()``
imports every module below ``ems.integrations`` and any third-party
package exposing the ``ems.drivers`` entry-point group, so a new
integration is added by dropping in a package — no core changes.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from importlib.metadata import entry_points

from ems.core.models import DeviceCategory
from ems.devices.base import DeviceDriver

log = logging.getLogger(__name__)


class DriverRegistry:
    def __init__(self) -> None:
        self._drivers: dict[str, type[DeviceDriver]] = {}
        self._discovered = False

    def register(self, cls: type[DeviceDriver]) -> type[DeviceDriver]:
        manifest = getattr(cls, "manifest", None)
        if manifest is None:
            raise TypeError(f"{cls.__name__} has no manifest")
        existing = self._drivers.get(manifest.driver_id)
        if existing is not None and existing is not cls:
            raise ValueError(f"driver id {manifest.driver_id!r} already registered by {existing.__name__}")
        self._drivers[manifest.driver_id] = cls
        return cls

    def get(self, driver_id: str) -> type[DeviceDriver]:
        self.discover()
        try:
            return self._drivers[driver_id]
        except KeyError:
            raise KeyError(f"onbekende driver {driver_id!r}; beschikbaar: {sorted(self._drivers)}") from None

    def list(self, category: DeviceCategory | None = None) -> list[type[DeviceDriver]]:
        self.discover()
        drivers = sorted(self._drivers.values(), key=lambda c: c.manifest.driver_id)
        if category is not None:
            drivers = [d for d in drivers if category in d.manifest.categories]
        return drivers

    def discover(self) -> None:
        if self._discovered:
            return
        self._discovered = True
        import ems.integrations as pkg

        for mod in pkgutil.walk_packages(pkg.__path__, prefix=f"{pkg.__name__}."):
            try:
                importlib.import_module(mod.name)
            except Exception:
                log.exception("failed to import integration module", extra={"module": mod.name})
        try:  # devices owned by other Energy Manager nodes
            importlib.import_module("ems.nodes.remote")
        except Exception:
            log.exception("failed to load node.remote driver")
        for ep in entry_points(group="ems.drivers"):
            try:
                ep.load()
            except Exception:
                log.exception("failed to load driver entry point", extra={"entry_point": ep.name})


registry = DriverRegistry()


def register_driver(cls: type[DeviceDriver]) -> type[DeviceDriver]:
    return registry.register(cls)
