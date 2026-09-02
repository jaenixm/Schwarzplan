"""
Map.move_to is a coroutine. Calling it without awaiting builds the coroutine
and drops it, so the map silently never moves — the bug that made search look
slow and broken.
"""
import ast
import inspect
import os

import flet_map as ftm
import pytest

from main import mercator_y, parse_coordinates, zoom_for_bounds

MAIN_PY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "main.py"
)


def test_move_to_is_still_a_coroutine():
    """If flet-map ever makes this synchronous, our awaits need revisiting."""
    assert inspect.iscoroutinefunction(ftm.Map.move_to)


def test_every_move_to_call_is_awaited():
    tree = ast.parse(open(MAIN_PY, encoding="utf-8").read())
    awaited, bare = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            fn = node.value.func
            if isinstance(fn, ast.Attribute) and fn.attr == "move_to":
                awaited.append(node.lineno)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "move_to" and node.lineno not in awaited:
                bare.append(node.lineno)
    assert not bare, f"move_to called without await at line(s) {bare}"
    assert awaited, "expected at least one move_to call"


# ── zoom_for_bounds ────────────────────────────────────────────────
@pytest.mark.parametrize("name,bbox,lo,hi", [
    # south, north, west, east — as Nominatim returns them
    ("Germany",      ["47.2701114", "55.0991610", "5.8663153", "15.0419309"], 4, 8),
    ("Hamburg",      ["53.3951118", "54.0276500", "8.1044993", "10.3252805"], 8, 11),
    ("Eiffel Tower", ["48.8574753", "48.8590453", "2.2933119", "2.2956897"], 15, 17),
])
def test_zoom_matches_the_size_of_the_result(name, bbox, lo, hi):
    assert lo <= zoom_for_bounds(bbox) <= hi, name


def test_bigger_area_gets_a_lower_zoom():
    country = zoom_for_bounds(["47.27", "55.09", "5.86", "15.04"])
    city = zoom_for_bounds(["53.39", "54.02", "8.10", "10.32"])
    building = zoom_for_bounds(["48.8574", "48.8590", "2.2933", "2.2956"])
    assert country < city < building


@pytest.mark.parametrize("bbox", [None, [], ["a", "b", "c", "d"], ["1", "2"], "nonsense"])
def test_unusable_bounds_fall_back_to_a_sane_zoom(bbox):
    assert 3 <= zoom_for_bounds(bbox) <= 17


def test_zoom_is_clamped_to_the_map_limits():
    # A zero-size bounding box would otherwise ask for infinite zoom.
    assert zoom_for_bounds(["53.5", "53.5", "9.9", "9.9"]) <= 17
    assert zoom_for_bounds(["-85", "85", "-180", "180"]) >= 3


def test_mercator_y_handles_the_poles():
    assert 0.0 <= mercator_y(89.9) <= 1.0
    assert 0.0 <= mercator_y(-89.9) <= 1.0
    assert mercator_y(0) == pytest.approx(0.5)


# ── parse_coordinates ──────────────────────────────────────────────
@pytest.mark.parametrize("text,expected", [
    ("53.5581, 9.9632", (53.5581, 9.9632)),
    ("53.5581 9.9632", (53.5581, 9.9632)),
    ("53.5581;9.9632", (53.5581, 9.9632)),
    ("  -33.8688, 151.2093  ", (-33.8688, 151.2093)),
    ("0,0", (0.0, 0.0)),
])
def test_pasted_coordinates_are_recognised(text, expected):
    assert parse_coordinates(text) == expected


@pytest.mark.parametrize("text", [
    "Hamburg", "", None, "53.5581", "91.0, 9.9", "53.5, 181.0", "a, b",
    "53.5, 9.9, 100",
])
def test_non_coordinates_fall_through_to_the_geocoder(text):
    assert parse_coordinates(text) is None
