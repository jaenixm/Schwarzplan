"""
Schwarzplan (Figure-Ground Diagram) Generation Engine.

Provides pure-Python spatial extraction and exact-scale architectural rendering:
- Direct OpenStreetMap Overpass API extraction with automatic mirror fallbacks & caching
- Multi-layer urban context: Buildings (Schwarzplan), Waterways (Blauplan), Greenery/Parks (Grünplan), and Roadways (Verkehrsplan)
- Sub-millimeter accurate WGS84 Transverse Mercator metric projection
- Exact-scale Vector PDF, SVG, and CAD DXF generation with courtyard (inner hole) cutouts
- 100% pure Python with zero native C-extension dependencies (no GDAL, GEOS, PROJ.4 required)
"""

import os
import ssl
import sys
import json
import math
import time
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

# Nominatim and Overpass both require a User-Agent that identifies the app and
# gives them a way to make contact. Requests without one get blocked.
APP_VERSION = "1.0.0"
CONTACT_URL = "https://github.com/jaenixm/Schwarzplan"
USER_AGENT = f"SchwarzplanGenerator/{APP_VERSION} ({CONTACT_URL})"

# OpenStreetMap data is ODbL-licensed and requires attribution on derived works.
OSM_ATTRIBUTION = "Map data \u00a9 OpenStreetMap contributors (ODbL)"

# Cached Overpass responses older than this are refetched, so plans do not show
# a city as it was months ago.
CACHE_TTL_SECONDS = 14 * 24 * 3600

# Above this radius an Overpass query for every building reliably times out.
MAX_QUERY_RADIUS_M = 6000.0


def normalize_hex(value: Optional[str]) -> Optional[str]:
    """
    Returns a color as '#RRGGBB', or None if it cannot be read.

    Accepts '#C5DCE8', 'c5dce8' and the 3-digit '#abc' shorthand. Callers use
    the None to keep a half-typed color out of an export.
    """
    if not value:
        return None
    h = value.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    # int(h, 16) is too permissive here: it accepts signs and underscores, so
    # "+12345" would pass and produce the unusable colour "#+12345".
    if any(c not in "0123456789abcdefABCDEF" for c in h):
        return None
    return "#" + h.upper()


def hex_to_rgb(hex_str: str, fallback: Tuple[float, float, float] = (0.0, 0.0, 0.0)) -> Tuple[float, float, float]:
    """Converts a hex color code like #C5DCE8 to a normalized (0.0-1.0) RGB tuple."""
    normalized = normalize_hex(hex_str)
    if normalized is None:
        return fallback
    h = normalized.lstrip("#")
    return (
        round(int(h[0:2], 16) / 255.0, 3),
        round(int(h[2:4], 16) / 255.0, 3),
        round(int(h[4:6], 16) / 255.0, 3),
    )


# ── Cache Management ────────────────────────────────────────────────
def _get_cache_dir() -> str:
    """
    Returns a user-writable directory for caching raw OSM responses.

    This must never be next to __file__: in a packaged app that path is inside
    the read-only, code-signed application bundle.
    """
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        base_dir = os.path.join(home, "Library", "Caches", "Schwarzplan")
    elif sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        base_dir = os.path.join(root, "Schwarzplan", "Cache")
    else:
        root = os.environ.get("XDG_CACHE_HOME") or os.path.join(home, ".cache")
        base_dir = os.path.join(root, "schwarzplan")

    try:
        os.makedirs(base_dir, exist_ok=True)
        return base_dir
    except Exception:
        temp_dir = os.path.join(tempfile.gettempdir(), "schwarzplan_cache")
        os.makedirs(temp_dir, exist_ok=True)
        return temp_dir


def _cache_key(lat: float, lon: float, radius: float, buildings: bool, water: bool, greenery: bool, roads: bool = False) -> str:
    key_str = f"{lat:.5f}_{lon:.5f}_{radius:.1f}_b{int(buildings)}_w{int(water)}_g{int(greenery)}_r{int(roads)}"
    return hashlib.sha1(key_str.encode("utf-8")).hexdigest() + ".json"


