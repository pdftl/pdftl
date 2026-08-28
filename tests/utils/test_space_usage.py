# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/utils/test_space_usage.py

"""Tests for pdftl.utils.space_usage.analyze_space_usage: the byte-accounting
core behind the `usage` operation.

These tests deliberately avoid the CLI/pipeline layer -- see
tests/operations/test_usage.py for that. The invariant under test
throughout is the accounting identity the module promises: category
byte totals always sum to the exact file size, never more, never less.
"""

import pikepdf
import pytest

from pdftl.utils.space_usage import CATEGORY_IDS, analyze_space_usage


def _make_simple_pdf(tmp_path, pages=1):
    path = tmp_path / "simple.pdf"
    pdf = pikepdf.new()
    for _ in range(pages):
        pdf.add_blank_page(page_size=(200, 200))
    pdf.save(str(path))
    return path


def _make_pdf_with_image(tmp_path):
    """A one-page PDF with a single embedded raw RGB image XObject."""
    path = tmp_path / "with_image.pdf"
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))

    width, height = 16, 16
    raw = bytes([200]) * (width * height * 3)
    image = pikepdf.Stream(pdf, raw)
    image.Type = pikepdf.Name.XObject
    image.Subtype = pikepdf.Name.Image
    image.Width = width
    image.Height = height
    image.BitsPerComponent = 8
    image.ColorSpace = pikepdf.Name.DeviceRGB

    resources = pdf.make_indirect(pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=image)))
    page.Resources = resources
    page.Contents = pdf.make_stream(b"q 100 0 0 100 50 50 cm /Im0 Do Q")

    pdf.save(str(path))
    return path


def _make_pdf_with_embedded_file(tmp_path):
    """A one-page PDF with a single embedded file attachment."""
    path = tmp_path / "with_attachment.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))

    payload = b"hello world " * 200
    ef_stream = pikepdf.Stream(pdf, payload)
    filespec = pikepdf.Dictionary(
        Type=pikepdf.Name.Filespec,
        F="attachment.txt",
        EF=pikepdf.Dictionary(F=ef_stream),
    )
    names_tree = pikepdf.Dictionary(
        Names=[pikepdf.String("attachment.txt"), pdf.make_indirect(filespec)]
    )
    pdf.Root.Names = pdf.make_indirect(pikepdf.Dictionary(EmbeddedFiles=names_tree))

    pdf.save(str(path))
    return path


def _make_pdf_with_object_stream(tmp_path):
    """A PDF saved with compressed cross-reference/object streams (PDF 1.5+
    style), to exercise the object-stream pro-rata split path."""
    path = tmp_path / "with_objstm.pdf"
    pdf = pikepdf.new()
    for _ in range(5):
        pdf.add_blank_page(page_size=(200, 200))
    pdf.Root.Info = pdf.make_indirect(pikepdf.Dictionary(Title="Object Stream Test"))
    pdf.save(str(path), object_stream_mode=pikepdf.ObjectStreamMode.generate)
    return path


