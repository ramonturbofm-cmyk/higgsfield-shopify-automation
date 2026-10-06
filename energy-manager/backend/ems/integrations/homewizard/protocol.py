"""HomeWizard data mapping and TLS helpers (official API documentation)."""

from __future__ import annotations

import ssl
from pathlib import Path
from typing import Any

from ems.core.models import Metric

CA_CERT = Path(__file__).with_name("homewizard-ca-cert.pem")
P1_PRODUCT_TYPE = "HWE-P1"
# Product type used in the certificate hostname differs for legacy reasons (docs: Authorization > HTTPS).
CERT_PRODUCT_TYPE = {"HWE-P1": "p1dongle", "HWE-SKT": "energysocket", "HWE-DSP": "display"}
USER_NAME = "local/energy-manager"
V2_HEADERS = {"X-Api-Version": "2"}


def cert_hostname(serial: str, product_type: str = P1_PRODUCT_TYPE) -> str:
    return f"appliance/{CERT_PRODUCT_TYPE.get(product_type, product_type.lower())}/{serial.lower()}"


def _relax_strict(ctx: ssl.SSLContext) -> ssl.SSLContext:
    # HomeWizard's "Appliance Access CA" is an X.509 v1 certificate without extensions.
    # Python >= 3.13 enables VERIFY_X509_STRICT by default, which rejects such CAs.
    # The chain is still verified against this pinned CA, and the hostname is still checked.
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return ctx


def ssl_context(verify: bool = True, cafile: Path | str = CA_CERT) -> ssl.SSLContext:
    if not verify:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    ctx = ssl.create_default_context(cafile=str(cafile))
    ctx.hostname_checks_common_name = True   # device certificates carry the name in the CN
    return _relax_strict(ctx)


def chain_only_context(cafile: Path | str = CA_CERT) -> ssl.SSLContext:
    """Verify the certificate chain against the HomeWizard CA without a hostname
    (used once to learn the device identity when only an IP address is known)."""
    ctx = ssl.create_default_context(cafile=str(cafile))
    ctx.check_hostname = False
    ctx.hostname_checks_common_name = True
    return _relax_strict(ctx)


def _f(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _sum_tariffs(data: dict, prefix: str, suffix: str = "_kwh") -> float | None:
    vals = [_f(data.get(f"{prefix}_t{i}{suffix}")) for i in range(1, 5)]
    vals = [v for v in vals if v is not None]
    return round(sum(vals), 3) if vals else None


def map_measurement_v2(data: dict) -> dict[Metric, Any]:
    """/api/measurement (v2) — all fields optional; missing stays missing."""
    m: dict[Metric, Any] = {}
    power = _f(data.get("power_w"))
    if power is not None:
        m[Metric.GRID_POWER_W] = power
        m[Metric.GRID_IMPORT_POWER_W] = max(0.0, power)
        m[Metric.GRID_EXPORT_POWER_W] = max(0.0, -power)
    for i, (pm, vm, cm) in enumerate(((Metric.GRID_POWER_L1_W, Metric.GRID_VOLTAGE_L1_V, Metric.GRID_CURRENT_L1_A),
                                      (Metric.GRID_POWER_L2_W, Metric.GRID_VOLTAGE_L2_V, Metric.GRID_CURRENT_L2_A),
                                      (Metric.GRID_POWER_L3_W, Metric.GRID_VOLTAGE_L3_V, Metric.GRID_CURRENT_L3_A)),
                                     start=1):
        if (v := _f(data.get(f"power_l{i}_w"))) is not None:
            m[pm] = v
        if (v := _f(data.get(f"voltage_l{i}_v"))) is not None:
            m[vm] = v
        if (v := _f(data.get(f"current_l{i}_a"))) is not None:   # negative when exporting (docs)
            m[cm] = v
    if Metric.GRID_VOLTAGE_L1_V not in m and (v := _f(data.get("voltage_v"))) is not None:
        m[Metric.GRID_VOLTAGE_L1_V] = v
    imp = _f(data.get("energy_import_kwh"))
    exp = _f(data.get("energy_export_kwh"))
    m_imp = imp if imp is not None else _sum_tariffs(data, "energy_import")
    m_exp = exp if exp is not None else _sum_tariffs(data, "energy_export")
    if m_imp is not None:
        m[Metric.GRID_IMPORT_ENERGY_KWH] = m_imp
    if m_exp is not None:
        m[Metric.GRID_EXPORT_ENERGY_KWH] = m_exp
    return m


def map_data_v1(data: dict) -> dict[Metric, Any]:
    """/api/v1/data (v1). Phase currents in v1 are documented without sign; the sign is
    taken from the phase power so export is negative like everywhere else in the EMS."""
    m: dict[Metric, Any] = {}
    power = _f(data.get("active_power_w"))
    if power is not None:
        m[Metric.GRID_POWER_W] = power
        m[Metric.GRID_IMPORT_POWER_W] = max(0.0, power)
        m[Metric.GRID_EXPORT_POWER_W] = max(0.0, -power)
    for i, (pm, vm, cm) in enumerate(((Metric.GRID_POWER_L1_W, Metric.GRID_VOLTAGE_L1_V, Metric.GRID_CURRENT_L1_A),
                                      (Metric.GRID_POWER_L2_W, Metric.GRID_VOLTAGE_L2_V, Metric.GRID_CURRENT_L2_A),
                                      (Metric.GRID_POWER_L3_W, Metric.GRID_VOLTAGE_L3_V, Metric.GRID_CURRENT_L3_A)),
                                     start=1):
        p = _f(data.get(f"active_power_l{i}_w"))
        if p is not None:
            m[pm] = p
        if (v := _f(data.get(f"active_voltage_l{i}_v"))) is not None:
            m[vm] = v
        c = _f(data.get(f"active_current_l{i}_a"))
        if c is not None:
            m[cm] = -abs(c) if (p is not None and p < 0) else abs(c) if p is not None else c
    imp = _f(data.get("total_power_import_kwh"))
    exp = _f(data.get("total_power_export_kwh"))
    imp = imp if imp is not None else _sum_tariffs(data, "total_power_import")
    exp = exp if exp is not None else _sum_tariffs(data, "total_power_export")
    if imp is not None:
        m[Metric.GRID_IMPORT_ENERGY_KWH] = imp
    if exp is not None:
        m[Metric.GRID_EXPORT_ENERGY_KWH] = exp
    return m


def meter_info(data: dict) -> dict:
    """Non-measurement fields useful for diagnostics."""
    keys = ("protocol_version", "smr_version", "meter_model", "unique_id", "timestamp", "tariff",
            "wifi_ssid", "wifi_strength", "any_power_fail_count", "long_power_fail_count")
    return {k: data[k] for k in keys if k in data}
