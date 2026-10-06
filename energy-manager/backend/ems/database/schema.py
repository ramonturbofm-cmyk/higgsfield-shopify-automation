"""Table definitions. Timestamps are UTC epoch seconds (float) for portability."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

metadata = MetaData()

schema_version = Table("schema_version", metadata, Column("version", Integer, nullable=False))

users = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True),
    Column("username", String(64), unique=True, nullable=False),
    Column("password_hash", String(256), nullable=False),
    Column("role", String(16), nullable=False),
    Column("created_ts", Float, nullable=False),
    Column("disabled", Boolean, nullable=False, default=False),
)

api_tokens = Table(
    "api_tokens", metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(64), nullable=False),
    Column("token_hash", String(128), unique=True, nullable=False),
    Column("role", String(16), nullable=False),
    Column("created_ts", Float, nullable=False),
    Column("last_used_ts", Float),
)

SAMPLE_FIELDS = ("grid_w", "pv_w", "battery_w", "soc", "hp_w", "ev_w", "house_w", "l1_a", "l2_a", "l3_a",
                 "indoor_c", "outdoor_c", "spot", "import_price", "export_price")

samples = Table(
    "samples", metadata,
    Column("id", Integer, primary_key=True),
    Column("ts", Float, nullable=False),
    Column("site_id", String(64), nullable=False),
    Column("grid_valid", Boolean, nullable=False, default=False),
    *(Column(f, Float) for f in SAMPLE_FIELDS),
    Index("ix_samples_site_ts", "site_id", "ts"),
)

SLOT_FIELDS = ("import_kwh", "export_kwh", "pv_kwh", "battery_charge_kwh", "battery_discharge_kwh", "hp_kwh",
               "ev_kwh", "house_kwh", "soc_end", "indoor_c", "outdoor_c", "spot", "import_price", "export_price",
               "cost_eur", "revenue_eur", "coverage")

samples_15m = Table(
    "samples_15m", metadata,
    Column("site_id", String(64), primary_key=True),
    Column("slot_ts", Float, primary_key=True),
    *(Column(f, Float) for f in SLOT_FIELDS),
)

device_samples = Table(
    "device_samples", metadata,
    Column("id", Integer, primary_key=True),
    Column("ts", Float, nullable=False),
    Column("device_id", String(64), nullable=False),
    Column("status", String(16), nullable=False),
    Column("values", JSON),
    Index("ix_device_samples_dev_ts", "device_id", "ts"),
)

decisions = Table(
    "decisions", metadata,
    Column("id", Integer, primary_key=True),
    Column("ts", Float, nullable=False, index=True),
    Column("site_id", String(64), nullable=False),
    Column("run_id", String(64)),
    Column("device", String(64)),
    Column("action", String(64)),
    Column("outcome", String(32)),
    Column("old_value", JSON),
    Column("new_value", JSON),
    Column("summary", Text),
    Column("reasons", JSON),
    Column("source", String(64)),
    Column("price", Float),
    Column("expected_profit", Float),
    Column("data", JSON),
)

prices = Table(
    "prices", metadata,
    Column("area", String(32), primary_key=True),
    Column("ts", Float, primary_key=True),
    Column("source", String(32), nullable=False),
    Column("spot_eur_kwh", Float, nullable=False),
    Column("resolution_min", Integer, nullable=False),
    Column("fetched_ts", Float, nullable=False),
)

plans = Table(
    "plans", metadata,
    Column("id", Integer, primary_key=True),
    Column("created_ts", Float, nullable=False, index=True),
    Column("site_id", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("trigger", String(64)),
    Column("expected_cost", Float),
    Column("baseline_cost", Float),
    Column("payload", JSON),
)

automations = Table(
    "automations", metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(128), nullable=False),
    Column("enabled", Boolean, nullable=False, default=True),
    Column("definition", JSON, nullable=False),
    Column("created_ts", Float, nullable=False),
    Column("updated_ts", Float, nullable=False),
    Column("last_state", Boolean),
    Column("last_fired_ts", Float),
)

notifications = Table(
    "notifications", metadata,
    Column("id", Integer, primary_key=True),
    Column("ts", Float, nullable=False, index=True),
    Column("level", String(16), nullable=False),
    Column("code", String(64), nullable=False),
    Column("message", Text, nullable=False),
    Column("data", JSON),
    Column("acknowledged", Boolean, nullable=False, default=False),
)

config_versions = Table(
    "config_versions", metadata,
    Column("id", Integer, primary_key=True),
    Column("ts", Float, nullable=False),
    Column("username", String(64)),
    Column("comment", String(256)),
    Column("yaml", Text, nullable=False),
)

jobs = Table(
    "jobs", metadata,
    Column("id", Integer, primary_key=True),
    Column("kind", String(32), nullable=False),
    Column("status", String(16), nullable=False),
    Column("created_ts", Float, nullable=False),
    Column("finished_ts", Float),
    Column("params", JSON),
    Column("result", JSON),
    Column("error", Text),
    Column("progress", Float),
)

kv = Table(
    "kv", metadata,
    Column("key", String(128), primary_key=True),
    Column("value", JSON),
    Column("updated_ts", Float),
)
