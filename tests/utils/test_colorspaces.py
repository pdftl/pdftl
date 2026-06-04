import pytest
from unittest.mock import MagicMock, patch

from pdftl.utils.colorspaces import (
    effective_family,
    family_from_device_op,
    image_colorspace,
    resolve_array,
    resolve_colorspace,
    resolve_device_n,
    resolve_icc,
    resolve_indexed,
    resolve_name,
    resolve_named_cs,
    resolve_separation,
    resolve_shading_cs,
)


class MockPikepdf:
    Name = str
    Array = list
    PdfError = ValueError


@pytest.fixture
def pikepdf_mock():
    return MockPikepdf()


def test_family_from_device_op():
    assert family_from_device_op("g") == "gray"
    assert family_from_device_op("RG") == "rgb"
    assert family_from_device_op("k") == "cmyk"
    assert family_from_device_op("unknown") == "unknown"


def test_resolve_colorspace(pikepdf_mock):
    assert resolve_colorspace(None, None, pikepdf_mock) == {"family": "unknown", "raw": "None"}
    assert resolve_colorspace("/DeviceRGB", None, pikepdf_mock) == {"family": "rgb"}
    assert resolve_colorspace(["/DeviceGray"], None, pikepdf_mock) == {
        "family": "unknown",
        "raw": "/DeviceGray",
    }
    assert resolve_colorspace(123, None, pikepdf_mock) == {"family": "unknown", "raw": "123"}


def test_resolve_name():
    assert resolve_name("/DeviceGray") == {"family": "gray"}
    assert resolve_name("/CustomName") == {"family": "unknown", "raw": "/CustomName"}


def test_resolve_array(pikepdf_mock):
    assert resolve_array([], None, pikepdf_mock) == {"family": "unknown", "raw": "[]"}

    # Pattern
    assert resolve_array(["/Pattern"], None, pikepdf_mock) == {"family": "pattern"}
    assert resolve_array(["/Pattern", "/DeviceRGB"], None, pikepdf_mock) == {
        "family": "pattern",
        "base": {"family": "rgb"},
    }

    # Unknown
    assert resolve_array(["/Custom"], None, pikepdf_mock) == {
        "family": "unknown",
        "raw": "/Custom",
    }

    # Coverage for routing other array types correctly (Contexts cleaned up for Ruff)
    with patch("pdftl.utils.colorspaces.resolve_icc", return_value={"family": "icc"}):
        assert resolve_array(["/ICCBased"], None, pikepdf_mock) == {"family": "icc"}
    with patch("pdftl.utils.colorspaces.resolve_separation", return_value={"family": "spot"}):
        assert resolve_array(["/Separation"], None, pikepdf_mock) == {"family": "spot"}
    with patch("pdftl.utils.colorspaces.resolve_device_n", return_value={"family": "spot"}):
        assert resolve_array(["/DeviceN"], None, pikepdf_mock) == {"family": "spot"}
    with patch("pdftl.utils.colorspaces.resolve_indexed", return_value={"family": "indexed"}):
        assert resolve_array(["/Indexed"], None, pikepdf_mock) == {"family": "indexed"}


def test_resolve_icc(pikepdf_mock):
    stream = MagicMock()
    stream.get.side_effect = (
        lambda k, d=None: 3 if k == "/N" else "/DeviceRGB" if k == "/Alternate" else d
    )

    # Build a minimal spec-valid ICC profile with a v2 'desc' tag.
    # Layout: 128-byte header | tag_count (4) | tag_table (12) | tag_data
    name_bytes = b"sRGB\x00"
    tag_data = (
        b"desc"  # type signature
        + b"\x00" * 4  # reserved
        + len(name_bytes).to_bytes(4, "big")  # ASCII string length
        + name_bytes
    )
    tag_data_offset = 128 + 4 + 12  # header + tag_count field + one 12-byte table entry
    tag_table_entry = (
        b"desc" + tag_data_offset.to_bytes(4, "big") + len(tag_data).to_bytes(4, "big")
    )
    raw = b"\x00" * 128 + (1).to_bytes(4, "big") + tag_table_entry + tag_data
    stream.read_raw_bytes.return_value = raw

    res = resolve_icc(["/ICCBased", stream], pikepdf_mock)
    assert res["components"] == 3
    assert res["icc_family"] == "rgb"
    assert res["alternate"] == "devicergb"
    assert res["profile_name"] == "sRGB"

    # PdfError path — profile_name should be absent
    stream.read_raw_bytes.side_effect = pikepdf_mock.PdfError("Bad stream")
    res2 = resolve_icc(["/ICCBased", stream], pikepdf_mock)
    assert "profile_name" not in res2

    # IndexError path
    assert resolve_icc(["/ICCBased"], pikepdf_mock) == {"family": "icc"}


