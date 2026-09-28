# tests/utils/test_pikepdf_compatibility_utils.py

import sys
from unittest.mock import MagicMock, patch

from pdftl.utils.pikepdf_compatibility_utils import (
    as_pil_image_compat,
    outline_item_has_style_properties,
    pikepdf_version,
    pikepdf_version_at_least,
    set_outline_item_style_compat,
)


def test_pikepdf_version_standard():
    """Test standard semantic version parsing."""
    mock_pikepdf = MagicMock()
    mock_pikepdf.__version__ = "10.10.1"

    with patch.dict(sys.modules, {"pikepdf": mock_pikepdf}):
        assert pikepdf_version() == [10, 10, 1]


def test_pikepdf_version_with_string_suffix():
    """Test version parsing stops gracefully at non-integer strings (e.g. dev versions)."""
    mock_pikepdf = MagicMock()
    mock_pikepdf.__version__ = "10.0.dev4"

    with patch.dict(sys.modules, {"pikepdf": mock_pikepdf}):
        assert pikepdf_version() == [10, 0]


def test_pikepdf_version_completely_invalid():
    """Test fallback when the version string contains no parseable integers."""
    mock_pikepdf = MagicMock()
    mock_pikepdf.__version__ = "invalid_version"

    with patch.dict(sys.modules, {"pikepdf": mock_pikepdf}):
        assert pikepdf_version() == [0, 0, 0]


@patch("pdftl.utils.pikepdf_compatibility_utils.pikepdf_version")
def test_pikepdf_version_at_least_int(mock_version):
    """Test version comparison when an integer is passed."""
    mock_version.return_value = [9, 0, 0]

    assert pikepdf_version_at_least(9) is True
    assert pikepdf_version_at_least(10) is False


@patch("pdftl.utils.pikepdf_compatibility_utils.pikepdf_version")
def test_pikepdf_version_at_least_list(mock_version):
    """Test version comparison when a list is passed."""
    mock_version.return_value = [10, 1, 0]

    assert pikepdf_version_at_least([10, 0, 0]) is True
    assert pikepdf_version_at_least([10, 2, 0]) is False


@patch("pdftl.utils.pikepdf_compatibility_utils.pikepdf_version")
def test_pikepdf_version_at_least_invalid_type(mock_version):
    """Test version comparison returns False for unhandled types like strings."""
    mock_version.return_value = [10, 1, 0]

    assert pikepdf_version_at_least("10.0.0") is False


@patch("pdftl.utils.pikepdf_compatibility_utils.pikepdf_version_at_least")
def test_as_pil_image_compat_new_pikepdf(mock_version_at_least):
    """Test PIL extraction applies the mask flag for pikepdf >= 10.10.0."""
    mock_version_at_least.return_value = True
    mock_image = MagicMock()

    as_pil_image_compat(mock_image)

    mock_image.as_pil_image.assert_called_once_with(apply_mask=False)


@patch("pdftl.utils.pikepdf_compatibility_utils.pikepdf_version_at_least")
def test_as_pil_image_compat_old_pikepdf(mock_version_at_least):
    """Test PIL extraction skips the mask flag for older pikepdf versions."""
    mock_version_at_least.return_value = False
    mock_image = MagicMock()

    as_pil_image_compat(mock_image)

    mock_image.as_pil_image.assert_called_once_with()


@patch("pdftl.utils.pikepdf_compatibility_utils.pikepdf_version_at_least")
def test_outline_item_has_style_properties_true(mock_version_at_least):
    mock_version_at_least.return_value = True
    assert outline_item_has_style_properties() is True
    mock_version_at_least.assert_called_once_with([10, 11, 0])


@patch("pdftl.utils.pikepdf_compatibility_utils.pikepdf_version_at_least")
def test_outline_item_has_style_properties_false(mock_version_at_least):
    mock_version_at_least.return_value = False
    assert outline_item_has_style_properties() is False


@patch("pdftl.utils.pikepdf_compatibility_utils.pikepdf_version_at_least")
def test_set_outline_item_style_compat_new_pikepdf_all_props(mock_version_at_least):
    """pikepdf >= 10.11.0: color/bold/italic set via property setters."""
    mock_version_at_least.return_value = True
    item = MagicMock()

    set_outline_item_style_compat(item, color=(1.0, 0.0, 0.0), bold=True, italic=True)

    assert item.color == (1.0, 0.0, 0.0)
    assert item.bold is True
    assert item.italic is True


@patch("pdftl.utils.pikepdf_compatibility_utils.pikepdf_version_at_least")
def test_set_outline_item_style_compat_new_pikepdf_no_color_no_style(mock_version_at_least):
    """When color is None and bold/italic are both False, none of the
    property setters should be touched at all."""
    mock_version_at_least.return_value = True
    item = MagicMock()

    set_outline_item_style_compat(item, color=None, bold=False, italic=False)

    assert not hasattr(item, "color") or isinstance(item.color, MagicMock)
    # Ensure attributes were never explicitly assigned by checking call absence
    # via a fresh MagicMock's default attribute access (auto-created, not set).
    assert "color" not in item.__dict__ if hasattr(item, "__dict__") else True


