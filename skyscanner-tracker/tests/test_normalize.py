"""Έλεγχοι του normalizer με εγγραφές στη μορφή του Apify Skyscanner actor.

Τρέξιμο:  python -m pytest -q   (ή  python tests/test_normalize.py)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker.normalize import normalize_batch, normalize_item, unflatten  # noqa: E402

TS = "2026-10-07T12:00:00Z"

# Μορφή flattenOutput=true (όπως στην τεκμηρίωση του memo23/skyscanner-scraper)
FLAT = {
    "id": "11616-2610130605--31731-0-12409-2610130655",
    "price-raw": 52.4,
    "price-formatted": "52 €",
    "legs-0-origin-displayCode": "CHQ",
    "legs-0-origin-city": "Chania",
    "legs-0-destination-displayCode": "ATH",
    "legs-0-destination-city": "Athens",
    "legs-0-durationInMinutes": 50,
    "legs-0-stopCount": 0,
    "legs-0-departure": "2026-10-13T06:05:00",
    "legs-0-arrival": "2026-10-13T06:55:00",
    "legs-0-carriers-marketing-0-name": "Aegean",
    "legs-0-carriers-marketing-0-logoUrl": "https://logos.skyscnr.com/images/airlines/favicon/A3.png",
    "legs-0-segments-0-flightNumber": "321",
    "tags-0": "cheapest",
    "score": 0.99,
}

# Μορφή flattenOutput=false
NESTED = {
    "price": {"raw": 49.0, "formatted": "49 €"},
    "legs": [{
        "origin": {"displayCode": "CHQ"}, "destination": {"displayCode": "ATH"},
        "departure": "2026-10-13T06:05:00", "arrival": "2026-10-13T06:55:00", "stopCount": 0,
        "carriers": {"marketing": [{"name": "Aegean", "alternateId": "A3"}]},
        "segments": [{"flightNumber": "321", "marketingCarrier": {"name": "Aegean", "alternateId": "A3"}}],
    }],
}

# Μορφή άλλου actor (diopside/skyscanner-flights)
OTHER = {
    "searchOrigin": "ATH", "searchDestination": "CHQ", "price": 61, "currency": "EUR",
    "departure": "2026-10-14T20:40:00", "arrival": "2026-10-14T21:30:00", "stopCount": 0,
    "carrierNames": ["SKY express"],
    "legs": [{"origin": {"id": "ATH"}, "destination": {"id": "CHQ"}, "departure": "2026-10-14T20:40:00",
              "arrival": "2026-10-14T21:30:00", "stopCount": 0, "carriers": [{"name": "SKY express",
              "logoUrl": "https://logos.skyscnr.com/images/airlines/favicon/GQ.png"}],
              "segments": [{"flightNumber": "174", "marketingCarrier": {"name": "SKY express"}}]}],
}


def test_unflatten():
    u = unflatten(FLAT)
    assert u["price"]["raw"] == 52.4
    assert u["legs"][0]["carriers"]["marketing"][0]["name"] == "Aegean"


def test_flat_and_nested_map_to_same_flight():
    a = normalize_item(FLAT, TS, "skyscanner", "EUR")
    b = normalize_item(NESTED, TS, "skyscanner", "EUR")
    assert a["flight_id"] == b["flight_id"] == "CHQATH_202610130605_A3321"
    assert a["carrier_code"] == "A3"  # από το logo όταν λείπει ο κωδικός
    assert a["flight_no"] == "A3 321"
    assert a["dep_local"] == "2026-10-13T06:05"


def test_other_actor_shape():
    o = normalize_item(OTHER, TS, "skyscanner", "EUR")
    assert o["route"] == "ATH-CHQ" and o["price"] == 61 and o["flight_no"] == "GQ 174"


def test_batch_keeps_cheapest_and_filters():
    connecting = dict(NESTED, legs=[dict(NESTED["legs"][0], stopCount=1)])
    notice = {"notice": "Date auto-adjusted"}
    rows = normalize_batch([FLAT, NESTED, connecting, notice, OTHER], TS, "skyscanner", "EUR",
                           nonstop_only=True, wanted_routes={"CHQ-ATH"})
    assert len(rows) == 1
    assert rows[0]["price"] == 49.0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
