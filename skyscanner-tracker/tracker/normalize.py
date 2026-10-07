"""Μετατροπή των εγγραφών Skyscanner (όπως τις επιστρέφει ο Apify actor) σε
ενιαίες «παρατηρήσεις τιμής» ανά πτήση.

Υποστηρίζει και τις δύο μορφές εξόδου του actor:
  * nested  (flattenOutput=false):  {"price": {"raw": 52}, "legs": [{...}]}
  * flat    (flattenOutput=true):   {"price-raw": 52, "legs-0-departure": "..."}
και επιπλέον τη μορφή άλλων Skyscanner actors ("price": 52, "carrierNames": [...]).
"""
from __future__ import annotations

import re
from typing import Any

OBS_COLUMNS = [
    "observed_at",   # UTC ISO, ώρα του κύκλου συλλογής
    "route",         # π.χ. CHQ-ATH
    "origin",
    "destination",
    "dep_date",      # YYYY-MM-DD (τοπική ώρα)
    "dep_local",     # YYYY-MM-DDTHH:MM (τοπική ώρα Ελλάδας)
    "arr_local",
    "carrier",
    "carrier_code",
    "flight_no",
    "flight_id",     # σταθερό κλειδί πτήσης
    "price",
    "currency",
    "stops",
    "source",
]


# ---------------------------------------------------------------- helpers
def unflatten(item: dict[str, Any]) -> dict[str, Any]:
    """'legs-0-origin-displayCode' -> {'legs': [{'origin': {'displayCode': ..}}]}"""
    if not any("-" in k for k in item):
        return item
    root: dict[str, Any] = {}
    for key, value in item.items():
        parts = key.split("-")
        node: Any = root
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            nxt = None if last else parts[i + 1]
            idx = int(part) if part.isdigit() else part
            if isinstance(node, list):
                while len(node) <= idx:
                    node.append(None)
                if last:
                    node[idx] = value
                else:
                    if node[idx] is None:
                        node[idx] = [] if nxt.isdigit() else {}
                    node = node[idx]
            else:
                if last:
                    node[idx] = value
                else:
                    if idx not in node or not isinstance(node[idx], (dict, list)):
                        node[idx] = [] if nxt.isdigit() else {}
                    node = node[idx]
    return root


def _get(obj: Any, *path, default=None):
    for p in path:
        if obj is None:
            return default
        if isinstance(p, int):
            if not isinstance(obj, list) or len(obj) <= p:
                return default
            obj = obj[p]
        else:
            if not isinstance(obj, dict):
                return default
            obj = obj.get(p)
    return default if obj is None else obj


def _first(*vals):
    for v in vals:
        if v not in (None, "", []):
            return v
    return None


def _price(item: dict) -> float | None:
    p = item.get("price")
    if isinstance(p, dict):
        p = _first(p.get("raw"), p.get("amount"))
    if p is None:
        p = _first(item.get("pricePerAdult"), item.get("bestPrice"))
    if isinstance(p, str):
        m = re.search(r"[\d.,]+", p)
        p = float(m.group(0).replace(",", "")) if m else None
    try:
        return float(p) if p is not None else None
    except (TypeError, ValueError):
        return None


def _hhmm(ts: str | None) -> str | None:
    if not ts:
        return None
    return str(ts)[:16]  # YYYY-MM-DDTHH:MM


def _carrier(leg: dict, item: dict) -> tuple[str, str]:
    name = _first(
        _get(leg, "carriers", "marketing", 0, "name"),
        _get(leg, "carriers", 0, "name"),
        _get(leg, "segments", 0, "marketingCarrier", "name"),
        _get(item, "carrierNames", 0),
        item.get("airline"),
    )
    code = _first(
        _get(leg, "segments", 0, "marketingCarrier", "displayCode"),
        _get(leg, "segments", 0, "marketingCarrier", "alternateId"),
        _get(leg, "carriers", "marketing", 0, "displayCode"),
        _get(leg, "carriers", "marketing", 0, "alternateId"),
    )
    if not code:
        logo = _first(_get(leg, "carriers", "marketing", 0, "logoUrl"), _get(leg, "carriers", 0, "logoUrl"))
        m = re.search(r"/([A-Z0-9]{2})\.png", str(logo or ""))
        code = m.group(1) if m else None
    return (str(name or "Άγνωστη"), str(code or "").upper())


