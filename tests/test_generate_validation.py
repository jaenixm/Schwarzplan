"""Guards that must reject bad input before any network request is made."""
import pytest

from schwarzplan_engine import generate_schwarzplan


def run(**overrides):
    args = dict(
        center_lat=53.5, center_lon=9.9, scale=1000,
        paper_size="A3 Landscape", margin_mm=15.0,
        output_path="/dev/null", include_buildings=True,
    )
    args.update(overrides)
    return generate_schwarzplan(**args)


def test_negative_border_rejected():
    result = run(margin_mm=-50)
    assert not result["success"]
    assert "zero or more" in result["message"]


def test_border_larger_than_paper_rejected():
    assert not run(margin_mm=200)["success"]


def test_oversized_area_rejected_before_fetching():
    """A0 at 1:25000 is a 29 km query that the map server will never answer."""
    result = run(scale=25000, paper_size="A0 Landscape")
    assert not result["success"]
    assert "km across" in result["message"]


def test_unknown_paper_rejected():
    assert not run(paper_size="A9 Tiny")["success"]


@pytest.mark.parametrize("lat,lon", [(999, 9.9), (53.5, 999), (-91, 0), (0, 181)])
def test_out_of_range_coordinates_rejected(lat, lon):
    assert not run(center_lat=lat, center_lon=lon)["success"]


def test_no_layers_selected_rejected():
    assert not run(include_buildings=False)["success"]
