# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/test_export_import_images.py

import io
import json
import zlib
from unittest.mock import patch, MagicMock

import pikepdf
import pytest
from PIL import Image

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.export_import_images import (
    export_images,
    export_images_cli_hook,
    import_images,
    _classify_tokens,
    _file_hash,
)


def _make_image_stream(pdf, color="red", mode="RGB", fmt="flatedecode", is_indirect=True):
    """Helper to create a structurally valid pikepdf.Stream image object."""
    img = Image.new(mode, (10, 10), color=color)
    if fmt == "dctdecode":
        out_buf = io.BytesIO()
        img.save(out_buf, format="JPEG")
        data = out_buf.getvalue()
        filt = pikepdf.Name("/DCTDecode")
    else:
        data = zlib.compress(img.tobytes())
        filt = pikepdf.Name("/FlateDecode")

    if is_indirect:
        stream = pdf.make_stream(data)
    else:
        stream = pikepdf.Stream(pdf, data)

    stream.Type = pikepdf.Name("/XObject")
    stream.Subtype = pikepdf.Name("/Image")
    stream.Width = 10
    stream.Height = 10
    stream.BitsPerComponent = 8
    stream.ColorSpace = (
        pikepdf.Name("/DeviceRGB") if mode != "CMYK" else pikepdf.Name("/DeviceCMYK")
    )
    stream.Filter = filt
    return stream


def _make_pdf_with_image(color="red", mode="RGB", fmt="flatedecode", is_indirect=True):
    """Creates a basic one-page PDF featuring the specified image mapping."""
    pdf = pikepdf.Pdf.new()
    image = _make_image_stream(pdf, color=color, mode=mode, fmt=fmt, is_indirect=is_indirect)
    content = b"q 10 0 0 10 0 0 cm /Im1 Do Q"
    page_obj = pikepdf.Dictionary(
        Type=pikepdf.Name("/Page"),
        MediaBox=pikepdf.Array([0, 0, 100, 100]),
        Contents=pikepdf.Stream(pdf, content),
        Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im1=image)),
    )
    pdf.pages.append(pikepdf.Page(page_obj))
    return pdf


class TestClassifyTokens:
    def test_classify_no_tokens(self):
        assert _classify_tokens([]) == (None, [])

    def test_classify_one_token_is_always_dir(self):
        assert _classify_tokens(["1-5"]) == ("1-5", [])
        assert _classify_tokens(["even"]) == ("even", [])
        assert _classify_tokens(["my_dir"]) == ("my_dir", [])

    def test_classify_multiple_tokens(self):
        assert _classify_tokens(["1-5", "my_dir"]) == ("my_dir", ["1-5"])
        assert _classify_tokens(["my_dir", "even"]) == ("my_dir", ["even"])
        assert _classify_tokens(["1-5", "even"]) == ("even", ["1-5"])


