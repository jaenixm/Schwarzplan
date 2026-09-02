"""Every tile source shown in the app has to be credited by name."""
import main
from schwarzplan_engine import OSM_ATTRIBUTION

PROVIDERS = [
    ("arcgisonline.com", "Esri"),
    ("cartocdn.com", "CARTO"),
    ("tile.openstreetmap.org", "OpenStreetMap"),
    ("stadiamaps.com", "Stadia"),
]


def test_each_palette_credits_the_host_it_actually_uses():
    for name, palette in [("dark", main.DARK_PALETTE), ("light", main.LIGHT_PALETTE)]:
        url = palette["tile_url"]
        credit = palette.get("tile_attribution", "")
        assert credit, f"{name} palette has no tile_attribution"
        for host, expected in PROVIDERS:
            if host in url:
                assert expected.lower() in credit.lower(), (
                    f"{name} palette serves tiles from {host} but credits {credit!r}"
                )
                break
        else:
            raise AssertionError(f"{name} palette uses an unrecognised tile host: {url}")


def test_each_palette_has_a_label_overlay_from_the_same_provider():
    """Esri serves labels separately; without them the map has no place names."""
    for name, palette in [("dark", main.DARK_PALETTE), ("light", main.LIGHT_PALETTE)]:
        assert palette.get("label_url"), f"{name} palette has no label_url"
        for host, _ in PROVIDERS:
            if host in palette["tile_url"]:
                assert host in palette["label_url"], (
                    f"{name} palette mixes tile and label providers"
                )
                break


def test_search_never_asks_for_a_zoom_the_map_cannot_show():
    """Esri's grey canvas stops at zoom 16; beyond that the tiles are blank."""
    tiny = ["48.8574753", "48.8590453", "2.2933119", "2.2956897"]
    zero = ["53.5", "53.5", "9.9", "9.9"]
    for bbox in (tiny, zero, None, []):
        z = main.zoom_for_bounds(bbox)
        assert main.MAP_MIN_ZOOM <= z <= main.MAP_MAX_ZOOM, (bbox, z)


def test_osm_data_attribution_names_openstreetmap_and_the_licence():
    assert "OpenStreetMap" in OSM_ATTRIBUTION
    assert "ODbL" in OSM_ATTRIBUTION
