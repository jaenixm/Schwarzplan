"""The on-screen coverage figure must match what the exporter actually renders."""
import pytest

from main import calculate_bbox_corners
from schwarzplan_engine import PAPER_SIZES


@pytest.mark.parametrize("paper,scale,margin", [
    ("A3 Landscape", 1000, 15.0),
    ("A4 Portrait", 2000, 10.0),
    ("A0 Landscape", 500, 0.0),
    ("Square 300×300", 1000, 25.0),
    ("A4 Portrait", 1000, 102.0),  # leaves a 6 mm strip, narrower than the old clamp
])
def test_preview_coverage_matches_the_exporter(paper, scale, margin):
    p = PAPER_SIZES[paper]
    _, preview_w, preview_h = calculate_bbox_corners(
        53.5581, 9.9632, p["width_mm"], p["height_mm"], margin, scale
    )
    # The same arithmetic generate_schwarzplan uses to size the drawing.
    expected_w = ((p["width_mm"] - 2 * margin) / 1000.0) * scale
    expected_h = ((p["height_mm"] - 2 * margin) / 1000.0) * scale
    assert preview_w == pytest.approx(expected_w)
    assert preview_h == pytest.approx(expected_h)


def test_bbox_is_centred_on_the_pin():
    corners, _, _ = calculate_bbox_corners(53.5581, 9.9632, 420.0, 297.0, 15.0, 1000)
    lats = [c.latitude for c in corners]
    lons = [c.longitude for c in corners]
    assert sum(lats) / 4 == pytest.approx(53.5581, abs=1e-9)
    assert sum(lons) / 4 == pytest.approx(9.9632, abs=1e-9)
