"""Settings from environment variables (``EMC_*``). See ``.env.example`` and DEPLOYMENT.md."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(v: str | None, default: bool) -> bool:
    return default if v is None or v == "" else v.strip().lower() in ("1", "true", "yes", "on")


class ConfigError(RuntimeError):
    pass


@dataclass
class Settings:
    environment: str = "development"                 # development | production
    database_url: str = "sqlite:///./emcloud.db"
    base_url: str = "http://localhost:8800"         # public URL (links in e-mails)
    secure_cookies: bool = False
    encryption_keys: list[str] = field(default_factory=list)   # Fernet keys; first = current (rotation)
    self_registration: bool = True
    trial_days: int = 30
    trial_plan: str = "PRO"
    mail_mode: str = "outbox"                         # outbox (stored, for development) | smtp
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    require_mfa_platform_admin: bool = True
    session_idle_hours: float = 12
    session_absolute_days: float = 30
    login_max_failures: int = 5
    lockout_minutes: float = 15
    node_heartbeat_s: int = 60
    node_token_grace_s: int = 600
    pairing_code_minutes: int = 15
    audit_retention_days: int = 365
    telemetry_retention_days: int = 30
    trusted_proxies: bool = False                    # take the client IP from X-Forwarded-For

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env
        keys = [k.strip() for k in (e.get("EMC_ENCRYPTION_KEYS") or "").split(",") if k.strip()]
        s = cls(
            environment=e.get("EMC_ENV", "development"),
            database_url=e.get("EMC_DATABASE_URL", cls.database_url),
            base_url=e.get("EMC_BASE_URL", cls.base_url).rstrip("/"),
            secure_cookies=_bool(e.get("EMC_SECURE_COOKIES"), e.get("EMC_ENV") == "production"),
            encryption_keys=keys,
            self_registration=_bool(e.get("EMC_SELF_REGISTRATION"), True),
            trial_days=int(e.get("EMC_TRIAL_DAYS", "30")),
            trial_plan=e.get("EMC_TRIAL_PLAN", "PRO"),
            mail_mode=e.get("EMC_MAIL_MODE", "outbox"),
            smtp_host=e.get("EMC_SMTP_HOST", ""),
            smtp_port=int(e.get("EMC_SMTP_PORT", "587")),
            smtp_user=e.get("EMC_SMTP_USER", ""),
            smtp_password=e.get("EMC_SMTP_PASSWORD", ""),
            smtp_from=e.get("EMC_SMTP_FROM", ""),
            smtp_starttls=_bool(e.get("EMC_SMTP_STARTTLS"), True),
            trusted_proxies=_bool(e.get("EMC_TRUSTED_PROXIES"), False),
        )
        s.check()
        return s

    def check(self) -> None:
        """Refuse an unsafe production start instead of silently running insecurely."""
        if self.environment != "production":
            return
        problems = []
        if not self.encryption_keys:
            problems.append("EMC_ENCRYPTION_KEYS ontbreekt")
        if not self.base_url.startswith("https://"):
            problems.append("EMC_BASE_URL moet https zijn")
        if not self.secure_cookies:
            problems.append("EMC_SECURE_COOKIES moet aan staan")
        if self.mail_mode != "smtp" or not self.smtp_host or not self.smtp_from:
            problems.append("e-mail (SMTP) is niet ingesteld")
        if self.database_url.startswith("sqlite"):
            problems.append("gebruik PostgreSQL in productie (EMC_DATABASE_URL)")
        if problems:
            raise ConfigError("productie-instellingen onvolledig: " + "; ".join(problems))