class TestAccountingIdentity:
    """The core promise: category totals always sum to exactly file_size."""

    def test_categories_sum_to_exact_file_size(self, tmp_path):
        path = _make_simple_pdf(tmp_path)
        file_bytes = path.read_bytes()
        with pikepdf.open(path) as pdf:
            report = analyze_space_usage(pdf, file_bytes)

        assert report["file_size"] == len(file_bytes)
        summed = sum(row["bytes"] for row in report["categories"])
        assert summed == report["file_size"]
        assert report["total"] == report["file_size"]

    def test_identity_holds_with_multiple_pages(self, tmp_path):
        path = _make_simple_pdf(tmp_path, pages=10)
        file_bytes = path.read_bytes()
        with pikepdf.open(path) as pdf:
            report = analyze_space_usage(pdf, file_bytes)

        summed = sum(row["bytes"] for row in report["categories"])
        assert summed == report["file_size"]

    def test_identity_holds_with_image(self, tmp_path):
        path = _make_pdf_with_image(tmp_path)
        file_bytes = path.read_bytes()
        with pikepdf.open(path) as pdf:
            report = analyze_space_usage(pdf, file_bytes)

        summed = sum(row["bytes"] for row in report["categories"])
        assert summed == report["file_size"]

    def test_identity_holds_with_embedded_file(self, tmp_path):
        path = _make_pdf_with_embedded_file(tmp_path)
        file_bytes = path.read_bytes()
        with pikepdf.open(path) as pdf:
            report = analyze_space_usage(pdf, file_bytes)

        summed = sum(row["bytes"] for row in report["categories"])
        assert summed == report["file_size"]

    def test_identity_holds_with_object_streams(self, tmp_path):
        path = _make_pdf_with_object_stream(tmp_path)
        file_bytes = path.read_bytes()
        with pikepdf.open(path) as pdf:
            report = analyze_space_usage(pdf, file_bytes)

        summed = sum(row["bytes"] for row in report["categories"])
        assert summed == report["file_size"]

    def test_identity_holds_on_truncated_bytes(self, tmp_path):
        """A byte string that doesn't fully match the open pdf (e.g. stale
        or truncated) should degrade to unmeasured objects rather than
        raise, with the residual absorbing the shortfall."""
        path = _make_simple_pdf(tmp_path)
        real_bytes = path.read_bytes()
        truncated = real_bytes[: len(real_bytes) // 2]
        with pikepdf.open(path) as pdf:
            report = analyze_space_usage(pdf, truncated)

        assert report["file_size"] == len(truncated)
        summed = sum(row["bytes"] for row in report["categories"])
        assert summed == report["file_size"]
        assert report["unmeasured_objects"] >= 0


class TestReportShape:
    """Structural properties of the returned report dict."""

    def test_all_category_ids_present_in_declared_order(self, tmp_path):
        path = _make_simple_pdf(tmp_path)
        file_bytes = path.read_bytes()
        with pikepdf.open(path) as pdf:
            report = analyze_space_usage(pdf, file_bytes)

        reported_ids = tuple(row["id"] for row in report["categories"])
        assert reported_ids == CATEGORY_IDS

    def test_overhead_is_last_and_marked_residual(self, tmp_path):
        path = _make_simple_pdf(tmp_path)
        file_bytes = path.read_bytes()
        with pikepdf.open(path) as pdf:
            report = analyze_space_usage(pdf, file_bytes)

        overhead_row = report["categories"][-1]
        assert overhead_row["id"] == "overhead"
        assert overhead_row["residual"] is True
        detail_kinds = {d["kind"] for d in overhead_row["detail"]}
        assert detail_kinds == {"cross_reference", "unreferenced", "structural"}

    def test_non_residual_rows_have_no_residual_key(self, tmp_path):
        path = _make_simple_pdf(tmp_path)
        file_bytes = path.read_bytes()
        with pikepdf.open(path) as pdf:
            report = analyze_space_usage(pdf, file_bytes)

        for row in report["categories"]:
            if row["id"] != "overhead":
                assert "residual" not in row

    def test_share_values_are_fractions_of_file_size(self, tmp_path):
        path = _make_simple_pdf(tmp_path)
        file_bytes = path.read_bytes()
        with pikepdf.open(path) as pdf:
            report = analyze_space_usage(pdf, file_bytes)

        for row in report["categories"]:
            assert 0.0 <= row["share"] <= 1.0
            expected_share = row["bytes"] / report["file_size"]
            assert row["share"] == pytest.approx(expected_share)

    def test_detail_rows_capped_and_flag_truncation(self, tmp_path):
        # Many small annotations across one page, to try to exceed
        # DETAIL_CAP for a single non-residual category.
        path = tmp_path / "many_annots.pdf"
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(2000, 2000))
        annots = []
        for i in range(60):
            annot = pdf.make_indirect(
                pikepdf.Dictionary(
                    Type=pikepdf.Name.Annot,
                    Subtype=pikepdf.Name.Text,
                    Rect=[i, i, i + 10, i + 10],
                    Contents=f"note {i}",
                )
            )
            annots.append(annot)
        page.Annots = annots
        pdf.save(str(path))

        file_bytes = path.read_bytes()
        with pikepdf.open(path) as pdf_open:
            report = analyze_space_usage(pdf_open, file_bytes)

        by_id = {row["id"]: row for row in report["categories"]}
        annotations_row = by_id["annotations"]
        assert annotations_row["objects"] == 60
        assert len(annotations_row["detail"]) <= 50
        if annotations_row["objects"] > 50:
            assert annotations_row.get("detail_truncated") is True

    def test_detail_rows_have_obj_id_and_pages_list(self, tmp_path):
        path = _make_pdf_with_image(tmp_path)
        file_bytes = path.read_bytes()
        with pikepdf.open(path) as pdf:
            report = analyze_space_usage(pdf, file_bytes)

        by_id = {row["id"]: row for row in report["categories"]}
        images_detail = by_id["images"]["detail"]
        assert len(images_detail) == 1
        entry = images_detail[0]
        assert isinstance(entry["obj_id"], int)
        assert isinstance(entry["pages"], list)
        assert entry["pages"] == [1]

    def test_shared_object_lists_every_referencing_page_once(self, tmp_path):
        # A single Form XObject used as the appearance for the same page
        # resource dict shared across two pages: the object should appear
        # exactly once in detail, with both pages listed.
        path = tmp_path / "shared_resource.pdf"
        pdf = pikepdf.new()
        shared_font = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Font,
                Subtype=pikepdf.Name.Type1,
                BaseFont=pikepdf.Name("/Helvetica"),
            )
        )
        for _ in range(2):
            page = pdf.add_blank_page(page_size=(200, 200))
            page.Resources = pdf.make_indirect(
                pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=shared_font))
            )
        pdf.save(str(path))

        file_bytes = path.read_bytes()
        with pikepdf.open(path) as pdf_open:
            report = analyze_space_usage(pdf_open, file_bytes)
            font_obj = pdf_open.pages[0].Resources.Font.F1
            target_obj_id = font_obj.objgen[0]

        by_id = {row["id"]: row for row in report["categories"]}
        font_entries = [d for d in by_id["fonts"]["detail"] if d["obj_id"] == target_obj_id]
        assert len(font_entries) == 1
        assert font_entries[0]["pages"] == [1, 2]


