#!/usr/bin/env python3
"""Download Geneva building-permit dossiers (SITG open data) for the east-lake
communes and save them as a GeoJSON file the map page reads.

Source layer: SIT_AUTOR_DOSSIER, Office des autorisations de construire,
published by the SITG under an open licence (attribution required).
This layer has no names of applicants or owners.
"""
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

LAYER = "https://ge.ch/terags/rest/services/WS_LOCALISATION/MapServer/9/query"

# Lon/lat box covering Cologny, Vandoeuvres, Collonge-Bellerive, Corsier and
# Anieres (it also clips a little of Choulex, Meinier and Hermance).
BBOX = (6.150, 46.198, 6.262, 46.292)

MONTHS_BACK = 24
PAGE = 1000
FIELDS = [
    "ID_DOSSIER", "TYPE_DOSSIER", "TYPE_OPERATION", "NOM_DOSSIER", "STATUT",
    "DATE_DEPOT", "DATE_MAJ_2", "DESCRIPTION", "OPERATION", "LIEN_SAD",
]
OUT_DIR = Path(__file__).resolve().parent / "docs" / "data"

TYPE_LABELS = {
    "DD": "Demande définitive",
    "DP": "Demande préalable",
    "DR": "Demande de renseignement",
    "APA": "Procédure accélérée",
    "APAT": "Annonce de travaux",
    "M": "Démolition",
    "MPA": "Modification (procédure accélérée)",
}
MAJOR_TYPES = {"DD", "M"}
FUTURE_TYPES = {"DP", "DR"}
MAJOR_WORDS = (
    "démolition", "demolition", "immeuble", "logements", "habitat groupé",
    "villas", "surélévation", "surelevation", "parking souterrain",
)


def query(params):
    url = LAYER + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url, headers={"User-Agent": "permit-watch (personal, non-commercial)"}
    )
    last_error = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.load(resp)
            if isinstance(data, dict) and "error" in data:
                raise RuntimeError(f"Server error: {data['error']}")
            return data
        except Exception as exc:  # network hiccups are common; retry
            last_error = exc
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Query failed after retries: {last_error}")


def fetch_all(where):
    features, offset = [], 0
    while True:
        data = query({
            "where": where,
            "geometry": ",".join(str(v) for v in BBOX),
            "geometryType": "esriGeometryEnvelope",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": ",".join(FIELDS),
            "returnGeometry": "true",
            "outSR": 4326,
            "orderByFields": "OBJECTID",
            "resultOffset": offset,
            "resultRecordCount": PAGE,
            "f": "geojson",
        })
        batch = data.get("features", [])
        features.extend(batch)
        more = data.get("exceededTransferLimit") or (
            data.get("properties") or {}
        ).get("exceededTransferLimit")
        if not batch or (len(batch) < PAGE and not more):
            return features
        offset += len(batch)


def to_iso(ms):
    if ms in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError):
        return None


def dossier_type(p):
    t = (p.get("TYPE_DOSSIER") or "").strip().upper()
    if t:
        return t
    for key in ("NOM_DOSSIER", "ID_DOSSIER"):
        token = str(p.get(key) or "").strip().split(" ")[0].split("/")[0].upper()
        if token.isalpha():
            return token
    return "?"


def category(t, description):
    if t in MAJOR_TYPES:
        return "major"
    if t in FUTURE_TYPES:
        return "future"
    text = (description or "").lower()
    if any(w in text for w in MAJOR_WORDS):
        return "major"
    return "minor"


def normalise(raw, since_iso):
    by_id = {}
    for f in raw:
        geom = f.get("geometry")
        if not geom or geom.get("type") != "Point":
            continue
        p = f.get("properties") or {}
        filed = to_iso(p.get("DATE_DEPOT"))
        updated = to_iso(p.get("DATE_MAJ_2"))
        if max(filed or "", updated or "") < since_iso:
            continue
        t = dossier_type(p)
        desc = (p.get("DESCRIPTION") or p.get("OPERATION") or "").strip()
        link = (p.get("LIEN_SAD") or "").strip()
        if link and not link.startswith("http"):
            link = "https://" + link.lstrip("/")
        key = str(p.get("ID_DOSSIER") or p.get("NOM_DOSSIER") or len(by_id))
        item = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [round(c, 6) for c in geom["coordinates"][:2]],
            },
            "properties": {
                "id": key,
                "name": (p.get("NOM_DOSSIER") or key).strip(),
                "type": t,
                "typeLabel": TYPE_LABELS.get(t, t),
                "category": category(t, desc),
                "status": (p.get("STATUT") or "").strip(),
                "filed": filed,
                "updated": updated,
                "description": desc,
                "link": link,
            },
        }
        old = by_id.get(key)
        if old is None or (updated or "") > (old["properties"]["updated"] or ""):
            by_id[key] = item
    return sorted(
        by_id.values(),
        key=lambda f: f["properties"]["filed"] or "",
        reverse=True,
    )


def main():
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=30 * MONTHS_BACK)).date().isoformat()
    where = f"DATE_DEPOT >= DATE '{since}' OR DATE_MAJ_2 >= DATE '{since}'"
    try:
        raw = fetch_all(where)
    except RuntimeError as exc:
        print(f"Date filter refused ({exc}); fetching everything in the box instead.")
        raw = fetch_all("1=1")
    features = normalise(raw, since)
    if not features:
        raise SystemExit("No dossiers returned: refusing to overwrite the map data.")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "permits.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features},
                   ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    (OUT_DIR / "meta.json").write_text(
        json.dumps({"updated": now.isoformat(timespec="minutes"),
                    "count": len(features), "since": since}),
        encoding="utf-8",
    )
    print(f"Saved {len(features)} dossiers (raw {len(raw)}) filed or updated since {since}.")


if __name__ == "__main__":
    main()