class TestExportImages:
    def test_missing_dir_argument(self):
        pdf = pikepdf.Pdf.new()
        with pytest.raises(InvalidArgumentError, match="Missing required directory argument"):
            export_images(pdf, specs=[])

    def test_successful_export_png_and_jpeg(self, tmp_path):
        pdf = _make_pdf_with_image(color="red", mode="RGB", fmt="flatedecode")
        jpeg_image = _make_image_stream(pdf, color="blue", mode="RGB", fmt="dctdecode")
        content = b"q 10 0 0 10 0 0 cm /Im2 Do Q"
        page_obj = pikepdf.Dictionary(
            Type=pikepdf.Name("/Page"),
            MediaBox=pikepdf.Array([0, 0, 100, 100]),
            Contents=pikepdf.Stream(pdf, content),
            Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im2=jpeg_image)),
        )
        pdf.pages.append(pikepdf.Page(page_obj))

        out_dir = tmp_path / "exports"
        result = export_images(pdf, specs=[str(out_dir)])

        assert result.success is True
        manifest = result.data
        assert "image_streams" in manifest
        assert len(manifest["image_streams"]) == 2

        files = list(out_dir.glob("*"))
        assert len(files) == 2

        exts = {f.suffix for f in files}
        assert ".png" in exts
        assert ".jpg" in exts

    def test_export_skips_inline_images(self, tmp_path):
        """Verifies that direct/inline images are correctly filtered out to prevent objgen collisions."""
        pdf = _make_pdf_with_image()
        out_dir = tmp_path / "exports"

        # Mock the return value of extract_pdf_images to simulate an inline/direct stream object
        mock_xobj = MagicMock()
        mock_xobj.is_indirect = False

        mock_image_data = [
            {
                "page": 1,
                "name": "/Im1",
                "bbox": [0, 0, 10, 10],
                "ppi_x": 72,
                "ppi_y": 72,
                "width_px": 10,
                "height_px": 10,
                "colorspace": {"family": "rgb"},
                "bits": 8,
                "stream_bytes": 100,
                "format": "flatedecode",
                "xobj": mock_xobj,
            }
        ]

        with patch("pdftl.utils.images.finders.extract_pdf_images", return_value=mock_image_data):
            result = export_images(pdf, specs=[str(out_dir)])

        assert result.success is True
        assert len(result.data["image_streams"]) == 0

    def test_export_cmyk_png_conversion(self, tmp_path):
        pdf = _make_pdf_with_image(color=(100, 50, 0, 0), mode="CMYK", fmt="flatedecode")
        out_dir = tmp_path / "exports"
        result = export_images(pdf, specs=[str(out_dir)])

        assert result.success is True
        files = list(out_dir.glob("*.png"))
        assert len(files) == 1

        img = Image.open(files[0])
        assert img.mode == "RGB"

    def test_export_extract_to_pil_fails(self, tmp_path):
        pdf = _make_pdf_with_image()
        out_dir = tmp_path / "exports"

        with patch("pdftl.operations.export_import_images.extract_to_pil", return_value=None):
            result = export_images(pdf, specs=[str(out_dir)])

        assert result.success is True
        assert len(result.data["image_streams"]) == 0

    def test_export_save_fails(self, tmp_path):
        pdf = _make_pdf_with_image()
        out_dir = tmp_path / "exports"

        with patch("PIL.Image.Image.save", side_effect=OSError("Simulated IO Error")):
            result = export_images(pdf, specs=[str(out_dir)])

        assert result.success is True
        assert len(result.data["image_streams"]) == 0

    def test_export_images_cli_hook(self, tmp_path):
        manifest_file = tmp_path / "manifest.json"
        result = OpResult(
            success=True, data={"test": "data"}, meta={c.META_OUTPUT_FILE: str(manifest_file)}
        )
        export_images_cli_hook(result, None, None)

        assert manifest_file.exists()
        with open(manifest_file) as f:
            data = json.load(f)
        assert data == {"test": "data"}

    def test_export_images_cli_hook_skipped_on_failure(self):
        result = OpResult(success=False)
        with patch("pdftl.operations.export_import_images.smart_open_maybe_dash") as mock_open:
            export_images_cli_hook(result, None, None)
            mock_open.assert_not_called()

    def test_export_file_hash_oserror(self, tmp_path):
        """Covers export_import_images.py lines 72-76 (OSError in _file_hash)."""
        filepath = tmp_path / "dummy.png"
        filepath.write_text("dummy")
        with patch("builtins.open", side_effect=OSError("Permission denied")):
            assert _file_hash(filepath) == ""

    def test_export_with_page_specs(self, tmp_path):
        """Covers export_images.py line 109 (resolving page specs in get_target_pages)."""
        pdf = _make_pdf_with_image()
        out_dir = tmp_path / "exports"
        result = export_images(pdf, specs=["1", str(out_dir)])
        assert result.success is True
        assert len(result.data["image_streams"]) == 1
        result = export_images(pdf, specs=[str(out_dir), "1"])
        assert result.success is True
        assert len(result.data["image_streams"]) == 1
        result = export_images(pdf, specs=[str(out_dir), "1-3odd"])
        assert result.success is True
        assert len(result.data["image_streams"]) == 1
        result = export_images(pdf, specs=["1", str(out_dir), "1-3even"])
        assert result.success is True
        assert len(result.data["image_streams"]) == 1
        result = export_images(pdf, specs=["r1even", str(out_dir), "1-3even"])
        assert result.success is True
        assert len(result.data["image_streams"]) == 0

    def test_export_duplicate_image_placements(self, tmp_path):
        pdf = pikepdf.Pdf.new()
        image = _make_image_stream(pdf, color="red")

        content1 = b"q 10 0 0 10 0 0 cm /Im1 Do Q"
        page_obj1 = pikepdf.Dictionary(
            Type=pikepdf.Name("/Page"),
            MediaBox=pikepdf.Array([0, 0, 100, 100]),
            Contents=pikepdf.Stream(pdf, content1),
            Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im1=image)),
        )
        pdf.pages.append(pikepdf.Page(page_obj1))

        content2 = b"q 20 0 0 20 0 0 cm /Im1 Do Q"
        page_obj2 = pikepdf.Dictionary(
            Type=pikepdf.Name("/Page"),
            MediaBox=pikepdf.Array([0, 0, 100, 100]),
            Contents=pikepdf.Stream(pdf, content2),
            Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im1=image)),
        )
        pdf.pages.append(pikepdf.Page(page_obj2))

        out_dir = tmp_path / "exports"
        result = export_images(pdf, specs=[str(out_dir)])
        assert result.success is True
        manifest = result.data
        obj_id = f"{image.objgen[0]}_{image.objgen[1]}"
        assert len(manifest["image_streams"][obj_id]["placements"]) == 2


