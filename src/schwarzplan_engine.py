"""
Schwarzplan (figure-ground diagram) generation engine.

Provides the core logic for fetching building footprints from OpenStreetMap
and rendering them as exact-scale PDFs suitable for architectural printing.
"""

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend for headless rendering

import matplotlib.pyplot as plt
from shapely.geometry import Point
import geopandas as gpd
import osmnx as ox


# Paper size definitions in meters (landscape orientation)
PAPER_SIZES = {
    "A3 Landscape": {"width_m": 0.420, "height_m": 0.297},
    "A4 Landscape": {"width_m": 0.297, "height_m": 0.210},
}

# Common scale options
SCALE_OPTIONS = [500, 1000, 2000, 5000, 10000]


def generate_schwarzplan(
    center_lat: float,
    center_lon: float,
    scale: int = 1000,
    paper_size: str = "A3 Landscape",
    margin_mm: float = 15.0,
    output_path: str = "schwarzplan.pdf",
    on_progress=None,
):
    """
    Generate an exact-scale Schwarzplan PDF.

    Args:
        center_lat: Latitude of the center point.
        center_lon: Longitude of the center point.
        scale: Scale denominator (e.g. 1000 for 1:1000).
        paper_size: Key from PAPER_SIZES dict.
        margin_mm: White border in millimeters on all sides.
        output_path: File path for the output PDF.
        on_progress: Optional callback(status_text: str, percent: float).
                     percent is 0.0–1.0, or -1 for indeterminate.

    Returns:
        dict with keys: success (bool), message (str), output_path (str | None)
    """

    def _progress(text, pct=-1):
        if on_progress:
            on_progress(text, pct)

    # --- Validate inputs ---
    if paper_size not in PAPER_SIZES:
        return {"success": False, "message": f"Unknown paper size: {paper_size}", "output_path": None}

    if not (-90 <= center_lat <= 90 and -180 <= center_lon <= 180):
        return {"success": False, "message": "Coordinates out of range.", "output_path": None}

    paper = PAPER_SIZES[paper_size]
    paper_width_m = paper["width_m"]
    paper_height_m = paper["height_m"]
    margin_m = margin_mm / 1000.0

    # Map area after removing borders
    map_width_m = paper_width_m - 2 * margin_m
    map_height_m = paper_height_m - 2 * margin_m

    if map_width_m <= 0 or map_height_m <= 0:
        return {"success": False, "message": "Margin is too large for the selected paper size.", "output_path": None}

    # Real-world coverage
    real_width_m = map_width_m * scale
    real_height_m = map_height_m * scale

    # Fetch radius with buffer for edge polygons
    max_radius = (max(real_width_m, real_height_m) / 2) * 1.1

    # --- Fetch buildings ---
    _progress(f"Fetching buildings (~{max_radius:.0f}m radius)…", 0.1)
    tags = {"building": True}

    try:
        try:
            gdf_buildings = ox.features_from_point(
                (center_lat, center_lon), tags=tags, dist=max_radius
            )
        except Exception:
            try:
                gdf_buildings = ox.geometries_from_point(
                    (center_lat, center_lon), tags=tags, dist=max_radius
                )
            except Exception:
                gdf_buildings = ox.features_from_point(
                    (center_lat, center_lon), tags=tags, dist=max_radius
                )
    except Exception as e:
        return {"success": False, "message": f"Failed to fetch buildings: {e}", "output_path": None}

    if gdf_buildings.empty:
        return {"success": False, "message": "No buildings found at this location.", "output_path": None}

    # Filter to polygons only (remove point nodes that render as blue dots)
    _progress("Filtering building polygons…", 0.3)
    gdf_buildings = gdf_buildings[
        gdf_buildings.geometry.type.isin(["Polygon", "MultiPolygon"])
    ]

    if gdf_buildings.empty:
        return {"success": False, "message": "No polygon buildings found at this location.", "output_path": None}

    # --- Project to UTM ---
    _progress("Projecting to UTM…", 0.4)
    target_crs = gdf_buildings.estimate_utm_crs()
    gdf_proj = gdf_buildings.to_crs(target_crs)

    center_pt = gpd.GeoSeries([Point(center_lon, center_lat)], crs="EPSG:4326")
    center_pt_proj = center_pt.to_crs(target_crs)
    center_x = center_pt_proj.geometry.x.iloc[0]
    center_y = center_pt_proj.geometry.y.iloc[0]

    # --- Render ---
    _progress("Rendering Schwarzplan…", 0.6)
    fig_width_in = paper_width_m * 1000 / 25.4
    fig_height_in = paper_height_m * 1000 / 25.4

    fig, ax = plt.subplots(figsize=(fig_width_in, fig_height_in))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    gdf_proj.plot(ax=ax, facecolor="black", edgecolor="none")

    ax.set_xlim([center_x - real_width_m / 2, center_x + real_width_m / 2])
    ax.set_ylim([center_y - real_height_m / 2, center_y + real_height_m / 2])
    ax.set_aspect("equal")
    ax.set_axis_off()

    plt.subplots_adjust(
        left=margin_m / paper_width_m,
        right=(paper_width_m - margin_m) / paper_width_m,
        bottom=margin_m / paper_height_m,
        top=(paper_height_m - margin_m) / paper_height_m,
    )

    # --- Save ---
    _progress("Saving PDF…", 0.85)
    try:
        plt.savefig(output_path, format="pdf", pad_inches=0, dpi=300)
    except Exception as e:
        plt.close(fig)
        return {"success": False, "message": f"Failed to save PDF: {e}", "output_path": None}
    finally:
        plt.close(fig)

    _progress("Done!", 1.0)
    return {"success": True, "message": "Schwarzplan generated successfully.", "output_path": output_path}
