#!/usr/bin/env python3
"""Weekly push notification (ntfy) summarising new and updated dossiers.

Settings come from environment variables (GitHub secrets), so the home
location never appears in the public repository:
  HOME_LAT, HOME_LON   home coordinates (decimal degrees)
  NTFY_TOPIC           private ntfy topic name (long and random)
  MAP_URL              link to the map page (optional)
  RADIUS_M             watch radius in metres (default 500)
  DAYS                 look-back window in days (default 7)
Without NTFY_TOPIC the message is printed instead of sent (dry run).
"""
import json
import math
import os
import urllib.request
from datetime import date, timedelta
from pathlib import Path

DATA = Path(__file__).resolve().parent / "docs" / "data" / "permits.geojson"


def distance_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def short(text, n=90):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def build_message(features, home, radius, days, today=None):
    today = today or date.today()
    cutoff = (today - timedelta(days=days)).isoformat()
    near_new, near_upd, area_new = [], [], []
    for f in features:
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"]
        is_new = (p.get("filed") or "") >= cutoff
        is_upd = not is_new and (p.get("updated") or "") >= cutoff
        if not (is_new or is_upd):
            continue
        d = distance_m(home[0], home[1], lat, lon) if home else None
        if d is not None and d <= radius:
            (near_new if is_new else near_upd).append((d, p))
        elif is_new and p.get("category") in ("major", "future"):
            area_new.append(p)

    lines = []
    if near_new or near_upd:
        lines.append(f"Dans un rayon de {radius} m : {len(near_new)} nouveau(x), "
                     f"{len(near_upd)} mis à jour.")
        for d, p in sorted(near_new + near_upd, key=lambda x: x[0])[:6]:
            flag = "NOUVEAU" if (p.get("filed") or "") >= cutoff else p.get("status") or "mis à jour"
            lines.append(f"• {p['type']} à {int(round(d, -1))} m ({flag}) : {short(p['description'])}")
    else:
        lines.append(f"Rien de nouveau dans un rayon de {radius} m.")

    if area_new:
        demolitions = sum(1 for p in area_new if p["type"] == "M")
        future = sum(1 for p in area_new if p["category"] == "future")
        lines.append(f"Communes : {len(area_new)} projet(s) important(s) "
                     f"({demolitions} démolition(s), {future} demande(s) préalable(s)/renseignement).")
        for p in area_new[:3]:
            lines.append(f"• {p['type']} : {short(p['description'], 70)}")

    urgent = bool(near_new)
    title = "Permis : nouveau près de chez vous" if urgent else "Permis : résumé de la semaine"
    return title, "\n".join(lines), urgent


def main():
    features = json.loads(DATA.read_text(encoding="utf-8"))["features"]
    try:
        home = (float(os.environ["HOME_LAT"]), float(os.environ["HOME_LON"]))
    except (KeyError, ValueError):
        home = None
        print("HOME_LAT/HOME_LON not set: only the commune summary will be sent.")
    radius = int(os.environ.get("RADIUS_M") or 500)
    days = int(os.environ.get("DAYS") or 7)
    title, message, urgent = build_message(features, home, radius, days)

    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print(f"[dry run]\n{title}\n{message}")
        return
    payload = {
        "topic": topic,
        "title": title,
        "message": message,
        "priority": 4 if urgent else 3,
        "tags": ["building_construction"],
    }
    map_url = os.environ.get("MAP_URL", "").strip()
    if map_url:
        payload["click"] = map_url
        payload["actions"] = [{"action": "view", "label": "Ouvrir la carte", "url": map_url}]
    req = urllib.request.Request(
        "https://ntfy.sh/",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        print(f"Push sent ({resp.status}).")


if __name__ == "__main__":
    main()
