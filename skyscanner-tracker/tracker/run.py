"""Σημείο εισόδου.

  python -m tracker.run            # συλλογή + μοντέλο + dashboard data (αυτό τρέχει το GitHub Action)
  python -m tracker.run --no-collect   # μόνο μοντέλο + export (π.χ. τοπικά)
  python -m tracker.run --demo     # φτιάχνει docs/data/demo.json με προσομοιωμένα δεδομένα
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

from . import ai_analyst, export, model, storage
from .config import LOCAL_TZ, load_config
from .normalize import normalize_batch


def cycle_timestamp(now: datetime) -> str:
    # στρογγυλοποίηση στην ώρα: όλες οι τιμές ενός κύκλου μοιράζονται το ίδιο timestamp
    return now.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def collect(cfg) -> dict:
    from .sources import apify_skyscanner as src

    now = datetime.now(timezone.utc)
    ts = cycle_timestamp(now)
    entry = {"ts": ts, "status": "error", "searches": 0, "flights": 0}
    try:
        today_local = now.astimezone(LOCAL_TZ).date()
        items, meta = src.fetch(cfg, os.environ.get("APIFY_TOKEN", ""), today_local)
        rows = normalize_batch(items, ts, "skyscanner", cfg.currency, cfg.nonstop_only,
                               {f"{o}-{t}" for o, t in cfg.routes})
        storage.append_observations(cfg.data_dir, rows)
        entry.update(status="ok" if rows else "empty", searches=meta["searches"],
                     raw_items=meta["raw_items"], flights=len(rows), cost_usd=meta.get("cost_usd"))
        print(f"[collect] {len(rows)} πτήσεις αποθηκεύτηκαν ({ts})")
    except Exception as e:  # δεν σταματάμε: το dashboard ενημερώνεται και δείχνει το σφάλμα
        entry["error"] = f"{type(e).__name__}: {e}"[:400]
        print(f"[collect] ΣΦΑΛΜΑ: {entry['error']}", file=sys.stderr)
        traceback.print_exc()
    storage.log_run(cfg.data_dir, entry)
    return entry


def build_outputs(cfg, out_name: str = "data.json", demo: bool = False) -> dict:
    df = storage.load_observations(cfg.data_dir)
    d = model.prepare(df)
    tr = model.train(d, cfg)
    fc = model.forecast(d, tr, cfg)
    payload = export.build(d, tr, fc, storage.load_runs(cfg.data_dir), cfg, demo=demo)
    today = datetime.now(timezone.utc).astimezone(LOCAL_TZ).date().isoformat()
    payload["ai"] = ai_analyst.analyse(payload, cfg, cfg.data_dir, today)
    export.write(payload, cfg.out_dir / out_name)
    print(f"[model] μέθοδος={tr.method} drop={tr.drop_method} pairs={tr.n_pairs} "
          f"backtest={tr.backtest} -> {cfg.out_dir / out_name}")
    return payload


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-collect", action="store_true")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--demo-days", type=int, default=24)
    args = ap.parse_args(argv)

    cfg = load_config()
    if args.demo:
        from .simulate import write_demo_history
        with tempfile.TemporaryDirectory() as tmp:
            cfg.data_dir = Path(tmp)
            write_demo_history(cfg, days=args.demo_days)
            build_outputs(cfg, "demo.json", demo=True)
        return
    if not args.no_collect:
        collect(cfg)
    build_outputs(cfg)


if __name__ == "__main__":
    main()