@patch("pdftl.utils.pikepdf_compatibility_utils.pikepdf_version_at_least")
def test_set_outline_item_style_compat_old_pikepdf_color_and_bold(mock_version_at_least):
    """pikepdf < 10.11.0: falls back to raw obj.C / obj.F mutation."""
    mock_version_at_least.return_value = False
    mock_pikepdf = MagicMock()
    mock_pikepdf.Array = list
    item = MagicMock()

    with patch.dict(sys.modules, {"pikepdf": mock_pikepdf}):
        set_outline_item_style_compat(item, color=(0.0, 1.0, 0.0), bold=True, italic=False)

    assert item.obj.C == [0.0, 1.0, 0.0]
    assert item.obj.F == 2


@patch("pdftl.utils.pikepdf_compatibility_utils.pikepdf_version_at_least")
def test_set_outline_item_style_compat_old_pikepdf_italic_only(mock_version_at_least):
    """bold=False, italic=True -> F flag should be 1 (italic bit only)."""
    mock_version_at_least.return_value = False
    mock_pikepdf = MagicMock()
    item = MagicMock()

    with patch.dict(sys.modules, {"pikepdf": mock_pikepdf}):
        set_outline_item_style_compat(item, color=None, bold=False, italic=True)

    assert item.obj.F == 1


@patch("pdftl.utils.pikepdf_compatibility_utils.pikepdf_version_at_least")
def test_set_outline_item_style_compat_old_pikepdf_no_color_no_style(mock_version_at_least):
    """Neither color nor bold/italic set -> obj.C/obj.F must not be touched."""
    mock_version_at_least.return_value = False
    mock_pikepdf = MagicMock()
    item = MagicMock()

    with patch.dict(sys.modules, {"pikepdf": mock_pikepdf}):
        set_outline_item_style_compat(item, color=None, bold=False, italic=False)

    item.obj.C.__class__  # no-op access just to ensure no exception
    assert not item.obj.C.called
    assert not item.obj.F.called


def _image(pdf, **entries):
    import pikepdf

    img = pikepdf.Stream(pdf, b"\x00" * 16)
    img.Type, img.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
    img.Width, img.Height, img.BitsPerComponent = 4, 4, 8
    for key, value in entries.items():
        img[f"/{key}"] = value
    return img


def test_image_extraction_errors_are_pikepdf_image_exceptions():
    import pikepdf

    from pdftl.utils.pikepdf_compatibility_utils import image_extraction_errors

    errors = image_extraction_errors()
    assert errors
    for cls in errors:
        assert issubclass(cls, Exception)
        assert cls.__module__.startswith("pikepdf")
    if hasattr(pikepdf, "UnsupportedImageTypeError"):
        assert pikepdf.UnsupportedImageTypeError in errors


def test_image_extraction_errors_without_the_private_module(monkeypatch):
    import pikepdf
    import pikepdf.models

    from pdftl.utils.pikepdf_compatibility_utils import image_extraction_errors

    monkeypatch.delattr(pikepdf.models, "_image_exceptions", raising=False)
    monkeypatch.setitem(sys.modules, "pikepdf.models._image_exceptions", None)
    public = [getattr(pikepdf, name) for name in dir(pikepdf)]
    assert all(cls in public for cls in image_extraction_errors())


def test_image_decode_errors_catch_undecodable_image_data():
    import pikepdf

    from pdftl.utils.pikepdf_compatibility_utils import image_decode_errors

    errors = image_decode_errors()
    assert pikepdf.DependencyError in errors
    pdf = pikepdf.new()
    img = _image(pdf, ColorSpace=pikepdf.Name.DeviceRGB, Filter=pikepdf.Name.DCTDecode)
    try:
        pikepdf.PdfImage(img).as_pil_image()
    except errors:
        pass
    else:
        raise AssertionError("garbage DCT data decoded")


def test_is_indexed_image():
    import pikepdf

    from pdftl.utils.pikepdf_compatibility_utils import is_indexed_image

    pdf = pikepdf.new()
    palette = pikepdf.Array([pikepdf.Name.Indexed, pikepdf.Name.DeviceRGB, 1, b"\x00" * 6])
    assert is_indexed_image(_image(pdf, ColorSpace=palette))
    assert not is_indexed_image(_image(pdf, ColorSpace=pikepdf.Name.DeviceGray))
    assert not is_indexed_image(_image(pdf, ColorSpace=pikepdf.Array([])))
    assert not is_indexed_image(_image(pdf))


def test_drop_stale_decode_array():
    import pikepdf

    from pdftl.utils.pikepdf_compatibility_utils import drop_stale_decode_array

    pdf = pikepdf.new()
    palette = pikepdf.Array([pikepdf.Name.Indexed, pikepdf.Name.DeviceRGB, 1, b"\x00" * 6])
    decode = pikepdf.Array([1, 0])

    kept = _image(pdf, ColorSpace=palette, Decode=decode)
    drop_stale_decode_array(kept, source_was_indexed=True)
    assert "/Decode" in kept

    for img, was_indexed in [
        (_image(pdf, ColorSpace=pikepdf.Name.DeviceGray, Decode=decode), True),
        (_image(pdf, ColorSpace=palette, Decode=decode), False),
        (_image(pdf, ColorSpace=pikepdf.Name.DeviceGray, Decode=decode), False),
    ]:
        drop_stale_decode_array(img, source_was_indexed=was_indexed)
        assert "/Decode" not in img

    bare = _image(pdf, ColorSpace=pikepdf.Name.DeviceGray)
    drop_stale_decode_array(bare, source_was_indexed=False)
    assert "/Decode" not in bare
