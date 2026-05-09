#!/usr/bin/env python3
"""
Enrich data.json with near_water and distance_water_m.

One Overpass query downloads all Swiss lake polygons and river/stream lines.
For each camp we find the nearest point on the nearest water geometry and
set near_water = 'lake' | 'river' | None and distance_water_m = metres.

Dependencies: requests, shapely  (pip install requests shapely)
Runtime: ~2-3 min (Overpass fetch + per-camp nearest-point search)
"""

import json, math, sys
import requests
from shapely.geometry import Polygon, LineString, MultiPolygon, Point
from shapely.validation import make_valid
from shapely.ops import nearest_points

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
DATA_FILE    = "data.json"
LAKE_MAX_M   = 500   # within 500 m → near lake
RIVER_MAX_M  = 300   # within 300 m → near river
HEADERS      = {"User-Agent": "camping-schweiz-enrich/1.0 (personal)"}

OVERPASS_QUERY = """
[out:json][timeout:240];
area["ISO3166-1"="CH"][admin_level=2]->.ch;
(
  way["natural"="water"](area.ch);
  relation["natural"="water"](area.ch);
  way["waterway"~"^(river|stream|canal)$"](area.ch);
);
out geom;
"""

# ── Helpers ───────────────────────────────────────────────────────────────────

def haversine_m(lat1, lng1, lat2, lng2):
    R = 6371000
    r = math.pi / 180
    a = (math.sin((lat2-lat1)*r/2)**2
         + math.cos(lat1*r) * math.cos(lat2*r) * math.sin((lng2-lng1)*r/2)**2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def dist_to_geom_m(lat, lng, geom):
    """Metres from (lat,lng) to the nearest point on a shapely geometry."""
    p = Point(lng, lat)
    np_on_geom, _ = nearest_points(geom, p)
    return haversine_m(lat, lng, np_on_geom.y, np_on_geom.x)

def bbox_precheck(lat, lng, bounds, max_m):
    """Quick degree-space bounding box rejection before expensive nearest_points."""
    dlat = max_m / 111_000
    dlng = max_m / (111_000 * math.cos(math.radians(lat)))
    minx, miny, maxx, maxy = bounds
    return not (minx - dlng > lng or maxx + dlng < lng
                or miny - dlat > lat or maxy + dlat < lat)

# ── Fetch water data ──────────────────────────────────────────────────────────

def fetch_water():
    print("Fetching Swiss water features from Overpass…", flush=True)
    r = requests.post(OVERPASS_URL, data={"data": OVERPASS_QUERY},
                      headers=HEADERS, timeout=300)
    r.raise_for_status()
    elements = r.json()["elements"]
    print(f"  → {len(elements)} raw elements", flush=True)

    lakes, rivers = [], []
    for el in elements:
        tags = el.get("tags", {})
        is_waterway = "waterway" in tags

        # ── Relations (multipolygon lakes like Vierwaldstättersee, Lake Zurich…)
        if el["type"] == "relation":
            for member in el.get("members", []):
                if member.get("role") != "outer":
                    continue
                raw = member.get("geometry", [])
                if len(raw) < 4:
                    continue
                coords = [(g["lon"], g["lat"]) for g in raw]
                try:
                    poly = make_valid(Polygon(coords))
                    if poly.is_valid and not poly.is_empty and poly.area > 0:
                        lakes.append((poly, poly.bounds))
                except Exception:
                    pass
            continue

        # ── Ways
        raw = el.get("geometry", [])
        if len(raw) < 2:
            continue
        coords = [(g["lon"], g["lat"]) for g in raw]

        if not is_waterway and len(coords) >= 4:
            try:
                poly = make_valid(Polygon(coords))
                if poly.is_valid and not poly.is_empty and poly.area > 0:
                    lakes.append((poly, poly.bounds))
                    continue
            except Exception:
                pass

        # River / stream / canal — or degenerate polygon → treat as line
        try:
            line = LineString(coords)
            if line.is_valid and not line.is_empty:
                rivers.append((line, line.bounds))
        except Exception:
            pass

    print(f"  → {len(lakes)} lake polygons, {len(rivers)} river/stream lines", flush=True)
    return lakes, rivers

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    lakes, rivers = fetch_water()

    data = json.load(open(DATA_FILE, encoding="utf-8"))
    total  = len(data)
    n_lake = n_river = n_none = 0

    print(f"\nEnriching {total} camps…", flush=True)

    for i, camp in enumerate(data, 1):
        lat, lng = camp["lat"], camp["lng"]

        # ── Lakes ─────────────────────────────────────────────────────────────
        best_d = float("inf")
        for geom, bounds in lakes:
            if not bbox_precheck(lat, lng, bounds, LAKE_MAX_M):
                continue
            d = dist_to_geom_m(lat, lng, geom)
            if d < best_d:
                best_d = d

        if best_d <= LAKE_MAX_M:
            camp["near_water"]       = "lake"
            camp["distance_water_m"] = round(best_d)
            n_lake += 1
        else:
            # ── Rivers ────────────────────────────────────────────────────────
            best_d = float("inf")
            for geom, bounds in rivers:
                if not bbox_precheck(lat, lng, bounds, RIVER_MAX_M):
                    continue
                d = dist_to_geom_m(lat, lng, geom)
                if d < best_d:
                    best_d = d

            if best_d <= RIVER_MAX_M:
                camp["near_water"]       = "river"
                camp["distance_water_m"] = round(best_d)
                n_river += 1
            else:
                camp["near_water"]       = None
                camp["distance_water_m"] = None
                n_none += 1

        if i % 100 == 0 or i == total:
            print(f"  [{i:4}/{total}] lake={n_lake} river={n_river} none={n_none}", flush=True)
            json.dump(data, open(DATA_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print(f"\n✓ Done  |  lake: {n_lake}  river: {n_river}  no water: {n_none}")

if __name__ == "__main__":
    main()
