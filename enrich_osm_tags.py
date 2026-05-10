#!/usr/bin/env python3
"""
Enrich data.json with additional OSM tags for OSM-sourced camps.

Fetches: website, phone, email, opening_hours, fee, description,
         operator, addr:city, addr:postcode, stars, capacity.

Queries Overpass in batches of 200 to stay well under timeout limits.
Run time: ~2 min for 745 OSM camps.

Dependencies: requests  (pip install requests)
"""

import json, re, time, sys
import requests

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
DATA_FILE    = "data.json"
HEADERS      = {"User-Agent": "camping-schweiz-enrich/1.0 (personal)"}
BATCH        = 200   # IDs per Overpass request
SLEEP        = 2.0   # seconds between batches

TAGS_TO_PULL = [
    "website", "contact:website",
    "phone", "contact:phone",
    "email", "contact:email",
    "opening_hours",
    "fee",                    # "yes"/"no"
    "charge",                 # price string e.g. "CHF 15"
    "description",
    "operator",
    "addr:city", "addr:postcode",
    "stars",                  # star rating
    "capacity",               # number of pitches
    "sanitary_dump_station",  # "yes"/"no"
    "drinking_water",
    "internet_access",
    "dog",                    # "yes"/"no"/"leashed"
]

# Map OSM tag → our data field name
FIELD_MAP = {
    "website":            "website",
    "contact:website":    "website",
    "phone":              "phone",
    "contact:phone":      "phone",
    "email":              "email",
    "contact:email":      "email",
    "opening_hours":      "opening_hours",
    "fee":                "fee",
    "charge":             "charge",
    "description":        "description",
    "operator":           "operator",
    "addr:city":          "city",
    "addr:postcode":      "postcode",
    "stars":              "stars",
    "capacity":           "capacity",
    "sanitary_dump_station": "dump_station",
    "drinking_water":     "drinking_water",
    "internet_access":    "internet_access",
    "dog":                "dogs_osm",
}


def parse_osm_id(camp):
    """Return (type, id) e.g. ('node', 12345) from url_osm, or None."""
    url = camp.get("url_osm", "") or ""
    m = re.search(r"openstreetmap\.org/(node|way|relation)/(\d+)", url)
    if m:
        return m.group(1), int(m.group(2))
    # Fallback: parse from id field "osm-12345"
    m2 = re.match(r"osm-(\d+)$", camp.get("id", ""))
    if m2:
        return "node", int(m2.group(1))
    return None


def fetch_tags(type_ids):
    """
    type_ids: list of (type, id) tuples, all same type recommended.
    Returns dict: {(type, id): tags_dict}
    """
    by_type = {}
    for t, i in type_ids:
        by_type.setdefault(t, []).append(i)

    parts = []
    for t, ids in by_type.items():
        id_list = ",".join(str(i) for i in ids)
        parts.append(f"{t}(id:{id_list});")

    query = f"[out:json][timeout:60];\n(\n  {'  '.join(parts)}\n);\nout tags;"
    r = requests.post(OVERPASS_URL, data={"data": query},
                      headers=HEADERS, timeout=90)
    r.raise_for_status()

    result = {}
    for el in r.json().get("elements", []):
        key = (el["type"], el["id"])
        result[key] = el.get("tags", {})
    return result


def merge_tags(camp, tags):
    """Apply fetched OSM tags to the camp dict. Returns True if anything changed."""
    changed = False
    for osm_tag, field in FIELD_MAP.items():
        val = tags.get(osm_tag)
        if not val:
            continue
        # Prefer longer existing value (don't overwrite good data with short)
        existing = camp.get(field)
        if existing and len(str(existing)) >= len(str(val)):
            continue
        camp[field] = val
        changed = True
    return changed


def main():
    data = json.load(open(DATA_FILE, encoding="utf-8"))

    osm_camps = [(i, c) for i, c in enumerate(data)
                 if c["id"].startswith("osm-")]
    print(f"OSM camps to process: {len(osm_camps)}")

    # Build (type, id) list
    id_map = {}   # (type, id) → list of data indices
    for i, camp in osm_camps:
        parsed = parse_osm_id(camp)
        if parsed:
            id_map.setdefault(parsed, []).append(i)

    type_id_list = list(id_map.keys())
    print(f"Unique OSM IDs parsed: {len(type_id_list)}")

    total_changed = 0
    batches = [type_id_list[x:x+BATCH] for x in range(0, len(type_id_list), BATCH)]

    for b_idx, batch in enumerate(batches, 1):
        print(f"  Batch {b_idx}/{len(batches)} ({len(batch)} IDs)…", flush=True)
        try:
            fetched = fetch_tags(batch)
        except Exception as e:
            print(f"  ERROR: {e} — skipping batch", file=sys.stderr)
            time.sleep(5)
            continue

        for key, tags in fetched.items():
            for idx in id_map.get(key, []):
                if merge_tags(data[idx], tags):
                    total_changed += 1

        json.dump(data, open(DATA_FILE, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        if b_idx < len(batches):
            time.sleep(SLEEP)

    # Summary
    fields = list(set(FIELD_MAP.values()))
    print(f"\n✓ Done  |  {total_changed} camps updated")
    print("\nField fill rates (OSM camps only):")
    for field in sorted(fields):
        n = sum(1 for _, c in osm_camps if c.get(field))
        if n:
            print(f"  {field:20} {n:4} / {len(osm_camps)}")


if __name__ == "__main__":
    main()
