# tests/operations/test_create.py

from unittest.mock import MagicMock

import pytest

from pdftl.exceptions import UserCommandLineError
from pdftl.operations.parsers.create_parser import parse_create_args

# ---------------------------------------------------------------------------
# parse_create_args
# ---------------------------------------------------------------------------


class TestParseCreateArgs:
    def test_empty_args_returns_default(self):
        result = parse_create_args([])
        assert result == [{"count": 1, "geometry": None}]

    def test_bare_count(self):
        result = parse_create_args(["3"])
        assert result == [{"count": 3, "geometry": None}]

    def test_geometry_only(self):
        result = parse_create_args(["(A4)"])
        assert result == [{"count": 1, "geometry": "A4"}]

    def test_count_and_geometry(self):
        result = parse_create_args(["5(letter)"])
        assert result == [{"count": 5, "geometry": "letter"}]

    def test_multiple_specs(self):
        result = parse_create_args(["2(A4)", "3(letter)"])
        assert result == [
            {"count": 2, "geometry": "A4"},
            {"count": 3, "geometry": "letter"},
        ]

    def test_invalid_arg_raises(self):
        with pytest.raises(UserCommandLineError, match="Unexpected argument"):
            parse_create_args(["notvalid"])


# ---------------------------------------------------------------------------
# create_pdf
# ---------------------------------------------------------------------------

from pdftl.operations.create import create_pdf


def _make_mock_pdf(page_count=1):
    """Return a minimal pikepdf.Pdf-like mock."""
    mock_pdf = MagicMock()
    mock_pdf.pages = MagicMock()
    mock_pdf.pages.__len__ = MagicMock(return_value=page_count)
    return mock_pdf


@pytest.fixture()
def mock_pikepdf(monkeypatch):
    """Patch pikepdf inside the create module."""
    mock = MagicMock()
    mock_pdf = _make_mock_pdf(1)
    mock.new.return_value = mock_pdf
    mock.Array.side_effect = lambda x: x  # pass-through
    monkeypatch.setattr("pdftl.operations.create.pikepdf", mock, raising=False)

    # Also patch the lazy `import pikepdf` inside create_pdf
    import sys

    sys.modules["pikepdf"] = mock
    yield mock, mock_pdf


class TestCreatePdf:
    def test_no_args_creates_one_a4_page(self):
        result = create_pdf([])
        assert result.success is True
        assert result.pdf is not None

    def test_explicit_count_and_standard_geometry(self):
        result = create_pdf(["2(A4)"])
        assert result.success is True

    def test_letter_size(self):
        result = create_pdf(["1(letter)"])
        assert result.success is True

    def test_custom_geometry_cm(self):
        result = create_pdf(["1(20cm,10cm)"])
        assert result.success is True

    def test_custom_geometry_mm(self):
        result = create_pdf(["1(200mm,100mm)"])
        assert result.success is True

    def test_unknown_geometry_raises(self):
        with pytest.raises(UserCommandLineError, match="Unknown geometry"):
            create_pdf(["1(totallymadeup)"])

    def test_invalid_custom_geometry_raises(self):
        # dim_str_to_pts raises InvalidArgumentError for unknown units,
        # which is not caught by the ValueError/KeyError handler in create.py
        from pdftl.exceptions import InvalidArgumentError

        with pytest.raises(InvalidArgumentError, match="Could not parse numeric dimension"):
            create_pdf(["1(99zz,10zz)"])

    def test_invalid_custom_geometry_wrong_part_count(self):
        # Three parts instead of two
        with pytest.raises(UserCommandLineError, match="Invalid custom geometry"):
            create_pdf(["1(10cm,20cm,30cm)"])

    def test_multiple_specs(self):
        result = create_pdf(["2(A4)", "1(letter)"])
        assert result.success is True

    def test_multiple_pages_same_spec(self):
        result = create_pdf(["5(A4)"])
        assert result.success is True