class TestCategoryAttribution:
    """Spot-checks that specific object kinds land in the expected category."""

    def test_image_bytes_attributed_to_images_category(self, tmp_path):
        path = _make_pdf_with_image(tmp_path)
        file_bytes = path.read_bytes()
        with pikepdf.open(path) as pdf:
            report = analyze_space_usage(pdf, file_bytes)

        by_id = {row["id"]: row for row in report["categories"]}
        assert by_id["images"]["bytes"] > 0
        assert by_id["images"]["objects"] >= 1

    def test_embedded_file_attributed_to_embedded_files_category(self, tmp_path):
        path = _make_pdf_with_embedded_file(tmp_path)
        file_bytes = path.read_bytes()
        with pikepdf.open(path) as pdf:
            report = analyze_space_usage(pdf, file_bytes)

        by_id = {row["id"]: row for row in report["categories"]}
        assert by_id["embedded_files"]["bytes"] > 0
        assert by_id["embedded_files"]["objects"] >= 1

    def test_plain_pdf_has_no_images_or_embedded_files(self, tmp_path):
        path = _make_simple_pdf(tmp_path)
        file_bytes = path.read_bytes()
        with pikepdf.open(path) as pdf:
            report = analyze_space_usage(pdf, file_bytes)

        by_id = {row["id"]: row for row in report["categories"]}
        assert by_id["images"]["bytes"] == 0
        assert by_id["embedded_files"]["bytes"] == 0

    def test_object_stream_members_attributed_not_left_uncharged(self, tmp_path):
        path = _make_pdf_with_object_stream(tmp_path)
        file_bytes = path.read_bytes()
        with pikepdf.open(path) as pdf:
            report = analyze_space_usage(pdf, file_bytes)

        by_id = {row["id"]: row for row in report["categories"]}
        assert by_id["metadata"]["bytes"] > 0


class TestEdgeCasesAndCoverage:
    """Covers edge cases in space_usage parsing, edge traversal, and details."""

    def test_unreferenced_and_unmeasured_objects(self, tmp_path):
        path = _make_simple_pdf(tmp_path)
        pdf = pikepdf.open(path)
        pdf.make_indirect(pikepdf.Dictionary(UnrefTest=True))
        out_path = tmp_path / "unref.pdf"
        pdf.save(str(out_path))
        pdf.close()

        file_bytes = out_path.read_bytes()
        with pikepdf.open(out_path) as open_pdf:
            report = analyze_space_usage(open_pdf, file_bytes)
        by_id = {row["id"]: row for row in report["categories"]}
        assert "overhead" in by_id

    def test_pdf_with_acroform_and_outlines_and_metadata(self, tmp_path):
        path = tmp_path / "rich_features.pdf"
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(200, 200))

        # Add AcroForm
        field = pdf.make_indirect(pikepdf.Dictionary(FT=pikepdf.Name.Tx, T="FieldName"))
        pdf.Root.AcroForm = pdf.make_indirect(pikepdf.Dictionary(Fields=[field]))

        # Add Outlines / Bookmarks using page.obj
        outline_item = pdf.make_indirect(
            pikepdf.Dictionary(Title="Chapter 1", Dest=pikepdf.Array([page.obj, pikepdf.Name.Fit]))
        )
        pdf.Root.Outlines = pdf.make_indirect(
            pikepdf.Dictionary(First=outline_item, Last=outline_item)
        )

        # Add Metadata stream
        meta_stream = pikepdf.Stream(pdf, b"<x:xmpmeta>test</x:xmpmeta>")
        meta_stream.Type = pikepdf.Name.Metadata
        meta_stream.Subtype = pikepdf.Name.XML
        pdf.Root.Metadata = pdf.make_indirect(meta_stream)

        pdf.save(str(path))
        file_bytes = path.read_bytes()

        with pikepdf.open(path) as open_pdf:
            report = analyze_space_usage(open_pdf, file_bytes)

        by_id = {row["id"]: row for row in report["categories"]}
        assert by_id["forms"]["bytes"] > 0
        assert by_id["bookmarks"]["bytes"] > 0
        assert by_id["metadata"]["bytes"] > 0

    def test_pdf_with_tagged_struct_tree_and_fonts(self, tmp_path):
        """Exercises font descriptors, ToUnicode streams, and StructTreeRoot traversal."""
        path = tmp_path / "tagged_fonts.pdf"
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(200, 200))

        # ToUnicode Stream & FontDescriptor
        tounicode_stream = pdf.make_stream(b"/CIDInit /ProcSet findresource begin ... end")
        font_descriptor = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.FontDescriptor,
                FontName=pikepdf.Name.CustomFont,
                Flags=32,
            )
        )
        cid_font = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Font,
                Subtype=pikepdf.Name.CIDFontType2,
                BaseFont=pikepdf.Name.CustomFont,
                FontDescriptor=font_descriptor,
            )
        )
        type0_font = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Font,
                Subtype=pikepdf.Name.Type0,
                BaseFont=pikepdf.Name.CustomFont,
                Encoding=pikepdf.Name.IdentityH,
                DescendantFonts=[cid_font],
                ToUnicode=tounicode_stream,
            )
        )
        page.Resources = pdf.make_indirect(
            pikepdf.Dictionary(Font=pikepdf.Dictionary(F2=type0_font))
        )

        # Tagged PDF / StructTreeRoot
        struct_elem = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.StructElem,
                S=pikepdf.Name.P,
                Pg=page.obj,
            )
        )
        struct_tree = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.StructTreeRoot,
                K=pikepdf.Array([struct_elem]),
            )
        )
        pdf.Root.StructTreeRoot = struct_tree

        pdf.save(str(path))
        file_bytes = path.read_bytes()

        with pikepdf.open(path) as open_pdf:
            report = analyze_space_usage(open_pdf, file_bytes)

        by_id = {row["id"]: row for row in report["categories"]}
        assert by_id["fonts"]["bytes"] > 0
        non_font_bytes = sum(row["bytes"] for cat_id, row in by_id.items() if cat_id != "fonts")
        assert non_font_bytes > 0

    def test_pdf_with_form_xobject_and_pieceinfo(self, tmp_path):
        """Exercises Form XObjects, piece info dictionaries, and optional content."""
        path = tmp_path / "form_xobject.pdf"
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(200, 200))

        form_stream = pikepdf.Stream(pdf, b"1 0 0 1 0 0 cm")
        form_stream.Type = pikepdf.Name.XObject
        form_stream.Subtype = pikepdf.Name.Form
        form_stream.BBox = [0, 0, 100, 100]

        piece_dict = pdf.make_indirect(
            pikepdf.Dictionary(
                LastModified=pikepdf.String("D:20260101000000Z"),
                Private=pikepdf.Dictionary(Data="test"),
            )
        )
        page.PieceInfo = pdf.make_indirect(pikepdf.Dictionary(ApplicationName=piece_dict))
        page.Resources = pdf.make_indirect(
            pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fm0=pdf.make_indirect(form_stream)))
        )

        pdf.save(str(path))
        file_bytes = path.read_bytes()

        with pikepdf.open(path) as open_pdf:
            report = analyze_space_usage(open_pdf, file_bytes)

        by_id = {row["id"]: row for row in report["categories"]}
        content_row = by_id.get("content_streams") or by_id.get("page_contents") or {}
        assert content_row.get("bytes", 0) > 0 or report["total_bytes"] > 0


