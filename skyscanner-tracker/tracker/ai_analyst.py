"""AI αναλυτής (Claude): διαβάζει όλα τα τρέχοντα δεδομένα, τις προβλέψεις και τις
«παρόμοιες περιπτώσεις» και γράφει ανάλυση στα ελληνικά: γενική εικόνα, ανά
διαδρομή, ανά ημέρα, ανά πτήση και τις καλύτερες επιλογές τώρα.

Οι ΑΡΙΘΜΟΙ (τιμές, πιθανότητες, εύρη) βγαίνουν από τα στατιστικά/ML μοντέλα του
model.py. Το γλωσσικό μοντέλο δεν μαντεύει τιμές: συνδυάζει και εξηγεί όσα
δείχνουν τα δεδομένα (π.χ. «η πτήση των 08:40 είναι 12€ φθηνότερη την ίδια μέρα»).

Χωρίς ANTHROPIC_API_KEY (ή αν αποτύχει η κλήση) παράγεται αυτόματη σύνοψη με κανόνες.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import requests

API_URL = "https://api.anthropic.com/v1/messages"

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["overview", "routes", "best_picks", "day_notes", "flight_notes"],
    "properties": {
        "overview": {"type": "string", "description": "2-4 προτάσεις: η γενική εικόνα τώρα"},
        "routes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["route", "summary"],
            "properties": {"route": {"type": "string"}, "summary": {"type": "string"}}}},
        "best_picks": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["route", "date", "flight_id", "reason"],
            "properties": {"route": {"type": "string"}, "date": {"type": "string"},
                           "flight_id": {"type": "string"}, "reason": {"type": "string"}}}},
        "day_notes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["route", "date", "note"],
            "properties": {"route": {"type": "string"}, "date": {"type": "string"}, "note": {"type": "string"}}}},
        "flight_notes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["flight_id", "note"],
            "properties": {"flight_id": {"type": "string"}, "note": {"type": "string"}}}},
    },
}

SYSTEM = """Είσαι αναλυτής τιμών αεροπορικών εισιτηρίων. Ο χρήστης ταξιδεύει συχνά Χανιά ⇄ Αθήνα και θέλει
να αγοράζει τη φθηνότερη στιγμή. Σου δίνονται σε JSON οι τρέχουσες τιμές Skyscanner κάθε πτήσης,
το ιστορικό τους σε σύνοψη, οι προβλέψεις του στατιστικού/ML μοντέλου (q10/q50/q90 = αισιόδοξη /
πιθανότερη / απαισιόδοξη τιμή), η πιθανότητα να εμφανιστεί φθηνότερη τιμή πριν την πτήση (p_drop),
η σύσταση του μοντέλου (buy/wait/hold) και τι έγινε σε «παρόμοιες περιπτώσεις» από πτήσεις που έχουν ήδη φύγει.

