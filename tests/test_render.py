import pytest

from schwarzplan_engine import (
    OSM_ATTRIBUTION,
    generate_schwarzplan,
    parse_osm_layers,
    render_dxf,
    render_pdf,
    render_svg,
)

# A block with a courtyard, a pond and a park, built by hand so the tests need
# no network and no cached fixtures.
OSM = {"elements": [
    {"type": "node", "id": 1, "lat": 53.5580, "lon": 9.9630},
    {"type": "node", "id": 2, "lat": 53.5585, "lon": 9.9630},
    {"type": "node", "id": 3, "lat": 53.5585, "lon": 9.9640},
    {"type": "node", "id": 4, "lat": 53.5580, "lon": 9.9640},
    {"type": "node", "id": 5, "lat": 53.5581, "lon": 9.9632},
    {"type": "node", "id": 6, "lat": 53.5584, "lon": 9.9632},
    {"type": "node", "id": 7, "lat": 53.5584, "lon": 9.9638},
    {"type": "node", "id": 8, "lat": 53.5581, "lon": 9.9638},
    {"type": "way", "id": 100, "nodes": [1, 2, 3, 4, 1], "tags": {}},
    {"type": "way", "id": 101, "nodes": [5, 6, 7, 8, 5], "tags": {}},
    {"type": "relation", "id": 200, "tags": {"type": "multipolygon", "building": "yes"},
     "members": [{"type": "way", "ref": 100, "role": "outer"},
                 {"type": "way", "ref": 101, "role": "inner"}]},
    {"type": "node", "id": 20, "lat": 53.5590, "lon": 9.9650},
    {"type": "node", "id": 21, "lat": 53.5592, "lon": 9.9650},
    {"type": "node", "id": 22, "lat": 53.5592, "lon": 9.9655},
    {"type": "node", "id": 23, "lat": 53.5590, "lon": 9.9655},
    {"type": "way", "id": 300, "nodes": [20, 21, 22, 23, 20],
     "tags": {"natural": "water"}},
    {"type": "way", "id": 301, "nodes": [20, 21, 22, 23, 20],
     "tags": {"leisure": "park"}},
]}

PAGE = dict(center_lat=53.5581, center_lon=9.9632, paper_w_mm=420.0,
            paper_h_mm=297.0, margin_mm=15.0, real_w_m=390.0, real_h_m=267.0)


@pytest.fixture
def layers():
    return parse_osm_layers(OSM)


def test_courtyard_survives_parsing(layers):
    assert len(layers["buildings"]) == 1
    assert len(layers["buildings"][0]["inners"]) == 1


def test_water_and_greenery_parsed(layers):
    assert len(layers["water"]) == 1
    assert len(layers["greenery"]) == 1


def test_pdf_is_structurally_valid(tmp_path, layers):
    out = tmp_path / "p.pdf"
    render_pdf(layers, output_path=str(out), **PAGE)
    data = out.read_bytes()
    assert data.startswith(b"%PDF-1.4")
    assert data.rstrip().endswith(b"%%EOF")
    assert b"startxref" in data
    # Every object needs an xref entry or readers reject the file.
    assert data.count(b" obj\n") == data.count(b"\nendobj\n")


def test_pdf_carries_odbl_attribution(tmp_path, layers):
    out = tmp_path / "p.pdf"
    render_pdf(layers, output_path=str(out), **PAGE)
    assert b"OpenStreetMap contributors" in out.read_bytes()


def test_pdf_declares_a_font_for_the_attribution(tmp_path, layers):
    out = tmp_path / "p.pdf"
    render_pdf(layers, output_path=str(out), **PAGE)
    assert b"/Font" in out.read_bytes()


def test_svg_is_wellformed_and_attributed(tmp_path, layers):
    import xml.etree.ElementTree as ET
    out = tmp_path / "p.svg"
    render_svg(layers, output_path=str(out), **PAGE)
    text = out.read_text(encoding="utf-8")
    ET.fromstring(text)  # raises if malformed
    assert OSM_ATTRIBUTION in text
    assert 'id="buildings_layer"' in text


def test_dxf_has_layer_table_and_attribution(tmp_path, layers):
    out = tmp_path / "p.dxf"
    render_dxf(layers, 53.5581, 9.9632, str(out))
    text = out.read_text(encoding="utf-8")
    assert text.startswith("999\n")
    assert "OpenStreetMap contributors" in text
    assert "BUILDINGS" in text and "WATER" in text
    assert text.rstrip().endswith("EOF")


def test_unknown_extension_falls_back_to_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr("schwarzplan_engine.fetch_osm_layers", lambda *a, **k: OSM)
    out = tmp_path / "plan.bogus"
    result = generate_schwarzplan(
        center_lat=53.5581, center_lon=9.9632, scale=1000,
        paper_size="A3 Landscape", margin_mm=15.0,
        output_path=str(out), include_buildings=True,
    )
    assert result["success"]
    assert result["output_path"].endswith(".pdf")


def test_generation_reports_counts(tmp_path, monkeypatch):
    monkeypatch.setattr("schwarzplan_engine.fetch_osm_layers", lambda *a, **k: OSM)
    result = generate_schwarzplan(
        center_lat=53.5581, center_lon=9.9632, scale=1000,
        paper_size="A3 Landscape", margin_mm=15.0,
        output_path=str(tmp_path / "plan.svg"),
        include_buildings=True, include_water=True, include_greenery=True,
    )
    assert result["success"]
    assert result["building_count"] == 1
    assert result["water_count"] == 1
    assert result["greenery_count"] == 1