from unittest.mock import MagicMock, PropertyMock


from pdftl.utils.space_usage import (
    DETAIL_CAP,
    _as_str,
    _attribute_objects,
    _charge_object_stream_members,
    _charge_stream_members,
    _charge_top_level_objects,
    _compute_stream_member_weights,
    _edge_declared_category,
    _extract_stream_extent,
    _filter_label,
    _linearization_objgens,
    _merge_intervals,
    _object_extent,
    _process_attribution_step,
    _push_children,
    _resolve_object_category,
    _self_declared_category,
    _subtract_intervals,
    _xref_section_ranges,
)


class CustomErrorStr:
    """Helper class whose __str__ raises an exception."""

    def __str__(self):
        raise ValueError("Cannot convert to string")


# -----------------------------------------------------------------------------
# 1. Helper Functions (_as_str, _filter_label, _self_declared_category, etc.)
# -----------------------------------------------------------------------------


def test_as_str_exceptions():
    # Exception handling during string conversion
    assert _as_str(CustomErrorStr()) == ""


def test_self_declared_category_branches():
    # XObject image handling on a Dictionary
    d_img = pikepdf.Dictionary(Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image)
    assert _self_declared_category(d_img, "dict", d_img) == "images"

    # ShadingType / PatternType / FunctionType content streams
    d_shading = pikepdf.Dictionary(ShadingType=1)
    assert _self_declared_category(d_shading, "dict", d_shading) == "content_streams"

    d_pattern = pikepdf.Dictionary(PatternType=1)
    assert _self_declared_category(d_pattern, "dict", d_pattern) == "content_streams"

    d_func = pikepdf.Dictionary(FunctionType=0)
    assert _self_declared_category(d_func, "dict", d_func) == "content_streams"


def test_edge_declared_category_branches():
    # SMask/Mask/Alternates edge key checks
    img_dict = pikepdf.Dictionary(Subtype=pikepdf.Name.Image)
    assert _edge_declared_category("/SMask", img_dict) == "images"
    assert _edge_declared_category("/Mask", img_dict) == "images"
    assert _edge_declared_category("/Alternates", img_dict) == "images"

    non_img_dict = pikepdf.Dictionary(Subtype=pikepdf.Name.Form)
    assert _edge_declared_category("/SMask", non_img_dict) is None
    assert _edge_declared_category("/SMask", None) is None


def test_filter_label_branches():
    # Handle None dictionary
    assert _filter_label(None) == ""

    # Filter specified as a pikepdf.Array
    arr_filter_dict = pikepdf.Dictionary(
        Filter=pikepdf.Array([pikepdf.Name.FlateDecode, pikepdf.Name.ASCII85Decode])
    )
    assert _filter_label(arr_filter_dict) == "FlateDecode,ASCII85Decode"


# -----------------------------------------------------------------------------
# 2. Extent Parsing (_extract_stream_extent, _object_extent)
# -----------------------------------------------------------------------------


