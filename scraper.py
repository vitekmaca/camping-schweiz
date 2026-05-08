#!/usr/bin/env python3
"""
Swiss Campsite Scraper
Fetches camp_site + caravan_site from OpenStreetMap via Overpass API,
enriches each entry with a canton code via Nominatim reverse geocoding,
and writes data.json compatible with the camping-schweiz web app.

Dependencies: requests  (pip install requests)
Runtime: ~5-10 min depending on number of results (Nominatim rate-limit 1 req/s)
"""

import json
import time
import sys
from collections import Counter

import requests

# ── Config ────────────────────────────────────────────────────────────────────

OVERPASS_URL  = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
HEADERS       = {"User-Agent": "camping-schweiz-scraper/1.0 (personal, github.com/vitekmaca/camping-schweiz)"}
OUTPUT_FILE   = "data.json"

CANTON_MAP = {
    "Aargau": "AG", "Appenzell Innerrhoden": "AI", "Appenzell Ausserrhoden": "AR",
    "Bern": "BE", "Basel-Landschaft": "BL", "Basel-Stadt": "BS",
    "Fribourg": "FR", "Freiburg": "FR",
    "Geneva": "GE", "Genève": "GE", "Genf": "GE",
    "Glarus": "GL",
    "Graubünden": "GR", "Grischun": "GR", "Grigioni": "GR",
    "Jura": "JU",
    "Luzern": "LU", "Lucerne": "LU",
    "Neuchâtel": "NE", "Neuenburg": "NE",
    "Nidwalden": "NW", "Obwalden": "OW",
    "St. Gallen": "SG", "Sankt Gallen": "SG",
    "Schaffhausen": "SH", "Solothurn": "SO", "Schwyz": "SZ", "Thurgau": "TG",
    "Ticino": "TI", "Uri": "UR",
    "Vaud": "VD",
    "Valais": "VS", "Wallis": "VS",
    "Zug": "ZG",
    "Zürich": "ZH", "Zurich": "ZH",
}

# ── Overpass query ────────────────────────────────────────────────────────────

OVERPASS_QUERY = """
[out:json][timeout:120];
area["ISO3166-1"="CH"][admin_level=2]->.ch;
(
  node["tourism"="camp_site"](area.ch);
  way["tourism"="camp_site"](area.ch);
  node["tourism"="caravan_site"](area.ch);
  way["tourism"="caravan_site"](area.ch);
);
out center tags;
"""

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_canton(lat, lng):
    try:
        r = requests.get(NOMINATIM_URL, params={
            "lat": lat, "lon": lng, "format": "json",
            "zoom": 6, "addressdetails": 1,
        }, headers=HEADERS, timeout=10)
        r.raise_for_status()
        state = r.json().get("address", {}).get("state", "")
        # Nominatim returns multilingual names like "Bern/Berne" or "Valais/Wallis"
        for part in state.split("/"):
            code = CANTON_MAP.get(part.strip())
            if code:
                return code
        return "?"
    except Exception:
        return "?"


def detect_type(tags):
    if tags.get("tourism") == "caravan_site":
        return "stellplatz"
    if tags.get("backcountry") == "yes":
        return "bivouac"
    name_op = (tags.get("name", "") + " " + tags.get("operator", "")).lower()
    farm_kw = ["hof", "farm", "bauernhof", "ferme", "fattoria", "agri", "agrar"]
    if any(k in name_op for k in farm_kw):
        return "farm"
    return "campsite"


def detect_amenities(tags):
    checks = {
        "toilets":     lambda t: t.get("toilets") == "yes",
        "showers":     lambda t: t.get("shower") in ("yes", "hot") or t.get("showers") == "yes",
        "electricity": lambda t: t.get("electricity") == "yes" or t.get("power_supply") == "yes",
        "wifi":        lambda t: t.get("internet_access") in ("wlan", "wifi") or t.get("wifi") == "yes",
        "pool":        lambda t: t.get("swimming_pool") == "yes" or t.get("pool") == "yes",
        "swimming":    lambda t: t.get("swimming") == "yes" or t.get("bathing_place") == "yes",
        "playground":  lambda t: t.get("playground") == "yes",
        "restaurant":  lambda t: t.get("restaurant") == "yes",
        "shop":        lambda t: t.get("shop") == "yes",
        "dogs":        lambda t: t.get("dog") == "yes" or t.get("animals") == "yes",
        "bbq":         lambda t: t.get("bbq") == "yes" or t.get("fireplace") == "yes",
        "bikes":       lambda t: t.get("rental") == "bicycle" or t.get("bicycle_rental") == "yes",
        "laundry":     lambda t: t.get("laundry") == "yes" or t.get("washing_machine") == "yes",
        "accessible":  lambda t: t.get("wheelchair") in ("yes", "limited"),
    }
    return [key for key, check in checks.items() if check(tags)]


