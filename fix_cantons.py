#!/usr/bin/env python3
import json, time, requests

HEADERS = {"User-Agent": "camping-schweiz-scraper/1.0 (personal)"}
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
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

def get_canton(lat, lng):
    try:
        r = requests.get(NOMINATIM_URL, params={
            "lat": lat, "lon": lng, "format": "json", "zoom": 6, "addressdetails": 1,
        }, headers=HEADERS, timeout=10)
        state = r.json().get("address", {}).get("state", "")
        for part in state.split("/"):
            code = CANTON_MAP.get(part.strip())
            if code:
                return code
        return "?"
    except Exception:
        return "?"

data = json.load(open("data.json"))
unknown = [c for c in data if c["canton"] == "?"]
print(f"Fixing {len(unknown)} unknown cantons...", flush=True)

fixed = 0
for i, camp in enumerate(unknown, 1):
    time.sleep(1.1)
    code = get_canton(camp["lat"], camp["lng"])
    camp["canton"] = code
    if code != "?":
        fixed += 1
    if i % 100 == 0 or i == len(unknown):
        print(f"  [{i}/{len(unknown)}] fixed: {fixed}", flush=True)
        json.dump(data, open("data.json", "w"), ensure_ascii=False, indent=2)

still = sum(1 for c in data if c["canton"] == "?")
print(f"\nDone. Fixed {fixed}/{len(unknown)} | Still unknown: {still}")
