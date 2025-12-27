from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from shapely.geometry import Point, shape
import fiona
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
# PATHS
# -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_GEOJSON = os.path.join(BASE_DIR, "Lothal_zones.geojson")

GEOJSON_URL = "https://github.com/Unjhamasala/gdcr-api/releases/download/data-v1/Lothal_zones.geojson"
GDCR_FILE = os.path.join(BASE_DIR, "gdcr_masterjson.json")
FIREBASE_KEY = os.path.join(BASE_DIR, "serviceAccountKey.json")

# -----------------------------
# DOWNLOAD GEOJSON ONCE
# -----------------------------
if not os.path.exists(LOCAL_GEOJSON):
    print("Downloading GeoJSON...")
    r = requests.get(GEOJSON_URL)
    r.raise_for_status()
    with open(LOCAL_GEOJSON, "wb") as f:
        f.write(r.content)
    print("GeoJSON downloaded")

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
# CORE GDCR LOGIC (ULTRA LOW MEMORY)
# -----------------------------
def find_gdcr(lat: float, lon: float):
    point = Point(lon, lat)
    buffer_deg = 0.05  # ~5 km
    bbox = (
        lon - buffer_deg,
        lat - buffer_deg,
        lon + buffer_deg,
        lat + buffer_deg,
    )

    with fiona.open(LOCAL_GEOJSON, "r") as src:
        for feature in src.filter(bbox=bbox):
            geom = shape(feature["geometry"])
            if geom.contains(point):
                props = feature["properties"]

                zone_name = None
                for k in props:
                    if k.lower() in ["zoning", "zone", "zone_name"]:
                        zone_name = str(props[k]).strip()
                        break

                if not zone_name:
                    return {"error": "Zoning column not found"}

                for row in GDCR_DATA:
                    if str(row.get("zoning", "")).strip().lower() == zone_name.lower():
                        return {
                            "zone": zone_name,
                            "base_fsi": row.get("base_fsi"),
                            "max_height_m": row.get("max_height_m"),
                            "permissible_use": row.get("permissible_use"),
                        }

                return {"zone": zone_name, "error": "GDCR data not found"}

    return {"error": "Point outside GDCR zones"}

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

    result = find_gdcr(float(geo.latitude), float(geo.longitude))

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
# RUN
# -----------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