def detect_water(tags):
    name = tags.get("name", "").lower()
    lake_kw  = ["see", " see", "lago", "lac ", "lake", "etang", "étang"]
    river_kw = ["bach", "aare", "rhein", "rhone", "linth", "reuss",
                "river", "fluss", "rivière", "torrent"]
    if any(k in name for k in lake_kw):
        return "lake"
    if any(k in name for k in river_kw):
        return "river"
    return None


def build_google_url(name, lat, lng):
    if not name:
        return f"https://maps.google.com/?q={lat},{lng}"
    q = requests.utils.quote(f"{name}, Switzerland")
    return f"https://maps.google.com/?q={q}"


# ── Main ──────────────────────────────────────────────────────────────────────

def fetch_osm():
    print("Querying Overpass API for Swiss campsites…", flush=True)
    r = requests.post(OVERPASS_URL, data={"data": OVERPASS_QUERY},
                      headers=HEADERS, timeout=180)
    r.raise_for_status()
    elements = r.json()["elements"]
    print(f"  → {len(elements)} elements received\n", flush=True)
    return elements


def process(elements):
    total  = len(elements)
    camps  = []
    skipped = 0

    print(f"Enriching {total} camps with canton (Nominatim, ~{total} s)…\n", flush=True)

    for i, el in enumerate(elements, 1):
        tags = el.get("tags", {})
        lat  = el.get("lat") or (el.get("center") or {}).get("lat")
        lng  = el.get("lon") or (el.get("center") or {}).get("lon")

        if not lat or not lng:
            skipped += 1
            continue

        osm_type = el["type"]
        osm_id   = el["id"]
        name     = tags.get("name") or tags.get("alt_name") or ""

        print(f"  [{i:3}/{total}] {name or '(unnamed)':40s}", end=" ", flush=True)

        time.sleep(1.1)  # Nominatim ToS: max 1 req/sec
        canton = get_canton(lat, lng)

        print(f"→ {canton}", flush=True)

        camps.append({
            "id":               f"osm-{osm_id}",
            "name":             name,
            "lat":              lat,
            "lng":              lng,
            "canton":           canton,
            "type":             detect_type(tags),
            "amenities":        detect_amenities(tags),
            "near_water":       detect_water(tags),
            "distance_water_m": None,   # reserved for future enrichment
            "distance_town_m":  None,
            "rating_google":    None,   # reserved for P4N enrichment
            "rating_p4n":       None,
            "url_google":       build_google_url(name, lat, lng),
            "url_p4n":          None,
            "url_osm":          f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
            "website":          tags.get("website") or tags.get("url") or tags.get("contact:website"),
        })

    return camps, skipped


def print_summary(camps, skipped):
    print(f"\n{'─'*50}")
    print(f"Saved : {len(camps)} camps  |  Skipped (no coords): {skipped}")
    types   = Counter(c["type"]   for c in camps)
    cantons = Counter(c["canton"] for c in camps)
    print(f"Types : {dict(types)}")
    print(f"Top 5 cantons: {dict(cantons.most_common(5))}")
    amenity_counts = Counter(a for c in camps for a in c["amenities"])
    print(f"Top 5 amenities: {dict(amenity_counts.most_common(5))}")
    print(f"{'─'*50}")


def main():
    try:
        elements = fetch_osm()
    except requests.RequestException as e:
        print(f"Overpass query failed: {e}", file=sys.stderr)
        sys.exit(1)

    camps, skipped = process(elements)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(camps, f, ensure_ascii=False, indent=2)

    print(f"\n✓  Written to {OUTPUT_FILE}")
    print_summary(camps, skipped)


if __name__ == "__main__":
    main()
