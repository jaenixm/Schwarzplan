"""
Standalone CLI & Script Generator for Schwarzplan Diagrams.
"""

import os
import sys

# Ensure src directory is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from schwarzplan_engine import (
    generate_schwarzplan,
    PAPER_SIZES,
    SCALE_OPTIONS,
)


def create_schwarzplan_a3_landscape(
    center_lat_lon,
    scale=1000,
    margin_mm=15.0,
    output_filename="schwarzplan_a3.pdf"
):
    """
    Creates an exact-scale A3 landscape Schwarzplan for a given center point and scale.

    Args:
        center_lat_lon (tuple): (latitude, longitude) center coordinate.
        scale (int): Architectural scale denominator (e.g. 1000 for 1:1000).
        margin_mm (float): Margin border in millimeters.
        output_filename (str): Path to save the resulting PDF/SVG/DXF.
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
        on_progress=on_prog,
    )

    if result["success"]:
        print(f"✓ {result['message']} ({result.get('building_count', 0)} buildings)")
    else:
        print(f"✗ Failed: {result['message']}")


def create_schwarzplan_by_bbox(bbox, output_filename="schwarzplan.pdf"):
    """
    Bounding box helper. Computes center lat/lon and approximates scale to fit A3.
    bbox format: (north, south, east, west)
    """
    north, south, east, west = bbox
    center_lat = (north + south) / 2.0
    center_lon = (east + west) / 2.0
    print(f"Generating Schwarzplan for BBox Center ({center_lat:.5f}, {center_lon:.5f})…")
    create_schwarzplan_a3_landscape((center_lat, center_lon), scale=1000, output_filename=output_filename)


if __name__ == "__main__":
    # Example center in Hamburg
    center_point = (53.558148, 9.963214)
    create_schwarzplan_a3_landscape(
        center_point,
        scale=1000,
        output_filename="schwarzplan_a3_hamburg_1_1000.pdf"
    )

