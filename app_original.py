from fastapi import FastAPI, HTTPException
import geopandas as gpd
from shapely.geometry import Point
from shapely.prepared import prep
import json
import os

app = FastAPI(title="GDCR API")

# -----------------------------
# FILE PATHS
# -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

GPKG_FILE = os.path.join(BASE_DIR, "Lothal_zones_dissolved_4326.gpkg")
GDCR_JSON = os.path.join(BASE_DIR, "gdcr_masterjson.json")

# -----------------------------
# LOAD GIS DATA
# -----------------------------
gdf = gpd.read_file(GPKG_FILE)

# Force CRS safety
if gdf.crs is None or gdf.crs.to_epsg() != 4326:
    gdf = gdf.to_crs(epsg=4326)

# Prepare geometries (IMPORTANT)
gdf["prepared_geom"] = gdf["geometry"].apply(prep)

# -----------------------------
# LOAD GDCR RULES
# -----------------------------
with open(GDCR_JSON, "r", encoding="utf-8") as f:
    gdcr_list = json.load(f)

# Convert GDCR list → dictionary (zone name → rule)
gdcr_dict = {}
for item in gdcr_list:
    key = (
        item.get("zoning")
        or item.get("zone")
        or item.get("zone_name")
    )
    if key:
        gdcr_dict[key.strip().lower()] = item

# -----------------------------
# API ENDPOINT
# -----------------------------
@app.get("/gdcr")
def get_gdcr(lat: float, lon: float):

    point = Point(lon, lat)

    for _, row in gdf.iterrows():
        if row["prepared_geom"].contains(point):

            zone_name = str(row["Zoning"]).strip()
            zone_key = zone_name.lower()

            gdcr = gdcr_dict.get(zone_key)

            return {
                "latitude": lat,
                "longitude": lon,
                "zone": zone_name,
                "gdcr": gdcr if gdcr else "No GDCR rule found",
            }

    raise HTTPException(
        status_code=404,
        detail="Point is outside all GDCR zones"
    )
