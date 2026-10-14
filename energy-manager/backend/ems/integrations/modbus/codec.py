"""Register encoding/decoding shared by Modbus TCP and RTU.

Data types (register count): uint16/int16 (1), uint32/int32/float32 (2), uint64/int64/float64 (4),
string (n registers, 2 ASCII bytes each), bitfield16/32, enum16. ``byte_order`` swaps the two bytes
inside each register, ``word_order`` the order of the registers of a multi-register value
("big" = most significant register first, the Modbus convention). Scale: value = raw * 10**sf * scale
+ offset (``sf`` = fixed exponent, e.g. SunSpec scale factors).
"""

from __future__ import annotations

import math
import struct

TYPES: dict[str, tuple[int, str | None]] = {
    "uint16": (1, ">H"), "int16": (1, ">h"), "enum16": (1, ">H"), "bitfield16": (1, ">H"),
    "uint32": (2, ">I"), "int32": (2, ">i"), "float32": (2, ">f"), "bitfield32": (2, ">I"), "acc32": (2, ">I"),
    "uint64": (4, ">Q"), "int64": (4, ">q"), "float64": (4, ">d"), "acc64": (4, ">Q"),
    "string": (0, None),
}
# SunSpec "not implemented" markers per type
NOT_IMPLEMENTED = {"uint16": 0xFFFF, "enum16": 0xFFFF, "bitfield16": 0xFFFF, "int16": -0x8000,
                   "uint32": 0xFFFFFFFF, "bitfield32": 0xFFFFFFFF, "int32": -0x80000000,
                   "acc32": 0, "uint64": 0xFFFFFFFFFFFFFFFF, "int64": -0x8000000000000000}


class CodecError(ValueError):
    pass


def register_count(dtype: str, size: int | None = None) -> int:
    if dtype not in TYPES:
        raise CodecError(f"onbekend datatype {dtype!r}")
    n = TYPES[dtype][0]
    if dtype == "string":
        if not size or size < 1:
            raise CodecError("string heeft een lengte (registers) nodig")
        return int(size)
    return n


def _order(registers: list[int], byte_order: str, word_order: str) -> bytes:
    regs = list(registers)
    if word_order == "little":
        regs.reverse()
    raw = struct.pack(f">{len(regs)}H", *regs)
    if byte_order == "little":
        raw = b"".join(raw[i:i + 2][::-1] for i in range(0, len(raw), 2))
    return raw


def decode(registers: list[int], dtype: str, *, byte_order: str = "big", word_order: str = "big"):
    """Raw registers -> python value (int/float/str). Returns None for SunSpec 'not implemented' and NaN."""
    n = register_count(dtype, len(registers) if dtype == "string" else None)
    if len(registers) < n:
        raise CodecError(f"{dtype}: {n} registers nodig, {len(registers)} ontvangen")
    raw = _order(registers[:n], byte_order, word_order if dtype != "string" else "big")
    if dtype == "string":
        return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()
    value = struct.unpack(TYPES[dtype][1], raw)[0]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def encode(value, dtype: str, *, byte_order: str = "big", word_order: str = "big") -> list[int]:
    if dtype == "string":
        raise CodecError("strings worden niet geschreven")
    n = register_count(dtype)
    fmt = TYPES[dtype][1]
    if fmt.endswith(("f", "d")):
        raw = struct.pack(fmt, float(value))
    else:
        iv = int(round(value))
        lo, hi = _int_range(dtype)
        if not lo <= iv <= hi:
            raise CodecError(f"waarde {iv} past niet in {dtype}")
        raw = struct.pack(fmt, iv)
    if byte_order == "little":
        raw = b"".join(raw[i:i + 2][::-1] for i in range(0, len(raw), 2))
    regs = list(struct.unpack(f">{n}H", raw))
    if word_order == "little":
        regs.reverse()
    return regs


def _int_range(dtype: str) -> tuple[int, int]:
    bits = {1: 16, 2: 32, 4: 64}[TYPES[dtype][0]]
    if dtype.startswith("int"):
        return -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    return 0, (1 << bits) - 1


def scaled(raw, *, sf: int = 0, scale: float = 1.0, offset: float = 0.0):
    if raw is None or isinstance(raw, str):
        return raw
    return raw * (10 ** sf) * scale + offset


def unscaled(value: float, *, sf: int = 0, scale: float = 1.0, offset: float = 0.0) -> float:
    return (float(value) - offset) / scale / (10 ** sf)
