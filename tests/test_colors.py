import pytest

from schwarzplan_engine import hex_to_rgb, normalize_hex


@pytest.mark.parametrize("value,expected", [
    ("#C5DCE8", "#C5DCE8"),
    ("c5dce8", "#C5DCE8"),
    ("#abc", "#AABBCC"),
    ("  #C5DCE8  ", "#C5DCE8"),
])
def test_normalize_accepts_valid_spellings(value, expected):
    assert normalize_hex(value) == expected


@pytest.mark.parametrize("value", [
    "#12", "", "   ", "#GGGGGG", "#12345", None, "zzzzzz",
    # int(x, 16) accepts these, so a naive parse would let them through.
    "+12345", "-12345", "1_2345",
])
def test_normalize_rejects_unreadable(value):
    assert normalize_hex(value) is None


def test_half_typed_colour_does_not_become_black():
    """A partially typed hex used to export as solid black."""
    assert hex_to_rgb("#C5DC", (1.0, 1.0, 1.0)) == (1.0, 1.0, 1.0)
    assert hex_to_rgb("#C5", (1.0, 1.0, 1.0)) == (1.0, 1.0, 1.0)


def test_valid_colour_converts():
    assert hex_to_rgb("#FFFFFF") == (1.0, 1.0, 1.0)
    assert hex_to_rgb("#000000") == (0.0, 0.0, 0.0)
