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

app = FastAPI()

# ------------------
# ⇒ ADD CORS
# ------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # You can restrict later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"status": "ok"}

# ------------------
# FILEPATHS
# ------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_GPKG = os.path.join(BASE_DIR, "Lothal_zones_dissolved_4326.gpkg")
GDCR_FILE = os.path.join(BASE_DIR, "gdcr_masterjson.json")
FIREBASE_KEY = os.path.join(BASE_DIR, "serviceAccountKey.json")

# Download if missing
GPKG_URL = "https://drive.google.com/uc?id=1X76hLR1p28mLmzxqn7yZUhSJEmh_J5cr"
if not os.path.exists(LOCAL_GPKG):
    r = requests.get(GPKG_URL)
    with open(LOCAL_GPKG, "wb") as f:
        f.write(r.content)

zones_gdf = gpd.read_file(LOCAL_GPKG, engine="fiona").to_crs(epsg=4326)
with open(GDCR_FILE, "r", encoding="utf-8") as f:
    GDCR_DATA = json.load(f)

if not firebase_admin._apps:
    cred = credentials.Certificate(FIREBASE_KEY)
    firebase_admin.initialize_app(cred)

db = firestore.client()

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

    gdcr_info = None
    for row in GDCR_DATA:
        if str(row.get("zoning", "")).strip().lower() == zone_name.lower():
            gdcr_info = row
            break

    if not gdcr_info:
        return {"zone": zone_name, "error": "GDCR data not found"}

    return {
        "zone": zone_name,
        "base_fsi": gdcr_info.get("base_fsi"),
        "max_height_m": gdcr_info.get("max_height_m"),
        "permissible_use": gdcr_info.get("permissible_use"),
    }

@app.get("/gdcr-by-latlon")
def gdcr_by_latlon(lat: float, lon: float):
    return {
        "zone": "TEST_ZONE",
        "base_fsi": 1.8,
        "max_height_m": 15,
        "permissible_use": "Residential"
    }

class DocRequest(BaseModel):
    doc_id: str

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
