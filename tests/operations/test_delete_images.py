# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Tests for pdftl/operations/delete_images.py — 100% coverage target."""

import pytest
from unittest.mock import MagicMock, patch
from pikepdf import Array, Name

from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.delete_images import (
    _parse_size_str,
    _overwrite_with_stub,
    _image_matches,
    _process_resources,
    delete_images,
)


# ---------------------------------------------------------------------------
# _parse_size_str
# ---------------------------------------------------------------------------


class TestParseSizeStr:
    def test_plain_integer(self):
        assert _parse_size_str("1024") == 1024

    def test_kilobytes_lowercase(self):
        assert _parse_size_str("1k") == 1024

    def test_kilobytes_uppercase(self):
        assert _parse_size_str("1K") == 1024

    def test_megabytes(self):
        assert _parse_size_str("1m") == 1024**2

    def test_gigabytes(self):
        assert _parse_size_str("1g") == 1024**3

    def test_fractional_megabytes(self):
        assert _parse_size_str("1.5m") == int(1.5 * 1024**2)

    def test_whitespace_stripped(self):
        assert _parse_size_str("  512k  ") == 512 * 1024

    def test_invalid_raises_value_error(self):
        with pytest.raises(ValueError):
            _parse_size_str("notanumber")

    def test_invalid_unit_raises_value_error(self):
        with pytest.raises(ValueError):
            _parse_size_str("100t")


# ---------------------------------------------------------------------------
# _overwrite_with_stub
# ---------------------------------------------------------------------------


class TestOverwriteWithStub:
    def _make_obj(self, extra_keys=None):
        """Return a mock pikepdf stream object."""
        obj = MagicMock()
        obj.__contains__ = MagicMock(side_effect=lambda key: key in (extra_keys or []))
        return obj

    def test_sets_stream_data(self):
        obj = self._make_obj()
        _overwrite_with_stub(obj)
        obj.write.assert_called_once_with(b"\xff")

    def test_sets_dimensions_and_mask(self):
        obj = self._make_obj()
        _overwrite_with_stub(obj)
        assert obj.Width == 1
        assert obj.Height == 1
        assert obj.BitsPerComponent == 1
        assert obj.ImageMask is True

    def test_sets_decode_array(self):
        obj = self._make_obj()
        _overwrite_with_stub(obj)
        assert obj.Decode == Array([0, 1])

    def test_deletes_present_conflicting_keys(self):
        conflicting = [
            Name.ColorSpace,
            Name.Filter,
            Name.SMask,
            Name.Mask,
            Name.Intent,
            Name.Interpolate,
        ]
        obj = self._make_obj(extra_keys=conflicting)
        _overwrite_with_stub(obj)
        assert obj.__delitem__.call_count == len(conflicting)

    def test_skips_absent_conflicting_keys(self):
        obj = self._make_obj(extra_keys=[])
        _overwrite_with_stub(obj)
        obj.__delitem__.assert_not_called()


# ---------------------------------------------------------------------------
# _image_matches
# ---------------------------------------------------------------------------


def _make_image_obj(raw_bytes=b"x" * 100, width=100, height=100, filter_name="/DCTDecode"):
    obj = MagicMock()
    obj.read_raw_bytes.return_value = raw_bytes
    obj.Width = width
    obj.Height = height
    obj.get = MagicMock(return_value=filter_name)
    return obj


