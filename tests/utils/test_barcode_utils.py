import pytest
from unittest.mock import MagicMock, patch
import numpy as np
from PIL import Image

from pdftl.utils.barcode_utils import _get_zxing, scan_image, scan_pdf_pages, generate_barcode


@pytest.fixture
def mock_zxing():
    """Fakes the zxingcpp module structure cleanly."""
    mock_mod = MagicMock()
    mock_mod.BarcodeFormat.QRCode = "QRCode"
    mock_mod.BarcodeFormat.Code128 = "Code128"

    with (
        patch("pdftl.utils.barcode_utils.ensure_dependencies") as mock_ensure,
        patch.dict("sys.modules", {"zxingcpp": mock_mod}),
    ):
        mock_ensure.assert_not_called()
        yield mock_mod


def test_get_zxing_lazy_load():
    """Verifies that third-party zxingcpp is checked and lazy-loaded on demand."""
    mock_zxing_mod = MagicMock()

    with (
        patch("pdftl.utils.barcode_utils.ensure_dependencies") as mock_ensure,
        patch.dict("sys.modules", {"zxingcpp": mock_zxing_mod}),
    ):
        res = _get_zxing()
        mock_ensure.assert_called_once_with("barcode", ["zxingcpp"], "barcode")
        assert res is mock_zxing_mod


def test_scan_image_success(mock_zxing):
    mock_result = MagicMock()
    mock_result.text = "payload_data"
    mock_result.format.name = "QRCode"
    mock_result.content_type.name = "Text"
    mock_result.position.top_left.x = 10
    mock_result.position.top_left.y = 20
    mock_result.position.bottom_right.x = 100
    mock_result.position.bottom_right.y = 200

    mock_zxing.read_barcodes.return_value = [mock_result]

    fake_img = Image.new("RGB", (10, 10))
    output = scan_image(fake_img)

    assert len(output) == 1
    assert output[0]["text"] == "payload_data"


def test_scan_pdf_pages(mock_zxing):
    fake_pdf = MagicMock()
    fake_img = Image.new("RGB", (10, 10))
    mock_iter = [(0, fake_img)]

    mock_result = MagicMock()
    mock_result.text = "page_data"
    mock_result.format.name = "Code128"
    mock_result.content_type.name = "Text"
    mock_result.position.top_left.x = 0
    mock_result.position.top_left.y = 0
    mock_result.position.bottom_right.x = 5
    mock_result.position.bottom_right.y = 5

    mock_zxing.read_barcodes.return_value = [mock_result]

    with patch("pdftl.utils.page_images.iter_pages_as_pil", return_value=mock_iter):
        results = scan_pdf_pages(fake_pdf, dpi=150.0, page_indices=[0])
        assert 0 in results


def test_generate_barcode_success(mock_zxing):
    mock_barcode = MagicMock()
    mock_barcode.to_image.return_value = np.zeros((50, 50, 3), dtype=np.uint8)
    mock_zxing.create_barcode.return_value = mock_barcode

    img = generate_barcode("hello", format_name="QRCode", scale=5)
    assert isinstance(img, Image.Image)


def test_generate_barcode_invalid_format(mock_zxing):
    mock_zxing.BarcodeFormat = MagicMock(spec=["QRCode", "Code128"])

    with pytest.raises(ValueError, match="Invalid barcode format 'InvalidFormat'"):
        generate_barcode("hello", format_name="InvalidFormat")