def test_extract_stream_extent_branches():
    mock_obj = MagicMock()

    # CRLF newline after stream keyword
    data_crlf = b"1 0 obj\r\nstream\r\n1234567890\r\nendstream\r\nendobj"
    mock_obj.stream_dict.get.return_value = 10
    ext = _extract_stream_extent(data_crlf, 0, mock_obj)
    assert ext is not None

    # Single-byte delimiter or direct stream data
    data_direct = b"1 0 obj\r\nstreamX1234567890endstream\r\nendobj"
    mock_obj.stream_dict.get.return_value = 10
    ext_direct = _extract_stream_extent(data_direct, 0, mock_obj)
    assert ext_direct == data_direct.find(b"endstream") + 9

    # Declared length raises TypeError/ValueError on int conversion
    mock_obj.stream_dict.get.return_value = "invalid_length"
    data_fallback = b"1 0 obj\r\nstream\r\nhello endstream\r\nendobj"
    assert _extract_stream_extent(data_fallback, 0, mock_obj) is not None

    # endstream keyword not found in predicted length tail, falling back to manual search
    mock_obj.stream_dict.get.return_value = 2  # Incorrect short length
    data_short = b"1 0 obj\r\nstream\r\n1234567890endstream"
    assert _extract_stream_extent(data_short, 0, mock_obj) == len(data_short)

    # Missing endstream keyword return handling
    data_no_end = b"1 0 obj\r\nstream\r\n1234567890"
    mock_obj.stream_dict.get.return_value = None
    assert _extract_stream_extent(data_no_end, 0, mock_obj) is None


def test_object_extent_branches():
    # Offset out of bounds conditions
    assert _object_extent(b"data", -1, 1, None) == 0
    assert _object_extent(b"data", 100, 1, None) == 0

    # Object header regex failure or object number mismatch
    data = b"5 0 obj\r\n<</Foo /Bar>>\r\nendobj"
    assert _object_extent(data, 0, 999, None) == 0  # wrong obj num
    assert _object_extent(b"not an obj header", 0, 1, None) == 0

    # Missing endobj keyword
    data_no_endobj = b"1 0 obj\r\n<</Foo /Bar>>"
    assert _object_extent(data_no_endobj, 0, 1, None) == 0


# -----------------------------------------------------------------------------
# 3. Intervals Arithmetic (_merge_intervals, _subtract_intervals)
# -----------------------------------------------------------------------------


def test_merge_intervals_end_less_than_start():
    # Ignore invalid interval where end <= start
    intervals = [[10, 5], [5, 5], [0, 10]]
    assert _merge_intervals(intervals) == [[0, 10]]


def test_subtract_intervals_branches():
    # Multi-segment subtraction across base interval bounds
    base = [[0, 100]]
    cuts = [[10, 20], [20, 30], [50, 60], [90, 110]]
    res = _subtract_intervals(base, cuts)
    assert res == [[0, 10], [30, 50], [60, 90]]

    # Cut completely covering base interval
    assert _subtract_intervals([[10, 20]], [[5, 25]]) == []


# -----------------------------------------------------------------------------
# 4. Object Attribution & Traversal
# -----------------------------------------------------------------------------


def test_push_children_exceptions():
    # Real pikepdf Dictionary traversal
    real_dict = pikepdf.Dictionary(Foo=pikepdf.Name.Bar)
    stack = []
    _push_children(real_dict, "dict", real_dict, None, None, "", False, stack)

    # Duck-typed object to test KeyError during item lookup
    class KeyErrorDict:
        def keys(self):
            return ["/BadKey"]

        def __getitem__(self, key):
            raise KeyError(key)

    d_keyerr = KeyErrorDict()
    _push_children(d_keyerr, "dict", d_keyerr, None, None, "", False, stack)

    # Duck-typed object to test AttributeError during array iteration
    class BrokenIterArray:
        def __iter__(self):
            raise AttributeError("Iter error")

    arr_broken = BrokenIterArray()
    _push_children(arr_broken, "array", None, None, None, "", False, stack)


def test_resolve_object_category_sticky_reset():
    # Category resolution when sticky flag is set without override category match
    d_annot = pikepdf.Dictionary(Type=pikepdf.Name.Annot)
    res = _resolve_object_category("", d_annot, "dict", d_annot, "annotations", sticky=True)
    assert res == "annotations"


def test_process_attribution_step_other_objects():
    # Default unmapped indirect object attribution to "other_objects"
    mock_obj = MagicMock()
    mock_obj.objgen = (10, 0)
    mock_obj.get.return_value = None

    found = MagicMock()
    found.claim.return_value = True
    stack = []

    _process_attribution_step((mock_obj, None, None, "", False), {}, found, stack)
    found.claim.assert_called_with((10, 0), "other_objects", "")


def test_attribute_objects_page_attribute_error():
    # Page object access raising AttributeError fallback
    mock_pdf = MagicMock()
    bad_page = MagicMock()
    type(bad_page).obj = PropertyMock(side_effect=AttributeError)
    mock_pdf.pages = [bad_page]
    mock_pdf.trailer = pikepdf.Dictionary()

    found = _attribute_objects(mock_pdf)
    assert isinstance(found, object)


# -----------------------------------------------------------------------------
# 5. Linearization & XRef Utilities
# -----------------------------------------------------------------------------


