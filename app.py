from fastapi import FastAPI, Body
from pydantic import BaseModel
import geopandas as gpd
from shapely.geometry import Point
import json
import os
import requests

# Firebase
import firebase_admin
from firebase_admin import credentials, firestore

# -----------------------------
# FASTAPI APP
# -----------------------------
app = FastAPI()

@app.get("/")
def root():
    return {"status": "ok"}

# -----------------------------
# FILE PATHS
# -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LOCAL_GPKG = os.path.join(BASE_DIR, "Lothal_zones_dissolved_4326.gpkg")
GDCR_FILE = os.path.join(BASE_DIR, "gdcr_masterjson.json")
FIREBASE_KEY = os.path.join(BASE_DIR, "serviceAccountKey.json")

# -----------------------------
# 🔥 DOWNLOAD GPKG IF NOT PRESENT
# -----------------------------
GPKG_URL = "https://drive.google.com/uc?id=https://drive.google.com/file/d/1X76hLR1p28mLmzxqn7yZUhSJEmh_J5cr/view?usp=drive_link"

if not os.path.exists(LOCAL_GPKG):
    print("📥 Downloading GPKG file...")
    r = requests.get(GPKG_URL)
    r.raise_for_status()
    with open(LOCAL_GPKG, "wb") as f:
        f.write(r.content)
    print("✅ GPKG downloaded")

# -----------------------------
# LOAD GIS + GDCR DATA
# -----------------------------
zones_gdf = gpd.read_file(LOCAL_GPKG).to_crs(epsg=4326)

with open(GDCR_FILE, "r", encoding="utf-8") as f:
    GDCR_DATA = json.load(f)

# -----------------------------
# FIREBASE INIT (ONCE)
# -----------------------------
if not firebase_admin._apps:
    cred = credentials.Certificate(FIREBASE_KEY)
    firebase_admin.initialize_app(cred)

db = firestore.client()

# -----------------------------
# CORE GDCR LOGIC
# -----------------------------
def find_gdcr(lat: float, lon: float):
    point = Point(lon, lat)  # IMPORTANT: (lon, lat)

    match = zones_gdf[zones_gdf.contains(point)]

    if match.empty:
        return {"error": "Point outside GDCR zones"}

    zone_col = None
    for col in match.columns:
        if col.strip().lower() in ["zoning", "zone", "zone_name"]:
            zone_col = col
            break

    if not zone_col:
        return {"error": "Zoning column not found"}

    zone_name = str(match.iloc[0][zone_col]).strip()

    gdcr_info = None
    for row in GDCR_DATA:
        if str(row.get("zoning", "")).strip().lower() == zone_name.lower():
            gdcr_info = row
            break

    if not gdcr_info:
        return {
            "zone": zone_name,
            "error": "GDCR data not found"
        }

    return {
        "zone": zone_name,
        "base_fsi": gdcr_info.get("base_fsi"),
        "max_height_m": gdcr_info.get("max_height_m"),
        "permissible_use": gdcr_info.get("permissible_use"),
    }

# -----------------------------
# SIMPLE LAT/LON API (FLUTTERFLOW)
# -----------------------------
@app.get("/gdcr-by-latlon")
def gdcr_by_latlon(lat: float, lon: float):
    return find_gdcr(lat, lon)

# -----------------------------
# REQUEST MODEL
# -----------------------------
class DocRequest(BaseModel):
    doc_id: str

# -----------------------------
# FIRESTORE BASED API
# -----------------------------
@app.post("/gdcr-by-doc")
def gdcr_by_doc(data: DocRequest = Body(...)):
    doc_ref = db.collection("properties").document(data.doc_id)
    doc = doc_ref.get()

    if not doc.exists:
        return {"error": "Document not found"}

    d = doc.to_dict()
    geo = d.get("lat_long_land") or d.get("lat_long_plot")

    if not geo:
        return {"error": "Lat/Long not found in document"}

    lat = float(geo.latitude)
    lon = float(geo.longitude)

    result = find_gdcr(lat, lon)

    if "error" in result:
        return result

    doc_ref.update({
        "zoning_admin": result["zone"],
        "fsi_admin": result.get("base_fsi"),
        "permissibleheight_admin": result.get("max_height_m"),
    })

    return {
        "status": "GDCR updated",
        "doc_id": data.doc_id,
        "zone": result["zone"]
    }

# -----------------------------
# RENDER ENTRYPOINT
# -----------------------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port)