def _response_problem(data: Any) -> Optional[str]:
    """
    Returns a message if this Overpass response must not be used or cached.

    Overpass answers a timed-out or overloaded query with HTTP 200, an empty
    element list and a "remark" field. Caching that would make the failure
    permanent for this location.
    """
    if not isinstance(data, dict):
        return "The map server sent an unreadable response."
    remark = data.get("remark")
    if remark:
        text = str(remark)
        if "timed out" in text.lower():
            return (
                "The map server timed out. Try a smaller paper size or a larger "
                "scale number, then generate again."
            )
        return f"The map server reported: {text}"
    if not data.get("elements"):
        return None  # Genuinely empty area; the caller reports it, we just do not cache it.
    return None


def _read_cache(cache_path: str) -> Optional[Dict[str, Any]]:
    """Returns a cached response, or None if it is missing, stale or unusable."""
    try:
        if time.time() - os.path.getmtime(cache_path) > CACHE_TTL_SECONDS:
            return None
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if _response_problem(data) is not None or not data.get("elements"):
        return None
    return data


def _write_cache(cache_path: str, data: Dict[str, Any]) -> None:
    """Stores a response, but only one that is actually worth reusing."""
    if _response_problem(data) is not None or not data.get("elements"):
        return
    try:
        with open(cache_path, "w", encoding="utf-8") as cf:
            json.dump(data, cf)
    except Exception:
        pass


