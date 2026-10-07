"""Συλλογή τιμών Skyscanner μέσω Apify actor.

Το επίσημο Skyscanner API δίνεται μόνο σε εγκεκριμένους partners, οπότε
χρησιμοποιούμε actor του Apify που διαβάζει τα ίδια αποτελέσματα που βλέπεις
στην εφαρμογή Skyscanner (με residential proxies, που απαιτούνται).

Σε κάθε κύκλο τρέχει ΕΝΑ run με όλες τις αναζητήσεις (διαδρομή × ημερομηνία)
ως startUrls, ώστε να πληρώνεται μία μόνο χρέωση εκκίνησης.
"""
from __future__ import annotations

import time
from datetime import date, timedelta

import requests

API = "https://api.apify.com/v2"


class SourceError(RuntimeError):
    pass


def search_urls(cfg, today: date) -> list[dict]:
    urls = []
    for origin, dest in cfg.routes:
        for d in range(cfg.days_ahead + 1):
            day = today + timedelta(days=d)
            urls.append({
                "url": f"{cfg.host}/transport/flights/{origin.lower()}/{dest.lower()}/{day:%y%m%d}/"
                       f"?adultsv2={cfg.adults}&cabinclass={cfg.cabin_class}&rtn=0",
                "userData": {"route": f"{origin}-{dest}", "date": day.isoformat()},
            })
    return urls


def build_input(cfg, today: date) -> dict:
    return {
        "startUrls": search_urls(cfg, today),
        "cabinClass": cfg.cabin_class,
        "adults": cfg.adults,
        "skyscannerMarket": cfg.market,
        "skyscannerCurrency": cfg.currency,
        "skyscannerLocale": cfg.locale,
        "resultSort": "cheapest",
        "filterNonStop": bool(cfg.nonstop_only),
        "flattenOutput": False,
        "maxItems": 5000,
        "maxRequestRetries": 5,
    }


def fetch(cfg, token: str, today: date, log=print) -> tuple[list[dict], dict]:
    """Επιστρέφει (raw items, meta). Πετάει SourceError σε αποτυχία."""
    if not token:
        raise SourceError("Λείπει το APIFY_TOKEN (GitHub secret).")
    headers = {"Authorization": f"Bearer {token}"}
    actor = cfg.actor.replace("/", "~")
    run_input = build_input(cfg, today)
    n = len(run_input["startUrls"])
    log(f"[apify] εκκίνηση {cfg.actor} με {n} αναζητήσεις")

    r = requests.post(
        f"{API}/acts/{actor}/runs",
        params={"maxTotalChargeUsd": cfg.max_charge_usd_per_run,
                "timeout": cfg.timeout_minutes * 60},
        json=run_input, headers=headers, timeout=60,
    )
    if r.status_code >= 300:
        raise SourceError(f"Apify start HTTP {r.status_code}: {r.text[:300]}")
    run = r.json()["data"]
    run_id = run["id"]

    deadline = time.time() + cfg.timeout_minutes * 60 + 120
    status = run.get("status")
    while status in ("READY", "RUNNING") and time.time() < deadline:
        rr = requests.get(f"{API}/actor-runs/{run_id}", params={"waitForFinish": 60},
                          headers=headers, timeout=90)
        rr.raise_for_status()
        run = rr.json()["data"]
        status = run.get("status")
    if status != "SUCCEEDED":
        msg = run.get("statusMessage") or ""
        raise SourceError(f"Apify run {run_id} τελείωσε με status={status} {msg}".strip())

    items: list[dict] = []
    offset = 0
    while True:
        ri = requests.get(f"{API}/datasets/{run['defaultDatasetId']}/items",
                          params={"clean": "true", "format": "json", "offset": offset, "limit": 1000},
                          headers=headers, timeout=120)
        ri.raise_for_status()
        batch = ri.json()
        items.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000

    meta = {"run_id": run_id, "searches": n, "raw_items": len(items),
            "cost_usd": run.get("usageTotalUsd")}
    log(f"[apify] OK: {len(items)} εγγραφές")
    return items, meta
