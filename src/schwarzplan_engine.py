"""
Schwarzplan (Figure-Ground Diagram) Generation Engine.

Provides pure-Python spatial extraction and exact-scale architectural rendering:
- Direct OpenStreetMap Overpass API extraction with automatic mirror fallbacks & caching
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
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

# Standard conversion: 72 PostScript points per inch (25.4 mm)
MM_TO_PT = 72.0 / 25.4


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


def _cache_key(lat: float, lon: float, radius: float) -> str:
    key_str = f"{lat:.5f}_{lon:.5f}_{radius:.1f}"
    return hashlib.sha1(key_str.encode("utf-8")).hexdigest() + ".json"


# ── Overpass API Fetcher ────────────────────────────────────────────
def fetch_osm_buildings(
    center_lat: float,
    center_lon: float,
    radius_m: float,
    on_progress: Optional[Callable[[str, float], None]] = None,
) -> Dict[str, Any]:
    """
    Fetches raw OSM building footprints within radius_m of center_lat, center_lon.
    Uses disk caching and falls back across multiple Overpass mirrors.
    """
    cache_path = os.path.join(_get_cache_dir(), _cache_key(center_lat, center_lon, radius_m))

    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if on_progress:
                    on_progress("Loaded buildings from local cache…", 0.3)
                return data
        except Exception:
            pass

    if on_progress:
        on_progress(f"Querying OpenStreetMap (~{radius_m:.0f}m radius)…", 0.15)

    overpass_query = f"""
