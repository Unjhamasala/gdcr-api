from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware
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

# -----------------------------
# CORS (FlutterFlow safe)
# -----------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"status": "ok"}

# -----------------------------
# FILE PATHS
# -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LOCAL_GEOJSON = os.path.join(BASE_DIR, "Lothal_zones.geojson")

# ✅ GITHUB RELEASE RAW FILE (CHANGE USERNAME ONLY)
GEOJSON_URL = (
    "https://github.com/Unjhamasala/gdcr-api/"
    "releases/download/data-v1/Lothal_zones.geojson"
)

GDCR_FILE = os.path.join(BASE_DIR, "gdcr_masterjson.json")
FIREBASE_KEY = os.path.join(BASE_DIR, "serviceAccountKey.json")

# -----------------------------
# DOWNLOAD GEOJSON IF NOT PRESENT
# -----------------------------
if not os.path.exists(LOCAL_GEOJSON):
    print("⬇️ Downloading GeoJSON from GitHub Release...")
    r = requests.get(GEOJSON_URL, stream=True)
    r.raise_for_status()

    with open(LOCAL_GEOJSON, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)

    print("✅ GeoJSON downloaded successfully")

# -----------------------------
# LOAD GIS DATA (FORCE FIONA)
# -----------------------------
zones_gdf = gpd.read_file(LOCAL_GEOJSON, engine="fiona")

# Ensure CRS
if zones_gdf.crs is None or zones_gdf.crs.to_epsg() != 4326:
    zones_gdf = zones_gdf.to_crs(epsg=4326)

# -----------------------------
# LOAD GDCR JSON
# -----------------------------
with open(GDCR_FILE, "r", encoding="utf-8") as f:
    GDCR_DATA = json.load(f)

# -----------------------------
# FIREBASE INIT
# -----------------------------
if not firebase_admin._apps:
    cred = credentials.Certificate(FIREBASE_KEY)
    firebase_admin.initialize_app(cred)

db = firestore.client()

# -----------------------------
# CORE GDCR LOGIC
# -----------------------------
def find_gdcr(lat: float, lon: float):
    point = Point(lon, lat)
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

    for row in GDCR_DATA:
        if str(row.get("zoning", "")).strip().lower() == zone_name.lower():
            return {
                "zone": zone_name,
                "base_fsi": row.get("base_fsi"),
                "max_height_m": row.get("max_height_m"),
                "permissible_use": row.get("permissible_use"),
            }

    return {"zone": zone_name, "error": "GDCR data not found"}

# -----------------------------
# API: LAT/LON
# -----------------------------
@app.get("/gdcr-by-latlon")
def gdcr_by_latlon(lat: float, lon: float):
    return find_gdcr(lat, lon)

# -----------------------------
# FIRESTORE REQUEST MODEL
# -----------------------------
class DocRequest(BaseModel):
    doc_id: str

# -----------------------------
# API: FIRESTORE DOC
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
# RUN (RENDER SAFE)
# -----------------------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
