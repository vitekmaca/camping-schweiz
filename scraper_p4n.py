#!/usr/bin/env python3
"""
Park4Night Scraper — Switzerland
Fetches Camping (C), Farm (F), Free motorhome area (ACC_G) and Paying motorhome area (ACC_P) from the P4N /api/places/around endpoint.
Covers Switzerland via a lat/lng grid, deduplicates by P4N ID,
then merges with existing data.json (OSM) by proximity.

No login required — the endpoint is public.
Dependencies: requests  (pip install requests)
"""

import base64
import json
import math
import time

import requests

# ── Config ────────────────────────────────────────────────────────────────────

CANTON_MAP = {
    "Aargau": "AG", "Appenzell Innerrhoden": "AI", "Appenzell Ausserrhoden": "AR",
    "Bern": "BE", "Berne": "BE", "Basel-Landschaft": "BL", "Basel-Stadt": "BS",
    "Fribourg": "FR", "Freiburg": "FR",
    "Geneva": "GE", "Genève": "GE", "Genf": "GE",
    "Glarus": "GL",
    "Graubünden": "GR", "Grischun": "GR", "Grigioni": "GR",
    "Jura": "JU", "Luzern": "LU", "Lucerne": "LU",
    "Neuchâtel": "NE", "Neuenburg": "NE",
    "Nidwalden": "NW", "Obwalden": "OW",
    "St. Gallen": "SG", "Sankt Gallen": "SG",
    "Schaffhausen": "SH", "Solothurn": "SO", "Schwyz": "SZ", "Thurgau": "TG",
    "Ticino": "TI", "Uri": "UR", "Vaud": "VD",
    "Valais": "VS", "Wallis": "VS",
    "Zug": "ZG", "Zürich": "ZH", "Zurich": "ZH",
}

def canton_from_address(address):
    """Extract canton code from P4N address dict (state field)."""
    state = address.get("state", "") or ""
    for part in state.split("/"):
        code = CANTON_MAP.get(part.strip())
        if code:
            return code
    return "?"

P4N_API     = "https://park4night.com/api/places/around"
HEADERS     = {"User-Agent": "Mozilla/5.0 (personal camping map project)"}
DATA_FILE   = "data.json"
MATCH_KM    = 1.0   # max distance (km) to consider an OSM↔P4N pair the same place
SLEEP       = 1.2   # seconds between grid requests (be polite)

# Switzerland grid: 4 lat rows × 6 lng cols = 24 queries, radius 55 km each
LAT_POINTS  = [46.05, 46.60, 47.15, 47.70]
LNG_POINTS  = [6.10,  6.90,  7.70,  8.50,  9.30, 10.10]
RADIUS      = 55   # km

# ── Helpers ───────────────────────────────────────────────────────────────────