[out:json][timeout:30];
(
  way["building"](around:{radius_m:.1f},{center_lat:.6f},{center_lon:.6f});
  relation["building"]["type"="multipolygon"](around:{radius_m:.1f},{center_lat:.6f},{center_lon:.6f});
);
out body;
>;
out skel qt;
"""
    encoded_data = urllib.parse.urlencode({"data": overpass_query}).encode("utf-8")
    headers = {
        "User-Agent": "SchwarzplanApp/2.0 (Architectural Nolli Diagram Generator)",
        "Accept": "application/json",
    }

    try:
        import certifi
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ssl_ctx = ssl.create_default_context()

    last_error = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            req = urllib.request.Request(endpoint, data=encoded_data, headers=headers)
            with urllib.request.urlopen(req, timeout=35, context=ssl_ctx) as resp:
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
def parse_building_polygons(osm_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extracts closed building polygons and handles multipolygon inner courtyards.
    Returns a list of dicts: {'outer': [(lat, lon), ...], 'inners': [[(lat, lon), ...], ...]}
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

    polygons: List[Dict[str, Any]] = []
    used_ways_in_relations = set()

    # 1. Process multipolygon relations (buildings with courtyards or composite parts)
    for rel in relations:
        tags = rel.get("tags", {})
        if tags.get("type") == "multipolygon" and "building" in tags:
            outers: List[List[Tuple[float, float]]] = []
            inners: List[List[Tuple[float, float]]] = []
            for member in rel.get("members", []):
                m_type = member.get("type")
                m_ref = member.get("ref")
                m_role = member.get("role", "outer")
                if m_type == "way" and m_ref in ways:
                    used_ways_in_relations.add(m_ref)
                    way_node_coords = [
                        nodes[nid] for nid in ways[m_ref]["nodes"] if nid in nodes
                    ]
                    if len(way_node_coords) >= 3:
                        if m_role == "inner":
                            inners.append(way_node_coords)
                        else:
                            outers.append(way_node_coords)
            for outer in outers:
                polygons.append({"outer": outer, "inners": inners})

    # 2. Process independent building ways
    for way_id, way in ways.items():
        if way_id in used_ways_in_relations:
            continue
        tags = way.get("tags", {})
        if "building" in tags:
            way_node_ids = way.get("nodes", [])
            if len(way_node_ids) >= 4 and way_node_ids[0] == way_node_ids[-1]:
                pts = [nodes[nid] for nid in way_node_ids if nid in nodes]
                if len(pts) >= 4:
                    polygons.append({"outer": pts, "inners": []})

    return polygons


# ── Ellipsoidal Transverse Mercator Metric Projection ────────────────
def latlon_to_metric(
    lat: float, lon: float, center_lat: float, center_lon: float
) -> Tuple[float, float]:
    """
    Projects latitude/longitude (WGS84) to metric coordinates (x, y in meters)
    relative to center_lat, center_lon using high-precision Transverse Mercator.
    Guarantees sub-millimeter precision for exact architectural scaling.
    """
    a = 6378137.0  # WGS84 semi-major axis (meters)
    f = 1 / 298.257223563  # WGS84 flattening
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

    # Meridional distance on ellipsoid
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

    # Origin offset
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
    polygons: List[Dict[str, Any]],
    center_lat: float,
    center_lon: float,
    paper_w_mm: float,
    paper_h_mm: float,
    margin_mm: float,
    real_w_m: float,
    real_h_m: float,
    output_path: str,
):
    """
    Renders an exact-scale vector PDF with margin clipping and courtyard cutouts.
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
    # Background white fill
    pdf_cmds.append(f"1 1 1 rg 0 0 {paper_w_pt:.2f} {paper_h_pt:.2f} re f")

    # Save state & set clipping mask for exact margins
    pdf_cmds.append("q")
    pdf_cmds.append(f"{margin_pt:.2f} {margin_pt:.2f} {map_w_pt:.2f} {map_h_pt:.2f} re W n")

    # Set solid black fill
    pdf_cmds.append("0 0 0 rg")

    for poly in polygons:
        path_tokens = []
        # Outer ring
        first = True
        for lat, lon in poly["outer"]:
            xm, ym = latlon_to_metric(lat, lon, center_lat, center_lon)
            u, v = m_to_pt(xm, ym)
            if first:
                path_tokens.append(f"{u:.2f} {v:.2f} m")
                first = False
            else:
                path_tokens.append(f"{u:.2f} {v:.2f} l")
        path_tokens.append("h")

        # Inner courtyard rings (holes)
        for inner in poly.get("inners", []):
            first = True
            for lat, lon in inner:
                xm, ym = latlon_to_metric(lat, lon, center_lat, center_lon)
                u, v = m_to_pt(xm, ym)
                if first:
                    path_tokens.append(f"{u:.2f} {v:.2f} m")
                    first = False
                else:
                    path_tokens.append(f"{u:.2f} {v:.2f} l")
            path_tokens.append("h")

        # Fill with even-odd winding rule to cleanly subtract courtyards
        path_tokens.append("f*")
        pdf_cmds.append(" ".join(path_tokens))

    pdf_cmds.append("Q")  # Restore state

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
    polygons: List[Dict[str, Any]],
    center_lat: float,
    center_lon: float,
    paper_w_mm: float,
    paper_h_mm: float,
    margin_mm: float,
    real_w_m: float,
    real_h_m: float,
    output_path: str,
):
    """
    Renders an exact-scale vector SVG formatted with physical millimeter dimensions.
    """
    map_w_mm = paper_w_mm - 2 * margin_mm
    map_h_mm = paper_h_mm - 2 * margin_mm

    x_min = -real_w_m / 2.0
    y_min = -real_h_m / 2.0

    def m_to_svg(xm: float, ym: float) -> Tuple[float, float]:
        # SVG y-axis points downwards, invert y
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
        f'  <rect width="{paper_w_mm:.2f}" height="{paper_h_mm:.2f}" fill="#FFFFFF" />',
        '  <g clip-path="url(#map-clip)" fill="#000000" fill-rule="evenodd">',
    ]

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
    svg_lines.append('</svg>')

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(svg_lines))