def _code(place: Any) -> str | None:
    if isinstance(place, dict):
        return _first(place.get("displayCode"), place.get("iata"), place.get("id"), place.get("airport"))
    return place


# ---------------------------------------------------------------- main
def normalize_item(raw: dict, observed_at: str, source: str, currency: str) -> dict | None:
    """Μία εγγραφή actor -> μία παρατήρηση (ή None αν δεν είναι πτήση)."""
    item = unflatten(raw)
    price = _price(item)
    leg = _get(item, "legs", 0, default={}) or {}
    dep = _hhmm(_first(leg.get("departure"), item.get("departure"), item.get("departTime"), item.get("departureTime")))
    if price is None or price <= 0 or not dep:
        return None  # π.χ. γραμμή ειδοποίησης «Date auto-adjusted»

    origin = _code(_first(_get(leg, "origin"), item.get("searchOrigin"), _get(item, "from"), item.get("origin")))
    dest = _code(_first(_get(leg, "destination"), item.get("searchDestination"), _get(item, "to"), item.get("destination")))
    if not origin or not dest:
        return None
    origin, dest = str(origin).upper(), str(dest).upper()

    arr = _hhmm(_first(leg.get("arrival"), item.get("arrival"), item.get("arriveTime"), item.get("arrivalTime")))
    stops = _first(leg.get("stopCount"), item.get("stopCount"), item.get("stops"))
    try:
        stops = int(stops) if stops is not None else 0
    except (TypeError, ValueError):
        stops = 0

    carrier, code = _carrier(leg, item)
    flight_no = str(_first(_get(leg, "segments", 0, "flightNumber"), item.get("flightNumber")) or "")
    flight_no = re.sub(r"\D", "", flight_no)
    label = f"{code}{flight_no}" if code and flight_no else re.sub(r"\W", "", carrier)[:12].upper()

    return {
        "observed_at": observed_at,
        "route": f"{origin}-{dest}",
        "origin": origin,
        "destination": dest,
        "dep_date": dep[:10],
        "dep_local": dep,
        "arr_local": arr or "",
        "carrier": carrier,
        "carrier_code": code,
        "flight_no": f"{code} {flight_no}".strip() if flight_no else "",
        "flight_id": f"{origin}{dest}_{dep.replace('-', '').replace(':', '').replace('T', '')}_{label}",
        "price": round(price, 2),
        "currency": str(_first(item.get("currency"), currency)),
        "stops": stops,
        "source": source,
    }


def normalize_batch(items: list[dict], observed_at: str, source: str, currency: str,
                    nonstop_only: bool, wanted_routes: set[str]) -> list[dict]:
    """Κανονικοποίηση + κράτηση της φθηνότερης τιμής ανά πτήση σε κάθε κύκλο
    (το ίδιο αεροπλάνο εμφανίζεται συχνά από πολλά πρακτορεία)."""
    best: dict[str, dict] = {}
    for raw in items:
        if not isinstance(raw, dict):
            continue
        obs = normalize_item(raw, observed_at, source, currency)
        if obs is None or obs["route"] not in wanted_routes:
            continue
        if nonstop_only and obs["stops"] > 0:
            continue
        cur = best.get(obs["flight_id"])
        if cur is None or obs["price"] < cur["price"]:
            best[obs["flight_id"]] = obs
    return sorted(best.values(), key=lambda o: (o["route"], o["dep_local"]))