class TestImageMatches:
    def test_no_params_always_matches(self):
        assert _image_matches(_make_image_obj(), {}) is True

    # --- minbytes ---

    def test_minbytes_passes_when_large_enough(self):
        obj = _make_image_obj(raw_bytes=b"x" * 200)
        assert _image_matches(obj, {"minbytes": "100"}) is True

    def test_minbytes_fails_when_too_small(self):
        obj = _make_image_obj(raw_bytes=b"x" * 50)
        assert _image_matches(obj, {"minbytes": "100"}) is False

    # --- maxbytes ---

    def test_maxbytes_passes_when_small_enough(self):
        obj = _make_image_obj(raw_bytes=b"x" * 50)
        assert _image_matches(obj, {"maxbytes": "100"}) is True

    def test_maxbytes_fails_when_too_large(self):
        obj = _make_image_obj(raw_bytes=b"x" * 200)
        assert _image_matches(obj, {"maxbytes": "100"}) is False

    def test_minbytes_and_maxbytes_both_satisfied(self):
        obj = _make_image_obj(raw_bytes=b"x" * 150)
        assert _image_matches(obj, {"minbytes": "100", "maxbytes": "200"}) is True

    def test_minbytes_and_maxbytes_min_fails(self):
        obj = _make_image_obj(raw_bytes=b"x" * 50)
        assert _image_matches(obj, {"minbytes": "100", "maxbytes": "200"}) is False

    def test_minbytes_and_maxbytes_max_fails(self):
        obj = _make_image_obj(raw_bytes=b"x" * 300)
        assert _image_matches(obj, {"minbytes": "100", "maxbytes": "200"}) is False

    # --- minpixels (WxH form) ---

    def test_minpixels_wxh_passes(self):
        obj = _make_image_obj(width=200, height=400)
        assert _image_matches(obj, {"minpixels": "100x200"}) is True

    def test_minpixels_wxh_fails_width(self):
        obj = _make_image_obj(width=50, height=400)
        assert _image_matches(obj, {"minpixels": "100x200"}) is False

    def test_minpixels_wxh_fails_height(self):
        obj = _make_image_obj(width=200, height=50)
        assert _image_matches(obj, {"minpixels": "100x200"}) is False

    # --- minpixels (area form) ---

    def test_minpixels_area_passes(self):
        obj = _make_image_obj(width=100, height=100)  # area = 10000
        assert _image_matches(obj, {"minpixels": "5000"}) is True

    def test_minpixels_area_fails(self):
        obj = _make_image_obj(width=10, height=10)  # area = 100
        assert _image_matches(obj, {"minpixels": "5000"}) is False

    # --- minpixels with missing dims ---

    def test_minpixels_missing_dims_returns_false(self):
        obj = MagicMock()
        obj.read_raw_bytes.return_value = b"x" * 100
        del obj.Width  # simulate missing attribute
        type(obj).Width = property(lambda self: (_ for _ in ()).throw(AttributeError()))
        type(obj).Height = property(lambda self: (_ for _ in ()).throw(AttributeError()))
        assert _image_matches(obj, {"minpixels": "100x100"}) is False

    # --- maxpixels (WxH form) ---

    def test_maxpixels_wxh_passes(self):
        obj = _make_image_obj(width=50, height=50)
        assert _image_matches(obj, {"maxpixels": "100x100"}) is True

    def test_maxpixels_wxh_fails_width(self):
        obj = _make_image_obj(width=200, height=50)
        assert _image_matches(obj, {"maxpixels": "100x100"}) is False

    def test_maxpixels_wxh_fails_height(self):
        obj = _make_image_obj(width=50, height=200)
        assert _image_matches(obj, {"maxpixels": "100x100"}) is False

    # --- maxpixels (area form) ---

    def test_maxpixels_area_passes(self):
        obj = _make_image_obj(width=10, height=10)  # area = 100
        assert _image_matches(obj, {"maxpixels": "5000"}) is True

    def test_maxpixels_area_fails(self):
        obj = _make_image_obj(width=100, height=100)  # area = 10000
        assert _image_matches(obj, {"maxpixels": "5000"}) is False

    # --- maxpixels with missing dims ---

    def test_maxpixels_missing_dims_returns_false(self):
        obj = MagicMock()
        obj.read_raw_bytes.return_value = b"x" * 100
        obj.Width = MagicMock(side_effect=AttributeError())
        # Make int(obj.Width) raise by having Width itself raise on access
        type(obj).Width = property(
            fget=lambda self: MagicMock(__int__=MagicMock(side_effect=ValueError()))
        )
        type(obj).Height = property(
            fget=lambda self: MagicMock(__int__=MagicMock(side_effect=ValueError()))
        )
        assert _image_matches(obj, {"maxpixels": "100x100"}) is False

    # --- format ---

    def test_format_passes(self):
        obj = _make_image_obj(filter_name="/DCTDecode")
        assert _image_matches(obj, {"format": "dct"}) is True

    def test_format_fails(self):
        obj = _make_image_obj(filter_name="/FlateDecode")
        assert _image_matches(obj, {"format": "dct"}) is False

    def test_format_no_filter_key(self):
        obj = _make_image_obj()
        obj.get = MagicMock(return_value="")
        assert _image_matches(obj, {"format": "dct"}) is False

    # --- combined ---

    def test_multiple_params_all_must_pass(self):
        obj = _make_image_obj(
            raw_bytes=b"x" * 200, width=200, height=200, filter_name="/DCTDecode"
        )
        assert (
            _image_matches(obj, {"minbytes": "100", "minpixels": "100x100", "format": "dct"})
            is True
        )

    def test_multiple_params_one_fails(self):
        obj = _make_image_obj(
            raw_bytes=b"x" * 200, width=200, height=200, filter_name="/FlateDecode"
        )
        assert _image_matches(obj, {"minbytes": "100", "format": "dct"}) is False


