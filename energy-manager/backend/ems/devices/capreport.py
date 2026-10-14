"""Capability report: per function of the device type what is really possible for *this* device.

SUPPORTED_READ          value is read (and, after a test, actually received)
SUPPORTED_WRITE         the EMS may control it now (commissioning level limited/full)
REQUIRES_CONFIGURATION  possible with this driver, but a mapping/profile/setting is missing
REQUIRES_COMMISSIONING  the driver can do it; commissioning (shadow -> limited/full) not done yet
NOT_SUPPORTED           this model/driver cannot do it
UNVERIFIED              described by a community source or untested: shown, never written
"""

from __future__ import annotations

from ems.core.models import ACTION_CAPABILITY, CAPABILITY_LABELS_NL, CAPABILITY_METRICS
from ems.devices.capabilities import TYPE_SCHEMAS

STATUS_LABELS = {
    "SUPPORTED_READ": "Uitleesbaar",
    "SUPPORTED_WRITE": "Bestuurbaar",
    "REQUIRES_CONFIGURATION": "Configuratie nodig",
    "REQUIRES_COMMISSIONING": "Na inbedrijfstelling",
    "NOT_SUPPORTED": "Niet ondersteund",
    "UNVERIFIED": "Niet geverifieerd",
}


def report(driver, category, *, level: str = "read_only", sample: dict | None = None) -> list[dict]:
    caps = set(driver.device_capabilities()) if driver is not None else set()
    profile = getattr(driver, "profile", None)
    generic = driver is not None and driver.manifest.driver_id.startswith("generic.")
    unverified = {c.capability for c in (profile.writable_controls if profile else []) if not c.writable}
    out = []
    for cap in sorted(TYPE_SCHEMAS[category].capabilities, key=lambda c: (c.value.startswith("control_"), c.value)):
        control = cap.value.startswith("control_")
        detail = ""
        if cap in caps:
            if control:
                status = "SUPPORTED_WRITE" if level in ("limited", "full") else "REQUIRES_COMMISSIONING"
                spec = (
                    next((profile.control(a) for a, c in ACTION_CAPABILITY.items() if c == cap and profile.control(a)), None)
                    if profile
                    else None
                )
                if spec is not None and (spec.min is not None or spec.max is not None):
                    detail = f"bereik {spec.min:g}–{spec.max:g}"
                elif spec is not None and spec.values:
                    detail = "standen: " + ", ".join(spec.values)
                if driver.manifest.driver_id == "sgready.relay":
                    detail = "bedrijfsverzoek via SG Ready (" + ", ".join(driver.modes()) + "); geen vermogensregeling"
            else:
                status = "SUPPORTED_READ"
                if sample is not None:
                    metrics = CAPABILITY_METRICS.get(cap, ())
                    if metrics and not all(str(m) in sample for m in metrics):
                        status, detail = "UNVERIFIED", "ondersteund, maar bij de test geen waarde ontvangen"
        elif cap in unverified:
            status, detail = "UNVERIFIED", "alleen in een community-/ongeverifieerde bron; wordt niet geschreven"
        elif generic:
            status, detail = "REQUIRES_CONFIGURATION", "met registers/velden uit de handleiding (Expert)"
        else:
            status = "NOT_SUPPORTED"
        out.append(
            {
                "capability": cap.value,
                "label": CAPABILITY_LABELS_NL.get(cap, cap.value),
                "kind": "control" if control else "read",
                "status": status,
                "status_label": STATUS_LABELS[status],
                "detail": detail,
            }
        )
    if driver is not None and driver.manifest.driver_id == "sgready.relay":
        out.append(
            {
                "capability": "power_setpoint",
                "label": "elektrisch vermogen direct instellen",
                "kind": "control",
                "status": "NOT_SUPPORTED",
                "status_label": STATUS_LABELS["NOT_SUPPORTED"],
                "detail": "SG Ready geeft alleen een verzoek; de warmtepomp regelt zijn compressor zelf",
            }
        )
    return out
