from unittest.mock import MagicMock, mock_open, patch

from pdftl.core.core_types import OpResult
import pdftl.core.constants as c
from pdftl.operations.dump_colorspaces import (
    _collect_icc_profiles,
    _extract_colorspace_info,
    _extract_page_info,
    _parse_colorspaces_args,
    _serialize_cs,
    dump_colorspaces,
    dump_colorspaces_cli_hook,
)


class MockPikepdf:
    pass


def test_parse_colorspaces_args():
    assert _parse_colorspaces_args(None) == ([], "summary")
    assert _parse_colorspaces_args(["pages", "1"]) == (["1"], "pages")
    assert _parse_colorspaces_args(["full", "pages"]) == ([], "full")


def test_extract_page_info():
    page = MagicMock()
    page.get.return_value = {}
    pikepdf_mock = MockPikepdf()

    def mock_walk(*args, **kwargs):
        kwargs["detail"]["images"].append(
            {"family": "rgb", "profile_name": "sRGB", "none_val": None}
        )
        kwargs["detail"]["strokes"].append(
            {"family": "icc", "icc_family": "cmyk", "components": 4}
        )

    with patch("pdftl.operations.dump_colorspaces.walk_page", side_effect=mock_walk):
        res_full = _extract_page_info(page, 1, pikepdf_mock, full=True)
        assert "cmyk" in res_full["families"]
        assert len(res_full["icc_profiles"]) == 1
        assert "detail" in res_full

        # Test serialization of None values
        assert "none_val" not in res_full["detail"]["images"][0]

    # Test AttributeError branch
    page.get.side_effect = AttributeError("No resources")
    res_err = _extract_page_info(page, 2, pikepdf_mock, full=False)
    assert res_err["families"] == []


def test_collect_icc_profiles():
    cs_list = [
        {"family": "icc", "icc_family": "rgb", "components": 3, "profile_name": "sRGB"},
        {
            "family": "icc",
            "icc_family": "rgb",
            "components": 3,
            "profile_name": "sRGB",
        },  # Duplicate
        {"family": "icc", "icc_family": "cmyk", "alternate": "devicergb"},
        {"family": "rgb"},  # Ignored
    ]
    res = _collect_icc_profiles(cs_list)
    assert len(res) == 2
    assert res[1]["alternate"] == "devicergb"


def test_serialize_cs():
    assert _serialize_cs({"valid": 1, "invalid": None}) == {"valid": 1}


@patch("pdftl.operations.dump_colorspaces._extract_page_info")
def test_extract_colorspace_info(mock_page_info):
    mock_pdf = MagicMock()
    mock_pdf.pages = [1, 2]

    mock_page_info.return_value = {
        "page": 1,
        "families": ["rgb", "cmyk"],
        "icc_profiles": [{"icc_family": "rgb", "profile_name": "sRGB"}],
    }

    # Test level: pages
    res_pages = _extract_colorspace_info(mock_pdf, ["1"], level="pages")
    assert "summary" in res_pages
    assert "pages" in res_pages
    assert len(res_pages["pages"]) == 1

    # Test level: summary (default)
    res_summary = _extract_colorspace_info(mock_pdf, None, level="summary")
    assert "pages" not in res_summary
    assert res_summary["summary"]["families"] == ["cmyk", "rgb"]


@patch("pdftl.operations.dump_colorspaces.smart_open_maybe_dash")
def test_dump_colorspaces_cli_hook(mock_open_file):
    mock_f = mock_open().return_value
    mock_open_file.return_value.__enter__.return_value = mock_f

    result = OpResult(success=True, data={"test": 1}, meta={c.META_OUTPUT_FILE: "out.json"})
    dump_colorspaces_cli_hook(result, None, None)

    mock_open_file.assert_called_once_with("out.json")
    mock_f.write.assert_called()


@patch("pdftl.operations.dump_colorspaces._extract_colorspace_info")
def test_dump_colorspaces(mock_extract):
    mock_extract.return_value = {"summary": {}}
    mock_pdf = MagicMock()

    res = dump_colorspaces(mock_pdf, ["full"], "out.json")
    assert res.success is True
    assert res.data == {"summary": {}}
    assert res.meta[c.META_OUTPUT_FILE] == "out.json"