class TestImportImages:
    def test_missing_required_arguments(self):
        pdf = pikepdf.Pdf.new()
        with pytest.raises(InvalidArgumentError, match="Missing required directory argument"):
            import_images(pdf, specs=[])

    def test_directory_does_not_exist(self, tmp_path):
        pdf = pikepdf.Pdf.new()
        nonexistent = tmp_path / "missing_dir"
        with pytest.raises(InvalidArgumentError, match="Target directory does not exist"):
            import_images(pdf, specs=[str(nonexistent)])

    def test_invalid_quality(self, tmp_path):
        pdf = pikepdf.Pdf.new()
        with pytest.raises(InvalidArgumentError, match="Invalid quality"):
            import_images(pdf, specs=[str(tmp_path), "quality=200"])

        with pytest.raises(InvalidArgumentError, match="Invalid quality"):
            import_images(pdf, specs=[str(tmp_path), "quality=abc"])

    def test_manifest_not_found(self, tmp_path):
        pdf = pikepdf.Pdf.new()
        with pytest.raises(InvalidArgumentError, match="Manifest file not found"):
            import_images(pdf, specs=[str(tmp_path), f"manifest={tmp_path}/missing.json"])

    def test_invalid_json_manifest(self, tmp_path):
        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text("INVALID JSON BLOCK")

        pdf = pikepdf.Pdf.new()
        with pytest.raises(InvalidArgumentError, match="Invalid JSON manifest"):
            import_images(pdf, specs=[str(tmp_path), f"manifest={manifest_file}"])

    def test_empty_image_streams(self, tmp_path):
        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text("{}")

        pdf = pikepdf.Pdf.new()
        result = import_images(pdf, specs=[str(tmp_path), f"manifest={manifest_file}"])
        assert result.success is True

    def test_path_traversal_protection(self, tmp_path):
        pdf = _make_pdf_with_image()
        manifest_file = tmp_path / "manifest.json"
        manifest_data = {
            "image_streams": {
                "1_0": {"export_file": "../../../etc/passwd", "_file_hash": "dummyhash"}
            }
        }
        with open(manifest_file, "w") as f:
            json.dump(manifest_data, f)

        with pytest.raises(
            InvalidArgumentError, match="Security violation: path traversal detected"
        ):
            import_images(pdf, specs=[str(tmp_path), f"manifest={manifest_file}"])

    def test_resolve_import_filepath_malformed_oserror(self, tmp_path):
        pdf = _make_pdf_with_image()
        manifest_file = tmp_path / "manifest.json"
        manifest_data = {
            "image_streams": {"1_0": {"export_file": "dummy.png", "_file_hash": "oldhash"}}
        }
        with open(manifest_file, "w") as f:
            json.dump(manifest_data, f)

        with patch("pathlib.Path.resolve", side_effect=OSError("Simulated file system error")):
            result = import_images(pdf, specs=[str(tmp_path), f"manifest={manifest_file}"])
            assert result.success is True

    def test_successful_import_and_skip_unmodified(self, tmp_path):
        pdf = _make_pdf_with_image(color="red", mode="RGB", fmt="flatedecode")
        out_dir = tmp_path / "exports"
        out_dir.mkdir()

        export_result = export_images(pdf, specs=[str(out_dir)])
        manifest_file = out_dir / "manifest.json"
        with open(manifest_file, "w") as f:
            json.dump(export_result.data, f)

        # Immediate re-import check — MD5 hashes identically match so it skips
        with patch(
            "pdftl.operations.export_import_images.encode_and_update_pdf_image"
        ) as mock_encode:
            import_result = import_images(pdf, specs=[str(out_dir)])
            assert import_result.success is True
            mock_encode.assert_not_called()

        # Dynamically locate the exported file from the manifest instead of hardcoding
        first_stream = list(export_result.data["image_streams"].values())[0]
        img_file = out_dir / first_stream["export_file"]

        img = Image.open(img_file)
        from PIL import ImageDraw

        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, 5, 5], fill="blue")
        img.save(img_file)

        # Run import again — file is modified, so encoding is triggered
        with patch(
            "pdftl.operations.export_import_images.encode_and_update_pdf_image"
        ) as mock_encode:
            import_result = import_images(pdf, specs=[str(out_dir)])
            assert import_result.success is True
            mock_encode.assert_called_once()

    def test_import_stdin_manifest(self, tmp_path, monkeypatch):
        pdf = _make_pdf_with_image(color="red", mode="RGB", fmt="flatedecode")
        out_dir = tmp_path / "exports"
        out_dir.mkdir()

        export_result = export_images(pdf, specs=[str(out_dir)])
        manifest_json_str = json.dumps(export_result.data)

        monkeypatch.setattr("sys.stdin", io.StringIO(manifest_json_str))

        result = import_images(pdf, specs=[str(out_dir), "manifest=-"])
        assert result.success is True

    def test_import_stdin_invalid_json(self, tmp_path, monkeypatch):
        pdf = pikepdf.Pdf.new()
        monkeypatch.setattr("sys.stdin", io.StringIO("NOT VALID JSON"))

        with pytest.raises(InvalidArgumentError, match="Invalid JSON manifest from stdin"):
            import_images(pdf, specs=[str(tmp_path), "manifest=-"])

    def test_import_alternative_format_fallback(self, tmp_path):
        """Verifies that import gracefully checks for swapped extensions (.png <-> .jpg)."""
        pdf = _make_pdf_with_image(color="red", mode="RGB", fmt="flatedecode")
        out_dir = tmp_path / "exports"
        out_dir.mkdir()

        export_result = export_images(pdf, specs=[str(out_dir)])
        manifest_file = out_dir / "manifest.json"
        with open(manifest_file, "w") as f:
            json.dump(export_result.data, f)

        # Dynamically locate the exported file from the manifest instead of hardcoding
        first_stream = list(export_result.data["image_streams"].values())[0]
        png_file = out_dir / first_stream["export_file"]
        assert png_file.exists()

        # Let's delete the png and save a jpg instead (simulating format change by user)
        with Image.open(png_file) as img:
            img.load()
        png_file.unlink()
        jpg_file = png_file.with_suffix(".jpg")
        img.save(jpg_file, format="JPEG")

        with patch(
            "pdftl.operations.export_import_images.encode_and_update_pdf_image"
        ) as mock_encode:
            import_result = import_images(pdf, specs=[str(out_dir)])
            assert import_result.success is True
            mock_encode.assert_called_once()

    def test_import_dimension_distortion_warning(self, tmp_path, caplog):
        """Ensures that resizing edited images triggers a visual layout distortion log warning."""
        pdf = _make_pdf_with_image(color="red", mode="RGB", fmt="flatedecode")
        out_dir = tmp_path / "exports"
        out_dir.mkdir()

        export_result = export_images(pdf, specs=[str(out_dir)])
        manifest_file = out_dir / "manifest.json"
        with open(manifest_file, "w") as f:
            json.dump(export_result.data, f)

        first_stream = list(export_result.data["image_streams"].values())[0]
        png_file = out_dir / first_stream["export_file"]

        img = Image.open(png_file)
        resized_img = img.resize((50, 50))
        resized_img.save(png_file)

        import logging

        with caplog.at_level(logging.WARNING, logger="pdftl.operations.export_import_images"):
            import_images(pdf, specs=[str(out_dir)])

        assert any("differ from original" in r.message for r in caplog.records)

    def test_import_missing_file_skipped(self, tmp_path):
        pdf = _make_pdf_with_image()
        manifest_file = tmp_path / "manifest.json"
        manifest_data = {
            "image_streams": {"99_0": {"export_file": "missing.png", "_file_hash": "dummyhash"}}
        }
        with open(manifest_file, "w") as f:
            json.dump(manifest_data, f)

        with patch(
            "pdftl.operations.export_import_images.encode_and_update_pdf_image"
        ) as mock_encode:
            result = import_images(pdf, specs=[str(tmp_path), f"manifest={manifest_file}"])
            assert result.success is True
            mock_encode.assert_not_called()

    def test_import_stream_not_in_pdf_skipped(self, tmp_path):
        pdf = _make_pdf_with_image()

        img_file = tmp_path / "dummy.png"
        Image.new("RGB", (10, 10)).save(img_file)

        manifest_file = tmp_path / "manifest.json"
        manifest_data = {
            "image_streams": {"999_0": {"export_file": "dummy.png", "_file_hash": "oldhash"}}
        }
        with open(manifest_file, "w") as f:
            json.dump(manifest_data, f)

        with patch(
            "pdftl.operations.export_import_images.encode_and_update_pdf_image"
        ) as mock_encode:
            result = import_images(pdf, specs=[str(tmp_path), f"manifest={manifest_file}"])
            assert result.success is True
            mock_encode.assert_not_called()

    def test_import_image_open_fails(self, tmp_path):
        pdf = _make_pdf_with_image()

        xobj = list(pdf.pages[0].Resources.XObject.values())[0]
        obj_id = f"{xobj.objgen[0]}_{xobj.objgen[1]}"

        img_file = tmp_path / "corrupt.png"
        img_file.write_bytes(b"not an image pixel buffer")

        manifest_file = tmp_path / "manifest.json"
        manifest_data = {
            "image_streams": {obj_id: {"export_file": "corrupt.png", "_file_hash": "oldhash"}}
        }
        with open(manifest_file, "w") as f:
            json.dump(manifest_data, f)

        with patch(
            "pdftl.operations.export_import_images.encode_and_update_pdf_image"
        ) as mock_encode:
            result = import_images(pdf, specs=[str(tmp_path), f"manifest={manifest_file}"])
            assert result.success is True
            mock_encode.assert_not_called()

    def test_import_encode_and_update_fails(self, tmp_path):
        pdf = _make_pdf_with_image()

        xobj = list(pdf.pages[0].Resources.XObject.values())[0]
        obj_id = f"{xobj.objgen[0]}_{xobj.objgen[1]}"

        img_file = tmp_path / "dummy.png"
        Image.new("RGB", (10, 10)).save(img_file)

        manifest_file = tmp_path / "manifest.json"
        manifest_data = {
            "image_streams": {obj_id: {"export_file": "dummy.png", "_file_hash": "oldhash"}}
        }
        with open(manifest_file, "w") as f:
            json.dump(manifest_data, f)

        with patch(
            "pdftl.operations.export_import_images.encode_and_update_pdf_image",
            side_effect=OSError("Encode layout failed"),
        ):
            result = import_images(pdf, specs=[str(tmp_path), f"manifest={manifest_file}"])
            assert result.success is True

    def test_import_file_hash_oserror(self, tmp_path):
        """Covers export_import_images.py lines 60-64 (OSError in _file_hash)."""
        filepath = tmp_path / "dummy.png"
        filepath.write_text("dummy")
        with patch("builtins.open", side_effect=OSError("Permission denied")):
            assert _file_hash(filepath) == ""

    def test_import_missing_export_file_key(self, tmp_path):
        pdf = _make_pdf_with_image()
        manifest_file = tmp_path / "manifest.json"
        manifest_data = {"image_streams": {"1_0": {"_file_hash": "dummyhash"}}}
        with open(manifest_file, "w") as f:
            json.dump(manifest_data, f)

        result = import_images(pdf, specs=[str(tmp_path), f"manifest={manifest_file}"])
        assert result.success is True

    def test_import_missing_placements_and_dimensions(self, tmp_path):
        """Covers edge cases where placements or dimensions are missing from the manifest streams."""
        pdf = _make_pdf_with_image()
        xobj = list(pdf.pages[0].Resources.XObject.values())[0]
        obj_id = f"{xobj.objgen[0]}_{xobj.objgen[1]}"

        img_file = tmp_path / "dummy.png"
        Image.new("RGB", (10, 10)).save(img_file)

        manifest_file = tmp_path / "manifest.json"
        manifest_data = {
            "image_streams": {
                obj_id: {
                    "export_file": "dummy.png",
                    "_file_hash": "oldhash",
                    # Omit placements, width_px, and height_px completely
                }
            }
        }
        with open(manifest_file, "w") as f:
            json.dump(manifest_data, f)

        with patch(
            "pdftl.operations.export_import_images.encode_and_update_pdf_image"
        ) as mock_encode:
            result = import_images(pdf, specs=[str(tmp_path), f"manifest={manifest_file}"])
            assert result.success is True
            mock_encode.assert_called_once()


