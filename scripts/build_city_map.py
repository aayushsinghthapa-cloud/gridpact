"""Build the offline ward map for the city page from the DataMeet GeoJSON.

Usage:  python3 scripts/build_city_map.py path/to/BMC_Wards.geojson

Writes data/cities/mumbai_map.json: one simplified SVG path per BMC ward, in a
fixed 1000-unit-wide frame, plus the projection so facilities (lat/lon) can be
placed on the same map. Run once; the output is committed so the page works offline.

WHY simplify: the source file is ~1 MB of survey-grade coordinates; the page
needs ward shapes, not centimetre accuracy. Douglas-Peucker at ~25 m keeps
every coastline feature you can see at laptop size and shrinks it ~20x.
"""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "cities" / "mumbai_map.json"
TOL_M = 25.0
WIDTH = 1000.0


def simplify(pts, tol):
    if len(pts) < 3:
        return pts
    (x0, y0), (x1, y1) = pts[0], pts[-1]
    dx, dy = x1 - x0, y1 - y0
    norm = math.hypot(dx, dy) or 1e-12
    best, idx = -1.0, 0
    for i in range(1, len(pts) - 1):
        x, y = pts[i]
        d = abs(dy * x - dx * y + x1 * y0 - y1 * x0) / norm if (dx or dy) else math.hypot(x - x0, y - y0)
        if d > best:
            best, idx = d, i
    if best <= tol:
        return [pts[0], pts[-1]]
    return simplify(pts[: idx + 1], tol)[:-1] + simplify(pts[idx:], tol)


def main(src):
    gj = json.loads(Path(src).read_text())
    lat0 = 19.07
    kx = 111_320 * math.cos(math.radians(lat0))   # metres per degree of longitude here
    ky = 110_574                                  # metres per degree of latitude
    rings_all = []
    for f in gj["features"]:
        geom = f["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for poly in polys:
            for ring in poly:
                rings_all.extend(ring)
    lons = [p[0] for p in rings_all]
    lats = [p[1] for p in rings_all]
    lon_min, lon_max, lat_min, lat_max = min(lons), max(lons), min(lats), max(lats)
    w_m = (lon_max - lon_min) * kx
    h_m = (lat_max - lat_min) * ky
    scale = WIDTH / w_m                           # SVG units per metre
    height = h_m * scale

    def proj(lon, lat):
        return ((lon - lon_min) * kx * scale, (lat_max - lat) * ky * scale)

    wards = []
    for f in gj["features"]:
        name = f["properties"]["name"]
        geom = f["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        parts = []
        area_sum = cx_sum = cy_sum = 0.0
        for poly in polys:
            ring = poly[0]                        # outer ring; BMC wards have no meaningful holes
            pts = [proj(lon, lat) for lon, lat in ring]
            pts = simplify(pts, TOL_M * scale)
            if len(pts) < 4:
                continue
            parts.append("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + "Z")
            a = 0.0
            cx = cy = 0.0
            for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
                c = xa * yb - xb * ya
                a += c
                cx += (xa + xb) * c
                cy += (ya + yb) * c
            if abs(a) > 1e-9:
                area_sum += a / 2
                cx_sum += cx / 6
                cy_sum += cy / 6
        centroid = [round(cx_sum / area_sum, 1), round(cy_sum / area_sum, 1)] if area_sum else None
        wards.append({"ward": name, "d": "".join(parts), "centroid": centroid})
    out = {
        "source": "DataMeet Municipal_Spatial_Data, Mumbai/BMC_Wards.geojson (simplified to ~25 m)",
        "width": WIDTH, "height": round(height, 1),
        "projection": {"lon_min": lon_min, "lat_max": lat_max, "kx": kx, "ky": ky, "scale": scale},
        "wards": sorted(wards, key=lambda w: w["ward"]),
    }
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    print(f"wrote {OUT.relative_to(ROOT)}: {len(wards)} wards, {OUT.stat().st_size // 1024} KB, "
          f"frame {WIDTH:.0f} x {height:.0f}")


if __name__ == "__main__":
    main(sys.argv[1])
