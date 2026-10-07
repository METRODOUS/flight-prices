"""Φόρτωση ρυθμίσεων και κοινές σταθερές."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
LOCAL_TZ = ZoneInfo("Europe/Athens")  # οι ώρες πτήσεων του Skyscanner είναι τοπικές


@dataclass
class Config:
    routes: list[tuple[str, str]]
    days_ahead: int = 10
    nonstop_only: bool = True
    adults: int = 1
    cabin_class: str = "economy"
    market: str = "GR"
    currency: str = "EUR"
    locale: str = "en-GB"
    host: str = "https://www.skyscanner.net"
    actor: str = "memo23/skyscanner-scraper"
    max_charge_usd_per_run: float = 0.60
    timeout_minutes: int = 15
    drop_threshold_eur: float = 3.0
    drop_threshold_pct: float = 0.03
    min_pairs_for_ml: int = 1500
    min_days_for_ml: int = 7
    keep_departed_days: int = 30
    ai_enabled: bool = True
    ai_model: str = "claude-sonnet-5-5"
    ai_max_tokens: int = 12000
    data_dir: Path = field(default_factory=lambda: ROOT / "data")
    out_dir: Path = field(default_factory=lambda: ROOT / "docs" / "data")


def load_config(path: Path | None = None) -> Config:
    path = path or ROOT / "config.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sky = raw.get("skyscanner", {})
    apify = raw.get("apify", {})
    model = raw.get("model", {})
    ai = raw.get("ai", {})
    cfg = Config(
        routes=[(r["origin"].upper(), r["destination"].upper()) for r in raw.get("routes", [])],
        days_ahead=int(raw.get("days_ahead", 10)),
        nonstop_only=bool(raw.get("nonstop_only", True)),
        adults=int(raw.get("adults", 1)),
        cabin_class=raw.get("cabin_class", "economy"),
        market=sky.get("market", "GR"),
        currency=sky.get("currency", "EUR"),
        locale=sky.get("locale", "en-GB"),
        host=sky.get("host", "https://www.skyscanner.net").rstrip("/"),
        actor=apify.get("actor", "memo23/skyscanner-scraper"),
        max_charge_usd_per_run=float(apify.get("max_charge_usd_per_run", 0.60)),
        timeout_minutes=int(apify.get("timeout_minutes", 15)),
        drop_threshold_eur=float(model.get("drop_threshold_eur", 3)),
        drop_threshold_pct=float(model.get("drop_threshold_pct", 0.03)),
        min_pairs_for_ml=int(model.get("min_pairs_for_ml", 1500)),
        min_days_for_ml=int(model.get("min_days_for_ml", 7)),
        keep_departed_days=int(model.get("keep_departed_days", 30)),
        ai_enabled=bool(ai.get("enabled", True)),
        ai_model=str(ai.get("model", "claude-sonnet-5-5")),
        ai_max_tokens=int(ai.get("max_output_tokens", 12000)),
    )
    # Επιτρέπει διαφορετικούς φακέλους (π.χ. για demo/tests)
    if os.environ.get("TRACKER_DATA_DIR"):
        cfg.data_dir = Path(os.environ["TRACKER_DATA_DIR"])
    if os.environ.get("TRACKER_OUT_DIR"):
        cfg.out_dir = Path(os.environ["TRACKER_OUT_DIR"])
    return cfg