class TestExportSkipsInlineImagesViaRealFinder:
    def test_export_skips_inline_image_from_real_extract_pdf_images(self, tmp_path):
        """Covers _build_manifest's `if img.get("inline"): ... continue` branch
        via the real extract_pdf_images path (not a mocked return value) -- an
        actual inline BI/ID/EI block on the page must be skipped with a
        warning, and must not prevent a sibling XObject image on the same
        page from being exported normally."""
        pdf = _make_pdf_with_image(color="red", mode="RGB", fmt="flatedecode")
        page = pdf.pages[0]
        old_contents = bytes(page.obj["/Contents"].read_bytes())
        inline_bytes = b"BI /W 1 /H 1 /BPC 8 /CS /RGB ID \x80\x80\x80 EI"
        page.obj["/Contents"] = pikepdf.Stream(pdf, old_contents + b" " + inline_bytes)

        out_dir = tmp_path / "exports"
        result = export_images(pdf, specs=[str(out_dir)])

        assert result.success is True
        # Only the one real XObject image should be in the manifest --
        # the inline image must have been skipped, not exported or crashed on.
        assert len(result.data["image_streams"]) == 1


import pdftl.operations.export_import_images as eii_mod  # codeql[py/import-and-import-from]


class TestExportSingleInlineImage:
    def test_decode_returns_none(self, tmp_path):
        img = {"page": 1, "host_objgen": (1, 0), "instruction_index": 0}
        with patch("pdftl.utils.images.inline_images.decode_inline_pil", return_value=None):
            result = eii_mod._export_single_inline_image(MagicMock(), img, tmp_path)
        assert result is None

    def test_successful_export_png(self, tmp_path):
        img = {
            "page": 1,
            "host_objgen": (5, 0),
            "instruction_index": 2,
            "format": "flatedecode",
        }
        pil_img = Image.new("RGB", (5, 5), color="red")
        with patch("pdftl.utils.images.inline_images.decode_inline_pil", return_value=pil_img):
            result = eii_mod._export_single_inline_image(MagicMock(), img, tmp_path)
        assert result is not None
        filename, file_hash = result
        assert filename == "inline_5_0_2.png"
        assert (tmp_path / filename).exists()

    def test_successful_export_jpeg(self, tmp_path):
        img = {
            "page": 1,
            "host_objgen": (6, 0),
            "instruction_index": 0,
            "format": "dctdecode",
        }
        pil_img = Image.new("RGB", (5, 5), color="blue")
        with patch("pdftl.utils.images.inline_images.decode_inline_pil", return_value=pil_img):
            result = eii_mod._export_single_inline_image(MagicMock(), img, tmp_path)
        assert result is not None
        filename, _ = result
        assert filename.endswith(".jpg")

    def test_cmyk_png_converted_to_rgb(self, tmp_path):
        img = {
            "page": 1,
            "host_objgen": (7, 0),
            "instruction_index": 0,
            "format": "flatedecode",
        }
        pil_img = Image.new("CMYK", (5, 5))
        with patch("pdftl.utils.images.inline_images.decode_inline_pil", return_value=pil_img):
            result = eii_mod._export_single_inline_image(MagicMock(), img, tmp_path)
        filename, _ = result
        saved = Image.open(tmp_path / filename)
        assert saved.mode == "RGB"

    def test_save_failure_returns_none(self, tmp_path):
        img = {
            "page": 1,
            "host_objgen": (8, 0),
            "instruction_index": 0,
            "format": "flatedecode",
        }
        pil_img = Image.new("RGB", (5, 5))
        with (
            patch("pdftl.utils.images.inline_images.decode_inline_pil", return_value=pil_img),
            patch("PIL.Image.Image.save", side_effect=OSError("disk full")),
        ):
            result = eii_mod._export_single_inline_image(MagicMock(), img, tmp_path)
        assert result is None