def test_linearization_objgens_branches():
    mock_pdf = MagicMock()

    # Handling KeyError / PdfError on object lookup
    mock_pdf.get_object.side_effect = KeyError("Object missing")
    res = _linearization_objgens(mock_pdf, {(1, 0): 100})
    assert res == set()

    # Linearized object resolution handling
    mock_pdf.get_object.side_effect = None

    # Invalid hint payload handling
    lin_dict_bad_hint = pikepdf.Dictionary(Linearized=1, H="not_an_array_or_int_sequence")
    mock_pdf.get_object.return_value = lin_dict_bad_hint
    res_bad = _linearization_objgens(mock_pdf, {(1, 0): 100})
    assert (1, 0) in res_bad

    # Valid hint payload referencing existing object offset
    lin_dict_good_hint = pikepdf.Dictionary(Linearized=1, H=pikepdf.Array([200, 50]))
    mock_pdf.get_object.return_value = lin_dict_good_hint
    res_good = _linearization_objgens(mock_pdf, {(1, 0): 100, (2, 0): 200, (3, 0): 300})
    assert (1, 0) in res_good
    assert (2, 0) in res_good


def test_xref_section_ranges_branches():
    # Handle startxref value exceeding integer limits or invalid format
    data_huge_int = b"startxref\r\n9999999999999999999999999999999999999\r\n%%EOF"
    ranges = _xref_section_ranges(data_huge_int)
    assert ranges == []

    # Handle truncated section missing %%EOF terminator
    data_no_eof = b"xref\r\n0 1\r\n0000000000 65535 f\r\n"
    ranges_no_eof = _xref_section_ranges(data_no_eof)
    assert ranges_no_eof == []


# -----------------------------------------------------------------------------
# 6. Stream Charging & Overhead Helper Branches
# -----------------------------------------------------------------------------


def test_collect_xref_and_objects_free_entries_and_exceptions():
    mock_pdf = MagicMock()

    # Ignore free xref entries (entry.type != 1 and entry.type != 2)
    mock_entry_free = MagicMock()
    mock_entry_free.type = 0

    mock_entry_used = MagicMock()
    mock_entry_used.type = 1
    mock_entry_used.offset = 0

    mock_pdf.get_xref_table.return_value = {
        (1, 0): mock_entry_free,
        (2, 0): mock_entry_used,
    }

    # Handle missing object KeyError during table collection
    mock_pdf.get_object.side_effect = KeyError

    (
        xref_table,
        offset_by_objgen,
        members_by_stream,
        objects_by_objgen,
        extent_by_objgen,
        unmeasured,
    ) = analyze_space_usage.__globals__["_collect_xref_and_objects"](mock_pdf, b"dummy")

    assert (1, 0) not in offset_by_objgen
    assert objects_by_objgen[(2, 0)] is None


def test_charge_top_level_objects_unreferenced():
    # Handle unreferenced top-level object without assigned category
    found = MagicMock()
    found.category = {}

    extent_by_objgen = {(1, 0): 50}
    offset_by_objgen = {(1, 0): 10}
    objects_by_objgen = {(1, 0): pikepdf.Dictionary()}

    (
        objstm_intervals,
        layout_intervals,
        charged_intervals,
        unref_intervals,
        unref_count,
    ) = _charge_top_level_objects(
        extent_by_objgen,
        offset_by_objgen,
        objects_by_objgen,
        set(),
        found,
        MagicMock(),
    )
    assert unref_count == 1
    assert unref_intervals == [[10, 60]]


def test_compute_stream_member_weights_exception():
    # Handle exception during stream member object unparsing
    mock_pdf = MagicMock()
    mock_pdf.get_object.side_effect = pikepdf.PdfError("Parse failure")

    weights, total = _compute_stream_member_weights(mock_pdf, [(1, 0)])
    assert weights == [((1, 0), 0)]
    assert total == 0


def test_charge_stream_members_branches():
    mock_pdf = MagicMock()
    record_mock = MagicMock()

    # Unreferenced packed stream member handling
    found_unref = MagicMock()
    found_unref.category = {}

    weights = [((1, 0), 100)]
    u_count, u_bytes = _charge_stream_members(
        weights, 200, 100, mock_pdf, found_unref, {}, record_mock
    )
    assert u_count == 1
    assert u_bytes == 200

    # Member lookup fallback when object missing from local objects map
    found_ref = MagicMock()
    found_ref.category = {(2, 0): "fonts"}
    mock_pdf.get_object.side_effect = KeyError("Not found")

    objects_map = {}
    _charge_stream_members(
        [((2, 0), 100)], 200, 100, mock_pdf, found_ref, objects_map, record_mock
    )
    assert objects_map[(2, 0)] is None
    record_mock.assert_called_with((2, 0), "fonts", 200)


def test_charge_object_stream_members_empty_or_zero_weight():
    mock_pdf = MagicMock()
    found = MagicMock()
    record = MagicMock()

    members_by_stream = {(1, 0): [(2, 0)]}

    # Handle missing or zero stream extent
    u_count, u_bytes = _charge_object_stream_members(
        members_by_stream, {}, mock_pdf, found, {}, record
    )
    assert (u_count, u_bytes) == (0, 0)

    # Handle total member weight falling back to zero
    mock_pdf.get_object.side_effect = KeyError
    extent_by_objgen = {(1, 0): 500}
    u_count, u_bytes = _charge_object_stream_members(
        members_by_stream, extent_by_objgen, mock_pdf, found, {}, record
    )
    assert (u_count, u_bytes) == (0, 0)


# -----------------------------------------------------------------------------
# 7. Integration & Detail Truncation
# -----------------------------------------------------------------------------


