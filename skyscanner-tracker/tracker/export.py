"""Παραγωγή του data.json που διαβάζει το dashboard."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .model import Trained

METHOD_LABEL = {
    "prior": "Αρχική υπόθεση (δεν υπάρχουν ακόμη αρκετά δεδομένα)",
    "empirical": "Εμπειρικό μοντέλο από τα δικά σου δεδομένα",
    "analog": "AI «παρόμοιες περιπτώσεις» (k-NN) — νίκησε τις άλλες μεθόδους στο backtest",
    "gbm": "AI Gradient Boosting (machine learning) — νίκησε τις άλλες μεθόδους στο backtest",
    "ensemble": "AI ensemble (συνδυασμός εμπειρικού, k-NN και Gradient Boosting) — το πιο ακριβές στο backtest",
}
DROP_LABEL = {
    "from_quantiles": "Υπολογισμένη από τις προβλέψεις τιμής",
    "empirical": "Ιστορική συχνότητα πτώσεων ανά ώρες-πριν-την-πτήση",
    "analog": "AI «παρόμοιες περιπτώσεις» (k-NN) σε πτήσεις που έχουν ήδη φύγει",
    "gbm": "AI classifier (Gradient Boosting) — νίκησε τις άλλες μεθόδους στο backtest",
}


def _iso(ts: pd.Timestamp) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def build(d: pd.DataFrame, tr: Trained, fc: dict, runs: list[dict], cfg, demo: bool = False) -> dict:
    now = datetime.now(timezone.utc)
    flights: dict = {}
    dates: dict = {}
    day_min: dict = {}
    latest_t = d["t"].max() if not d.empty else None

    if not d.empty:
        cutoff = latest_t - pd.Timedelta(days=cfg.keep_departed_days)
        keep = d.groupby("flight_id")["dep_utc"].transform("first") >= cutoff
        dd = d[keep]
        for fid, g in dd.groupby("flight_id", sort=False):
            g = g.sort_values("t")
            last = g.iloc[-1]
            departed = bool(last["dep_utc"] <= latest_t)
            seen_now = bool(last["t"] == latest_t)
            status = "departed" if departed else ("active" if seen_now else "missing")
            hist = [[_iso(t), float(p)] for t, p in zip(g["t"], g["price"])]
            prev24 = g[g["t"] <= last["t"] - pd.Timedelta(hours=20)]
            change24 = float(last["price"] - prev24.iloc[-1]["price"]) if len(prev24) else None
            flights[fid] = {
                "route": last["route"], "origin": last["origin"], "destination": last["destination"],
                "dep_date": last["dep_date"], "dep_local": last["dep_local"], "arr_local": last["arr_local"],
                "carrier": last["carrier"], "carrier_code": last["carrier_code"], "flight_no": last["flight_no"],
                "currency": last["currency"], "status": status,
                "last_price": float(last["price"]), "last_seen": _iso(last["t"]),
                "first_seen": _iso(g["t"].iloc[0]),
                "min_price": float(g["price"].min()), "max_price": float(g["price"].max()),
                "change_24h": change24, "n_obs": int(len(g)),
                "history": hist,
                "forecast": fc.get(fid),
            }
            dates.setdefault(last["route"], {}).setdefault(last["dep_date"], []).append(fid)

        # ελάχιστη τιμή ημέρας ανά κύκλο (για το «ημερολόγιο»)
        mins = dd.groupby(["route", "dep_date", "t"])["price"].min().reset_index()
        for (route, dep_date), g in mins.groupby(["route", "dep_date"]):
            day_min[f"{route}|{dep_date}"] = [[_iso(t), float(p)] for t, p in zip(g["t"], g["price"])]

    for route in dates:
        for dep_date in dates[route]:
            dates[route][dep_date].sort(key=lambda f: flights[f]["dep_local"])

    ok_runs = [r for r in runs if r.get("status") == "ok"]
    return {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "demo": demo,
        "latest_observation": _iso(latest_t) if latest_t is not None else None,
        "last_run": runs[-1] if runs else None,
        "last_success": ok_runs[-1]["ts"] if ok_runs else None,
        "recent_runs": runs[-12:],
        "config": {"routes": [f"{o}-{t}" for o, t in cfg.routes], "days_ahead": cfg.days_ahead,
                   "currency": cfg.currency, "step_hours": 6},
        "model": {
            "method": tr.method, "method_label": METHOD_LABEL[tr.method],
            "drop_method": tr.drop_method, "drop_label": DROP_LABEL[tr.drop_method],
            "backtest": tr.backtest, "drop_backtest": tr.drop_backtest,
            "n_pairs": tr.n_pairs, "data_days": tr.data_days,
            "n_flights": int(d["flight_id"].nunique()) if not d.empty else 0,
            "n_observations": int(len(d)),
        },
        "dates": dates,
        "day_min": day_min,
        "flights": flights,
    }


def write(payload: dict, path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    def default(o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        raise TypeError(type(o))

    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=default),
                    encoding="utf-8")