Κανόνες:
- Γράφε σε φυσικά, σύντομα ελληνικά. Τιμές σε ευρώ χωρίς δεκαδικά (π.χ. «48 €»).
- Χρησιμοποίησε ΜΟΝΟ αριθμούς που υπάρχουν στα δεδομένα ή απλές πράξεις πάνω τους. Μην επινοείς τιμές, πτήσεις ή γεγονότα.
- Μη γράφεις κάτι αντίθετο με τη σύσταση του μοντέλου. Μπορείς να την εξηγήσεις και να προτείνεις εναλλακτική πτήση ίδιας μέρας.
- Να είσαι ειλικρινής για την αβεβαιότητα (π.χ. όταν το εύρος q10–q90 είναι μεγάλο ή τα δεδομένα λίγα). Ποτέ «σίγουρα».
- Ημερομηνίες ως «Τρίτη 13/10». Ώρες ως «06:05».
- overview: 2–4 προτάσεις για όλη την εικόνα.
- routes: μία σύνοψη 2–3 προτάσεων ανά διαδρομή.
- best_picks: έως 2 ανά διαδρομή, οι πιο συμφέρουσες κινήσεις τώρα, με flight_id ακριβώς όπως δίνεται.
- day_notes: μία πρόταση για κάθε ημερομηνία κάθε διαδρομής.
- flight_notes: μία σύντομη πρόταση (≤ 25 λέξεις) για ΚΑΘΕ πτήση, με flight_id ακριβώς όπως δίνεται."""


# ------------------------------------------------------------------ context
def _r(v):
    return None if v is None else round(float(v))


def build_context(payload: dict, today: str) -> dict:
    flights = payload.get("flights", {})
    routes = {}
    for route, dates in payload.get("dates", {}).items():
        for date, ids in sorted(dates.items()):
            if date < today:
                continue
            day = []
            for fid in ids:
                f = flights[fid]
                if f["status"] != "active" or not f.get("forecast"):
                    continue
                fc = f["forecast"]
                steps = {s["h"]: s for s in fc["steps"]}
                pick = lambda h: ([_r(steps[h]["q10"]), _r(steps[h]["q50"]), _r(steps[h]["q90"])] if h in steps else None)  # noqa: E731
                last = fc["steps"][-1] if fc["steps"] else None
                day.append({
                    "flight_id": fid, "dep": f["dep_local"][11:16], "carrier": f["carrier"], "flight_no": f["flight_no"],
                    "price": _r(f["last_price"]), "change_24h": _r(f["change_24h"]),
                    "min_seen": _r(f["min_price"]), "max_seen": _r(f["max_price"]), "n_obs": f["n_obs"],
                    "rec": fc["rec"], "p_drop": fc["p_drop"], "expected_low": _r(fc["expected_low"]),
                    "fc_24h": pick(24), "fc_48h": pick(48),
                    "fc_before_departure": [_r(last["q10"]), _r(last["q50"]), _r(last["q90"])] if last else None,
                    "similar": fc.get("similar"),
                })
            if day:
                series = payload.get("day_min", {}).get(f"{route}|{date}", [])
                routes.setdefault(route, []).append({
                    "date": date, "cheapest_now": min(x["price"] for x in day),
                    "cheapest_3_days_ago": _r(series[-13][1]) if len(series) >= 13 else None,
                    "flights": day,
                })
    m = payload.get("model", {})
    bt = m.get("backtest", {}).get(m.get("method"), {})
    return {
        "today": today,
        "now_utc": payload.get("latest_observation"),
        "model": {"method": m.get("method"), "data_days": m.get("data_days"),
                  "avg_error_eur": bt.get("mae_eur"), "naive_error_eur": m.get("backtest", {}).get("naive", {}).get("mae_eur"),
                  "interval80_coverage": bt.get("coverage80")},
        "routes": routes,
    }


# ------------------------------------------------------------------ Claude
def call_claude(ctx: dict, model: str, max_tokens: int, api_key: str) -> dict:
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": SYSTEM,
        "messages": [{"role": "user", "content":
                      "Δεδομένα (JSON):\n" + json.dumps(ctx, ensure_ascii=False, separators=(",", ":"))}],
        "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}},
    }
    r = requests.post(API_URL, json=body, timeout=240, headers={
        "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    if r.status_code >= 300:
        raise RuntimeError(f"Claude API HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    out = json.loads(text)
    out["_usage"] = data.get("usage", {})
    return out


# ------------------------------------------------------------------ χωρίς AI
DAYS = ["Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή", "Σάββατο", "Κυριακή"]
PLACES = {"CHQ": "Χανιά", "ATH": "Αθήνα", "HER": "Ηράκλειο"}


def _day(date: str) -> str:
    d = datetime.strptime(date, "%Y-%m-%d")
    return f"{DAYS[d.weekday()]} {d.day}/{d.month}"


def _route(r: str) -> str:
    a, b = r.split("-")
    return f"{PLACES.get(a, a)} → {PLACES.get(b, b)}"


def rules_analysis(ctx: dict) -> dict:
    out = {"overview": "", "routes": [], "best_picks": [], "day_notes": [], "flight_notes": []}
    ov = []
    for route, days in ctx["routes"].items():
        allf = [(d["date"], f) for d in days for f in d["flights"]]
        cheapest_date, cheapest = min(allf, key=lambda x: x[1]["price"])
        n_buy = sum(f["rec"] == "buy" for _, f in allf)
        n_wait = sum(f["rec"] == "wait" for _, f in allf)
        ov.append(f"{_route(route)}: φθηνότερη τώρα η {_day(cheapest_date)} στις {cheapest['dep']} "
                  f"({cheapest['carrier']}) με {cheapest['price']} €.")
        rising = [d for d in days if d["cheapest_3_days_ago"] and d["cheapest_now"] > d["cheapest_3_days_ago"] + 2]
        out["routes"].append({"route": route, "summary":
                              f"{len(allf)} πτήσεις σε {len(days)} μέρες. {n_buy} με σύσταση «αγόρασε», {n_wait} «περίμενε». "
                              + (f"Σε {len(rising)} μέρες η φθηνότερη τιμή ανέβηκε τις τελευταίες 3 μέρες." if rising else "")})
        picks = sorted([x for x in allf if x[1]["rec"] != "wait"], key=lambda x: x[1]["price"])[:2]
        for date, f in picks:
            out["best_picks"].append({"route": route, "date": date, "flight_id": f["flight_id"],
                                      "reason": f"{_day(date)} {f['dep']} με {f['price']} €· μόλις {round(f['p_drop'] * 100)}% πιθανότητα να πέσει."})
        for d in days:
            fl = sorted(d["flights"], key=lambda f: f["price"])
            trend = ""
            if d["cheapest_3_days_ago"]:
                diff = d["cheapest_now"] - d["cheapest_3_days_ago"]
                trend = (" Ίδια με πριν 3 μέρες." if abs(diff) < 2 else
                         f" {'Ανέβηκε' if diff > 0 else 'Έπεσε'} {abs(diff)} € σε 3 μέρες.")
            out["day_notes"].append({"route": route, "date": d["date"], "note":
                                     f"Φθηνότερη η {fl[0]['dep']} ({fl[0]['carrier']}) με {fl[0]['price']} €.{trend}"})
            for f in fl:
                sim = f.get("similar")
                if sim and sim.get("n"):
                    note = (f"Σε {sim['n']} παρόμοιες περιπτώσεις, το {round(sim['pct_drop'] * 100)}% είδε φθηνότερη τιμή "
                            f"και το {round(sim['pct_final_higher'] * 100)}% έκλεισε ακριβότερα.")
                else:
                    note = f"Πιθανότερη χαμηλή τιμή μέχρι την πτήση ~{f['expected_low']} €."
                if f is not fl[0] and f["price"] - fl[0]["price"] >= 5:
                    note += f" Η {fl[0]['dep']} είναι {f['price'] - fl[0]['price']} € φθηνότερη."
                out["flight_notes"].append({"flight_id": f["flight_id"], "note": note})
    out["overview"] = " ".join(ov) or "Δεν υπάρχουν ακόμη ενεργές πτήσεις για ανάλυση."
    return out


# ------------------------------------------------------------------ main
def _clean(res: dict, ctx: dict) -> dict:
    valid = {f["flight_id"] for days in ctx["routes"].values() for d in days for f in d["flights"]}
    return {
        "overview": str(res.get("overview", ""))[:1200],
        "routes": {r["route"]: str(r["summary"])[:800] for r in res.get("routes", []) if r.get("route") in ctx["routes"]},
        "best_picks": [p for p in res.get("best_picks", []) if p.get("flight_id") in valid][:6],
        "day_notes": {f"{n['route']}|{n['date']}": str(n["note"])[:400] for n in res.get("day_notes", [])},
        "flight_notes": {n["flight_id"]: str(n["note"])[:300] for n in res.get("flight_notes", []) if n.get("flight_id") in valid},
    }


def analyse(payload: dict, cfg, data_dir, today: str, log=print) -> dict:
    ctx = build_context(payload, today)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not ctx["routes"]:
        return {"source": "none", "generated_at": now, **_clean(rules_analysis(ctx), ctx)}
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    error = None
    if cfg.ai_enabled and key:
        try:
            res = call_claude(ctx, cfg.ai_model, cfg.ai_max_tokens, key)
            usage = res.pop("_usage", {})
            out = {"source": "claude", "model": cfg.ai_model, "generated_at": now, "usage": usage, **_clean(res, ctx)}
            (data_dir / "ai_latest.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
            log(f"[ai] Claude ({cfg.ai_model}) OK, tokens={usage}")
            return out
        except Exception as e:  # συνεχίζουμε με κανόνες
            error = f"{type(e).__name__}: {e}"[:300]
            log(f"[ai] ΣΦΑΛΜΑ: {error}")
    prev = data_dir / "ai_latest.json"
    if error and prev.exists():  # κράτα την τελευταία ανάλυση Claude αν είναι πρόσφατη (< 12 ώρες)
        try:
            old = json.loads(prev.read_text(encoding="utf-8"))
            age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(old["generated_at"].replace("Z", "+00:00"))).total_seconds() / 3600
            if age_h < 12:
                return {**old, "stale": True, "error": error}
        except Exception:
            pass
    out = {"source": "rules", "generated_at": now, **_clean(rules_analysis(ctx), ctx)}
    if error:
        out["error"] = error
    elif cfg.ai_enabled and not key:
        out["note"] = "Πρόσθεσε ANTHROPIC_API_KEY για ανάλυση με Claude."
    return out