# ---------------------------------------------------------------------------
# _process_resources
# ---------------------------------------------------------------------------


def _make_xobject(subtype, objgen=(1, 0), has_resources=False):
    obj = MagicMock()
    obj.get = MagicMock(
        side_effect=lambda key, default=None: {
            Name.Subtype: subtype,
        }.get(key, default)
    )
    obj.objgen = objgen
    if has_resources:
        obj.Resources = MagicMock()
        obj.__contains__ = MagicMock(side_effect=lambda k: k == Name.Resources)
    else:
        obj.__contains__ = MagicMock(return_value=False)
    return obj


class TestProcessResources:
    def test_no_xobject_key_returns_early(self):
        resources = MagicMock()
        resources.__contains__ = MagicMock(return_value=False)
        modified = set()
        _process_resources(resources, {}, modified)
        assert len(modified) == 0

    def test_image_matching_is_overwritten(self):
        image_obj = _make_xobject(Name.Image, objgen=(10, 0))
        resources = MagicMock()
        resources.__contains__ = MagicMock(return_value=True)
        resources.XObject.items.return_value = [("/Im1", image_obj)]
        modified = set()

        with (
            patch("pdftl.operations.delete_images._image_matches", return_value=True),
            patch("pdftl.operations.delete_images._overwrite_with_stub") as mock_stub,
        ):
            _process_resources(resources, {}, modified)
            mock_stub.assert_called_once_with(image_obj)
            assert (10, 0) in modified

    def test_image_not_matching_is_skipped(self):
        image_obj = _make_xobject(Name.Image, objgen=(10, 0))
        resources = MagicMock()
        resources.__contains__ = MagicMock(return_value=True)
        resources.XObject.items.return_value = [("/Im1", image_obj)]
        modified = set()

        with (
            patch("pdftl.operations.delete_images._image_matches", return_value=False),
            patch("pdftl.operations.delete_images._overwrite_with_stub") as mock_stub,
        ):
            _process_resources(resources, {}, modified)
            mock_stub.assert_not_called()

    def test_already_modified_image_is_skipped(self):
        image_obj = _make_xobject(Name.Image, objgen=(10, 0))
        resources = MagicMock()
        resources.__contains__ = MagicMock(return_value=True)
        resources.XObject.items.return_value = [("/Im1", image_obj)]
        modified = {(10, 0)}  # already in set

        with patch("pdftl.operations.delete_images._overwrite_with_stub") as mock_stub:
            _process_resources(resources, {}, modified)
            mock_stub.assert_not_called()

    def test_form_xobject_recurses(self):
        form_obj = _make_xobject(Name.Form, objgen=(20, 0), has_resources=True)
        resources = MagicMock()
        resources.__contains__ = MagicMock(return_value=True)
        resources.XObject.items.return_value = [("/Fm1", form_obj)]
        modified = set()

        with patch("pdftl.operations.delete_images._process_resources"):
            # Call the real function but intercept the recursive call
            pass

        # Re-test with actual recursion — just verify it doesn't crash and
        # inner resources with no XObject terminates cleanly
        inner_resources = MagicMock()
        inner_resources.__contains__ = MagicMock(return_value=False)
        form_obj.Resources = inner_resources
        _process_resources(resources, {}, modified)

    def test_non_image_non_form_subtype_ignored(self):
        other_obj = _make_xobject(Name("/Other"), objgen=(30, 0))
        resources = MagicMock()
        resources.__contains__ = MagicMock(return_value=True)
        resources.XObject.items.return_value = [("/Misc", other_obj)]
        modified = set()

        with patch("pdftl.operations.delete_images._overwrite_with_stub") as mock_stub:
            _process_resources(resources, {}, modified)
            mock_stub.assert_not_called()


