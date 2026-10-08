"""``node.remote``: a device that is physically owned by another node (device gateway).

To the EMS core and the optimizer this is an ordinary driver; reads and commands
travel to the owner node, which validates every command itself (safety, lease).
"""

from __future__ import annotations

from typing import Any

from ems.core.models import Capability, Command, DeviceCategory, Metric
from ems.devices.base import DeviceDriver, DeviceUnavailableError, DriverError, DriverManifest
from ems.devices.registry import register_driver
from ems.nodes.link import NodeLinkError


@register_driver
class RemoteDeviceDriver(DeviceDriver):
    manifest = DriverManifest(
        driver_id="node.remote",
        display_name="Apparaat op een andere Energy Manager-node",
        vendor="Energy Manager",
        categories=tuple(DeviceCategory),
        capabilities=frozenset(Capability),
        connection_types=("node",),
        documentation="Energy Manager node API (/api/v1/node)",
        notes="Wordt toegevoegd via Systeem → Nodes; de eigenaar-node controleert iedere opdracht zelf.",
    )

    def __init__(self, config, context) -> None:
        super().__init__(config, context)
        self.node_id = str(config.connection.get("node_id", ""))
        self.remote_id = str(config.connection.get("remote_id", ""))

    def _link(self):
        links = getattr(self.context, "nodes", None) or {}
        link = links.get(self.node_id)
        if link is None:
            raise DeviceUnavailableError("eigenaar-node niet gekoppeld")
        return link

    def capabilities(self) -> frozenset[Capability]:
        try:
            info = self._link().devices.get(self.remote_id) or {}
        except DriverError:
            return frozenset()
        return frozenset(Capability(c) for c in info.get("capabilities", []) if c in Capability._value2member_map_)

    @property
    def write_capable(self) -> bool:
        try:
            return bool((self._link().devices.get(self.remote_id) or {}).get("write_capable"))
        except DriverError:
            return False

    async def connect(self) -> None:
        link = self._link()
        if not link.online:
            raise DeviceUnavailableError(f"node {link.name} offline: {link.error or 'geen verbinding'}")
        if self.remote_id not in link.devices:
            try:
                await link.refresh_devices()
            except NodeLinkError as exc:
                raise DeviceUnavailableError(str(exc)) from exc
            if self.remote_id not in link.devices:
                raise DeviceUnavailableError(f"apparaat {self.remote_id} niet (meer) aanwezig op node {link.name}")

    async def read(self) -> dict[Metric, Any]:
        link = self._link()
        try:
            st = await link.state(self.remote_id)
        except NodeLinkError as exc:
            raise DeviceUnavailableError(str(exc)) from exc
        if st.get("status") not in ("online", "degraded"):
            raise DeviceUnavailableError(f"op node {link.name}: {st.get('status')} {st.get('error') or ''}".strip())
        return {Metric(k): v for k, v in (st.get("values") or {}).items() if k in Metric._value2member_map_}

    async def apply(self, command: Command) -> None:
        try:
            await self._link().command(self.remote_id, command)
        except NodeLinkError as exc:
            raise DriverError(str(exc)) from exc

    async def release_control(self) -> None:
        try:
            await self._link().release(self.remote_id)
        except NodeLinkError as exc:
            raise DeviceUnavailableError(str(exc)) from exc
