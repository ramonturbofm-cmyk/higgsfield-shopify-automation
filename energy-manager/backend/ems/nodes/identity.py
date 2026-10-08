"""Node identity (persistent UUID), platform detection and roles."""

from __future__ import annotations

import json
import platform as _platform
import socket
import sys
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ems import __version__

ROLES = ("PRIMARY_CONTROLLER", "OPTIMIZER", "DATABASE", "DEVICE_GATEWAY", "PRICE_SERVICE", "FORECAST_SERVICE",
         "USER_INTERFACE")
ROLE_PRESETS = {
    "all_in_one": ROLES,
    "controller": ROLES,
    "gateway": ("DEVICE_GATEWAY", "USER_INTERFACE"),
}
ROLE_LABELS_NL = {
    "PRIMARY_CONTROLLER": "Primaire regelaar", "OPTIMIZER": "Optimizer", "DATABASE": "Database",
    "DEVICE_GATEWAY": "Apparaatgateway", "PRICE_SERVICE": "Prijzen", "FORECAST_SERVICE": "Prognoses",
    "USER_INTERFACE": "Bediening",
}


def detect_platform() -> str:
    if sys.platform.startswith("win"):
        return "WINDOWS"
    if sys.platform.startswith("linux"):
        try:
            model = Path("/proc/device-tree/model").read_bytes().decode("utf-8", "ignore")
        except OSError:
            model = ""
        return "RASPBERRY_PI" if "raspberry pi" in model.lower() else "LINUX"
    return "OTHER"


@dataclass
class NodeIdentity:
    node_id: str
    name: str
    hostname: str
    platform: str
    os: str
    version: str = __version__
    roles: list[str] = field(default_factory=list)

    @property
    def is_controller(self) -> bool:
        return "PRIMARY_CONTROLLER" in self.roles

    def to_dict(self) -> dict:
        return asdict(self)


def load_identity(data_dir: Path, name: str = "", preset: str = "all_in_one") -> NodeIdentity:
    """The node id is created once and kept in ``node.json`` (survives updates and restores)."""
    path = Path(data_dir) / "node.json"
    stored: dict = {}
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    node_id = stored.get("node_id")
    try:
        uuid.UUID(str(node_id))
    except ValueError:
        node_id = str(uuid.uuid4())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"node_id": node_id}), encoding="utf-8")
    host = socket.gethostname()
    return NodeIdentity(node_id=node_id, name=name or host, hostname=host, platform=detect_platform(),
                        os=f"{_platform.system()} {_platform.release()}".strip(),
                        roles=list(ROLE_PRESETS.get(preset, ROLES)))
