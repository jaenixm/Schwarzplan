"""
Schwarzplan (Figure-Ground Diagram) Generation Engine.

Provides pure-Python spatial extraction and exact-scale architectural rendering:
- Direct OpenStreetMap Overpass API extraction with automatic mirror fallbacks & caching
- Multi-layer urban context: Buildings (Schwarzplan), Waterways (Blauplan), and Greenery/Parks (Grünplan)
- Sub-millimeter accurate WGS84 Transverse Mercator metric projection
- Exact-scale Vector PDF, SVG, and CAD DXF generation with courtyard (inner hole) cutouts
- 100% pure Python with zero native C-extension dependencies (no GDAL, GEOS, PROJ.4 required)
"""

import os
import ssl
import json
import math
import hashlib
import tempfile
import urllib.request
import urllib.parse
from typing import Callable, Optional, Dict, Any, List, Tuple


# ── Paper Sizes (Dimensions in Millimeters) ──────────────────────────
PAPER_SIZES: Dict[str, Dict[str, float]] = {
    "A4 Landscape": {"width_mm": 297.0, "height_mm": 210.0},
    "A4 Portrait": {"width_mm": 210.0, "height_mm": 297.0},
    "A3 Landscape": {"width_mm": 420.0, "height_mm": 297.0},
    "A3 Portrait": {"width_mm": 297.0, "height_mm": 420.0},
    "A2 Landscape": {"width_mm": 594.0, "height_mm": 420.0},
    "A2 Portrait": {"width_mm": 420.0, "height_mm": 594.0},
    "A1 Landscape": {"width_mm": 841.0, "height_mm": 594.0},
    "A1 Portrait": {"width_mm": 594.0, "height_mm": 841.0},
    "A0 Landscape": {"width_mm": 1189.0, "height_mm": 841.0},
    "A0 Portrait": {"width_mm": 841.0, "height_mm": 1189.0},
    "Square 300×300": {"width_mm": 300.0, "height_mm": 300.0},
    "Square 500×500": {"width_mm": 500.0, "height_mm": 500.0},
}

# Common Architectural Scale Options
SCALE_OPTIONS = [200, 500, 1000, 2000, 2500, 5000, 10000, 20000, 25000]

# Supported Export Formats
SUPPORTED_FORMATS = [".pdf", ".svg", ".dxf"]

# Overpass API Mirror Endpoints for High Availability
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

# Standard conversion: 72 PostScript points per inch (25.4 mm)
MM_TO_PT = 72.0 / 25.4


def hex_to_rgb(hex_str: str) -> Tuple[float, float, float]:
    """Converts a hex color code like #C5DCE8 to normalized (0.0-1.0) RGB float tuple."""
    h = hex_str.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return (0.0, 0.0, 0.0)
    try:
        r = int(h[0:2], 16) / 255.0
        g = int(h[2:4], 16) / 255.0
        b = int(h[4:6], 16) / 255.0
        return (round(r, 3), round(g, 3), round(b, 3))
    except Exception:
        return (0.0, 0.0, 0.0)


# ── Cache Management ────────────────────────────────────────────────
def _get_cache_dir() -> str:
    """Returns directory path for caching raw OSM responses."""
    try:
        base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
        os.makedirs(base_dir, exist_ok=True)
        return base_dir
    except Exception:
        temp_dir = os.path.join(tempfile.gettempdir(), "schwarzplan_cache")
        os.makedirs(temp_dir, exist_ok=True)
        return temp_dir


def _cache_key(lat: float, lon: float, radius: float, buildings: bool, water: bool, greenery: bool) -> str:
    key_str = f"{lat:.5f}_{lon:.5f}_{radius:.1f}_b{int(buildings)}_w{int(water)}_g{int(greenery)}"
    return hashlib.sha1(key_str.encode("utf-8")).hexdigest() + ".json"