class TestBuildManifestInlineImages:
    def test_inline_entry_added_to_manifest(self, tmp_path):
        entry = {
            "inline": True,
            "host_objgen": (1, 0),
            "instruction_index": 0,
            "format": "flatedecode",
            "width_px": 10,
            "height_px": 10,
            "colorspace": {"family": "rgb"},
            "bits": 8,
            "page": 1,
            "bbox": [0, 0, 10, 10],
            "ppi_x": 72,
            "ppi_y": 72,
        }
        with (
            patch("pdftl.utils.images.finders.extract_pdf_images", return_value=[entry]),
            patch(
                "pdftl.operations.export_import_images._export_single_inline_image",
                return_value=("inline_1_0_0.png", "abc123"),
            ),
        ):
            manifest = eii_mod._build_manifest(MagicMock(), [1], tmp_path)

        key = "inline_1_0_0"
        assert key in manifest["image_streams"]
        stream = manifest["image_streams"][key]
        assert stream["inline"] is True
        assert stream["host_objgen"] == [1, 0]
        assert stream["export_file"] == "inline_1_0_0.png"
        assert stream["placements"] == [
            {"page": 1, "name": None, "bbox": [0, 0, 10, 10], "ppi_x": 72, "ppi_y": 72}
        ]

    def test_inline_entry_export_failure_skips(self, tmp_path):
        entry = {
            "inline": True,
            "host_objgen": (1, 0),
            "instruction_index": 0,
            "format": "flatedecode",
            "width_px": 10,
            "height_px": 10,
            "colorspace": {"family": "rgb"},
            "bits": 8,
            "page": 1,
            "bbox": [0, 0, 10, 10],
            "ppi_x": 72,
            "ppi_y": 72,
        }
        with (
            patch("pdftl.utils.images.finders.extract_pdf_images", return_value=[entry]),
            patch(
                "pdftl.operations.export_import_images._export_single_inline_image",
                return_value=None,
            ),
        ):
            manifest = eii_mod._build_manifest(MagicMock(), [1], tmp_path)
        assert manifest["image_streams"] == {}


