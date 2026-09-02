"""The format dropdown, not the typed extension, decides the export format."""
import pytest

from main import with_extension


@pytest.mark.parametrize("name,fmt,expected", [
    ("schwarzplan_a3_1_1000.pdf", "svg", "schwarzplan_a3_1_1000.svg"),
    ("schwarzplan.svg", "pdf", "schwarzplan.pdf"),
    ("plan.dxf", "dxf", "plan.dxf"),
    ("no_extension", "pdf", "no_extension.pdf"),
    ("PLAN.PDF", "svg", "PLAN.svg"),
])
def test_extension_follows_the_selected_format(name, fmt, expected):
    assert with_extension(name, fmt) == expected


@pytest.mark.parametrize("name", ["", "   ", None])
def test_blank_name_gets_a_default(name):
    assert with_extension(name, "pdf") == "plan.pdf"


@pytest.mark.parametrize("name", [
    "../../../etc/passwd",
    "/tmp/elsewhere.pdf",
    "sub/dir/plan.pdf",
])
def test_directory_parts_are_stripped(name):
    """A typed path separator must not redirect where the file is written."""
    result = with_extension(name, "pdf")
    assert "/" not in result and ".." not in result


def test_only_the_trailing_extension_is_replaced():
    assert with_extension("plan.v2.pdf", "svg") == "plan.v2.svg"
