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


def test_osm_data_attribution_names_openstreetmap_and_the_licence():
    assert "OpenStreetMap" in OSM_ATTRIBUTION
    assert "ODbL" in OSM_ATTRIBUTION
