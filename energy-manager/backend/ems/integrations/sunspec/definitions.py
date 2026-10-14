"""Load official SunSpec model definitions and compute point offsets inside a model block."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from ems.integrations.modbus.codec import decode

MODELS_DIR = Path(__file__).parent / "models"
SUNS = [0x5375, 0x6E53]                     # "SunS"
BASES = (40000, 0, 50000)
SIZES = {"uint16": 1, "int16": 1, "enum16": 1, "bitfield16": 1, "sunssf": 1, "count": 1, "pad": 1,
         "uint32": 2, "int32": 2, "acc32": 2, "bitfield32": 2, "float32": 2, "ipaddr": 2,
         "uint64": 4, "int64": 4, "acc64": 4, "float64": 4, "ipv6addr": 8, "eui48": 4}


@dataclass(frozen=True)
class PointDef:
    name: str
    type: str
    offset: int          # registers from the start of the model block (ID at 0, L at 1)
    size: int
    sf: str | int | None
    access: str
    symbols: dict[int, str]


@cache
def model(model_id: int) -> dict[str, PointDef] | None:
    path = MODELS_DIR / f"model_{model_id}.json"
    if not path.exists():
        return None
    g = json.loads(path.read_text(encoding="utf-8"))["group"]
    out, off = {}, 0
    for p in g["points"]:
        size = int(p.get("size") or SIZES.get(p["type"], 1))
        if p["type"] == "string":
            size = int(p["size"])
        out[p["name"]] = PointDef(p["name"], p["type"], off, size, p.get("sf"), p.get("access", "R"),
                                  {int(s["value"]): s["name"] for s in p.get("symbols", [])})
        off += size
    return out


def value(block: list[int], pdefs: dict[str, PointDef], name: str):
    """Decoded, scaled value of one point (None when absent or 'not implemented')."""
    p = pdefs.get(name)
    if p is None or p.offset + p.size > len(block):
        return None
    t = {"sunssf": "int16", "count": "uint16", "acc32": "uint32", "acc64": "uint64", "pad": "uint16"}.get(p.type, p.type)
    if t not in ("string",) and t not in SIZES:
        return None
    regs = block[p.offset:p.offset + p.size]
    if t == "string":
        return decode(regs, "string")
    raw = decode(regs, t)
    if raw is None:
        return None
    if (t in ("uint16", "enum16", "bitfield16") and raw == 0xFFFF) or (t == "int16" and raw == -0x8000) \
            or (t in ("uint32", "bitfield32") and raw == 0xFFFFFFFF and p.type != "acc32") \
            or (t == "int32" and raw == -0x80000000):
        return None
    if p.type == "acc32" and raw == 0:
        return None
    if p.symbols and p.type.startswith("enum"):
        return p.symbols.get(raw, str(raw))
    sf = p.sf
    if isinstance(sf, str):
        sf = value(block, pdefs, sf)
        if sf is None:
            return None
    if isinstance(sf, int) and sf:
        return raw * (10 ** sf)
    return raw


def raw_register(pdefs: dict[str, PointDef], name: str) -> PointDef:
    return pdefs[name]