def render_dxf(
    polygons: List[Dict[str, Any]],
    center_lat: float,
    center_lon: float,
    output_path: str,
):
    """
    Renders an AutoCAD-compatible DXF file containing 1:1 real-world building polylines in meters.
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
        "70", "1",
        "0", "LAYER",
        "2", "BUILDINGS",
        "70", "0",
        "62", "7",  # White/Black
        "6", "CONTINUOUS",
        "0", "ENDTAB",
        "0", "ENDSEC",
        "0", "SECTION",
        "2", "ENTITIES",
    ]

    for poly in polygons:
        # Outer ring
        outer = poly["outer"]
        if len(outer) >= 3:
            dxf_lines.extend([
                "0", "LWPOLYLINE",
                "100", "AcDbEntity",
                "8", "BUILDINGS",
                "100", "AcDbPolyline",
                "90", str(len(outer)),
                "70", "1",  # 1 = Closed
            ])
            for lat, lon in outer:
                xm, ym = latlon_to_metric(lat, lon, center_lat, center_lon)
                dxf_lines.extend(["10", f"{xm:.3f}", "20", f"{ym:.3f}"])

        # Inner rings
        for inner in poly.get("inners", []):
            if len(inner) >= 3:
                dxf_lines.extend([
                    "0", "LWPOLYLINE",
                    "100", "AcDbEntity",
                    "8", "BUILDINGS",
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
    on_progress: Optional[Callable[[str, float], None]] = None,
) -> Dict[str, Any]:
    """
    Generates an exact-scale Schwarzplan (figure-ground diagram) in PDF, SVG, or DXF format.

    Args:
        center_lat: Latitude of the center point (-90 to 90).
        center_lon: Longitude of the center point (-180 to 180).
        scale: Architectural scale denominator (e.g. 1000 for 1:1000).
        paper_size: Key from PAPER_SIZES dict.
        margin_mm: Border in millimeters on all sides.
        output_path: Destination file path (.pdf, .svg, or .dxf).
        on_progress: Callback for progress updates (text: str, pct: float 0.0-1.0).

    Returns:
        Dict with keys: success (bool), message (str), output_path (str | None),
        building_count (int), coverage_w_m (float), coverage_h_m (float).
    """
    def _prog(txt: str, p: float = -1.0):
        if on_progress:
            on_progress(txt, p)

    # 1. Validation
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

    # Real-world metric dimensions
    real_w_m = (map_w_mm / 1000.0) * scale
    real_h_m = (map_h_mm / 1000.0) * scale

    # Buffer radius to ensure complete edge polygons
    query_radius_m = (max(real_w_m, real_h_m) / 2.0) * 1.15

    # 2. Fetch OpenStreetMap Buildings
    try:
        osm_data = fetch_osm_buildings(
            center_lat, center_lon, query_radius_m, on_progress=_prog
        )
    except Exception as e:
        return {
            "success": False,
            "message": f"Data fetch failed: {str(e)}",
            "output_path": None,
        }

    # 3. Parse Polygons
    _prog("Parsing building geometries…", 0.45)
    polygons = parse_building_polygons(osm_data)

    if not polygons:
        return {
            "success": False,
            "message": "No building polygons found in this region.",
            "output_path": None,
        }

    # 4. Render Target Format
    ext = os.path.splitext(output_path)[1].lower()
    if ext not in SUPPORTED_FORMATS:
        ext = ".pdf"
        output_path += ".pdf"

    _prog(f"Rendering {ext.upper()[1:]} ({len(polygons)} buildings)…", 0.75)

    try:
        out_dir = os.path.dirname(os.path.abspath(output_path))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        if ext == ".pdf":
            render_pdf(
                polygons,
                center_lat,
                center_lon,
                paper_w_mm,
                paper_h_mm,
                margin_mm,
                real_w_m,
                real_h_m,
                output_path,
            )
        elif ext == ".svg":
            render_svg(
                polygons,
                center_lat,
                center_lon,
                paper_w_mm,
                paper_h_mm,
                margin_mm,
                real_w_m,
                real_h_m,
                output_path,
            )
        elif ext == ".dxf":
            render_dxf(
                polygons,
                center_lat,
                center_lon,
                output_path,
            )

        _prog("Complete!", 1.0)
        return {
            "success": True,
            "message": f"Successfully generated {os.path.basename(output_path)}",
            "output_path": output_path,
            "building_count": len(polygons),
            "coverage_w_m": real_w_m,
            "coverage_h_m": real_h_m,
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Export failed: {str(e)}",
            "output_path": None,
        }

