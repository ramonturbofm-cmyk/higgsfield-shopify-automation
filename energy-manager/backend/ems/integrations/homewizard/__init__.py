"""HomeWizard P1 Meter (HWE-P1) — local API v1 (HTTP) and v2 (HTTPS + token + WebSocket).

Implemented from the official HomeWizard Local API documentation
(https://api-documentation.homewizard.com, source repository
github.com/homewizard/api-documentation, commit 724362f, 2026-06-09).
``homewizard-ca-cert.pem`` is HomeWizard's published "Appliance Access CA" certificate
from that repository (static/homewizard-ca-cert.pem), used to validate device TLS.

Licence note from HomeWizard: the API is licensed for personal, non-commercial use.
"""

from ems.integrations.homewizard.driver import HomeWizardP1Driver  # noqa: F401
