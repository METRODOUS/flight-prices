"""Προσομοιωμένα δεδομένα ΜΟΝΟ για demo/έλεγχο του pipeline.

Παράγει εγγραφές στην ίδια μορφή που επιστρέφει ο Apify actor του Skyscanner
(nested και flattened), ώστε να ελέγχεται όλη η αλυσίδα normalize -> storage ->
model -> dashboard. ΔΕΝ είναι πραγματικές τιμές.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from . import storage
from .config import LOCAL_TZ
from .normalize import normalize_batch

LADDER = [39, 45, 52, 59, 66, 74, 82, 92, 104, 119, 139, 165, 195]

SCHEDULE = {  # (ώρα, διάρκεια λεπτά, κωδικός, όνομα, αριθμός, ζήτηση 0-1, φθηνή εταιρεία)
    "CHQ-ATH": [("06:05", 50, "A3", "Aegean", "321", .55, False), ("08:40", 55, "GQ", "SKY express", "171", .45, True),
                ("12:20", 50, "A3", "Aegean", "327", .40, False), ("16:10", 55, "GQ", "SKY express", "175", .50, True),
                ("19:35", 50, "A3", "Aegean", "333", .75, False), ("22:15", 50, "OA", "Olympic Air", "35", .60, False)],
    "ATH-CHQ": [("07:15", 50, "A3", "Aegean", "320", .50, False), ("10:30", 55, "GQ", "SKY express", "170", .40, True),
                ("14:45", 50, "A3", "Aegean", "326", .45, False), ("17:55", 55, "GQ", "SKY express", "174", .60, True),
                ("20:40", 50, "A3", "Aegean", "332", .70, False), ("23:05", 50, "OA", "Olympic Air", "34", .55, False)],
}


def _nested_item(origin, dest, dep, arr, code, name, num, price, stops=0):
    return {
        "id": f"{origin}-{dep:%y%m%d%H%M}-{code}{num}",
        "price": {"raw": price, "formatted": f"€{price:.0f}"},
        "legs": [{
            "origin": {"displayCode": origin}, "destination": {"displayCode": dest},
            "departure": dep.strftime("%Y-%m-%dT%H:%M:%S"), "arrival": arr.strftime("%Y-%m-%dT%H:%M:%S"),
            "durationInMinutes": int((arr - dep).total_seconds() / 60), "stopCount": stops,
            "carriers": {"marketing": [{"name": name, "alternateId": code,
                                        "logoUrl": f"https://logos.skyscnr.com/images/airlines/favicon/{code}.png"}]},
            "segments": [{"flightNumber": num, "marketingCarrier": {"name": name, "alternateId": code}}],
        }],
        "score": 0.9,
    }


def _flatten(d, prefix=""):
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(_flatten(v, f"{prefix}{k}-"))
    elif isinstance(d, list):
        for i, v in enumerate(d):
            out.update(_flatten(v, f"{prefix}{i}-"))
    else:
        out[prefix[:-1]] = d
    return out


class _Flight:
    def __init__(self, rng, route, day, spec):
        hhmm, dur, code, name, num, demand, cheap = spec
        self.route = route
        self.origin, self.dest = route.split("-")
        h, m = map(int, hhmm.split(":"))
        self.dep_local = datetime(day.year, day.month, day.day, h, m)
        self.arr_local = self.dep_local + timedelta(minutes=dur)
        self.dep_utc = self.dep_local.replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc)
        self.code, self.name, self.num = code, name, num
        wk = 0.15 if day.weekday() in (4, 6) else (-0.05 if day.weekday() in (1, 2) else 0)
        self.demand = min(max(demand + wk + rng.uniform(-.15, .15), 0.05), 0.95)
        self.idx = max(0, int(round(self.demand * 4 + rng.uniform(-1, 1)))) - (1 if cheap else 0)
        self.idx = max(self.idx, 0)
        self.cheap = cheap
        self.sold_out = False

    def step(self, rng, h2d):
        if self.sold_out:
            return
        days = h2d / 24
        p_up = 0.04 + 0.25 * self.demand * max(0, (7 - days) / 7) + (0.18 if days < 1.5 else 0)
        p_down = 0.07 if days > 3 else (0.03 if days > 1 else 0.01)
        u = rng.random()
        if u < p_up:
            self.idx = min(self.idx + (2 if rng.random() < .2 else 1), len(LADDER) - 1)
        elif u < p_up + p_down:
            self.idx = max(self.idx - (2 if rng.random() < .25 else 1), 0)
        if days < 1 and self.idx >= len(LADDER) - 2 and rng.random() < .3:
            self.sold_out = True

    def price(self, rng):
        base = LADDER[self.idx] * (0.92 if self.cheap else 1.0)
        return round(base + rng.choice([0, 0, 0, -1.2, -2.5, 1.5]), 2)  # θόρυβος πρακτορείων


def write_demo_history(cfg, days: int = 24, seed: int = 7) -> None:
    rng = random.Random(seed)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(days=days)
    start = start.replace(hour=(start.hour // 6) * 6)
    flights: dict = {}
    t = start
    routes = {f"{o}-{d}" for o, d in cfg.routes}
    while t <= now:
        jitter = timedelta(minutes=rng.choice([3, 11, 24, 38, 52, 70]))  # καθυστερήσεις του GitHub cron
        obs_t = t + jitter
        today = obs_t.astimezone(LOCAL_TZ).date()
        items = []
        for route in routes:
            for k in range(cfg.days_ahead + 1):
                day = today + timedelta(days=k)
                for spec in SCHEDULE.get(route, []):
                    key = (route, day, spec[0])
                    if key not in flights:
                        flights[key] = _Flight(rng, route, day, spec)
                    f = flights[key]
                    h2d = (f.dep_utc - obs_t).total_seconds() / 3600
                    if h2d <= 0:
                        continue
                    f.step(rng, h2d)
                    if f.sold_out:
                        continue
                    item = _nested_item(f.origin, f.dest, f.dep_local, f.arr_local, f.code, f.name, f.num,
                                        f.price(rng))
                    items.append(_flatten(item) if rng.random() < 0.2 else item)
                    if rng.random() < 0.3:  # ίδια πτήση από 2ο πρακτορείο, ακριβότερα
                        items.append(_nested_item(f.origin, f.dest, f.dep_local, f.arr_local, f.code, f.name,
                                                  f.num, f.price(rng) + 6))
                # μια πτήση με ανταπόκριση (πρέπει να φιλτραριστεί)
                dep = datetime(day.year, day.month, day.day, 9, 0)
                items.append(_nested_item(*route.split("-"), dep, dep + timedelta(hours=4), "OA", "Olympic Air",
                                          "99", 210, stops=1))
        items.append({"notice": "Date auto-adjusted"})  # γραμμή ειδοποίησης χωρίς τιμή
        ts = obs_t.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = normalize_batch(items, ts, "demo", cfg.currency, cfg.nonstop_only, routes)
        storage.append_observations(cfg.data_dir, rows)
        storage.log_run(cfg.data_dir, {"ts": ts, "status": "ok", "searches": len(routes) * (cfg.days_ahead + 1),
                                       "flights": len(rows), "cost_usd": None})
        t += timedelta(hours=6)