def test_resolve_separation(pikepdf_mock):
    res = resolve_separation(["/Separation", "/Cyan", "/DeviceCMYK"], None, pikepdf_mock)
    assert res["colorants"] == ["Cyan"]
    assert res["alternate_family"] == "cmyk"

    # IndexError fallback
    assert resolve_separation(["/Separation"], None, pikepdf_mock) == {"family": "spot"}


def test_resolve_device_n(pikepdf_mock):
    res = resolve_device_n(["/DeviceN", ["/Cyan", "/Magenta"], "/DeviceCMYK"], None, pikepdf_mock)
    assert res["colorants"] == ["Cyan", "Magenta"]
    assert res["alternate_family"] == "cmyk"

    # IndexError fallback
    assert resolve_device_n(["/DeviceN"], None, pikepdf_mock) == {"family": "spot"}


def test_resolve_indexed(pikepdf_mock):
    res = resolve_indexed(["/Indexed", "/DeviceRGB", 255], None, pikepdf_mock)
    assert res["base_family"] == "rgb"
    assert res["hival"] == 255

    # IndexError fallback
    assert resolve_indexed(["/Indexed"], None, pikepdf_mock) == {"family": "indexed"}


def test_resolve_named_cs(pikepdf_mock):
    resources = MagicMock()
    resources.get.return_value = {"/CS1": "/DeviceRGB"}

    assert resolve_named_cs("CS1", resources, pikepdf_mock) == {"family": "rgb"}
    assert resolve_named_cs("/Unknown", resources, pikepdf_mock) == {
        "family": "unknown",
        "raw": "/Unknown",
    }

    # AttributeError fallback
    resources.get.side_effect = AttributeError
    assert resolve_named_cs("CS1", resources, pikepdf_mock) == {"family": "unknown", "raw": "/CS1"}


def test_image_colorspace(pikepdf_mock):
    xobj = MagicMock()
    xobj.get.return_value = "/DeviceRGB"
    assert image_colorspace(xobj, None, pikepdf_mock) == {"family": "rgb"}

    xobj.get.return_value = None
    assert image_colorspace(xobj, None, pikepdf_mock) == {
        "family": "unknown",
        "raw": "Image without ColorSpace",
    }


def test_resolve_shading_cs(pikepdf_mock):
    resources = MagicMock()
    shading_obj = MagicMock()
    shading_obj.get.return_value = "/DeviceRGB"
    resources.get.return_value = {"Sh1": shading_obj}

    assert resolve_shading_cs("Sh1", resources, pikepdf_mock) == {
        "family": "pattern",
        "base": {"family": "rgb"},
    }

    # Missing objects
    assert resolve_shading_cs("Sh2", resources, pikepdf_mock) == {"family": "pattern"}
    resources.get.return_value = None
    assert resolve_shading_cs("Sh1", resources, pikepdf_mock) == {"family": "pattern"}

    # AttributeError fallback
    resources.get.side_effect = AttributeError
    assert resolve_shading_cs("Sh1", resources, pikepdf_mock) == {"family": "pattern"}


def test_effective_family():
    assert effective_family({"family": "rgb"}) == "rgb"
    assert effective_family({"family": "icc", "icc_family": "cmyk"}) == "cmyk"
    assert effective_family({"family": "icc"}) == "icc"
    assert effective_family({}) == "unknown"