# ── Overpass API Fetcher ────────────────────────────────────────────
def fetch_osm_layers(
    center_lat: float,
    center_lon: float,
    radius_m: float,
    include_buildings: bool = True,
    include_water: bool = False,
    include_greenery: bool = False,
    on_progress: Optional[Callable[[str, float], None]] = None,
) -> Dict[str, Any]:
    """
    Fetches OSM layers: buildings, waterbodies, and/or greenery.
    """
    cache_path = os.path.join(
        _get_cache_dir(),
        _cache_key(center_lat, center_lon, radius_m, include_buildings, include_water, include_greenery),
    )

    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if on_progress:
                    on_progress("Loaded geometry from local cache…", 0.3)
                return data
        except Exception:
            pass

    if on_progress:
        layers_desc = []
        if include_buildings:
            layers_desc.append("Buildings")
        if include_water:
            layers_desc.append("Water")
        if include_greenery:
            layers_desc.append("Greenery")
        on_progress(f"Querying OpenStreetMap ({', '.join(layers_desc)}, ~{radius_m:.0f}m radius)…", 0.15)

    subqueries = []
    if include_buildings:
        subqueries.extend([
            f'way["building"](around:{radius_m:.1f},{center_lat:.6f},{center_lon:.6f});',
            f'relation["building"]["type"="multipolygon"](around:{radius_m:.1f},{center_lat:.6f},{center_lon:.6f});',
        ])

    if include_water:
        subqueries.extend([
            f'way["natural"="water"](around:{radius_m:.1f},{center_lat:.6f},{center_lon:.6f});',
            f'relation["natural"="water"]["type"="multipolygon"](around:{radius_m:.1f},{center_lat:.6f},{center_lon:.6f});',
            f'way["waterway"~"riverbank|dock|canal|river"](around:{radius_m:.1f},{center_lat:.6f},{center_lon:.6f});',
            f'relation["waterway"~"riverbank|dock|canal|river"]["type"="multipolygon"](around:{radius_m:.1f},{center_lat:.6f},{center_lon:.6f});',
            f'way["water"](around:{radius_m:.1f},{center_lat:.6f},{center_lon:.6f});',
            f'relation["water"]["type"="multipolygon"](around:{radius_m:.1f},{center_lat:.6f},{center_lon:.6f});',
            f'way["landuse"~"basin|reservoir"](around:{radius_m:.1f},{center_lat:.6f},{center_lon:.6f});',
        ])

    if include_greenery:
        subqueries.extend([
            f'way["leisure"~"park|garden|pitch|recreation_ground"](around:{radius_m:.1f},{center_lat:.6f},{center_lon:.6f});',
            f'relation["leisure"~"park|garden|pitch|recreation_ground"]["type"="multipolygon"](around:{radius_m:.1f},{center_lat:.6f},{center_lon:.6f});',
            f'way["landuse"~"forest|grass|meadow|village_green|cemetery|allotments"](around:{radius_m:.1f},{center_lat:.6f},{center_lon:.6f});',
            f'relation["landuse"~"forest|grass|meadow|village_green|cemetery|allotments"]["type"="multipolygon"](around:{radius_m:.1f},{center_lat:.6f},{center_lon:.6f});',
            f'way["natural"="wood"](around:{radius_m:.1f},{center_lat:.6f},{center_lon:.6f});',
            f'relation["natural"="wood"]["type"="multipolygon"](around:{radius_m:.1f},{center_lat:.6f},{center_lon:.6f});',
        ])

    if not subqueries:
        return {"elements": []}

    overpass_query = f"""[out:json][timeout:50];
(
  {chr(10).join('  ' + q for q in subqueries)}
);
out body;
>;
out skel qt;"""

    # 1. Try requests if available
    try:
        import requests
        for endpoint in OVERPASS_ENDPOINTS:
            try:
                resp = requests.post(
                    endpoint,
                    data={"data": overpass_query},
                    headers={"User-Agent": "SchwarzplanApp/2.0"},
                    timeout=40,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    try:
                        with open(cache_path, "w", encoding="utf-8") as cf:
                            json.dump(data, cf)
                    except Exception:
                        pass
                    return data
            except Exception:
                continue
    except ImportError:
        pass

    # 2. Fallback to urllib.request with robust SSL context
    encoded_data = urllib.parse.urlencode({"data": overpass_query}).encode("utf-8")
    headers = {
        "User-Agent": "SchwarzplanApp/2.0",
        "Accept": "*/*",
    }

    last_error = None
    for endpoint in OVERPASS_ENDPOINTS:
        for verify_ssl in [True, False]:
            try:
                if verify_ssl:
                    try:
                        import certifi
                        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
                    except Exception:
                        ssl_ctx = ssl.create_default_context()
                else:
                    ssl_ctx = ssl._create_unverified_context()

                req = urllib.request.Request(endpoint, data=encoded_data, headers=headers)
                with urllib.request.urlopen(req, timeout=40, context=ssl_ctx) as resp:
                    if resp.status == 200:
                        raw_content = resp.read().decode("utf-8")
                        data = json.loads(raw_content)
                        try:
                            with open(cache_path, "w", encoding="utf-8") as cf:
                                json.dump(data, cf)
                        except Exception:
                            pass
                        return data
            except Exception as err:
                last_error = err
                continue

    raise RuntimeError(f"All OpenStreetMap Overpass mirrors failed. Last error: {last_error}")


# ── Geometry Parser ─────────────────────────────────────────────────
def parse_osm_layers(osm_data: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Extracts closed polygons for:
    - 'buildings'
    - 'water'
    - 'greenery'
    Handles multipolygon inner rings (courtyards/islands).
    """
    nodes: Dict[int, Tuple[float, float]] = {}
    ways: Dict[int, Dict[str, Any]] = {}
    relations: List[Dict[str, Any]] = []

    for element in osm_data.get("elements", []):
        el_type = element.get("type")
        el_id = element.get("id")
        if el_type == "node":
            nodes[el_id] = (element.get("lat"), element.get("lon"))
        elif el_type == "way":
            ways[el_id] = {
                "nodes": element.get("nodes", []),
                "tags": element.get("tags", {}),
            }
        elif el_type == "relation":
            relations.append(element)

    def is_building(tags: Dict[str, str]) -> bool:
        return "building" in tags

    def is_water(tags: Dict[str, str]) -> bool:
        if tags.get("natural") == "water" or "waterway" in tags or "water" in tags:
            return True
        if tags.get("landuse") in ("basin", "reservoir"):
            return True
        return False

    def is_greenery(tags: Dict[str, str]) -> bool:
        if tags.get("leisure") in ("park", "garden", "pitch", "recreation_ground", "nature_reserve"):
            return True
        if tags.get("landuse") in ("forest", "grass", "meadow", "village_green", "cemetery", "allotments", "recreation_ground"):
            return True
        if tags.get("natural") in ("wood", "grassland", "scrub", "heath"):
            return True
        return False

    buildings: List[Dict[str, Any]] = []
    water: List[Dict[str, Any]] = []
    greenery: List[Dict[str, Any]] = []
    used_ways = set()

    # 1. Process Relations
    for rel in relations:
        tags = rel.get("tags", {})
        if tags.get("type") == "multipolygon":
            category = None
            if is_building(tags):
                category = buildings
            elif is_water(tags):
                category = water
            elif is_greenery(tags):
                category = greenery

            if category is not None:
                outers: List[List[Tuple[float, float]]] = []
                inners: List[List[Tuple[float, float]]] = []
                for member in rel.get("members", []):
                    m_type = member.get("type")
                    m_ref = member.get("ref")
                    m_role = member.get("role", "outer")
                    if m_type == "way" and m_ref in ways:
                        used_ways.add(m_ref)
                        coords = [nodes[nid] for nid in ways[m_ref]["nodes"] if nid in nodes]
                        if len(coords) >= 3:
                            if m_role == "inner":
                                inners.append(coords)
                            else:
                                outers.append(coords)
                for outer in outers:
                    category.append({"outer": outer, "inners": inners})

    # 2. Process Ways
    for way_id, way in ways.items():
        if way_id in used_ways:
            continue
        tags = way.get("tags", {})
        category = None
        if is_building(tags):
            category = buildings
        elif is_water(tags):
            category = water
        elif is_greenery(tags):
            category = greenery

        if category is not None:
            node_ids = way.get("nodes", [])
            if len(node_ids) >= 4 and node_ids[0] == node_ids[-1]:
                pts = [nodes[nid] for nid in node_ids if nid in nodes]
                if len(pts) >= 4:
                    category.append({"outer": pts, "inners": []})

    return {
        "buildings": buildings,
        "water": water,
        "greenery": greenery,
    }


# ── Ellipsoidal Transverse Mercator Metric Projection ────────────────
def latlon_to_metric(
    lat: float, lon: float, center_lat: float, center_lon: float
) -> Tuple[float, float]:
    """
    Projects latitude/longitude (WGS84) to metric coordinates (x, y in meters)
    relative to center_lat, center_lon using high-precision Transverse Mercator.
    Guarantees sub-millimeter precision for exact architectural scaling.
    """
    a = 6378137.0
    f = 1 / 298.257223563
    b = a * (1 - f)
    e2 = (a**2 - b**2) / (a**2)
    e_prime2 = (a**2 - b**2) / (b**2)

    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    lon_origin_rad = math.radians(center_lon)

    sin_lat = math.sin(lat_rad)
    cos_lat = math.cos(lat_rad)
    tan_lat = math.tan(lat_rad)

    N = a / math.sqrt(1 - e2 * sin_lat**2)
    T = tan_lat**2
    C = e_prime2 * cos_lat**2
    A = (lon_rad - lon_origin_rad) * cos_lat

    M = a * (
        (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256) * lat_rad
        - (3 * e2 / 8 + 3 * e2**2 / 32 + 45 * e2**3 / 1024) * math.sin(2 * lat_rad)
        + (15 * e2**2 / 256 + 45 * e2**3 / 1024) * math.sin(4 * lat_rad)
        - (35 * e2**3 / 3072) * math.sin(6 * lat_rad)
    )

    x = N * (
        A
        + (1 - T + C) * A**3 / 6
        + (5 - 18 * T + T**2 + 72 * C - 58 * e_prime2) * A**5 / 120
    )
    y = M + N * tan_lat * (
        A**2 / 2
        + (5 - T + 9 * C + 4 * C**2) * A**4 / 24
        + (61 - 58 * T + T**2 + 600 * C - 330 * e_prime2) * A**6 / 720
    )

    lat0_rad = math.radians(center_lat)
    M0 = a * (
        (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256) * lat0_rad
        - (3 * e2 / 8 + 3 * e2**2 / 32 + 45 * e2**3 / 1024) * math.sin(2 * lat0_rad)
        + (15 * e2**2 / 256 + 45 * e2**3 / 1024) * math.sin(4 * lat0_rad)
        - (35 * e2**3 / 3072) * math.sin(6 * lat0_rad)
    )

    return x, y - M0


# ── Renderers ────────────────────────────────────────────────────────
def render_pdf(
    layers: Dict[str, List[Dict[str, Any]]],
    center_lat: float,
    center_lon: float,
    paper_w_mm: float,
    paper_h_mm: float,
    margin_mm: float,
    real_w_m: float,
    real_h_m: float,
    output_path: str,
    water_rgb: Tuple[float, float, float] = (0.77, 0.86, 0.91),
    greenery_rgb: Tuple[float, float, float] = (0.86, 0.91, 0.85),
    building_rgb: Tuple[float, float, float] = (0.0, 0.0, 0.0),
    background_rgb: Tuple[float, float, float] = (1.0, 1.0, 1.0),
):
    """
    Renders an exact-scale multi-layer vector PDF.
    """
    paper_w_pt = paper_w_mm * MM_TO_PT
    paper_h_pt = paper_h_mm * MM_TO_PT
    margin_pt = margin_mm * MM_TO_PT

    map_w_mm = paper_w_mm - 2 * margin_mm
    map_h_mm = paper_h_mm - 2 * margin_mm
    map_w_pt = map_w_mm * MM_TO_PT
    map_h_pt = map_h_mm * MM_TO_PT

    x_min = -real_w_m / 2.0
    y_min = -real_h_m / 2.0

    def m_to_pt(xm: float, ym: float) -> Tuple[float, float]:
        u = margin_pt + ((xm - x_min) / real_w_m) * map_w_pt
        v = margin_pt + ((ym - y_min) / real_h_m) * map_h_pt
        return u, v

    pdf_cmds: List[str] = []
    # 1. Background paper
    br, bg, bb = background_rgb
    pdf_cmds.append(f"{br:.3f} {bg:.3f} {bb:.3f} rg 0 0 {paper_w_pt:.2f} {paper_h_pt:.2f} re f")

    # 2. Clipping mask
    pdf_cmds.append("q")
    pdf_cmds.append(f"{margin_pt:.2f} {margin_pt:.2f} {map_w_pt:.2f} {map_h_pt:.2f} re W n")

    def draw_layer_polygons(polygons: List[Dict[str, Any]], color: Tuple[float, float, float]):
        if not polygons:
            return
        cr, cg, cb = color
        pdf_cmds.append(f"{cr:.3f} {cg:.3f} {cb:.3f} rg")
        for poly in polygons:
            path_tokens = []
            first = True
            for lat, lon in poly["outer"]:
                xm, ym = latlon_to_metric(lat, lon, center_lat, center_lon)
                u, v = m_to_pt(xm, ym)
                path_tokens.append(f"{u:.2f} {v:.2f} {'m' if first else 'l'}")
                first = False
            path_tokens.append("h")

            for inner in poly.get("inners", []):
                first = True
                for lat, lon in inner:
                    xm, ym = latlon_to_metric(lat, lon, center_lat, center_lon)
                    u, v = m_to_pt(xm, ym)
                    path_tokens.append(f"{u:.2f} {v:.2f} {'m' if first else 'l'}")
                    first = False
                path_tokens.append("h")

            path_tokens.append("f*")
            pdf_cmds.append(" ".join(path_tokens))

    # Layer order: Water -> Greenery -> Buildings on top
    draw_layer_polygons(layers.get("water", []), water_rgb)
    draw_layer_polygons(layers.get("greenery", []), greenery_rgb)
    draw_layer_polygons(layers.get("buildings", []), building_rgb)

    # 3. Outer border frame
    fr, fg, fb = (0.0, 0.0, 0.0) if background_rgb[0] > 0.5 else (1.0, 1.0, 1.0)
    pdf_cmds.append(f"{fr:.3f} {fg:.3f} {fb:.3f} RG")
    pdf_cmds.append("0.5 w")
    pdf_cmds.append(f"{margin_pt:.2f} {margin_pt:.2f} {map_w_pt:.2f} {map_h_pt:.2f} re S")

    pdf_cmds.append("Q")

    content_stream = "\n".join(pdf_cmds).encode("utf-8")
    stream_len = len(content_stream)

    pdf_objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {paper_w_pt:.2f} {paper_h_pt:.2f}] "
            f"/Contents 4 0 R /Resources << >> >>"
        ).encode("utf-8"),
        f"<< /Length {stream_len} >>\nstream\n".encode("utf-8")
        + content_stream
        + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, obj in enumerate(pdf_objects, 1):
        offsets.append(len(out))
        out.extend(f"{i} 0 obj\n".encode("utf-8"))
        out.extend(obj)
        out.extend(b"\nendobj\n")

    xref_offset = len(out)
    out.extend(f"xref\n0 {len(pdf_objects) + 1}\n0000000000 65535 f \n".encode("utf-8"))
    for off in offsets:
        out.extend(f"{off:010d} 00000 n \n".encode("utf-8"))

    out.extend(
        f"trailer\n<< /Size {len(pdf_objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("utf-8")
    )

    with open(output_path, "wb") as f:
        f.write(out)


def render_svg(
    layers: Dict[str, List[Dict[str, Any]]],
    center_lat: float,
    center_lon: float,
    paper_w_mm: float,
    paper_h_mm: float,
    margin_mm: float,
    real_w_m: float,
    real_h_m: float,
    output_path: str,
    water_hex: str = "#C5DCE8",
    greenery_hex: str = "#DCE8D8",
    building_hex: str = "#000000",
    background_hex: str = "#FFFFFF",
):
    """
    Renders an exact-scale multi-layer SVG with organized vector layers for Illustrator/Figma.
    """
    map_w_mm = paper_w_mm - 2 * margin_mm
    map_h_mm = paper_h_mm - 2 * margin_mm

    x_min = -real_w_m / 2.0
    y_min = -real_h_m / 2.0

    def m_to_svg(xm: float, ym: float) -> Tuple[float, float]:
        u = margin_mm + ((xm - x_min) / real_w_m) * map_w_mm
        v = paper_h_mm - (margin_mm + ((ym - y_min) / real_h_m) * map_h_mm)
        return u, v

    svg_lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{paper_w_mm:.2f}mm" height="{paper_h_mm:.2f}mm" '
        f'viewBox="0 0 {paper_w_mm:.2f} {paper_h_mm:.2f}">',
        '  <defs>',
        '    <clipPath id="map-clip">',
        f'      <rect x="{margin_mm:.2f}" y="{margin_mm:.2f}" width="{map_w_mm:.2f}" height="{map_h_mm:.2f}" />',
        '    </clipPath>',
        '  </defs>',
        f'  <rect width="{paper_w_mm:.2f}" height="{paper_h_mm:.2f}" fill="{background_hex}" />',
    ]

    def render_layer(layer_id: str, polygons: List[Dict[str, Any]], fill_hex: str):
        if not polygons:
            return
        svg_lines.append(f'  <g id="{layer_id}" clip-path="url(#map-clip)" fill="{fill_hex}" fill-rule="evenodd">')
        for poly in polygons:
            path_parts = []
            first = True
            for lat, lon in poly["outer"]:
                xm, ym = latlon_to_metric(lat, lon, center_lat, center_lon)
                u, v = m_to_svg(xm, ym)
                path_parts.append(f"{'M' if first else 'L'} {u:.3f} {v:.3f}")
                first = False
            path_parts.append("Z")

            for inner in poly.get("inners", []):
                first = True
                for lat, lon in inner:
                    xm, ym = latlon_to_metric(lat, lon, center_lat, center_lon)
                    u, v = m_to_svg(xm, ym)
                    path_parts.append(f"{'M' if first else 'L'} {u:.3f} {v:.3f}")
                    first = False
                path_parts.append("Z")

            d_str = " ".join(path_parts)
            svg_lines.append(f'    <path d="{d_str}" />')
        svg_lines.append('  </g>')

    render_layer("water_layer", layers.get("water", []), water_hex)
    render_layer("greenery_layer", layers.get("greenery", []), greenery_hex)
    render_layer("buildings_layer", layers.get("buildings", []), building_hex)

    # Frame border
    border_color = "#000000" if background_hex.upper() in ("#FFFFFF", "#FFF", "#F4F6F8") else "#FFFFFF"
    svg_lines.append(
        f'  <rect x="{margin_mm:.2f}" y="{margin_mm:.2f}" width="{map_w_mm:.2f}" height="{map_h_mm:.2f}" '
        f'fill="none" stroke="{border_color}" stroke-width="0.3" />'
    )
    svg_lines.append('</svg>')

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(svg_lines))


def render_dxf(
    layers: Dict[str, List[Dict[str, Any]]],
    center_lat: float,
    center_lon: float,
    output_path: str,
):
    """
    Renders an AutoCAD-compatible DXF with organized layers: BUILDINGS, WATER, GREENERY.
    """
    dxf_lines = [
        "0", "SECTION",
        "2", "HEADER",
        "9", "$ACADVER",
        "1", "AC1015",
        "9", "$INSUNITS",
        "70", "6",  # 6 = Meters
        "0", "ENDSEC",
        "0", "SECTION",
        "2", "TABLES",
        "0", "TABLE",
        "2", "LAYER",
        "70", "3",
        "0", "LAYER",
        "2", "BUILDINGS",
        "70", "0",
        "62", "7",  # White/Black
        "6", "CONTINUOUS",
        "0", "LAYER",
        "2", "WATER",
        "70", "0",
        "62", "140",  # Cyan-Blue
        "6", "CONTINUOUS",
        "0", "LAYER",
        "2", "GREENERY",
        "70", "0",
        "62", "70",  # Green
        "6", "CONTINUOUS",
        "0", "ENDTAB",
        "0", "ENDSEC",
        "0", "SECTION",
        "2", "ENTITIES",
    ]

    for layer_name, layer_key in [("WATER", "water"), ("GREENERY", "greenery"), ("BUILDINGS", "buildings")]:
        for poly in layers.get(layer_key, []):
            outer = poly["outer"]
            if len(outer) >= 3:
                dxf_lines.extend([
                    "0", "LWPOLYLINE",
                    "100", "AcDbEntity",
                    "8", layer_name,
                    "100", "AcDbPolyline",
                    "90", str(len(outer)),
                    "70", "1",
                ])
                for lat, lon in outer:
                    xm, ym = latlon_to_metric(lat, lon, center_lat, center_lon)
                    dxf_lines.extend(["10", f"{xm:.3f}", "20", f"{ym:.3f}"])

            for inner in poly.get("inners", []):
                if len(inner) >= 3:
                    dxf_lines.extend([
                        "0", "LWPOLYLINE",
                        "100", "AcDbEntity",
                        "8", layer_name,
                        "100", "AcDbPolyline",
                        "90", str(len(inner)),
                        "70", "1",
                    ])
                    for lat, lon in inner:
                        xm, ym = latlon_to_metric(lat, lon, center_lat, center_lon)
                        dxf_lines.extend(["10", f"{xm:.3f}", "20", f"{ym:.3f}"])

    dxf_lines.extend(["0", "ENDSEC", "0", "EOF"])

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(dxf_lines))


# ── Main Entrypoint ──────────────────────────────────────────────────
def generate_schwarzplan(
    center_lat: float,
    center_lon: float,
    scale: int = 1000,
    paper_size: str = "A3 Landscape",
    margin_mm: float = 15.0,
    output_path: str = "schwarzplan.pdf",
    include_buildings: bool = True,
    building_hex: str = "#000000",
    include_water: bool = False,
    water_hex: str = "#C5DCE8",
    include_greenery: bool = False,
    greenery_hex: str = "#DCE8D8",
    background_hex: str = "#FFFFFF",
    on_progress: Optional[Callable[[str, float], None]] = None,
) -> Dict[str, Any]:
    """
    Generates an exact-scale plan with selectable Buildings, Water, and Greenery layers.
    """
    def _prog(txt: str, p: float = -1.0):
        if on_progress:
            on_progress(txt, p)

    if not include_buildings and not include_water and not include_greenery:
        return {
            "success": False,
            "message": "Please enable at least one layer (Buildings, Water, or Greenery).",
            "output_path": None,
        }

    if paper_size not in PAPER_SIZES:
        return {
            "success": False,
            "message": f"Unsupported paper size: '{paper_size}'",
            "output_path": None,
        }

    if not (-90.0 <= center_lat <= 90.0 and -180.0 <= center_lon <= 180.0):
        return {
            "success": False,
            "message": "Coordinates are outside valid geographic range.",
            "output_path": None,
        }

    paper = PAPER_SIZES[paper_size]
    paper_w_mm = paper["width_mm"]
    paper_h_mm = paper["height_mm"]

    map_w_mm = paper_w_mm - 2 * margin_mm
    map_h_mm = paper_h_mm - 2 * margin_mm

    if map_w_mm <= 0 or map_h_mm <= 0:
        return {
            "success": False,
            "message": "Margin is too large for the selected paper format.",
            "output_path": None,
        }

    real_w_m = (map_w_mm / 1000.0) * scale
    real_h_m = (map_h_mm / 1000.0) * scale
    query_radius_m = (max(real_w_m, real_h_m) / 2.0) * 1.15

    # 1. Fetch OSM Layers
    try:
        osm_data = fetch_osm_layers(
            center_lat,
            center_lon,
            query_radius_m,
            include_buildings=include_buildings,
            include_water=include_water,
            include_greenery=include_greenery,
            on_progress=_prog,
        )
    except Exception as e:
        return {
            "success": False,
            "message": f"Data fetch failed: {str(e)}",
            "output_path": None,
        }

    # 2. Parse Polygons into Layers
    _prog("Parsing urban geometry layers…", 0.50)
    layers = parse_osm_layers(osm_data)

    total_features = len(layers["buildings"]) + len(layers["water"]) + len(layers["greenery"])
    if total_features == 0:
        return {
            "success": False,
            "message": "No features found in this region for the selected layers.",
            "output_path": None,
        }

    # 3. Render Output
    ext = os.path.splitext(output_path)[1].lower()
    if ext not in SUPPORTED_FORMATS:
        ext = ".pdf"
        output_path += ".pdf"

    counts_list = []
    if include_buildings:
        counts_list.append(f"{len(layers['buildings'])} buildings")
    if include_water:
        counts_list.append(f"{len(layers['water'])} water")
    if include_greenery:
        counts_list.append(f"{len(layers['greenery'])} parks")

    _prog(f"Rendering {ext.upper()[1:]} ({', '.join(counts_list)})…", 0.75)

    try:
        out_dir = os.path.dirname(os.path.abspath(output_path))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        if ext == ".pdf":
            render_pdf(
                layers,
                center_lat,
                center_lon,
                paper_w_mm,
                paper_h_mm,
                margin_mm,
                real_w_m,
                real_h_m,
                output_path,
                water_rgb=hex_to_rgb(water_hex),
                greenery_rgb=hex_to_rgb(greenery_hex),
                building_rgb=hex_to_rgb(building_hex),
                background_rgb=hex_to_rgb(background_hex),
            )
        elif ext == ".svg":
            render_svg(
                layers,
                center_lat,
                center_lon,
                paper_w_mm,
                paper_h_mm,
                margin_mm,
                real_w_m,
                real_h_m,
                output_path,
                water_hex=water_hex,
                greenery_hex=greenery_hex,
                building_hex=building_hex,
                background_hex=background_hex,
            )
        elif ext == ".dxf":
            render_dxf(
                layers,
                center_lat,
                center_lon,
                output_path,
            )

        _prog("Complete!", 1.0)
        return {
            "success": True,
            "message": f"Successfully generated {os.path.basename(output_path)}",
            "output_path": output_path,
            "building_count": len(layers["buildings"]),
            "water_count": len(layers["water"]),
            "greenery_count": len(layers["greenery"]),
            "coverage_w_m": real_w_m,
            "coverage_h_m": real_h_m,
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Export failed: {str(e)}",
            "output_path": None,
        }