def haversine_km(lat1, lng1, lat2, lng2):
    R = 6371
    r = math.pi / 180
    dlat = (lat2 - lat1) * r
    dlng = (lng2 - lng1) * r
    a = math.sin(dlat/2)**2 + math.cos(lat1*r) * math.cos(lat2*r) * math.sin(dlng/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def fetch_grid():
    """Query every grid point, return deduplicated dict {p4n_id: place}."""
    seen = {}
    total_queries = len(LAT_POINTS) * len(LNG_POINTS)
    i = 0

    for lat in LAT_POINTS:
        for lng in LNG_POINTS:
            i += 1
            print(f"  [{i:2}/{total_queries}] lat={lat} lng={lng} ...", end=" ", flush=True)
            try:
                r = requests.get(P4N_API, params={
                    "lat": lat, "lng": lng,
                    "radius": RADIUS,
                    "filter": '{"type":["C","F","ACC_G","ACC_P"]}',
                    "lang": "en",
                }, headers=HEADERS, timeout=20)
                r.raise_for_status()
                places = json.loads(base64.b64decode(r.content).decode("utf-8"))
                ch_only = [p for p in places if p.get("address", {}).get("country") == "Switzerland"]
                new = sum(1 for p in ch_only if p["id"] not in seen)
                for p in ch_only:
                    seen[p["id"]] = p
                print(f"{len(places)} results ({len(ch_only)} CH), {new} new  (total unique: {len(seen)})")
            except Exception as e:
                print(f"ERROR: {e}")
            time.sleep(SLEEP)

    return seen


def map_services(services):
    """Translate P4N service codes to our amenity keys."""
    mapping = {
        "animaux":      "dogs",
        "douche":       "showers",
        "electricite":  "electricity",
        "wifi":         "wifi",
        "piscine":      "pool",
        "laverie":      "laundry",
        "wc_public":    "toilets",
        "point_eau":    "toilets",  # drinking water → treat as basic facility
    }
    return [mapping[s] for s in services if s in mapping]


def map_activities(activities):
    """Translate P4N activity codes to our amenity keys."""
    mapping = {
        "baignade":     "swimming",
        "jeux_enfants": "playground",
    }
    return [mapping[a] for a in activities if a in mapping]


# ── Merge logic ───────────────────────────────────────────────────────────────

def merge(osm_camps, p4n_places):
    """
    For each P4N place, find the nearest OSM camp within MATCH_KM.
    - Match found  → enrich OSM record (rating, url_p4n, fill missing amenities)
    - No match     → add P4N place as a new entry (may lack canton detail)
    Returns updated list.
    """
    enriched = 0
    added    = 0

    # Index original OSM camps only (snapshot before we add P4N-only entries)
    original_osm = list(osm_camps)
    osm_by_id = {c["id"]: c for c in original_osm}

    for pid, place in p4n_places.items():
        plat, plng = place["lat"], place["lng"]
        p4n_url    = f"https://park4night.com{place['url']}"
        rating     = place.get("rating")   # float, e.g. 4.54

        # Find nearest OSM camp (search only original OSM, not P4N-only additions)
        best_id, best_dist = None, float("inf")
        for camp in original_osm:
            d = haversine_km(plat, plng, camp["lat"], camp["lng"])
            if d < best_dist:
                best_dist, best_id = d, camp["id"]

        if best_dist <= MATCH_KM:
            # Enrich OSM record
            camp = osm_by_id[best_id]
            camp["url_p4n"]     = p4n_url
            camp["rating_p4n"]  = round(rating * 2, 1) if rating else None  # scale 0-5 → 0-10
            # Add any amenities detected by P4N not already in OSM
            p4n_amenities = list(set(map_services(place.get("services", [])) +
                                     map_activities(place.get("activities", []))))
            for a in p4n_amenities:
                if a not in camp["amenities"]:
                    camp["amenities"].append(a)
            enriched += 1
        else:
            # Add as new entry
            p4n_type = place.get("type", {}).get("code", "C")
            type_map = {"C": "campsite", "F": "farm", "ACC_G": "stellplatz", "ACC_P": "stellplatz"}
            amenities = list(set(map_services(place.get("services", [])) +
                                  map_activities(place.get("activities", []))))
            osm_camps.append({
                "id":               f"p4n-{pid}",
                "name":             place.get("name", ""),
                "lat":              plat,
                "lng":              plng,
                "canton":           canton_from_address(place.get("address", {})),
                "type":             type_map.get(p4n_type, "campsite"),
                "amenities":        amenities,
                "near_water":       None,
                "distance_water_m": None,
                "distance_town_m":  None,
                "rating_google":    None,
                "rating_p4n":       round(rating * 2, 1) if rating else None,
                "url_google":       None,
                "url_p4n":          p4n_url,
                "url_osm":          None,
                "website":          None,
            })
            added += 1

    return osm_camps, enriched, added


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("Park4Night scraper — Switzerland (Camping + Farm)")
    print("=" * 55)

    print("\n1. Fetching P4N data via grid…\n")
    p4n_places = fetch_grid()
    print(f"\n   → {len(p4n_places)} unique P4N places found\n")

    print("2. Loading existing OSM data…")
    with open(DATA_FILE, encoding="utf-8") as f:
        osm_camps = json.load(f)
    print(f"   → {len(osm_camps)} OSM camps loaded\n")

    print("3. Merging…")
    osm_camps, enriched, added = merge(osm_camps, p4n_places)
    print(f"   → Enriched: {enriched} OSM camps got P4N rating/url")
    print(f"   → Added:    {added} P4N-only places (not in OSM)\n")

    print("4. Saving data.json…")
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(osm_camps, f, ensure_ascii=False, indent=2)

    has_p4n = sum(1 for c in osm_camps if c.get("url_p4n"))
    has_rat  = sum(1 for c in osm_camps if c.get("rating_p4n"))
    print(f"   → {len(osm_camps)} total camps  |  {has_p4n} with P4N link  |  {has_rat} with P4N rating")
    print("\n✓ Done")


if __name__ == "__main__":
    main()
