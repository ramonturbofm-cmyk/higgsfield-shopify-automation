"""DSMR P1 telegram parser.

Telegram:  /XXX5<ident>\r\n\r\n <OBIS lines> !<CRC16>\r\n
CRC (DSMR 4/5): CRC-16 (polynomial 0xA001, reflected, init 0) over everything
from '/' up to and including '!'. DSMR 2.2/3.0 telegrams carry no CRC.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ems.core.models import Metric

_LINE = re.compile(r"^(\d+-\d+:\d+\.\d+\.\d+)((?:\([^)]*\))+)\s*$")
_VALUE = re.compile(r"\(([^)]*)\)")

# OBIS -> (metric, scale). Powers are in kW in the telegram.
OBIS = {
    "1-0:1.7.0": ("power_delivered_kw", 1.0),
    "1-0:2.7.0": ("power_returned_kw", 1.0),
    "1-0:1.8.1": ("import_t1_kwh", 1.0), "1-0:1.8.2": ("import_t2_kwh", 1.0),
    "1-0:2.8.1": ("export_t1_kwh", 1.0), "1-0:2.8.2": ("export_t2_kwh", 1.0),
    "1-0:32.7.0": ("voltage_l1_v", 1.0), "1-0:52.7.0": ("voltage_l2_v", 1.0), "1-0:72.7.0": ("voltage_l3_v", 1.0),
    "1-0:31.7.0": ("current_l1_a", 1.0), "1-0:51.7.0": ("current_l2_a", 1.0), "1-0:71.7.0": ("current_l3_a", 1.0),
    "1-0:21.7.0": ("power_l1_delivered_kw", 1.0), "1-0:41.7.0": ("power_l2_delivered_kw", 1.0),
    "1-0:61.7.0": ("power_l3_delivered_kw", 1.0),
    "1-0:22.7.0": ("power_l1_returned_kw", 1.0), "1-0:42.7.0": ("power_l2_returned_kw", 1.0),
    "1-0:62.7.0": ("power_l3_returned_kw", 1.0),
    "0-0:96.14.0": ("tariff", 1.0),
    "1-3:0.2.8": ("version", None), "0-0:1.0.0": ("timestamp", None), "0-0:96.1.1": ("equipment_id", None),
}


class TelegramError(ValueError):
    pass


def crc16(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


@dataclass
class Telegram:
    header: str
    values: dict[str, float | str] = field(default_factory=dict)
    timestamp: datetime | None = None
    crc_checked: bool = False


def _number(raw: str) -> float:
    return float(raw.split("*")[0])


def parse_timestamp(raw: str) -> datetime | None:
    """YYMMDDhhmmssX — X = S (summer, UTC+2) / W (winter, UTC+1)."""
    m = re.fullmatch(r"(\d{12})([SW])", raw)
    if not m:
        return None
    offset = timedelta(hours=2 if m.group(2) == "S" else 1)
    return datetime.strptime(m.group(1), "%y%m%d%H%M%S").replace(tzinfo=timezone(offset))


def parse_telegram(text: str, require_crc: bool | None = None) -> Telegram:
    start = text.find("/")
    end = text.find("!", start)
    if start < 0 or end < 0:
        raise TelegramError("onvolledig telegram")
    body = text[start:end + 1]
    crc_text = text[end + 1:end + 5]
    crc_checked = False
    if re.fullmatch(r"[0-9A-Fa-f]{4}", crc_text or ""):
        if crc16(body.encode("ascii", "replace")) != int(crc_text, 16):
            raise TelegramError("CRC-fout: telegram beschadigd")
        crc_checked = True
    elif require_crc:
        raise TelegramError("telegram zonder CRC")
    lines = body.splitlines()
    tg = Telegram(header=lines[0].strip(), crc_checked=crc_checked)
    for line in lines[1:]:
        m = _LINE.match(line.strip())
        if not m:
            continue
        obis, groups = m.group(1), _VALUE.findall(m.group(2))
        if obis not in OBIS or not groups:
            continue
        key, scale = OBIS[obis]
        raw = groups[-1]
        if scale is None:
            tg.values[key] = raw
            if key == "timestamp":
                tg.timestamp = parse_timestamp(raw)
        else:
            try:
                tg.values[key] = _number(raw) * scale
            except ValueError:
                continue
    return tg


def to_metrics(tg: Telegram) -> dict[Metric, float]:
    v = tg.values
    m: dict[Metric, float] = {}
    if "power_delivered_kw" in v and "power_returned_kw" in v:
        p = (float(v["power_delivered_kw"]) - float(v["power_returned_kw"])) * 1000
        m[Metric.GRID_POWER_W] = round(p, 1)
        m[Metric.GRID_IMPORT_POWER_W] = round(max(0.0, p), 1)
        m[Metric.GRID_EXPORT_POWER_W] = round(max(0.0, -p), 1)
    for i, (pm, vm, cm) in enumerate(((Metric.GRID_POWER_L1_W, Metric.GRID_VOLTAGE_L1_V, Metric.GRID_CURRENT_L1_A),
                                      (Metric.GRID_POWER_L2_W, Metric.GRID_VOLTAGE_L2_V, Metric.GRID_CURRENT_L2_A),
                                      (Metric.GRID_POWER_L3_W, Metric.GRID_VOLTAGE_L3_V, Metric.GRID_CURRENT_L3_A)),
                                     start=1):
        d, r = v.get(f"power_l{i}_delivered_kw"), v.get(f"power_l{i}_returned_kw")
        phase_p = None
        if d is not None and r is not None:
            phase_p = (float(d) - float(r)) * 1000
            m[pm] = round(phase_p, 1)
        if f"voltage_l{i}_v" in v:
            m[vm] = float(v[f"voltage_l{i}_v"])
        if f"current_l{i}_a" in v:  # DSMR current is unsigned; sign from the phase power
            c = abs(float(v[f"current_l{i}_a"]))
            m[cm] = -c if (phase_p is not None and phase_p < 0) else c
    imp = [v.get("import_t1_kwh"), v.get("import_t2_kwh")]
    exp = [v.get("export_t1_kwh"), v.get("export_t2_kwh")]
    if all(x is not None for x in imp):
        m[Metric.GRID_IMPORT_ENERGY_KWH] = round(sum(float(x) for x in imp), 3)
    if all(x is not None for x in exp):
        m[Metric.GRID_EXPORT_ENERGY_KWH] = round(sum(float(x) for x in exp), 3)
    return m


class TelegramBuffer:
    """Accumulates bytes/lines from a stream and yields complete telegrams."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, chunk: str) -> list[str]:
        self._buf += chunk
        out = []
        while True:
            start = self._buf.find("/")
            if start < 0:
                self._buf = ""
                break
            end = self._buf.find("!", start)
            if end < 0:
                self._buf = self._buf[start:]
                break
            nl = self._buf.find("\n", end)
            if nl < 0:
                self._buf = self._buf[start:]
                break
            out.append(self._buf[start:nl + 1])
            self._buf = self._buf[nl + 1:]
        if len(self._buf) > 20000:
            self._buf = ""
        return out