class TestImportSingleInlineImage:
    def test_missing_export_file_key(self, tmp_path):
        assert eii_mod._import_single_inline_image(None, {}, tmp_path, 75) is False

    def test_file_not_resolved(self, tmp_path):
        stream_info = {"export_file": "missing.png"}
        assert eii_mod._import_single_inline_image(None, stream_info, tmp_path, 75) is False

    def test_hash_unchanged_skips(self, tmp_path):
        img_file = tmp_path / "im.png"
        Image.new("RGB", (5, 5)).save(img_file)
        h = eii_mod._file_hash(img_file)
        stream_info = {"export_file": "im.png", "file_hash": h}
        assert eii_mod._import_single_inline_image(None, stream_info, tmp_path, 75) is False

    def test_missing_host_objgen_or_index(self, tmp_path):
        img_file = tmp_path / "im.png"
        Image.new("RGB", (5, 5)).save(img_file)
        stream_info = {"export_file": "im.png", "file_hash": "oldhash"}
        assert eii_mod._import_single_inline_image(None, stream_info, tmp_path, 75) is False

    def test_image_open_fails(self, tmp_path):
        img_file = tmp_path / "corrupt.png"
        img_file.write_bytes(b"not an image")
        stream_info = {
            "export_file": "corrupt.png",
            "file_hash": "oldhash",
            "host_objgen": [1, 0],
            "instruction_index": 0,
        }
        assert eii_mod._import_single_inline_image(None, stream_info, tmp_path, 75) is False

    def test_dctdecode_success(self, tmp_path):
        img_file = tmp_path / "im.jpg"
        Image.new("RGB", (5, 5), color="blue").save(img_file, format="JPEG")
        stream_info = {
            "export_file": "im.jpg",
            "file_hash": "oldhash",
            "host_objgen": [2, 0],
            "instruction_index": 1,
            "format": "dctdecode",
            "placements": [{"page": 1}],
        }
        with (
            patch(
                "pdftl.utils.images.inline_images.apply_inline_rewrites", return_value=True
            ) as mock_apply,
            patch(
                "pdftl.utils.images.inline_images.build_inline_image_bytes",
                return_value=b"raw",
            ),
        ):
            result = eii_mod._import_single_inline_image(MagicMock(), stream_info, tmp_path, 80)
        assert result is True
        mock_apply.assert_called_once()

    def test_flate_default_success(self, tmp_path):
        img_file = tmp_path / "im.png"
        Image.new("L", (5, 5)).save(img_file)
        stream_info = {
            "export_file": "im.png",
            "file_hash": "oldhash",
            "host_objgen": [3, 0],
            "instruction_index": 2,
            "format": "flatedecode",
        }
        with (
            patch("pdftl.utils.images.inline_images.apply_inline_rewrites", return_value=True),
            patch(
                "pdftl.utils.images.inline_images.build_inline_image_bytes",
                return_value=b"raw",
            ),
        ):
            result = eii_mod._import_single_inline_image(MagicMock(), stream_info, tmp_path, 75)
        assert result is True

    def test_unsupported_mode_converts_to_rgb(self, tmp_path):
        img_file = tmp_path / "im.png"
        Image.new("P", (5, 5)).save(img_file)
        stream_info = {
            "export_file": "im.png",
            "file_hash": "oldhash",
            "host_objgen": [6, 0],
            "instruction_index": 0,
        }
        with (
            patch("pdftl.utils.images.inline_images.apply_inline_rewrites", return_value=True),
            patch(
                "pdftl.utils.images.inline_images.build_inline_image_bytes",
                return_value=b"raw",
            ),
        ):
            result = eii_mod._import_single_inline_image(MagicMock(), stream_info, tmp_path, 75)
        assert result is True

    def test_apply_rewrites_raises_is_caught(self, tmp_path):
        img_file = tmp_path / "im.png"
        Image.new("RGB", (5, 5)).save(img_file)
        stream_info = {
            "export_file": "im.png",
            "file_hash": "oldhash",
            "host_objgen": [4, 0],
            "instruction_index": 0,
        }
        with (
            patch(
                "pdftl.utils.images.inline_images.apply_inline_rewrites",
                side_effect=RuntimeError("boom"),
            ),
            patch(
                "pdftl.utils.images.inline_images.build_inline_image_bytes",
                return_value=b"x",
            ),
        ):
            result = eii_mod._import_single_inline_image(MagicMock(), stream_info, tmp_path, 75)
        assert result is False

    def test_changed_false_returns_false(self, tmp_path):
        img_file = tmp_path / "im.png"
        Image.new("RGB", (5, 5)).save(img_file)
        stream_info = {
            "export_file": "im.png",
            "file_hash": "oldhash",
            "host_objgen": [5, 0],
            "instruction_index": 0,
        }
        with (
            patch("pdftl.utils.images.inline_images.apply_inline_rewrites", return_value=False),
            patch(
                "pdftl.utils.images.inline_images.build_inline_image_bytes",
                return_value=b"x",
            ),
        ):
            result = eii_mod._import_single_inline_image(MagicMock(), stream_info, tmp_path, 75)
        assert result is False


