from schwarzplan_engine import (
    _build_polygons,
    _point_in_ring,
    _stitch_rings,
    latlon_to_metric,
    parse_osm_layers,
)

SQUARE = [(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)]


def test_split_ring_is_stitched_into_one_closed_ring():
    """OSM splits large outer rings across ways; each fragment is open."""
    rings = _stitch_rings([
        [(0, 0), (0, 10)],
        [(0, 10), (10, 10), (10, 5)],
        [(10, 5), (10, 0), (0, 0)],
    ])
    assert len(rings) == 1
    assert rings[0][0] == rings[0][-1]


def test_fragments_stitch_regardless_of_direction():
    rings = _stitch_rings([
        [(0, 0), (0, 10)],
        [(10, 10), (0, 10)],
        [(10, 10), (10, 0), (0, 0)],
    ])
    assert len(rings) == 1
    assert rings[0][0] == rings[0][-1]


def test_already_closed_way_is_left_alone():
    rings = _stitch_rings([SQUARE])
    assert rings == [SQUARE]


def test_separate_rings_stay_separate():
    far = [(100, 100), (100, 110), (110, 110), (110, 100), (100, 100)]
    assert len(_stitch_rings([SQUARE, far])) == 2


def test_hole_goes_only_to_the_ring_containing_it():
    """Every outer used to receive every inner, punching phantom holes."""
    outers = [SQUARE, [(100, 100), (100, 110), (110, 110), (110, 100), (100, 100)]]
    inners = [
        [(2, 2), (2, 4), (4, 4), (4, 2), (2, 2)],
        [(102, 102), (102, 104), (104, 104), (104, 102), (102, 102)],
    ]
    polygons = _build_polygons(outers, inners)
    assert [len(p["inners"]) for p in polygons] == [1, 1]


def test_single_outer_keeps_all_holes():
    inners = [[(2, 2), (2, 4), (4, 4), (4, 2), (2, 2)]]
    assert len(_build_polygons([SQUARE], inners)[0]["inners"]) == 1


def test_point_in_ring():
    assert _point_in_ring((5, 5), SQUARE)
    assert not _point_in_ring((50, 50), SQUARE)


def test_nodes_without_coordinates_are_dropped():
    """A null lat/lon used to crash the renderer with a TypeError."""
    layers = parse_osm_layers({"elements": [
        {"type": "node", "id": 1, "lat": None, "lon": None},
        {"type": "node", "id": 2, "lat": 53.5, "lon": 9.9},
        {"type": "way", "id": 10, "nodes": [1, 2, 1, 1], "tags": {"building": "yes"}},
    ]})
    assert layers["buildings"] == []


def test_projection_is_accurate_to_a_metre():
    lat, lon = 53.5581, 9.9632
    assert latlon_to_metric(lat, lon, lat, lon) == (0.0, 0.0)
    _, north = latlon_to_metric(lat + 0.01, lon, lat, lon)
    assert abs(north - 1112.6) < 1.0
