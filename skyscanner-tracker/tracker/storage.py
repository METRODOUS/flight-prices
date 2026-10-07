"""Αποθήκευση ιστορικού σε CSV ανά μήνα (μικρά αρχεία, φιλικά για git)."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd

from .normalize import OBS_COLUMNS


def obs_dir(data_dir: Path) -> Path:
    d = data_dir / "observations"
    d.mkdir(parents=True, exist_ok=True)
    return d


def append_observations(data_dir: Path, rows: list[dict]) -> int:
    if not rows:
        return 0
    by_month: dict[str, list[dict]] = {}
    for r in rows:
        by_month.setdefault(r["observed_at"][:7], []).append(r)
    for month, rs in by_month.items():
        path = obs_dir(data_dir) / f"{month}.csv"
        new = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=OBS_COLUMNS)
            if new:
                w.writeheader()
            w.writerows(rs)
    return len(rows)


def load_observations(data_dir: Path) -> pd.DataFrame:
    files = sorted(obs_dir(data_dir).glob("*.csv"))
    if not files:
        return pd.DataFrame(columns=OBS_COLUMNS)
    df = pd.concat([pd.read_csv(f, dtype={"flight_no": str, "carrier_code": str}) for f in files],
                   ignore_index=True)
    df["carrier_code"] = df["carrier_code"].fillna("")
    df["flight_no"] = df["flight_no"].fillna("")
    df["arr_local"] = df["arr_local"].fillna("")
    # ίδια πτήση, ίδιος κύκλος -> κράτα τη φθηνότερη (αν ξανάτρεξε ο κύκλος)
    df = (df.sort_values("price")
            .drop_duplicates(["flight_id", "observed_at"], keep="first")
            .sort_values(["flight_id", "observed_at"])
            .reset_index(drop=True))
    return df


def log_run(data_dir: Path, entry: dict) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "runs.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_runs(data_dir: Path, last: int = 60) -> list[dict]:
    path = data_dir / "runs.jsonl"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(x) for x in lines[-last:] if x.strip()]
