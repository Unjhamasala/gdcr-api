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

# GitHub Release raw file
GEOJSON_URL = "https://github.com/Unjhamasala/gdcr-api/releases/download/data-v1/Lothal_zones.geojson"

GDCR_FILE = os.path.join(BASE_DIR, "gdcr_masterjson.json")
FIREBASE_KEY = os.path.join(BASE_DIR, "serviceAccountKey.json")

# -----------------------------
# DOWNLOAD GEOJSON IF NOT PRESENT
# -----------------------------
if not os.path.exists(LOCAL_GEOJSON):
    print("⬇️ Downloading GeoJSON...")
    r = requests.get(GEOJSON_URL)
    r.raise_for_status()

    with open(LOCAL_GEOJSON, "wb") as f:
        f.write(r.content)

    print("✅ GeoJSON downloaded")

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
# MEMORY-SAFE GIS LOADER
# -----------------------------
def load_zones_near_point(lat: float, lon: float):
    """
    Load only nearby polygons using bounding box
    (keeps RAM usage very low)
    """
    buffer_deg = 0.05  # approx 5 km

    bbox = (
        lon - buffer_deg,
        lat - buffer_deg,
        lon + buffer_deg,
        lat + buffer_deg,
    )

    gdf = gpd.read_file(
        LOCAL_GEOJSON,
        engine="fiona",
        bbox=bbox
    )

    # Ensure CRS
    if gdf.crs is None or gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)

    return gdf

# -----------------------------
# CORE GDCR LOGIC
# -----------------------------
def find_gdcr(lat: float, lon: float):
    point = Point(lon, lat)

    zones_gdf = load_zones_near_point(lat, lon)
    match = zones_gdf[zones_gdf.contains(point)]

    if match.empty:
        return {"error": "Point outside GDCR zones"}

    zone_col = None
    for col in match.columns:
        if col.strip().lower() in ["zoning", "zone", "zone_name"]:
            zone_col = col
            break

    if zone_col is None:
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
# API: LAT / LON
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
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