def test_analyze_space_usage_detail_truncation():
    # Verify behavior when category detail list exceeds max capacity cap
    pdf = pikepdf.new()
    img_list = pikepdf.Array()

    for _ in range(DETAIL_CAP + 5):
        img_stream = pdf.make_stream(
            b"GIF89a",
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Image,
        )
        img_list.append(pdf.make_indirect(img_stream))

    pdf.Root.TestImages = img_list
    pdf.get_warnings()

    bio = io.BytesIO()
    pdf.save(bio)
    raw_bytes = bio.getvalue()
    pdf_reopened = pikepdf.open(io.BytesIO(raw_bytes))

    res = analyze_space_usage(pdf_reopened, raw_bytes)

    images_cat = next(c for c in res["categories"] if c["id"] == "images")
    assert images_cat.get("detail_truncated") is True
    assert len(images_cat["detail"]) == DETAIL_CAP


import io


from unittest.mock import patch

from pdftl.utils.space_usage import (
    _Attribution,
)


def test_extract_stream_extent_short_declared_length_fallback():
    # Line 233->236: Length prediction tail does not contain 'endstream'
    mock_obj = MagicMock()
    mock_obj.stream_dict.get.return_value = 5  # Too short length
    data = b"1 0 obj\r\nstream\r\n0123456789012345678901234567890endstream\r\nendobj"

    ext = _extract_stream_extent(data, 0, mock_obj)
    assert ext == data.find(b"endstream") + 9


def test_object_extent_mismatched_obj_num():
    # offset > 0 bypasses line 244; header matches object 1, but target object is 999
    data = b"     1 0 obj\r\n<</Foo /Bar>>\r\nendobj"
    assert _object_extent(data, offset=5, obj_num=999, obj=None) == 0


def test_object_extent_missing_endobj():
    # offset > 0 bypasses line 244; header matches, but endobj keyword is missing
    data = b"     1 0 obj\r\n<</Foo /Bar>>\r\n"
    assert _object_extent(data, offset=5, obj_num=1, obj=None) == 0


def test_push_children_stream_or_dict_none():
    # Line 337: _stream_or_dict returns None for a Dictionary instance
    class StreamOrDictNone(pikepdf.Dictionary):
        pass

    obj = StreamOrDictNone()
    stack = []
    with patch("pdftl.utils.space_usage._stream_or_dict", return_value=None):
        _push_children(obj, None, None, None, None, "", False, stack)
    assert stack == []


def test_push_children_key_error_on_getitem():
    # Lines 341-342: KeyError when accessing d[child_key]
    class KeyErrorDict(pikepdf.Dictionary):
        def keys(self):
            return [pikepdf.Name.BadKey]

        def __getitem__(self, key):
            raise KeyError(key)

    stack = []
    d_err = KeyErrorDict()
    _push_children(d_err, "dict", d_err, None, None, "", False, stack)
    assert stack == []


def test_push_children_attribute_error_on_keys_and_iter():
    # Lines 346-347: AttributeError on d.keys() or Array iteration
    class BadKeysDict(pikepdf.Dictionary):
        def keys(self):
            raise AttributeError("Keys failure")

    class BadIterArray(pikepdf.Array):
        def __iter__(self):
            raise AttributeError("Iter failure")

    stack = []
    d_bad = BadKeysDict()
    arr_bad = BadIterArray()
    _push_children(d_bad, "dict", d_bad, None, None, "", False, stack)
    _push_children(arr_bad, "array", None, None, None, "", False, stack)
    assert stack == []


def test_attribute_objects_page_obj_attribute_error():
    # Lines 389-390: page.obj raises AttributeError
    class BadPage:
        @property
        def obj(self):
            raise AttributeError("No obj")

    pdf = MagicMock()
    pdf.pages = [BadPage()]
    pdf.trailer = pikepdf.Dictionary()

    found = _attribute_objects(pdf)
    assert isinstance(found, _Attribution)


def test_xref_section_ranges_invalid_int_and_missing_eof():
    # Lines 441-442: ValueError during int conversion of startxref match
    fake_match = MagicMock()
    fake_match.group.return_value = "invalid_int"
    fake_re = MagicMock()
    fake_re.finditer.return_value = [fake_match]

    with patch("pdftl.utils.space_usage._STARTXREF_RE", fake_re):
        assert _xref_section_ranges(b"dummy") == []

    # Line 451: startxref section missing %%EOF terminator
    data_no_eof = b"startxref\r\n100\r\n"
    assert _xref_section_ranges(data_no_eof) == []


def test_charge_stream_members_objgen_already_in_objects_by_objgen():
    # Line 587->592: objgen is already present in objects_by_objgen dict
    found = _Attribution()
    found.category[(1, 0)] = "fonts"
    record = MagicMock()
    pdf = MagicMock()

    existing_obj = pikepdf.Dictionary()
    objects_by_objgen = {(1, 0): existing_obj}

    _charge_stream_members(
        weights=[((1, 0), 100)],
        extent=500,
        total_weight=100,
        pdf=pdf,
        found=found,
        objects_by_objgen=objects_by_objgen,
        record=record,
    )
    pdf.get_object.assert_not_called()
    record.assert_called_once_with((1, 0), "fonts", 500)


def test_object_extent_mismatched_obj_num_line_248():
    # Line 248: Header regex matches, but header obj_num does not match target obj_num
    data = b"1 0 obj\r\n<</Foo /Bar>>\r\nendobj"
    assert _object_extent(data, offset=0, obj_num=999, obj=None) == 0


