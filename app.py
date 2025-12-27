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

# GitHub Release raw file (already correct)
GEOJSON_URL = "https://github.com/Unjhamasala/gdcr-api/releases/download/data-v1/Lothal_zones.geojson"

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

    print("✅ GeoJSON downloaded")

# -----------------------------
# LOAD GDCR JSON (SMALL FILE)
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
    Keeps memory usage LOW (Render-safe)
    """
    buffer_deg = 0.05  # ~5 km buffer (adjust if needed)

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

    if gdf.crs is None or gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(eps_