# ── Overpass API Fetcher ────────────────────────────────────────────
def fetch_osm_layers(
    center_lat: float,
    center_lon: float,
    radius_m: float,
    include_buildings: bool = True,
    include_water: bool = False,
    include_greenery: bool = False,
    include_roads: bool = False,
    on_progress: Optional[Callable[[str, float], None]] = None,
) -> Dict[str, Any]:
    """
    Fetches OSM layers: buildings, waterbodies, greenery, and/or roadways.
    """
    cache_path = os.path.join(
        _get_cache_dir(),
        _cache_key(center_lat, center_lon, radius_m, include_buildings, include_water, include_greenery, include_roads),
    )

    cached = _read_cache(cache_path)
    if cached is not None:
        if on_progress:
            on_progress("Loaded geometry from local cache…", 0.3)
        return cached

    if on_progress:
        layers_desc = []
        if include_buildings:
            layers_desc.append("Buildings")
        if include_water:
            layers_desc.append("Water")
        if include_greenery:
            layers_desc.append("Greenery")
        if include_roads:
            layers_desc.append("Roadways")
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

    if include_roads:
        subqueries.extend([
            f'way["highway"](around:{radius_m:.1f},{center_lat:.6f},{center_lon:.6f});',
            f'relation["highway"]["type"="multipolygon"](around:{radius_m:.1f},{center_lat:.6f},{center_lon:.6f});',
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

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    last_error: Optional[str] = None

    # 1. Try requests if available
    try:
        import requests
    except ImportError:
        requests = None

    total = len(OVERPASS_ENDPOINTS)
    if requests is not None:
        for attempt, endpoint in enumerate(OVERPASS_ENDPOINTS, 1):
            if on_progress and attempt > 1:
                on_progress(f"Mirror {attempt - 1} did not answer, trying {attempt} of {total}…", 0.15)
            try:
                resp = requests.post(
                    endpoint, data={"data": overpass_query}, headers=headers, timeout=60
                )
                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}"
                    continue
                data = resp.json()
            except Exception as err:
                last_error = str(err)
                continue
            problem = _response_problem(data)
            if problem is not None:
                last_error = problem
                continue
            _write_cache(cache_path, data)
            return data

        # requests already tried every mirror; repeating them over urllib would
        # only double the wait before the error reaches the user.
        raise RuntimeError(last_error or "Could not reach any OpenStreetMap server.")

    # 2. Fallback to urllib.request. Certificates are always verified: a plan is
    #    not worth handing an attacker a foothold for.
    encoded_data = urllib.parse.urlencode({"data": overpass_query}).encode("utf-8")
    try:
        import certifi
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ssl_ctx = ssl.create_default_context()

    for attempt, endpoint in enumerate(OVERPASS_ENDPOINTS, 1):
        if on_progress and attempt > 1:
            on_progress(f"Mirror {attempt - 1} did not answer, trying {attempt} of {total}…", 0.15)
        try:
            req = urllib.request.Request(endpoint, data=encoded_data, headers=headers)
            with urllib.request.urlopen(req, timeout=60, context=ssl_ctx) as resp:
                if resp.status != 200:
                    last_error = f"HTTP {resp.status}"
                    continue
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as err:
            last_error = str(err)
            continue
        problem = _response_problem(data)
        if problem is not None:
            last_error = problem
            continue
        _write_cache(cache_path, data)
        return data

    raise RuntimeError(last_error or "Could not reach any OpenStreetMap server.")


# ── Ring Assembly ───────────────────────────────────────────────────
def _stitch_rings(fragments: List[List[Tuple[float, float]]]) -> List[List[Tuple[float, float]]]:
    """
    Joins way fragments end-to-end into closed rings.

    OpenStreetMap splits a large outer ring across several ways, so a single
    member way is usually an open fragment. Treating each fragment as its own
    closed ring cuts a chord straight across the shape.
    """
    rings: List[List[Tuple[float, float]]] = []
    pending = [list(f) for f in fragments if len(f) >= 2]

    while pending:
        ring = pending.pop(0)
        joined = True
        while joined and ring[0] != ring[-1]:
            joined = False
            for i, frag in enumerate(pending):
                if frag[0] == ring[-1]:
                    ring = ring + frag[1:]
                elif frag[-1] == ring[-1]:
                    ring = ring + frag[-2::-1]
                elif frag[-1] == ring[0]:
                    ring = frag[:-1] + ring
                elif frag[0] == ring[0]:
                    ring = frag[:0:-1] + ring
                else:
                    continue
                pending.pop(i)
                joined = True
                break

        if len(ring) < 3:
            continue
        if ring[0] != ring[-1]:
            ring = ring + [ring[0]]  # Data gap: close it so the area still renders.
        if len(ring) >= 4:
            rings.append(ring)

    return rings


def _point_in_ring(point: Tuple[float, float], ring: List[Tuple[float, float]]) -> bool:
    """Ray-casting containment test, used to match courtyards to their building."""
    px, py = point
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _build_polygons(
    outer_fragments: List[List[Tuple[float, float]]],
    inner_fragments: List[List[Tuple[float, float]]],
) -> List[Dict[str, Any]]:
    """Assembles a multipolygon, giving each hole to the ring that contains it."""
    outers = _stitch_rings(outer_fragments)
    inners = _stitch_rings(inner_fragments)
    if not outers:
        return []
    if len(outers) == 1:
        return [{"outer": outers[0], "inners": inners}]

    polygons = [{"outer": o, "inners": []} for o in outers]
    for inner in inners:
        for poly in polygons:
            if _point_in_ring(inner[0], poly["outer"]):
                poly["inners"].append(inner)
                break
    return polygons


# ── Geometry Parser ─────────────────────────────────────────────────
def parse_osm_layers(osm_data: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Extracts geometric features for:
    - 'buildings' (polygons)
    - 'water' (polygons)
    - 'greenery' (polygons)
    - 'roads' (polylines/polygons)
    Handles multipolygon inner rings (courtyards/islands).
    """
    nodes: Dict[int, Tuple[float, float]] = {}
    ways: Dict[int, Dict[str, Any]] = {}
    relations: List[Dict[str, Any]] = []

    for element in osm_data.get("elements", []):
        el_type = element.get("type")
        el_id = element.get("id")
        if el_type == "node":
            lat, lon = element.get("lat"), element.get("lon")
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                nodes[el_id] = (lat, lon)
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

    def is_roadway(tags: Dict[str, str]) -> bool:
        hw = tags.get("highway")
        if not hw:
            return False
        if hw in ("proposed", "abandoned", "razed", "demolished", "platform", "raceway", "corridor", "elevator", "escalator"):
            return False
        return True

    buildings: List[Dict[str, Any]] = []
    water: List[Dict[str, Any]] = []
    greenery: List[Dict[str, Any]] = []
    roads: List[Dict[str, Any]] = []
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
            elif is_roadway(tags):
                category = roads

            if category is not None:
                outer_parts: List[List[Tuple[float, float]]] = []
                inner_parts: List[List[Tuple[float, float]]] = []
                for member in rel.get("members", []):
                    m_ref = member.get("ref")
                    if member.get("type") != "way" or m_ref not in ways:
                        continue
                    used_ways.add(m_ref)
                    coords = [nodes[nid] for nid in ways[m_ref]["nodes"] if nid in nodes]
                    if len(coords) >= 2:
                        if member.get("role") == "inner":
                            inner_parts.append(coords)
                        else:
                            outer_parts.append(coords)

                if category is roads:
                    hw = tags.get("highway", "pedestrian")
                    for ring in _stitch_rings(outer_parts) + _stitch_rings(inner_parts):
                        roads.append({"points": ring, "highway": hw, "is_area": True})
                else:
                    category.extend(_build_polygons(outer_parts, inner_parts))

    # 2. Process Ways
    for way_id, way in ways.items():
        if way_id in used_ways:
            continue
        tags = way.get("tags", {})
        if is_building(tags):
            node_ids = way.get("nodes", [])
            if len(node_ids) >= 4 and node_ids[0] == node_ids[-1]:
                pts = [nodes[nid] for nid in node_ids if nid in nodes]
                if len(pts) >= 4:
                    buildings.append({"outer": pts, "inners": []})
        elif is_water(tags):
            node_ids = way.get("nodes", [])
            if len(node_ids) >= 4 and node_ids[0] == node_ids[-1]:
                pts = [nodes[nid] for nid in node_ids if nid in nodes]
                if len(pts) >= 4:
                    water.append({"outer": pts, "inners": []})
        elif is_greenery(tags):
            node_ids = way.get("nodes", [])
            if len(node_ids) >= 4 and node_ids[0] == node_ids[-1]:
                pts = [nodes[nid] for nid in node_ids if nid in nodes]
                if len(pts) >= 4:
                    greenery.append({"outer": pts, "inners": []})
        elif is_roadway(tags):
            node_ids = way.get("nodes", [])
            if len(node_ids) >= 2:
                pts = [nodes[nid] for nid in node_ids if nid in nodes]
                if len(pts) >= 2:
                    is_area = (len(pts) >= 4 and pts[0] == pts[-1] and tags.get("area") == "yes")
                    roads.append({
                        "points": pts,
                        "highway": tags.get("highway", "residential"),
                        "name": tags.get("name", ""),
                        "is_area": is_area,
                    })

    return {
        "buildings": buildings,
        "water": water,
        "greenery": greenery,
        "roads": roads,
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
    road_rgb: Tuple[float, float, float] = (0.60, 0.60, 0.60),
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

    def draw_roads(roads: List[Dict[str, Any]], color: Tuple[float, float, float]):
        if not roads:
            return
        rr, rg, rb = color
        pdf_cmds.append(f"{rr:.3f} {rg:.3f} {rb:.3f} RG")
        pdf_cmds.append("1 J 1 j")  # round cap, round join

        for road in roads:
            pts = road.get("points", [])
            if len(pts) < 2:
                continue
            hw = road.get("highway", "")
            if hw in ("motorway", "motorway_link", "trunk", "trunk_link"):
                lw = 1.1
            elif hw in ("primary", "primary_link", "secondary", "secondary_link"):
                lw = 0.8
            elif hw in ("tertiary", "tertiary_link", "unclassified", "residential", "living_street"):
                lw = 0.55
            elif hw in ("service", "pedestrian", "track", "busway"):
                lw = 0.4
            else:
                lw = 0.3

            pdf_cmds.append(f"{lw:.2f} w")
            path_tokens = []
            first = True
            for lat, lon in pts:
                xm, ym = latlon_to_metric(lat, lon, center_lat, center_lon)
                u, v = m_to_pt(xm, ym)
                path_tokens.append(f"{u:.2f} {v:.2f} {'m' if first else 'l'}")
                first = False
            path_tokens.append("S")
            pdf_cmds.append(" ".join(path_tokens))

    # Layer order: Water -> Greenery -> Roadways -> Buildings on top
    draw_layer_polygons(layers.get("water", []), water_rgb)
    draw_layer_polygons(layers.get("greenery", []), greenery_rgb)
    draw_roads(layers.get("roads", []), road_rgb)
    draw_layer_polygons(layers.get("buildings", []), building_rgb)

    pdf_cmds.append("Q")

    # 3. Outer border frame. Drawn after the clip is released: the clip
    #    rectangle is the frame itself, so stroking inside it cut the outer
    #    half of the line away and the border came out at half weight.
    fr, fg, fb = (0.0, 0.0, 0.0) if background_rgb[0] > 0.5 else (1.0, 1.0, 1.0)
    pdf_cmds.append(f"{fr:.3f} {fg:.3f} {fb:.3f} RG")
    pdf_cmds.append("0.5 w")
    pdf_cmds.append(f"{margin_pt:.2f} {margin_pt:.2f} {map_w_pt:.2f} {map_h_pt:.2f} re S")

    # 4. ODbL attribution. Sits in the margin, below the frame.
    label_pt = 6.0
    label_y = margin_pt - label_pt - 2.0
    if label_y < 2.0:
        label_y = margin_pt + 3.0  # Margin too tight: place it just inside the frame.
    escaped = (
        OSM_ATTRIBUTION.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    )
    pdf_cmds.append("BT")
    pdf_cmds.append(f"/F1 {label_pt:.1f} Tf")
    pdf_cmds.append(f"{fr:.3f} {fg:.3f} {fb:.3f} rg")
    pdf_cmds.append(f"{margin_pt:.2f} {label_y:.2f} Td")
    pdf_cmds.append(f"({escaped}) Tj")
    pdf_cmds.append("ET")

    content_stream = "\n".join(pdf_cmds).encode("latin-1", "replace")
    stream_len = len(content_stream)

    pdf_objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {paper_w_pt:.2f} {paper_h_pt:.2f}] "
            f"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ).encode("utf-8"),
        f"<< /Length {stream_len} >>\nstream\n".encode("utf-8")
        + content_stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
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
    road_hex: str = "#A0A0A0",
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

    def render_roads_layer(layer_id: str, roads: List[Dict[str, Any]], stroke_hex: str):
        if not roads:
            return
        svg_lines.append(f'  <g id="{layer_id}" clip-path="url(#map-clip)" fill="none" stroke="{stroke_hex}" stroke-linecap="round" stroke-linejoin="round">')
        for road in roads:
            pts = road.get("points", [])
            if len(pts) < 2:
                continue
            hw = road.get("highway", "")
            if hw in ("motorway", "motorway_link", "trunk", "trunk_link"):
                lw_mm = 0.38
            elif hw in ("primary", "primary_link", "secondary", "secondary_link"):
                lw_mm = 0.28
            elif hw in ("tertiary", "tertiary_link", "unclassified", "residential", "living_street"):
                lw_mm = 0.20
            elif hw in ("service", "pedestrian", "track", "busway"):
                lw_mm = 0.14
            else:
                lw_mm = 0.10

            path_parts = []
            first = True
            for lat, lon in pts:
                xm, ym = latlon_to_metric(lat, lon, center_lat, center_lon)
                u, v = m_to_svg(xm, ym)
                path_parts.append(f"{'M' if first else 'L'} {u:.3f} {v:.3f}")
                first = False

            d_str = " ".join(path_parts)
            svg_lines.append(f'    <path d="{d_str}" stroke-width="{lw_mm:.3f}" />')
        svg_lines.append('  </g>')

    render_layer("water_layer", layers.get("water", []), water_hex)
    render_layer("greenery_layer", layers.get("greenery", []), greenery_hex)
    render_roads_layer("roadways_layer", layers.get("roads", []), road_hex)
    render_layer("buildings_layer", layers.get("buildings", []), building_hex)

    # Frame border
    border_color = "#000000" if background_hex.upper() in ("#FFFFFF", "#FFF", "#F4F6F8") else "#FFFFFF"
    svg_lines.append(
        f'  <rect x="{margin_mm:.2f}" y="{margin_mm:.2f}" width="{map_w_mm:.2f}" height="{map_h_mm:.2f}" '
        f'fill="none" stroke="{border_color}" stroke-width="0.3" />'
    )
    label_y = margin_mm - 2.0 if margin_mm >= 5.0 else paper_h_mm - 2.0
    svg_lines.append(
        f'  <text x="{margin_mm:.2f}" y="{label_y:.2f}" font-family="Helvetica, Arial, sans-serif" '
        f'font-size="2.1" fill="{border_color}">{OSM_ATTRIBUTION}</text>'
    )
    svg_lines.append('</svg>')

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(svg_lines))


def _dxf_ring(points: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """
    Drops the repeated closing vertex from a ring.

    OSM rings repeat the first point at the end. Writing that alongside the
    LWPOLYLINE closed flag (70=1) leaves a zero-length closing segment that
    CAD software reports as a duplicate vertex.
    """
    if len(points) > 1 and points[0] == points[-1]:
        return points[:-1]
    return points


def render_dxf(
    layers: Dict[str, List[Dict[str, Any]]],
    center_lat: float,
    center_lon: float,
    output_path: str,
):
    """
    Renders an AutoCAD-compatible DXF with organized layers: BUILDINGS, WATER, GREENERY, ROADWAYS.
    """
    dxf_lines = [
        "999", OSM_ATTRIBUTION,
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
        "70", "4",
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
        "0", "LAYER",
        "2", "ROADWAYS",
        "70", "0",
        "62", "8",  # Gray
        "6", "CONTINUOUS",
        "0", "ENDTAB",
        "0", "ENDSEC",
        "0", "SECTION",
        "2", "ENTITIES",
    ]

    for layer_name, layer_key in [("WATER", "water"), ("GREENERY", "greenery"), ("BUILDINGS", "buildings")]:
        for poly in layers.get(layer_key, []):
            for ring in [poly["outer"]] + list(poly.get("inners", [])):
                vertices = _dxf_ring(ring)
                if len(vertices) < 3:
                    continue
                dxf_lines.extend([
                    "0", "LWPOLYLINE",
                    "100", "AcDbEntity",
                    "8", layer_name,
                    "100", "AcDbPolyline",
                    "90", str(len(vertices)),
                    "70", "1",
                ])
                for lat, lon in vertices:
                    xm, ym = latlon_to_metric(lat, lon, center_lat, center_lon)
                    dxf_lines.extend(["10", f"{xm:.3f}", "20", f"{ym:.3f}"])

    for road in layers.get("roads", []):
        pts = road.get("points", [])
        if len(pts) >= 2:
            closed = len(pts) >= 4 and pts[0] == pts[-1]
            if closed:
                pts = _dxf_ring(pts)
            if len(pts) < 2:
                continue
            dxf_lines.extend([
                "0", "LWPOLYLINE",
                "100", "AcDbEntity",
                "8", "ROADWAYS",
                "100", "AcDbPolyline",
                "90", str(len(pts)),
                "70", "1" if closed else "0",
            ])
            for lat, lon in pts:
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
    include_roads: bool = False,
    road_hex: str = "#A0A0A0",
    background_hex: str = "#FFFFFF",
    on_progress: Optional[Callable[[str, float], None]] = None,
) -> Dict[str, Any]:
    """
    Generates an exact-scale plan with selectable Buildings, Water, Greenery, and Roadways layers.
    """
    def _prog(txt: str, p: float = -1.0):
        if on_progress:
            on_progress(txt, p)

    if not include_buildings and not include_water and not include_greenery and not include_roads:
        return {
            "success": False,
            "message": "Please enable at least one layer (Buildings, Water, Greenery, or Roadways).",
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

    if margin_mm < 0:
        return {
            "success": False,
            "message": "Border must be zero or more.",
            "output_path": None,
        }

    map_w_mm = paper_w_mm - 2 * margin_mm
    map_h_mm = paper_h_mm - 2 * margin_mm

    if map_w_mm <= 0 or map_h_mm <= 0:
        return {
            "success": False,
            "message": "Border is too large for the selected paper format.",
            "output_path": None,
        }

    real_w_m = (map_w_mm / 1000.0) * scale
    real_h_m = (map_h_mm / 1000.0) * scale
    query_radius_m = (max(real_w_m, real_h_m) / 2.0) * 1.15

    if query_radius_m > MAX_QUERY_RADIUS_M:
        area_km = max(real_w_m, real_h_m) / 1000.0
        return {
            "success": False,
            "message": (
                f"This covers {area_km:.1f} km across, which is more than the map "
                f"server will return. Choose a smaller paper size or a scale below "
                f"1:{scale}."
            ),
            "output_path": None,
        }

    # 1. Fetch OSM Layers
    try:
        osm_data = fetch_osm_layers(
            center_lat,
            center_lon,
            query_radius_m,
            include_buildings=include_buildings,
            include_water=include_water,
            include_greenery=include_greenery,
            include_roads=include_roads,
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

    total_features = (
        len(layers["buildings"])
        + len(layers["water"])
        + len(layers["greenery"])
        + len(layers["roads"])
    )
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
    if include_roads:
        counts_list.append(f"{len(layers['roads'])} roads")

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
                water_rgb=hex_to_rgb(water_hex, (0.77, 0.86, 0.91)),
                greenery_rgb=hex_to_rgb(greenery_hex, (0.86, 0.91, 0.85)),
                road_rgb=hex_to_rgb(road_hex, (0.60, 0.60, 0.60)),
                building_rgb=hex_to_rgb(building_hex, (0.0, 0.0, 0.0)),
                background_rgb=hex_to_rgb(background_hex, (1.0, 1.0, 1.0)),
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
                road_hex=road_hex,
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
            "road_count": len(layers["roads"]),
            "coverage_w_m": real_w_m,
            "coverage_h_m": real_h_m,
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Export failed: {str(e)}",
            "output_path": None,
        }

