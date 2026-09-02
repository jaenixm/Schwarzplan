"""
Standalone CLI & Script Generator for Schwarzplan Diagrams.
"""

import os
import sys

# Ensure src directory is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from schwarzplan_engine import (
    generate_schwarzplan,
    latlon_to_metric,
    PAPER_SIZES,
    SCALE_OPTIONS,
)


def create_schwarzplan_a3_landscape(
    center_lat_lon,
    scale=1000,
    margin_mm=15.0,
    output_filename="schwarzplan_a3.pdf",
    include_buildings=True,
    include_water=False,
    include_greenery=False,
    include_roads=False,
    building_hex="#000000",
    water_hex="#C5DCE8",
    greenery_hex="#DCE8D8",
    road_hex="#A0A0A0",
):
    """
    Creates an exact-scale A3 landscape Schwarzplan for a given center point and scale.

    Args:
        center_lat_lon (tuple): (latitude, longitude) center coordinate.
        scale (int): Architectural scale denominator (e.g. 1000 for 1:1000).
        margin_mm (float): Margin border in millimeters.
        output_filename (str): Path to save the resulting PDF/SVG/DXF.
        include_buildings (bool): Include building footprints.
        include_water (bool): Include water bodies (Blauplan).
        include_greenery (bool): Include parks & greenery (Grünplan).
        include_roads (bool): Include roadways & street network (Strassennetz).
    """
    lat, lon = center_lat_lon
    print(f"Generating A3 Landscape Schwarzplan for ({lat}, {lon}) at 1:{scale} scale…")
    
    def on_prog(msg, pct):
        if pct >= 0:
            print(f"  [{int(pct * 100):3d}%] {msg}")
        else:
            print(f"  {msg}")

    result = generate_schwarzplan(
        center_lat=lat,
        center_lon=lon,
        scale=scale,
        paper_size="A3 Landscape",
        margin_mm=margin_mm,
        output_path=output_filename,
        include_buildings=include_buildings,
        building_hex=building_hex,
        include_water=include_water,
        water_hex=water_hex,
        include_greenery=include_greenery,
        greenery_hex=greenery_hex,
        include_roads=include_roads,
        road_hex=road_hex,
        on_progress=on_prog,
    )

    if result["success"]:
        parts = []
        if include_buildings:
            parts.append(f"{result.get('building_count', 0)} buildings")
        if include_water:
            parts.append(f"{result.get('water_count', 0)} water")
        if include_greenery:
            parts.append(f"{result.get('greenery_count', 0)} parks")
        if include_roads:
            parts.append(f"{result.get('road_count', 0)} roads")
        print(f"✓ {result['message']} ({', '.join(parts)})")
    else:
        print(f"✗ Failed: {result['message']}")


def fit_scale_to_bbox(bbox, paper_size="A3 Landscape", margin_mm=15.0):
    """
    Returns the largest available scale that still fits the bbox on the sheet.

    Args:
        bbox (tuple): (north, south, east, west) in degrees.
        paper_size (str): A key of PAPER_SIZES.
        margin_mm (float): Border in millimetres.

    Returns:
        int: A denominator from SCALE_OPTIONS, or the coarsest one if the area
        is larger than any of them can hold.
    """
    north, south, east, west = bbox
    center_lat = (north + south) / 2.0
    center_lon = (east + west) / 2.0

    # Project the corners so the extent is in metres, not degrees.
    x_east, _ = latlon_to_metric(center_lat, east, center_lat, center_lon)
    x_west, _ = latlon_to_metric(center_lat, west, center_lat, center_lon)
    _, y_north = latlon_to_metric(north, center_lon, center_lat, center_lon)
    _, y_south = latlon_to_metric(south, center_lon, center_lat, center_lon)
    width_m = abs(x_east - x_west)
    height_m = abs(y_north - y_south)

    paper = PAPER_SIZES[paper_size]
    map_w_m = (paper["width_mm"] - 2 * margin_mm) / 1000.0
    map_h_m = (paper["height_mm"] - 2 * margin_mm) / 1000.0

    for scale in sorted(SCALE_OPTIONS):
        if width_m <= map_w_m * scale and height_m <= map_h_m * scale:
            return scale
    return max(SCALE_OPTIONS)


def create_schwarzplan_by_bbox(bbox, output_filename="schwarzplan.pdf", **kwargs):
    """
    Renders the area of a bounding box, choosing a scale that fits it on A3.
    bbox format: (north, south, east, west)
    """
    north, south, east, west = bbox
    center_lat = (north + south) / 2.0
    center_lon = (east + west) / 2.0
    scale = fit_scale_to_bbox(bbox)
    print(f"Generating Schwarzplan for BBox Center ({center_lat:.5f}, {center_lon:.5f}) at 1:{scale}…")
    create_schwarzplan_a3_landscape(
        (center_lat, center_lon), scale=scale, output_filename=output_filename, **kwargs
    )


if __name__ == "__main__":
    # Example center in Hamburg
    center_point = (53.558148, 9.963214)
    create_schwarzplan_a3_landscape(
        center_point,
        scale=1000,
        output_filename="schwarzplan_a3_hamburg_1_1000.pdf"
    )