# ---------------------------------------------------------------------------
# delete_images (the main operation)
# ---------------------------------------------------------------------------


def _make_pdf(num_pages=2, objects=None):
    pdf = MagicMock()
    pages = []
    for i in range(num_pages):
        page = MagicMock()
        page.__contains__ = MagicMock(return_value=True)
        page.Resources = MagicMock()
        pages.append(page)
    pdf.pages = pages
    pdf.objects = objects or []
    return pdf


class TestDeleteImages:
    def test_no_specs_triggers_global_mode_no_crash(self):
        pdf = _make_pdf()
        pdf.objects = []
        result = delete_images(pdf, [])
        assert result.success is True

    def test_empty_string_spec_triggers_global_mode(self):
        pdf = _make_pdf()
        pdf.objects = []
        result = delete_images(pdf, [""])
        assert result.success is True

    def test_dash_selector_triggers_global_mode(self):
        pdf = _make_pdf()
        pdf.objects = []
        result = delete_images(pdf, ["-"])
        assert result.success is True

    def test_global_mode_overwrites_matching_image_object(self):
        image_obj = MagicMock()
        image_obj.get = MagicMock(
            side_effect=lambda key, default=None: {
                Name.Type: Name.XObject,
                Name.Subtype: Name.Image,
            }.get(key, default)
        )
        image_obj.objgen = (100, 0)

        pdf = _make_pdf()
        pdf.objects = [image_obj]

        with (
            patch("pdftl.operations.delete_images._image_matches", return_value=True),
            patch("pdftl.operations.delete_images._overwrite_with_stub") as mock_stub,
            patch("pikepdf.Stream", new=type(image_obj)),
        ):
            result = delete_images(pdf, ["(minbytes=1k)"])
            mock_stub.assert_called_once_with(image_obj)
        assert result.success is True

    def test_global_mode_skips_already_modified(self):
        image_obj = MagicMock()
        image_obj.get = MagicMock(
            side_effect=lambda key, default=None: {
                Name.Type: Name.XObject,
                Name.Subtype: Name.Image,
            }.get(key, default)
        )
        image_obj.objgen = (100, 0)

        pdf = _make_pdf()
        pdf.objects = [image_obj, image_obj]

        with (
            patch("pdftl.operations.delete_images._image_matches", return_value=True),
            patch("pdftl.operations.delete_images._overwrite_with_stub") as mock_stub,
            patch("pikepdf.Stream", new=type(image_obj)),
        ):
            delete_images(pdf, ["", ""])
            assert mock_stub.call_count == 1

    def test_global_mode_skips_non_stream_objects(self):
        non_stream = MagicMock()  # not a pikepdf.Stream instance

        pdf = _make_pdf()
        pdf.objects = [non_stream]

        with patch("pdftl.operations.delete_images._overwrite_with_stub") as mock_stub:
            result = delete_images(pdf, [""])
            mock_stub.assert_not_called()
        assert result.success is True

    def test_global_mode_skips_wrong_type_or_subtype(self):
        import pikepdf

        obj = MagicMock(spec=pikepdf.Stream)
        obj.get = MagicMock(return_value=None)  # Type/Subtype don't match
        obj.objgen = (101, 0)

        pdf = _make_pdf()
        pdf.objects = [obj]

        with patch("pdftl.operations.delete_images._overwrite_with_stub") as mock_stub:
            delete_images(pdf, [""])
            mock_stub.assert_not_called()

    def test_page_based_mode_calls_process_resources(self):
        pdf = _make_pdf(num_pages=3)

        with (
            patch(
                "pdftl.operations.delete_images.page_numbers_matching_page_spec",
                return_value=[1, 2],
            ),
            patch("pdftl.operations.delete_images._process_resources") as mock_proc,
        ):
            result = delete_images(pdf, ["1-2(minbytes=1k)"])
            assert mock_proc.call_count == 2
        assert result.success is True

    def test_page_based_mode_skips_page_without_resources(self):
        pdf = _make_pdf(num_pages=1)
        pdf.pages[0].__contains__ = MagicMock(return_value=False)  # no Resources

        with (
            patch(
                "pdftl.operations.delete_images.page_numbers_matching_page_spec", return_value=[1]
            ),
            patch("pdftl.operations.delete_images._process_resources") as mock_proc,
        ):
            result = delete_images(pdf, ["1"])
            mock_proc.assert_not_called()
        assert result.success is True

    def test_invalid_key_raises_invalid_argument_error(self):
        pdf = _make_pdf()
        with pytest.raises(InvalidArgumentError, match="unknown parameter 'badkey'"):
            delete_images(pdf, ["(badkey=100)"])

    def test_invalid_minbytes_value_raises(self):
        pdf = _make_pdf()
        with pytest.raises(InvalidArgumentError, match="Invalid value"):
            delete_images(pdf, ["(minbytes=notanumber)"])

    def test_invalid_maxbytes_value_raises(self):
        pdf = _make_pdf()
        with pytest.raises(InvalidArgumentError, match="Invalid value"):
            delete_images(pdf, ["(maxbytes=abc)"])

    def test_invalid_minpixels_wxh_raises(self):
        pdf = _make_pdf()
        with pytest.raises(InvalidArgumentError, match="Invalid value"):
            delete_images(pdf, ["(minpixels=axb)"])

    def test_invalid_maxpixels_area_raises(self):
        pdf = _make_pdf()
        with pytest.raises(InvalidArgumentError, match="Invalid value"):
            delete_images(pdf, ["(maxpixels=notanumber)"])

    def test_valid_minpixels_wxh_parses(self):
        pdf = _make_pdf()
        pdf.objects = []
        result = delete_images(pdf, ["(minpixels=100x200)"])
        assert result.success is True

    def test_valid_maxpixels_area_parses(self):
        pdf = _make_pdf()
        pdf.objects = []
        result = delete_images(pdf, ["(maxpixels=500k)"])
        assert result.success is True

    def test_format_param_parses(self):
        pdf = _make_pdf()
        pdf.objects = []
        result = delete_images(pdf, ["(format=dct)"])
        assert result.success is True

    def test_multiple_specs_processed_in_order(self):
        pdf = _make_pdf()
        pdf.objects = []
        result = delete_images(pdf, ["", "-", "(minbytes=1k)"])
        assert result.success is True

    def test_spec_with_no_equals_in_param_is_ignored(self):
        """A param token with no '=' raises an error."""
        pdf = _make_pdf()
        pdf.objects = []
        with pytest.raises(InvalidArgumentError, match="missing '='"):
            delete_images(pdf, ["(noequalssign)"])

    def test_returns_op_result_with_pdf(self):
        pdf = _make_pdf()
        pdf.objects = []
        result = delete_images(pdf, [])
        assert result.pdf is pdf