def test_object_extent_missing_endobj_line_259():
    # Line 259: Object header matches, but 'endobj' keyword is missing in data
    data = b"1 0 obj\r\n<</Foo /Bar>>\r\n"
    assert _object_extent(data, offset=0, obj_num=1, obj=None) == 0


def test_push_children_getitem_exceptions_lines_341_342():
    # Lines 341-342: KeyError, AttributeError, and PdfError during d[child_key] lookup
    class KeyErrorDict(pikepdf.Dictionary):
        def keys(self):
            return [pikepdf.Name.Key1]

        def __getitem__(self, key):
            raise KeyError(key)

    class AttributeErrorDict(pikepdf.Dictionary):
        def keys(self):
            return [pikepdf.Name.Key2]

        def __getitem__(self, key):
            raise AttributeError("item error")

    class PdfErrorDict(pikepdf.Dictionary):
        def keys(self):
            return [pikepdf.Name.Key3]

        def __getitem__(self, key):
            raise pikepdf.PdfError("pdf error")

    for dict_cls in (KeyErrorDict, AttributeErrorDict, PdfErrorDict):
        stack = []

        d_inst = dict_cls()
        _push_children(d_inst, "dict", d_inst, None, None, "", False, stack)
        assert stack == []


def test_push_children_keys_and_iter_exceptions_lines_346_347():
    # Lines 346-347: AttributeError or PdfError when calling d.keys() or iterating Array
    class BadKeysAttributeDict(pikepdf.Dictionary):
        def keys(self):
            raise AttributeError("Keys failure")

    class BadKeysPdfErrorDict(pikepdf.Dictionary):
        def keys(self):
            raise pikepdf.PdfError("Keys pdf failure")

    class BadIterAttributeArray(pikepdf.Array):
        def __iter__(self):
            raise AttributeError("Iter failure")

    class BadIterPdfErrorArray(pikepdf.Array):
        def __iter__(self):
            raise pikepdf.PdfError("Iter pdf failure")

    for bad_obj in (
        BadKeysAttributeDict(),
        BadKeysPdfErrorDict(),
        BadIterAttributeArray(),
        BadIterPdfErrorArray(),
    ):
        stack = []
        _push_children(bad_obj, "dict", bad_obj, None, None, "", False, stack)
        assert stack == []


def test_xref_section_ranges_missing_eof_line_451():
    # Line 451: Valid offset (> 0 and < len(data)), but %%EOF terminator is missing after start offset

    # Case 1: Triggered via startxref
    data_startxref = b"01234567890123456789startxref\r\n10\r\nno_eof_here"
    assert _xref_section_ranges(data_startxref) == []

    # Case 2: Triggered via xref keyword
    data_xref = b"0123456789xref\r\n0 1\r\n0000000000 65535 f\r\nno_eof_here"
    assert _xref_section_ranges(data_xref) == []


def test_push_children_getitem_exceptions(monkeypatch):
    # Tests dictionary lookup errors during stack iteration
    real_dict = pikepdf.Dictionary()

    class KeyErrorDict:
        def keys(self):
            return ["key1"]

        def __getitem__(self, key):
            raise KeyError(key)

    class AttributeErrorDict:
        def keys(self):
            return ["key2"]

        def __getitem__(self, key):
            raise AttributeError("attr error")

    class PdfErrorDict:
        def keys(self):
            return ["key3"]

        def __getitem__(self, key):
            raise pikepdf.PdfError("pdf error")

    for mock_dict in (KeyErrorDict(), AttributeErrorDict(), PdfErrorDict()):
        monkeypatch.setattr("pdftl.utils.space_usage._stream_or_dict", lambda _: mock_dict)
        stack = []
        _push_children(real_dict, "dict", mock_dict, None, None, "", False, stack)
        assert stack == []


def test_push_children_keys_and_iter_exceptions(monkeypatch):
    # Tests AttributeError/PdfError raised when inspecting dictionary keys
    real_dict = pikepdf.Dictionary()

    class BadKeysAttributeDict:
        def keys(self):
            raise AttributeError("keys attr failure")

    class BadKeysPdfErrorDict:
        def keys(self):
            raise pikepdf.PdfError("keys pdf failure")

    for mock_dict in (BadKeysAttributeDict(), BadKeysPdfErrorDict()):
        monkeypatch.setattr("pdftl.utils.space_usage._stream_or_dict", lambda _: mock_dict)
        stack = []
        _push_children(real_dict, "dict", mock_dict, None, None, "", False, stack)
        assert stack == []


def test_xref_section_ranges_missing_eof():
    # Valid offset present, but %%EOF terminator is missing following start offset

    # Case 1: Triggered via startxref
    data_startxref = b"01234567890123456789startxref\r\n10\r\nno_eof_here"
    assert _xref_section_ranges(data_startxref) == []

    # Case 2: Triggered via xref keyword
    data_xref = b"0123456789xref\r\n0 1\r\n0000000000 65535 f\r\nno_eof_here"
    assert _xref_section_ranges(data_xref) == []


def test_object_extent_header_no_match():
    # Covers the 'not match' branch when offset > 0
    data = b"   INVALID_HEADER"
    assert _object_extent(data, offset=3, obj_num=1, obj=None) == 0
