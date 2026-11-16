import pytest
from tools.client import _parse_file_spec


@pytest.mark.parametrize(
    "spec, total, expected",
    [
        ("A=first.pdf", 2, ("A", "first.pdf")),
        ("AB=first.pdf", 2, ("AB", "first.pdf")),
        ("A=A=x.pdf", 1, ("A", "A=x.pdf")),
        ("Whence F=ma.pdf", 1, ("file", "Whence F=ma.pdf")),
        ("a=b.pdf", 2, ("file_1", "a=b.pdf")),
    ],
)
def test_parse_file_spec_handle_rules(spec, total, expected):
    assert _parse_file_spec(1, spec, total) == expected
