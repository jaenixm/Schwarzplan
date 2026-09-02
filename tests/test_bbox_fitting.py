"""create_schwarzplan_by_bbox documented scale fitting but hardcoded 1:1000."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generate_schwarzplan import fit_scale_to_bbox
from schwarzplan_engine import PAPER_SIZES, SCALE_OPTIONS

HAMBURG = (53.5581, 9.9632)


def bbox_around(center, half_deg_lat, half_deg_lon):
    lat, lon = center
    return (lat + half_deg_lat, lat - half_deg_lat, lon + half_deg_lon, lon - half_deg_lon)


def test_returns_a_supported_scale():
    assert fit_scale_to_bbox(bbox_around(HAMBURG, 0.002, 0.003)) in SCALE_OPTIONS


def test_small_area_gets_a_fine_scale():
    fine = fit_scale_to_bbox(bbox_around(HAMBURG, 0.0005, 0.0008))
    coarse = fit_scale_to_bbox(bbox_around(HAMBURG, 0.02, 0.03))
    assert fine < coarse


def test_huge_area_falls_back_to_the_coarsest_scale():
    assert fit_scale_to_bbox(bbox_around(HAMBURG, 5.0, 5.0)) == max(SCALE_OPTIONS)


@pytest.mark.parametrize("half_lat,half_lon", [
    (0.0005, 0.0008), (0.002, 0.003), (0.01, 0.02), (0.05, 0.08),
])
def test_chosen_scale_actually_fits_the_sheet(half_lat, half_lon):
    from schwarzplan_engine import latlon_to_metric
    bbox = bbox_around(HAMBURG, half_lat, half_lon)
    north, south, east, west = bbox
    clat, clon = (north + south) / 2, (east + west) / 2
    width_m = abs(latlon_to_metric(clat, east, clat, clon)[0]
                  - latlon_to_metric(clat, west, clat, clon)[0])
    height_m = abs(latlon_to_metric(north, clon, clat, clon)[1]
                   - latlon_to_metric(south, clon, clat, clon)[1])

    scale = fit_scale_to_bbox(bbox)
    if scale == max(SCALE_OPTIONS):
        return  # Fallback case; not required to fit.
    paper = PAPER_SIZES["A3 Landscape"]
    assert width_m <= (paper["width_mm"] - 30) / 1000.0 * scale
    assert height_m <= (paper["height_mm"] - 30) / 1000.0 * scale


def test_margin_reaches_the_fitting_maths():
    """A wider border leaves less drawing area, so the scale must get coarser."""
    bbox = bbox_around(HAMBURG, 0.004, 0.006)
    narrow = fit_scale_to_bbox(bbox, margin_mm=0.0)
    wide = fit_scale_to_bbox(bbox, margin_mm=80.0)
    assert wide >= narrow


def test_paper_size_reaches_the_fitting_maths():
    bbox = bbox_around(HAMBURG, 0.004, 0.006)
    small = fit_scale_to_bbox(bbox, paper_size="A4 Landscape")
    large = fit_scale_to_bbox(bbox, paper_size="A0 Landscape")
    assert large <= small