class TestImportImagesInlineDispatch:
    def test_inline_stream_routed_to_inline_importer(self, tmp_path):
        pdf = MagicMock()
        manifest_file = tmp_path / "manifest.json"
        manifest_data = {
            "image_streams": {"inline_1_0_0": {"inline": True, "export_file": "x.png"}}
        }
        with open(manifest_file, "w") as f:
            json.dump(manifest_data, f)

        with patch(
            "pdftl.operations.export_import_images._import_single_inline_image",
            return_value=True,
        ) as mock_inline:
            result = eii_mod.import_images(pdf, specs=[str(tmp_path), f"manifest={manifest_file}"])
        assert result.success is True
        mock_inline.assert_called_once()

    def test_inline_stream_import_returns_false_not_counted(self, tmp_path):
        """Sibling to test_inline_stream_routed_to_inline_importer: the
        false-return path from _import_single_inline_image must not be
        counted toward updated_count."""
        pdf = MagicMock()
        manifest_file = tmp_path / "manifest.json"
        manifest_data = {
            "image_streams": {"inline_1_0_0": {"inline": True, "export_file": "x.png"}}
        }
        with open(manifest_file, "w") as f:
            json.dump(manifest_data, f)

        with patch(
            "pdftl.operations.export_import_images._import_single_inline_image",
            return_value=False,
        ) as mock_inline:
            result = import_images(pdf, specs=[str(tmp_path), f"manifest={manifest_file}"])
        assert result.success is True
        mock_inline.assert_called_once()

    # tests/operations/test_export_import_images.py

    def test_bitonal_mode_preserved_not_converted_to_rgb(self, tmp_path):
        img_file = tmp_path / "im.png"
        Image.new("1", (5, 5)).save(img_file)
        stream_info = {
            "export_file": "im.png",
            "file_hash": "oldhash",
            "host_objgen": [9, 0],
            "instruction_index": 0,
            "format": "flatedecode",
        }
        captured = {}

        def fake_encode(pil_img, fmt, quality):
            captured["mode"] = pil_img.mode
            return b"raw"

        with (
            patch("pdftl.utils.images.inline_images.apply_inline_rewrites", return_value=True),
            patch(
                "pdftl.utils.images.inline_images.encode_inline_replacement",
                side_effect=fake_encode,
            ),
        ):
            result = eii_mod._import_single_inline_image(MagicMock(), stream_info, tmp_path, 75)

        assert result is True
        assert captured["mode"] == "1"
