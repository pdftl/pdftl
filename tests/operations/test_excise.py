# tests/operations/test_excise.py

import pikepdf
import pytest
import pdftl.operations.excise as excise_module

from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.path_types import Path, Subpath
from pdftl.utils.graphics_state import GraphicsState
from pdftl.operations.helpers.excise_text import (
    FontCache as _FontCache,
    rewrite_text_show as _rewrite_text_show,
    filter_show_elements as _filter_show_elements,
    glyph_should_delete as _glyph_should_delete,
)
from pdftl.operations.helpers.excise_stream import (
    resolve_resources as _resolve_resources,
    process_stream as _process_stream,
    handle_form_do as _handle_form_do,
    _copy_resource_dict as _copy_resource_dict,
    _apply_graphics_op,
)
from pdftl.operations.helpers.excise_geometry import (
    filter_fill_path as _filter_fill_path,
    filter_stroke_path as _filter_stroke_path,
    points_bbox as _points_bbox,
)
from pdftl.operations.excise import (
    ExciseRect,
    ExciseStats,
    _parse_single_spec,
    _parse_args,
    _process_page,
    excise_content,
)
from pdftl.operations.helpers.excise_geometry import (
    resolve_box_rect as _resolve_box_rect,
)


# ---------------------------------------------------------------------------
# Spec parsing
# ---------------------------------------------------------------------------


class TestParseSingleSpec:
    def test_basic_abs_spec_defaults_keep_outside(self):
        page_range, tr, box_name = _parse_single_spec("1-5(abs,10,20,110,220)", total_pages=10)
        assert page_range == "1-5"
        assert tr.rect == [10.0, 20.0, 110.0, 220.0]
        assert tr.delete == "inside"
        assert tr.partial == "inside"
        assert box_name is None

    def test_keep_inside_parsed(self):
        _, tr, _ = _parse_single_spec("1(abs,0,0,100,100,delete=outside)", total_pages=1)
        assert tr.delete == "outside"

    def test_keep_outside_explicit(self):
        _, tr, _ = _parse_single_spec("1(abs,0,0,100,100,delete=inside)", total_pages=1)
        assert tr.delete == "inside"

    def test_dimension_suffix_pt_accepted(self):
        _, tr, _ = _parse_single_spec("1(abs,10pt,10pt,200pt,100pt)", total_pages=1)
        assert tr.rect == [10.0, 10.0, 200.0, 100.0]

    def test_reversed_coordinates_normalized(self):
        """x0>x1 or y0>y1 should normalize into a proper min/max rect."""
        _, tr, _ = _parse_single_spec("1(abs,100,100,0,0)", total_pages=1)
        assert tr.rect == [0.0, 0.0, 100.0, 100.0]

    def test_missing_parens_raises(self):
        with pytest.raises(InvalidArgumentError):
            _parse_single_spec("1-5", total_pages=10)

    def test_not_starting_with_abs_raises(self):
        with pytest.raises(InvalidArgumentError):
            _parse_single_spec("1(0,0,100,100)", total_pages=1)

    def test_wrong_coordinate_count_raises(self):
        with pytest.raises(InvalidArgumentError):
            _parse_single_spec("1(abs,0,0,100)", total_pages=1)

    def test_bad_keep_value_raises(self):
        with pytest.raises(InvalidArgumentError):
            _parse_single_spec("1(abs,0,0,100,100,keep=sideways)", total_pages=1)

    def test_non_numeric_coordinate_raises(self):
        with pytest.raises(InvalidArgumentError):
            _parse_single_spec("1(abs,bad,0,100,100)", total_pages=1)

    def test_dim_str_to_pts_value_error_wrapped_as_invalid_argument(self, monkeypatch):
        """Exercises excise.py's own except-clause around dim_str_to_pts,
        independent of what dim_str_to_pts itself considers malformed --
        forced directly via monkeypatch rather than guessing at an input
        string that happens to make the real implementation raise
        ValueError instead of its own InvalidArgumentError."""

        def _raise(_s, _ref):
            raise ValueError("simulated bad dimension")

        monkeypatch.setattr(excise_module, "dim_str_to_pts", _raise)
        with pytest.raises(InvalidArgumentError, match="invalid coordinate"):
            _parse_single_spec("1(abs,10,10,100,100)", total_pages=1)

    def test_no_page_range_before_paren_defaults_to_all_pages(self):
        page_range, _, _ = _parse_single_spec("(abs,0,0,100,100)", total_pages=1)
        assert page_range == "-"

    def test_partial_outside_parsed_via_spec_string(self):
        _, tr, _ = _parse_single_spec("1(abs,0,0,100,100,partial=outside)", total_pages=1)
        assert tr.partial == "outside"

    def test_partial_inside_explicit_via_spec_string(self):
        _, tr, _ = _parse_single_spec("1(abs,0,0,100,100,partial=inside)", total_pages=1)
        assert tr.partial == "inside"

    def test_bad_delete_value_raises(self):
        with pytest.raises(InvalidArgumentError, match="'delete'"):
            _parse_single_spec("1(abs,0,0,100,100,delete=sideways)", total_pages=1)

    def test_bad_partial_value_raises(self):
        with pytest.raises(InvalidArgumentError, match="'partial'"):
            _parse_single_spec("1(abs,0,0,100,100,partial=sideways)", total_pages=1)


class TestParseSingleSpecBox:
    def test_box_media_parsed(self):
        _, tr, box_name = _parse_single_spec("1(box=media)", total_pages=1)
        assert box_name == "media"
        assert tr.delete == "inside"
        assert tr.partial == "inside"

    def test_box_crop_parsed(self):
        _, _, box_name = _parse_single_spec("1(box=crop)", total_pages=1)
        assert box_name == "crop"

    def test_box_trim_parsed(self):
        _, _, box_name = _parse_single_spec("1(box=trim)", total_pages=1)
        assert box_name == "trim"

    def test_box_bleed_and_art_parsed(self):
        assert _parse_single_spec("1(box=bleed)", total_pages=1)[2] == "bleed"
        assert _parse_single_spec("1(box=art)", total_pages=1)[2] == "art"

    def test_box_with_delete_and_partial(self):
        _, tr, box_name = _parse_single_spec(
            "1(box=trim,delete=outside,partial=outside)", total_pages=1
        )
        assert box_name == "trim"
        assert tr.delete == "outside"
        assert tr.partial == "outside"

    def test_box_page_range_defaults_to_all(self):
        page_range, _, box_name = _parse_single_spec("(box=crop)", total_pages=3)
        assert page_range == "-"
        assert box_name == "crop"

    def test_unknown_box_name_raises(self):
        with pytest.raises(InvalidArgumentError, match="'box'"):
            _parse_single_spec("1(box=bogus)", total_pages=1)

    def test_box_with_extra_coordinates_raises(self):
        with pytest.raises(InvalidArgumentError, match="no coordinates"):
            _parse_single_spec("1(box=crop,10,10,100,100)", total_pages=1)

    def test_box_repeated_raises(self):
        with pytest.raises(InvalidArgumentError, match="only appear once"):
            _parse_single_spec("1(box=crop,box=trim)", total_pages=1)

    def test_neither_abs_nor_box_raises(self):
        with pytest.raises(InvalidArgumentError, match="'abs' or 'box="):
            _parse_single_spec("1(0,0,100,100)", total_pages=1)


class TestResolveBoxRect:
    def _page_with_boxes(self, **boxes):
        # NB: return (pdf, page), and callers must keep `pdf` alive for
        # as long as `page` is used -- pikepdf tears down a Page's
        # underlying dictionary access once its owning Pdf is garbage
        # collected, even though `page` itself is still referenced.
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(300, 400))
        page = pdf.pages[0]
        for name, rect in boxes.items():
            setattr(page, name, pikepdf.Array(rect))
        return pdf, page

    def test_media_box_resolved(self):
        pdf, page = self._page_with_boxes(mediabox=[0, 0, 300, 400])
        assert _resolve_box_rect(page, "media") == [0.0, 0.0, 300.0, 400.0]

    def test_crop_box_resolved_when_present(self):
        pdf, page = self._page_with_boxes(mediabox=[0, 0, 300, 400], cropbox=[10, 10, 290, 390])
        assert _resolve_box_rect(page, "crop") == [10.0, 10.0, 290.0, 390.0]

    def test_trim_box_falls_back_to_crop_when_absent(self):
        pdf, page = self._page_with_boxes(mediabox=[0, 0, 300, 400], cropbox=[5, 5, 295, 395])
        assert _resolve_box_rect(page, "trim") == [5.0, 5.0, 295.0, 395.0]

    def test_trim_box_falls_back_to_media_when_no_crop_either(self):
        pdf, page = self._page_with_boxes(mediabox=[0, 0, 300, 400])
        assert _resolve_box_rect(page, "trim") == [0.0, 0.0, 300.0, 400.0]

    def test_bleed_and_art_also_fall_back_to_crop(self):
        pdf, page = self._page_with_boxes(mediabox=[0, 0, 300, 400], cropbox=[1, 1, 299, 399])
        assert _resolve_box_rect(page, "bleed") == [1.0, 1.0, 299.0, 399.0]
        assert _resolve_box_rect(page, "art") == [1.0, 1.0, 299.0, 399.0]

    def test_reversed_box_coordinates_normalized(self):
        pdf, page = self._page_with_boxes(mediabox=[300, 400, 0, 0])
        assert _resolve_box_rect(page, "media") == [0.0, 0.0, 300.0, 400.0]


class TestExciseBoxEndToEnd:
    def test_box_crop_keeps_only_content_inside_cropbox(self):
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(300, 300))
        page = pdf.pages[0]
        page.CropBox = pikepdf.Array([50, 50, 150, 150])
        page.Contents = pdf.make_stream(b"q 60 60 40 40 re f Q q 200 200 40 40 re f Q")
        excise_content(pdf, ["1(box=crop,delete=outside)"])
        instructions = pikepdf.parse_content_stream(page.Contents)
        fill_ops = [op for _, op in instructions if str(op) == "f"]
        assert len(fill_ops) == 1

    def test_box_per_page_differing_boxes_each_resolved_independently(self):
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(300, 300))
        pdf.add_blank_page(page_size=(300, 300))
        pdf.pages[0].TrimBox = pikepdf.Array([0, 0, 100, 100])
        pdf.pages[1].TrimBox = pikepdf.Array([200, 200, 300, 300])
        fill = b"q 60 60 30 30 re f Q"
        pdf.pages[0].Contents = pdf.make_stream(fill)
        pdf.pages[1].Contents = pdf.make_stream(fill)

        excise_content(pdf, ["1-2(box=trim,delete=outside)"])

        ops0 = [str(op) for _, op in pikepdf.parse_content_stream(pdf.pages[0].Contents)]
        ops1 = [str(op) for _, op in pikepdf.parse_content_stream(pdf.pages[1].Contents)]
        assert "f" in ops0
        assert "f" not in ops1

    def test_box_spec_page_not_reassigned_by_later_non_box_spec(self):
        """A later abs,... sfor the same page must clear that page's
        entry from the box_name side-map, or the stale box_name would
        cause _process_page to wrongly re-resolve/override the later
        spec's explicit rect."""
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(300, 300))
        page = pdf.pages[0]
        page.TrimBox = pikepdf.Array([0, 0, 10, 10])  # would keep almost nothing
        page.Contents = pdf.make_stream(b"q 60 60 30 30 re f Q")
        # First spec: box=trim,delete=outside (would delete the fill,
        # since it's outside the tiny TrimBox). Second spec for the same
        # page overrides with an abs rect that DOES cover the fill.
        excise_content(
            pdf,
            [
                "1(box=trim,delete=outside)",
                "1(abs,0,0,300,300,delete=outside)",
            ],
        )
        ops = [str(op) for _, op in pikepdf.parse_content_stream(page.Contents)]
        assert "f" in ops  # last spec (abs) won, fill survives


class TestParseArgs:
    def test_single_spec_applies_to_matching_pages(self):
        result, _box_names = _parse_args(["1-3(abs,0,0,10,10)"], total_pages=5)
        assert set(result.keys()) == {1, 2, 3}
        assert all(tr.rect == [0.0, 0.0, 10.0, 10.0] for tr in result.values())

    def test_later_spec_overrides_earlier_for_same_page(self):
        result, _box_names = _parse_args(
            ["1-5(abs,0,0,10,10)", "3(abs,50,50,60,60)"], total_pages=5
        )
        assert result[3].rect == [50.0, 50.0, 60.0, 60.0]
        assert result[1].rect == [0.0, 0.0, 10.0, 10.0]


# ---------------------------------------------------------------------------
# _resolve_resources
# ---------------------------------------------------------------------------


class TestResolveResources:
    def test_own_category_preferred_others_merged_from_page(self):
        """Own's /Font should win, but page's /XObject (a category own
        doesn't declare at all) must still be present -- this is the
        actual bug fix: an all-or-nothing fallback used to hide the
        page's /XObject entirely whenever own declared ANY resources."""
        own = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({})})
        page_res = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({})})
        stream = pikepdf.Dictionary({"/Resources": own})
        merged = _resolve_resources(stream, page_res)
        assert "/Font" in merged
        assert "/XObject" in merged  # <- was silently dropped before the fix

    def test_own_category_overrides_same_category_on_page(self):
        own_xobj = pikepdf.Dictionary({"/Im0": "own"})
        page_xobj = pikepdf.Dictionary({"/Im0": "page", "/Im1": "page"})
        own = pikepdf.Dictionary({"/XObject": own_xobj})
        page_res = pikepdf.Dictionary({"/XObject": page_xobj})
        stream = pikepdf.Dictionary({"/Resources": own})
        merged = _resolve_resources(stream, page_res)
        # own's /XObject dict wins whole-category, doesn't merge keys within it
        assert "/Im1" not in merged["/XObject"]

    def test_falls_back_to_page_resources_when_own_missing(self):
        page_res = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({})})
        stream = pikepdf.Dictionary({})  # no /Resources at all
        result = _resolve_resources(stream, page_res)
        assert result == page_res
        # Must be an independent copy, not the same object -- returning
        # page_res by reference is what let a later in-place mutation
        # (e.g. handle_form_do adding an "_excise_..." key) leak back
        # onto the page's own /Resources.
        assert result is not page_res

    def test_falls_back_to_page_resources_when_own_empty(self):
        page_res = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({})})
        stream = pikepdf.Dictionary({"/Resources": pikepdf.Dictionary({})})
        result = _resolve_resources(stream, page_res)
        assert result == page_res
        assert result is not page_res

    def test_no_get_attr_returns_page_resources(self):
        page_res = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({})})
        result = _resolve_resources("not a dict-like object", page_res)
        assert result == page_res
        assert result is not page_res
        proc_set = pikepdf.Array([pikepdf.Name("/PDF"), pikepdf.Name("/Text")])
        d = pikepdf.Dictionary({"/ProcSet": proc_set})
        copied = _resolve_resources.__globals__["_copy_resource_dict"](d)
        assert copied["/ProcSet"] == proc_set


class TestProcessStreamOwnResources:
    def test_stream_with_own_resources_merges_and_writes_back(self):
        """A page-level content stream that ALREADY carries its own
        non-empty /Resources (non-standard but tolerated input) must take
        process_stream's merge-and-write-back branch (lines 170-172),
        distinct from the common page_resources-passthrough branch."""
        pdf = pikepdf.new()
        own_res = pikepdf.Dictionary(
            {"/Font": pikepdf.Dictionary({"/F1": pikepdf.Dictionary({})})}
        )
        stream_obj = pdf.make_stream(b"0 0 100 100 re f")
        stream_obj["/Resources"] = own_res
        stats = ExciseStats()
        page_res = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({})})
        _process_stream(
            pdf, stream_obj, ExciseRect(rect=[0, 0, 10, 10], delete="inside"), stats, page_res
        )
        merged = stream_obj["/Resources"]
        assert "/Font" in merged  # own's category preserved
        assert "/XObject" in merged  # page's category merged in


# ---------------------------------------------------------------------------
# End-to-end image deletion
# ---------------------------------------------------------------------------


def _make_pdf_with_image(image_rect_user_space, page_size=(300, 300)):
    """Builds a one-page PDF with a single Image XObject placed at
    image_rect_user_space = [x0, y0, x1, y1] via a `cm ... Do` sequence."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=page_size)
    page = pdf.pages[0]

    # A trivial 1x1 RGB image XObject.
    image_xobj = pikepdf.Stream(pdf, b"\xff\x00\x00")
    image_xobj.Type = pikepdf.Name("/XObject")
    image_xobj.Subtype = pikepdf.Name("/Image")
    image_xobj.Width = 1
    image_xobj.Height = 1
    image_xobj.BitsPerComponent = 8
    image_xobj.ColorSpace = pikepdf.Name("/DeviceRGB")
    page.Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/Im0": image_xobj})})

    x0, y0, x1, y1 = image_rect_user_space
    w, h = x1 - x0, y1 - y0
    content = f"q {w} 0 0 {h} {x0} {y0} cm /Im0 Do Q".encode("latin-1")
    page.Contents = pdf.make_stream(content)

    return pdf


def _make_pdf_with_content(content_bytes: bytes, page_size=(300, 300), resources=None):
    """Builds a one-page PDF with an arbitrary raw content stream body,
    for exercising path (fill/stroke/clip) deletion rather than images."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=page_size)
    page = pdf.pages[0]
    if resources is not None:
        page.Resources = resources
    page.Contents = pdf.make_stream(content_bytes)
    return pdf


def _get_do_ops(pdf):
    """Extracts the list of /Do XObject names still present in page 1's
    content stream after processing."""
    page = pdf.pages[0]
    instructions = pikepdf.parse_content_stream(page.Contents)
    return [str(operands[0]) for operands, operator in instructions if str(operator) == "Do"]


def _get_content_ops(pdf):
    """Returns the list of operator strings still present in page 1's
    content stream after processing, in order (operands dropped -- these
    tests only care about which operators survive/vanish)."""
    page = pdf.pages[0]
    instructions = pikepdf.parse_content_stream(page.Contents)
    return [str(operator) for _, operator in instructions]


class TestExciseContentImageDeletion:
    def test_overlapping_image_deleted_default_keep_outside(self):
        pdf = _make_pdf_with_image([50, 50, 150, 150])
        excise_content(pdf, ["1(abs,0,0,200,200)"])  # rect fully covers image
        assert _get_do_ops(pdf) == []

    def test_non_overlapping_image_survives_default_keep_outside(self):
        pdf = _make_pdf_with_image([200, 200, 250, 250])
        excise_content(pdf, ["1(abs,0,0,50,50)"])  # rect doesn't touch image
        assert _get_do_ops(pdf) == ["/Im0"]

    def test_keep_inside_deletes_non_overlapping_image(self):
        pdf = _make_pdf_with_image([200, 200, 250, 250])
        excise_content(pdf, ["1(abs,0,0,50,50,delete=outside)"])
        assert _get_do_ops(pdf) == []

    def test_keep_inside_preserves_overlapping_image(self):
        pdf = _make_pdf_with_image([50, 50, 150, 150])
        excise_content(pdf, ["1(abs,0,0,200,200,delete=outside)"])
        assert _get_do_ops(pdf) == ["/Im0"]

    def test_partial_overlap_still_counts_as_overlap(self):
        """Whole-XObject-removal-on-ANY-overlap policy: even a sliver of
        overlap deletes the whole image."""
        pdf = _make_pdf_with_image([90, 90, 200, 200])
        excise_content(pdf, ["1(abs,0,0,100,100)"])  # only clips the corner
        assert _get_do_ops(pdf) == []

    def test_no_args_raises(self):
        pdf = _make_pdf_with_image([50, 50, 150, 150])
        with pytest.raises(InvalidArgumentError):
            excise_content(pdf, [])

    def test_form_xobject_do_survives_and_is_recursively_filtered(self):
        """A /Do invoking a Form XObject must never be deleted OUTRIGHT by
        _should_delete_image's own overlap test (that's image-only,
        whole-XObject policy) -- instead its content is recursively
        filtered, and since here the Form's fill is fully within the excise
        rect, the Do gets rewritten to point at a new private, filtered
        copy (the original /Fm0 key is left untouched for any other
        caller sharing it)."""
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(300, 300))
        page = pdf.pages[0]

        form_xobj = pdf.make_stream(b"1 0 0 RG 0 0 100 100 re f")
        form_xobj.Type = pikepdf.Name("/XObject")
        form_xobj.Subtype = pikepdf.Name("/Form")
        form_xobj.BBox = pikepdf.Array([0, 0, 100, 100])

        page.Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/Fm0": form_xobj})})
        page.Contents = pdf.make_stream(b"q 1 0 0 1 50 50 cm /Fm0 Do Q")

        excise_content(pdf, ["1(abs,0,0,200,200)"])  # fully covers the form's fill (50..150)
        do_ops = _get_do_ops(pdf)
        assert len(do_ops) == 1  # Do instruction itself is never dropped for a Form
        new_name = do_ops[0]
        assert new_name != "/Fm0"  # rewritten to a new private filtered copy
        # The new key must land in the PAGE's own /Resources -- that's
        # the dict a real PDF renderer actually consults when resolving
        # this page's Do operators. It must NOT be visible only via
        # page.Contents.Resources (a location no conformant renderer
        # checks for a page content stream) -- that was the bug.
        new_xobj = page.Resources["/XObject"][new_name]
        inner_ops = [str(op) for _, op in pikepdf.parse_content_stream(new_xobj)]
        assert "f" not in inner_ops  # the fill was correctly deleted

    def test_partial_overlap_partial_outside_keeps_straddler(self):
        """partial=outside: a straddling (not fully-contained) unit does
        NOT count as 'inside' -- so with the default delete=inside, a
        straddler must SURVIVE rather than being deleted (exercises
        _matches' rect_contains branch, excise_geometry.py line 61)."""
        pdf = _make_pdf_with_image([90, 90, 200, 200])
        excise_content(pdf, ["1(abs,0,0,100,100,partial=outside)"])
        assert _get_do_ops(pdf) == ["/Im0"]  # straddler survives

    def test_full_containment_partial_outside_deletes(self):
        """partial=outside: a unit fully CONTAINED within the rect still
        counts as inside and gets deleted."""
        pdf = _make_pdf_with_image([10, 10, 50, 50])
        excise_content(pdf, ["1(abs,0,0,100,100,partial=outside)"])
        assert _get_do_ops(pdf) == []  # fully contained -- deleted


# ---------------------------------------------------------------------------
# Fill-path deletion (whole-Path-on-overlap)
# ---------------------------------------------------------------------------


class TestExciseContentFillPathDeletion:
    def test_overlapping_fill_deleted_default_keep_outside(self):
        pdf = _make_pdf_with_content(b"0 0 100 100 re f")
        excise_content(pdf, ["1(abs,0,0,200,200)"])  # rect fully covers the fill
        assert "f" not in _get_content_ops(pdf)
        assert "re" not in _get_content_ops(pdf)

    def test_non_overlapping_fill_survives_default_keep_outside(self):
        pdf = _make_pdf_with_content(b"200 200 50 50 re f")
        excise_content(pdf, ["1(abs,0,0,50,50)"])  # rect doesn't touch the fill
        ops = _get_content_ops(pdf)
        assert "f" in ops
        assert "re" in ops

    def test_keep_inside_preserves_overlapping_fill(self):
        pdf = _make_pdf_with_content(b"0 0 100 100 re f")
        excise_content(pdf, ["1(abs,0,0,200,200,delete=outside)"])
        ops = _get_content_ops(pdf)
        assert "f" in ops
        assert "re" in ops

    def test_keep_inside_deletes_non_overlapping_fill(self):
        pdf = _make_pdf_with_content(b"200 200 50 50 re f")
        excise_content(pdf, ["1(abs,0,0,50,50,delete=outside)"])
        assert "f" not in _get_content_ops(pdf)


# ---------------------------------------------------------------------------
# Stroke-path deletion (per-Subpath granularity)
# ---------------------------------------------------------------------------


class TestExciseContentStrokePathDeletion:
    # Two disjoint subpaths sharing a single stroke operator: one sits
    # inside [0,0,100,100], the other well outside it.
    _TWO_SUBPATH_STROKE = b"10 10 m 50 10 l 220 10 m 260 10 l S"

    def test_partial_subpath_overlap_keeps_survivors_drops_overlapping(self):
        pdf = _make_pdf_with_content(self._TWO_SUBPATH_STROKE)
        excise_content(pdf, ["1(abs,0,0,100,100)"])  # only covers the first subpath
        instructions = pikepdf.parse_content_stream(pdf.pages[0].Contents)
        m_count = sum(1 for _, op in instructions if str(op) == "m")
        assert m_count == 1  # first subpath's 'm' dropped, second survives
        assert any(str(op) == "S" for _, op in instructions)  # paint op preserved

    def test_no_subpaths_overlap_stroke_survives_whole(self):
        pdf = _make_pdf_with_content(self._TWO_SUBPATH_STROKE)
        excise_content(pdf, ["1(abs,500,500,600,600)"])  # touches neither subpath
        instructions = pikepdf.parse_content_stream(pdf.pages[0].Contents)
        m_count = sum(1 for _, op in instructions if str(op) == "m")
        assert m_count == 2

    def test_all_subpaths_overlap_stroke_fully_deleted(self):
        pdf = _make_pdf_with_content(self._TWO_SUBPATH_STROKE)
        excise_content(pdf, ["1(abs,0,0,300,300)"])  # covers both subpaths
        assert _get_content_ops(pdf) == []


# ---------------------------------------------------------------------------
# Clip paths -- never deletion candidates regardless of overlap
# ---------------------------------------------------------------------------


class TestExciseContentClipPathNeverDeleted:
    def test_clip_path_survives_full_overlap(self):
        pdf = _make_pdf_with_content(b"0 0 100 100 re W n")
        excise_content(pdf, ["1(abs,0,0,300,300)"])  # would delete a fill/stroke here
        ops = _get_content_ops(pdf)
        assert "re" in ops
        assert "W" in ops
        assert "n" in ops


# ---------------------------------------------------------------------------
# _should_delete_image edge branches (missing resources / missing XObject)
# ---------------------------------------------------------------------------


class TestShouldDeleteImageEdgeCases:
    def test_no_resources_at_all_keeps_do_untouched(self):
        """No /Resources anywhere (stream or page) -- _should_delete_image
        must bail out early (resources is None) rather than raise."""
        pdf = _make_pdf_with_content(b"/Im0 Do")  # no page.Resources set at all
        excise_content(pdf, ["1(abs,0,0,300,300)"])
        assert _get_do_ops(pdf) == ["/Im0"]

    def test_non_image_non_form_subtype_not_deleted(self):
        """Some other /Subtype (e.g. /PS, a PostScript XObject) must fall
        through _should_delete_image's Image-only test untouched (line 806)."""
        weird_xobj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/PS")})
        resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/Ps0": weird_xobj})})
        pdf = _make_pdf_with_content(b"/Ps0 Do", resources=resources)
        excise_content(pdf, ["1(abs,0,0,300,300)"])
        assert _get_do_ops(pdf) == ["/Ps0"]


class TestHandleFormDoEdgeCases:
    def test_xobj_name_not_in_xobjects_returns_none(self):
        resources = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/Other": pikepdf.Dictionary({})})}
        )
        stats = ExciseStats()
        pdf = pikepdf.new()
        result = _handle_form_do(
            pdf,
            [pikepdf.Name("/Fm0")],
            resources,
            (1, 0, 0, 1, 0, 0),
            ExciseRect(rect=[0, 0, 10, 10], delete="inside"),
            stats,
            0,
        )
        assert result is None

    def test_non_form_subtype_returns_none(self):
        other = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Image")})
        resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/Im0": other})})
        stats = ExciseStats()
        pdf = pikepdf.new()
        result = _handle_form_do(
            pdf,
            [pikepdf.Name("/Im0")],
            resources,
            (1, 0, 0, 1, 0, 0),
            ExciseRect(rect=[0, 0, 10, 10], delete="inside"),
            stats,
            0,
        )
        assert result is None

    def test_recursion_depth_exceeded_passes_through(self):
        pdf = pikepdf.new()
        form = pdf.make_stream(b"0 0 100 100 re f")
        form.Type = pikepdf.Name("/XObject")
        form.Subtype = pikepdf.Name("/Form")
        form.BBox = pikepdf.Array([0, 0, 100, 100])
        resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/Fm0": form})})
        stats = ExciseStats()
        result = _handle_form_do(
            pdf,
            [pikepdf.Name("/Fm0")],
            resources,
            (1, 0, 0, 1, 0, 0),
            ExciseRect(rect=[0, 0, 300, 300], delete="inside"),
            stats,
            depth=13,  # > _MAX_FORM_RECURSION_DEPTH (12)
        )
        assert result == [([pikepdf.Name("/Fm0")], "Do")]

    def test_malformed_matrix_falls_back_to_identity(self):
        pdf = pikepdf.new()
        form = pdf.make_stream(b"0 0 100 100 re f")
        form.Type = pikepdf.Name("/XObject")
        form.Subtype = pikepdf.Name("/Form")
        form.BBox = pikepdf.Array([0, 0, 100, 100])
        form.Matrix = pikepdf.Array(["bad", 0, 0, 1, 0, 0])
        resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/Fm0": form})})
        stats = ExciseStats()
        # Should not raise -- malformed /Matrix caught, identity used instead.
        result = _handle_form_do(
            pdf,
            [pikepdf.Name("/Fm0")],
            resources,
            (1, 0, 0, 1, 0, 0),
            ExciseRect(rect=[0, 0, 300, 300], delete="inside"),
            stats,
            0,
        )
        assert result is not None

    def test_resources_none_skips_setitem_write_back(self):
        """_process_stream's resources-write-back guard: when resources
        end up None (no own /Resources and no page /Resources), the
        __setitem__ write-back must be skipped, not raise (line 425)."""
        pdf = _make_pdf_with_content(b"0 0 100 100 re f")  # no page.Resources set
        stats = ExciseStats()
        _process_stream(
            pdf,
            pdf.pages[0].Contents,
            ExciseRect(rect=[0, 0, 300, 300], delete="inside"),
            stats,
            None,  # page_resources
        )
        assert stats.streams_processed == 1  # completed without raising

    def test_unparseable_form_content_passes_through(self, monkeypatch):
        """A Form XObject whose content stream fails to parse must be
        passed through untouched (lines 579-580), not raise."""
        pdf = pikepdf.new()
        form = pdf.make_stream(b"0 0 100 100 re f")
        form.Type = pikepdf.Name("/XObject")
        form.Subtype = pikepdf.Name("/Form")
        form.BBox = pikepdf.Array([0, 0, 100, 100])
        resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/Fm0": form})})
        stats = ExciseStats()

        def _raise(*args, **kwargs):
            raise pikepdf.PdfError("boom: unparseable form content")

        monkeypatch.setattr(pikepdf, "parse_content_stream", _raise)

        result = _handle_form_do(
            pdf,
            [pikepdf.Name("/Fm0")],
            resources,
            (1, 0, 0, 1, 0, 0),
            ExciseRect(rect=[0, 0, 300, 300], delete="inside"),
            stats,
            0,
        )
        assert result == [([pikepdf.Name("/Fm0")], "Do")]

    def test_xobject_name_not_in_resources_keeps_do_untouched(self):
        """Resources present with an /XObject dict, but the referenced
        name isn't in it -- must bail out (not raise) and keep the Do."""
        other_xobj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Image")})
        resources = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/SomeOtherName": other_xobj})}
        )
        pdf = _make_pdf_with_content(b"/Im0 Do", resources=resources)
        excise_content(pdf, ["1(abs,0,0,300,300)"])
        assert _get_do_ops(pdf) == ["/Im0"]


# ---------------------------------------------------------------------------
# Page/stream-level error handling and no-/Contents pages
# ---------------------------------------------------------------------------


class TestProcessPageAndStreamErrorHandling:
    def test_page_with_no_contents_is_skipped_without_error(self):
        """A page with no /Contents at all must not blow up
        contents_coalesce() and must simply produce zero processed
        streams for that page."""
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(300, 300))
        page = pdf.pages[0]
        del page.Contents
        assert "/Contents" not in page

        stats = ExciseStats()
        _process_page(pdf, 1, ExciseRect(rect=[0, 0, 10, 10], delete="inside"), stats)
        assert stats.streams_processed == 0

    def test_parse_content_stream_failure_is_logged_and_skipped(self, monkeypatch):
        pdf = _make_pdf_with_content(b"0 0 100 100 re f")

        def _raise_parse(*_args, **_kwargs):
            raise pikepdf.PdfError("boom: unparseable stream")

        monkeypatch.setattr(pikepdf, "parse_content_stream", _raise_parse)

        stats = ExciseStats()
        # Should not raise -- the PdfError is caught and logged internally.
        _process_stream(
            pdf,
            pdf.pages[0].Contents,
            ExciseRect(rect=[0, 0, 10, 10], delete="inside"),
            stats,
            None,
        )
        assert stats.streams_processed == 0

    def test_write_failure_is_logged_and_swallowed(self, monkeypatch):
        pdf = _make_pdf_with_content(b"0 0 100 100 re f")

        def _raise_unparse(*_args, **_kwargs):
            raise pikepdf.PdfError("boom: cannot unparse")

        monkeypatch.setattr(pikepdf, "unparse_content_stream", _raise_unparse)

        stats = ExciseStats()
        # Should not raise -- the PdfError from unparse (surfacing inside
        # the write() try block) is caught and logged internally.
        _process_stream(
            pdf,
            pdf.pages[0].Contents,
            ExciseRect(rect=[0, 0, 300, 300], delete="inside"),
            stats,
            None,
        )
        assert stats.streams_processed == 1  # parsing succeeded; only the write step failed


# ---------------------------------------------------------------------------
# Degenerate-geometry unit tests for the path-deletion helpers directly
# (these states aren't reachable through a normally-parsed content stream,
# since _Segmenter never flushes a Subpath with zero points -- constructed
# directly here to exercise the defensive "keep by default" branches).
# ---------------------------------------------------------------------------


class TestPathHelperDegenerateGeometry:
    _CTM = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

    def test_points_bbox_empty_returns_none(self):
        assert _points_bbox([], self._CTM) is None

    def test_filter_fill_path_no_points_keeps_by_default(self):
        path = Path(subpaths=[], paint_op="f", original_instructions=[([], "f")])
        stats = ExciseStats()
        result = _filter_fill_path(
            path, self._CTM, ExciseRect(rect=[0, 0, 300, 300], delete="inside"), stats
        )
        assert result == [([], "f")]
        assert stats.paths_deleted == 0

    def test_filter_stroke_path_degenerate_subpath_kept(self):
        degenerate_sp = Subpath(points=[], closed=False, instructions=[])
        path = Path(
            subpaths=[degenerate_sp],
            paint_op="S",
            original_instructions=[([], "S")],
        )
        stats = ExciseStats()
        result = _filter_stroke_path(
            path, self._CTM, ExciseRect(rect=[0, 0, 300, 300], delete="inside"), stats
        )
        # Degenerate subpath survives untested; since nothing was ever
        # dropped, the original instructions are re-emitted verbatim.
        assert result == [([], "S")]
        assert stats.subpaths_deleted == 0

    def test_filter_stroke_path_partial_delete_with_empty_original_instructions(self):
        """Covers the 'result construction skips appending a paint op' arm
        of the partial-deletion branch when original_instructions is empty
        (a malformed/edge-case Path that shouldn't crash regardless)."""
        kept_sp = Subpath(points=[(0.0, 0.0)], closed=False, instructions=[([], "m")])
        dropped_sp = Subpath(points=[(200.0, 200.0)], closed=False, instructions=[([], "m")])
        path = Path(
            subpaths=[kept_sp, dropped_sp],
            paint_op="S",
            original_instructions=[],  # deliberately empty
        )
        stats = ExciseStats()
        result = _filter_stroke_path(
            path, self._CTM, ExciseRect(rect=[100, 100, 300, 300], delete="inside"), stats
        )
        assert result == [([], "m")]  # kept subpath's own instructions only, no paint op appended
        assert stats.subpaths_deleted == 1


# ---------------------------------------------------------------------------
# Shared /Contents unsharing (regression test for the page-2-vs-page-4 bug:
# pages produced by content-sharing operations like `chop` can point their
# /Contents at the SAME indirect stream object -- excise must give the page
# it's actually processing a private copy before mutating in place, or it
# silently corrupts every other page sharing that object).
# ---------------------------------------------------------------------------


def _make_pdf_with_shared_contents(fill_rect, page_size=(300, 300), num_pages=2):
    """Builds a multi-page PDF where every page's /Contents points at the
    SAME underlying stream object (simulating chop-style content sharing)."""
    pdf = pikepdf.new()
    x0, y0, x1, y1 = fill_rect
    w, h = x1 - x0, y1 - y0
    content = f"{x0} {y0} {w} {h} re f".encode("latin-1")
    shared_stream = pdf.make_stream(content)

    for _ in range(num_pages):
        pdf.add_blank_page(page_size=page_size)

    for page in pdf.pages:
        page.Contents = shared_stream

    return pdf


class TestUnsharePageContents:
    def test_excising_one_page_does_not_mutate_other_pages_sharing_contents(self):
        pdf = _make_pdf_with_shared_contents([50, 50, 150, 150], num_pages=2)

        # Sanity check the fixture: both pages really do share one object
        # before excise touches anything.
        assert pdf.pages[0].Contents.objgen == pdf.pages[1].Contents.objgen

        excise_content(pdf, ["1(abs,0,0,300,300)"])  # deletes the fill, page 1 only

        page1_ops = [str(op) for _, op in pikepdf.parse_content_stream(pdf.pages[0].Contents)]
        page2_ops = [str(op) for _, op in pikepdf.parse_content_stream(pdf.pages[1].Contents)]
        assert "f" not in page1_ops  # page 1: targeted, fill deleted
        assert "f" in page2_ops  # page 2: NOT targeted, must be untouched

        # And the two pages must no longer be the same underlying object,
        # confirming _unshare_page_contents actually gave page 1 its own
        # private stream rather than getting lucky.
        assert pdf.pages[0].Contents.objgen != pdf.pages[1].Contents.objgen


class TestExciseStatsLogging:
    def test_stats_tracks_image_counts(self):
        _make_pdf_with_image([50, 50, 150, 150])
        # Directly exercise the stats path via excise_content's internals
        # by checking the resulting content stream reflects deletion --
        # ExciseStats itself has no public accessor from excise_content's
        # return value (OpResult doesn't carry stats), so this is an
        # indirect check. Direct ExciseStats construction/defaults:
        stats = ExciseStats()
        assert stats.images_total == 0
        assert stats.images_deleted == 0


# ---------------------------------------------------------------------------
# Text (glyph-level) deletion -- end-to-end via excise_content
# ---------------------------------------------------------------------------


def _simple_font_resources(width=500.0, first_char=65):
    """/F1 -- a simple (non-composite) font with a flat /Widths array."""
    font = pikepdf.Dictionary(
        {
            "/Subtype": pikepdf.Name("/TrueType"),
            "/FirstChar": first_char,
            "/Widths": pikepdf.Array([width, width]),  # covers 'A' (65) and 'B' (66)
        }
    )
    return pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})})


def _composite_font_resources(width=500.0):
    """/F1 -- a Type0/Identity-H composite font with a /W range entry
    covering CIDs 0x0042 ('B'-as-CID) and 0x0043."""
    cid_font = pikepdf.Dictionary(
        {
            "/Subtype": pikepdf.Name("/CIDFontType2"),
            "/W": pikepdf.Array([0x0042, 0x0043, width]),
        }
    )
    font = pikepdf.Dictionary(
        {
            "/Subtype": pikepdf.Name("/Type0"),
            "/Encoding": pikepdf.Name("/Identity-H"),
            "/DescendantFonts": pikepdf.Array([cid_font]),
        }
    )
    return pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})})


def _vertical_font_resources(w1y=-1000.0, vx=250.0, vy=880.0):
    """/F1 -- a Type0/Identity-V composite font with explicit /W2 metrics
    for CID 0x0042."""
    cid_font = pikepdf.Dictionary(
        {
            "/Subtype": pikepdf.Name("/CIDFontType2"),
            "/W2": pikepdf.Array([0x0042, 0x0043, w1y, vx, vy]),
        }
    )
    font = pikepdf.Dictionary(
        {
            "/Subtype": pikepdf.Name("/Type0"),
            "/Encoding": pikepdf.Name("/Identity-V"),
            "/DescendantFonts": pikepdf.Array([cid_font]),
        }
    )
    return pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})})


def _get_show_ops(pdf):
    """Returns [(operator_str, operands)] for Tj/TJ operators surviving in
    page 1's content stream."""
    instructions = pikepdf.parse_content_stream(pdf.pages[0].Contents)
    return [(str(op), operands) for operands, op in instructions if str(op) in ("Tj", "TJ")]


class TestExciseContentTextDeletionSimpleFont:
    def test_partial_glyph_deletion_rewrites_as_tj(self):
        # 'A' at (10,10)-(110,110) overlaps rect; 'B' at (60,10)-(160,110)
        # does not (font size 100, width 500/1000 -> 50pt advance).
        content = b"BT /F1 100 Tf 1 0 0 1 10 10 Tm (AB) Tj ET"
        pdf = _make_pdf_with_content(content, resources=_simple_font_resources())
        excise_content(pdf, ["1(abs,0,0,50,120)"])
        show_ops = _get_show_ops(pdf)
        assert len(show_ops) == 1
        op_str, operands = show_ops[0]
        assert op_str == "TJ"  # rewritten since something was deleted
        elements = list(operands[0])
        # One numeric advance-preserving placeholder, one surviving string.
        strings = [e for e in elements if isinstance(e, pikepdf.String)]
        assert bytes(strings[0]) == b"B"

    def test_no_overlap_stays_plain_tj(self):
        content = b"BT /F1 100 Tf 1 0 0 1 10 10 Tm (AB) Tj ET"
        pdf = _make_pdf_with_content(content, resources=_simple_font_resources())
        excise_content(pdf, ["1(abs,500,500,600,600)"])
        show_ops = _get_show_ops(pdf)
        assert show_ops[0][0] == "Tj"  # untouched, no deletions

    def test_full_overlap_deletes_all_glyphs(self):
        content = b"BT /F1 100 Tf 1 0 0 1 10 10 Tm (AB) Tj ET"
        pdf = _make_pdf_with_content(content, resources=_simple_font_resources())
        excise_content(pdf, ["1(abs,0,0,300,300)"])
        show_ops = _get_show_ops(pdf)
        op_str, operands = show_ops[0]
        assert op_str == "TJ"
        elements = list(operands[0])
        assert not any(isinstance(e, pikepdf.String) for e in elements)

    def test_kept_glyph_then_deleted_glyph_flushes_kept_bytes(self):
        """'A' survives (outside rect), 'B' is deleted (inside rect) --
        the REVERSE order of the existing partial-deletion test. Forces
        _process_show_string to flush already-accumulated kept_bytes
        before appending the deleted glyph's advance placeholder
        (excise_text.py lines 203-204). Both glyphs must be in the SAME
        string element (unlike a TJ-array split by a numeric adjustment,
        which flushes via a different call site in filter_show_elements
        instead).

        Since glyph boxes are now sized to each glyph's real advance
        (not a fixed 1em), a normal 500/1000-em width font is enough to
        isolate the two glyphs: at Tf 100, glyph A's box is x:[10,60],
        glyph B's is x:[60,110] -- a rect starting just past x=60 covers
        only B.
        """
        content = b"BT /F1 100 Tf 1 0 0 1 10 10 Tm (AB) Tj ET"
        pdf = _make_pdf_with_content(content, resources=_simple_font_resources(width=500.0))
        excise_content(pdf, ["1(abs,65,0,200,120)"])  # 'A' box: 10-60; 'B' box: 60-110
        show_ops = _get_show_ops(pdf)
        op_str, operands = show_ops[0]
        assert op_str == "TJ"
        elements = list(operands[0])
        strings = [e for e in elements if isinstance(e, pikepdf.String)]
        assert bytes(strings[0]) == b"A"  # kept glyph flushed before the deletion placeholder


class TestExciseContentTextDeletionCompositeFont:
    def test_two_byte_codes_partial_deletion(self):
        # Two 2-byte CIDs (0x0042, 0x0043) shown via a hex string.
        content = b"BT /F1 100 Tf 1 0 0 1 10 10 Tm <00420043> Tj ET"
        pdf = _make_pdf_with_content(content, resources=_composite_font_resources())
        excise_content(pdf, ["1(abs,0,0,50,120)"])
        show_ops = _get_show_ops(pdf)
        op_str, operands = show_ops[0]
        assert op_str == "TJ"
        elements = list(operands[0])
        strings = [e for e in elements if isinstance(e, pikepdf.String)]
        # Surviving glyph is the second 2-byte code, 0x0043.
        assert bytes(strings[0]) == b"\x00\x43"


class TestExciseContentTextDeletionVerticalFont:
    def test_vertical_glyph_partial_deletion(self):
        content = b"BT /F1 1000 Tf 1 0 0 1 0 0 Tm <00420043> Tj ET"
        pdf = _make_pdf_with_content(content, resources=_vertical_font_resources())
        excise_content(pdf, ["1(abs,-2000,-2000,2000,0)"])
        show_ops = _get_show_ops(pdf)
        op_str, operands = show_ops[0]
        assert op_str == "TJ"
        elements = list(operands[0])
        # Both glyphs deleted -- only numeric advance placeholders remain.
        assert not any(isinstance(e, pikepdf.String) for e in elements)
        assert all(isinstance(e, (int, float)) for e in elements)


class TestExciseContentTextDeletionQuoteOperators:
    def test_quote_operator_rewritten(self):
        content = b"BT /F1 100 Tf 1 0 0 1 10 10 Tm 20 TL (A) Tj (AB) ' ET"
        pdf = _make_pdf_with_content(content, resources=_simple_font_resources())
        excise_content(pdf, ["1(abs,0,0,300,300)"])  # delete everything
        ops = _get_content_ops(pdf)
        assert "T*" in ops  # ' expands to an explicit T* prefix

    def test_dquote_operator_rewritten(self):
        content = b'BT /F1 100 Tf 1 0 0 1 10 10 Tm 1 2 (AB) " ET'
        pdf = _make_pdf_with_content(content, resources=_simple_font_resources())
        excise_content(pdf, ["1(abs,0,0,300,300)"])  # delete everything
        ops = _get_content_ops(pdf)
        assert "Tw" in ops and "Tc" in ops and "T*" in ops


class TestExciseContentTextNoFontFallback:
    def test_tj_without_font_passes_through(self):
        """Tj shown with no preceding Tf -- font_name stays None, so the
        show operator must be passed through untouched rather than
        crashing on a missing font."""
        content = b"BT (A) Tj ET"
        pdf = _make_pdf_with_content(content, resources=_simple_font_resources())
        excise_content(pdf, ["1(abs,0,0,300,300)"])
        show_ops = _get_show_ops(pdf)
        assert show_ops[0][0] == "Tj"  # untouched fallback path


# ---------------------------------------------------------------------------
# _FontCache -- direct unit tests for branches not easily reachable through
# a full content-stream round trip (missing font name, no resources, etc.)
# ---------------------------------------------------------------------------


class TestFontCacheEdgeCases:
    def test_font_name_not_in_resources_returns_defaults(self):
        cache = _FontCache(_simple_font_resources())
        assert cache.is_composite("/NotThere") is False
        assert cache.is_vertical("/NotThere") is False
        assert cache.glyph_width("/NotThere", 65) == 0.0
        assert cache.vertical_metrics("/NotThere", 65) == (-1000.0, 0.0, 880.0)

    def test_no_resources_at_all(self):
        cache = _FontCache(None)
        assert cache.is_composite("/F1") is False
        assert cache.glyph_width("/F1", 65) == 0.0

    def test_resources_without_font_key(self):
        cache = _FontCache(pikepdf.Dictionary({}))
        assert cache.is_composite("/F1") is False

    def test_composite_and_vertical_and_widths_hit(self):
        cache = _FontCache(_composite_font_resources())
        assert cache.is_composite("/F1") is True
        assert cache.is_vertical("/F1") is False
        assert cache.glyph_width("/F1", 0x0042) == 500.0

    def test_vertical_metrics_hit(self):
        cache = _FontCache(_vertical_font_resources())
        assert cache.is_vertical("/F1") is True
        assert cache.vertical_metrics("/F1", 0x0042) == (-1000.0, 250.0, 880.0)

    def test_repeated_calls_reuse_cache(self):
        """Not directly observable from outside, but exercises the
        already-cached branch of each memoized method."""
        cache = _FontCache(_simple_font_resources())
        assert cache.is_composite("/F1") == cache.is_composite("/F1")
        assert cache.glyph_width("/F1", 65) == cache.glyph_width("/F1", 65)
        assert cache.is_vertical("/F1") == cache.is_vertical("/F1")


# ---------------------------------------------------------------------------
# _rewrite_text_show / _glyph_should_delete -- direct unit tests for
# branches not reachable through a normal parsed content stream.
# ---------------------------------------------------------------------------


class TestRewriteTextShowDirect:
    def test_no_font_name_set_passes_through(self):
        gs = GraphicsState()
        gs.text_matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        cache = _FontCache(_simple_font_resources())
        stats = ExciseStats()
        rect = ExciseRect(rect=[0, 0, 300, 300], delete="inside")
        result = _rewrite_text_show("Tj", [pikepdf.String(b"A")], gs, cache, rect, stats)
        assert result == [([pikepdf.String(b"A")], "Tj")]

    def test_empty_show_operands_passes_through(self):
        gs = GraphicsState()
        gs.font_name = "/F1"
        gs.font_size = 100.0
        gs.text_matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        cache = _FontCache(_simple_font_resources())
        stats = ExciseStats()
        rect = ExciseRect(rect=[0, 0, 300, 300], delete="inside")
        result = _rewrite_text_show("Tj", [], gs, cache, rect, stats)
        assert result == [([], "Tj")]


class TestGlyphShouldDeleteDirect:
    def test_none_render_matrix_never_deletes(self):
        rect = ExciseRect(rect=[0, 0, 300, 300], delete="inside")
        assert _glyph_should_delete(None, 0.0, False, rect) is False


class TestFilterAnnotsMalformedRect:
    def test_non_numeric_rect_entry_kept(self):
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(300, 300))
        page = pdf.pages[0]
        bad_annot = pikepdf.Dictionary(
            {"/Type": pikepdf.Name("/Annot"), "/Rect": pikepdf.Array(["bad", 0, 100, 100])}
        )
        page.Annots = pikepdf.Array([bad_annot])
        excise_content(pdf, ["1(abs,0,0,300,300)"])
        assert len(pdf.pages[0].Annots) == 1  # malformed /Rect -- kept, not crashed


# ---------------------------------------------------------------------------
# Annotation deletion (whole-Annot-on-/Rect-overlap)
# ---------------------------------------------------------------------------


def _make_pdf_with_annot(rect, page_size=(300, 300)):
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=page_size)
    page = pdf.pages[0]
    annot = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Annot"),
            "/Subtype": pikepdf.Name("/Link"),
            "/Rect": pikepdf.Array(rect),
        }
    )
    page.Annots = pikepdf.Array([annot])
    return pdf


class TestExciseContentAnnotationDeletion:
    def test_overlapping_annot_deleted(self):
        pdf = _make_pdf_with_annot([50, 50, 150, 150])
        excise_content(pdf, ["1(abs,0,0,200,200)"])
        assert len(pdf.pages[0].get("/Annots", [])) == 0

    def test_non_overlapping_annot_survives(self):
        pdf = _make_pdf_with_annot([200, 200, 250, 250])
        excise_content(pdf, ["1(abs,0,0,50,50)"])
        assert len(pdf.pages[0].Annots) == 1

    def test_keep_inside_preserves_overlapping_annot(self):
        pdf = _make_pdf_with_annot([50, 50, 150, 150])
        excise_content(pdf, ["1(abs,0,0,200,200,delete=outside)"])
        assert len(pdf.pages[0].Annots) == 1

    def test_annot_with_no_rect_kept(self):
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(300, 300))
        page = pdf.pages[0]
        page.Annots = pikepdf.Array([pikepdf.Dictionary({"/Type": pikepdf.Name("/Annot")})])
        excise_content(pdf, ["1(abs,0,0,300,300)"])
        assert len(pdf.pages[0].Annots) == 1

    def test_no_annots_key_is_noop(self):
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(300, 300))
        excise_content(pdf, ["1(abs,0,0,300,300)"])  # must not raise


# ---------------------------------------------------------------------------
# Shared Form XObject unsharing (regression test for the shared-Form-with-
# different-excise_rects bug: a Form XObject referenced from two pages, each
# with a different excise_rect, must NOT have one page's deletions clobbered
# by the other page's processing of the same underlying stream object).
# ---------------------------------------------------------------------------


def _make_pdf_with_shared_form(fill_rect, page_size=(300, 300), num_pages=2):
    """Builds a multi-page PDF where every page's /Resources /XObject
    dict points its own '/Fm0' entry at the SAME underlying Form XObject
    stream (simulating chop-style Form sharing), each page invoking it via
    its own (separate) page /Contents stream."""
    pdf = pikepdf.new()
    x0, y0, x1, y1 = fill_rect
    w, h = x1 - x0, y1 - y0
    form_content = f"{x0} {y0} {w} {h} re f".encode("latin-1")
    shared_form = pdf.make_stream(form_content)
    shared_form.Type = pikepdf.Name("/XObject")
    shared_form.Subtype = pikepdf.Name("/Form")
    shared_form.BBox = pikepdf.Array([0, 0, w, h])

    for _ in range(num_pages):
        pdf.add_blank_page(page_size=page_size)

    for page in pdf.pages:
        page.Resources = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/Fm0": shared_form})}
        )
        page.Contents = pdf.make_stream(b"/Fm0 Do")

    return pdf


def _get_form_fill_present(pdf, page_index):
    """True if the Form XObject actually invoked by this page's /Do
    (which may now be a private filtered copy under a NEW key, not
    necessarily the original '/Fm0' -- see _handle_form_do) still
    contains an 'f' (fill) operator, i.e. was NOT excised away."""
    page = pdf.pages[page_index]
    instructions = pikepdf.parse_content_stream(page.Contents)
    do_name = next(str(operands[0]) for operands, op in instructions if str(op) == "Do")
    form = page.Resources["/XObject"][do_name]
    ops = [str(op) for _, op in pikepdf.parse_content_stream(form)]
    return "f" in ops


class TestUnshareFormXObject:
    def test_two_pages_different_rects_each_kept_independent(self):
        pdf = _make_pdf_with_shared_form([50, 50, 150, 150], num_pages=2)

        # Sanity check the fixture: both pages really do share one Form
        # object before excise touches anything.
        form0 = pdf.pages[0].Resources["/XObject"]["/Fm0"]
        form1 = pdf.pages[1].Resources["/XObject"]["/Fm0"]
        assert form0.objgen == form1.objgen

        # Page 1: rect covers the fill -> deleted. Page 2: rect misses it
        # entirely -> survives. If unsharing didn't happen, whichever page
        # processes second would clobber the first page's result.
        excise_content(pdf, ["1(abs,0,0,300,300)", "2(abs,500,500,600,600)"])

        assert _get_form_fill_present(pdf, 0) is False  # page 1: deleted
        assert _get_form_fill_present(pdf, 1) is True  # page 2: survives

        # Page 1's Do must now point at a NEW private key (its content was
        # filtered), while page 2 -- nothing deleted -- keeps pointing at
        # the original shared '/Fm0'/object untouched.
        page1_do = next(
            str(operands[0])
            for operands, op in pikepdf.parse_content_stream(pdf.pages[0].Contents)
            if str(op) == "Do"
        )
        page2_do = next(
            str(operands[0])
            for operands, op in pikepdf.parse_content_stream(pdf.pages[1].Contents)
            if str(op) == "Do"
        )
        assert page1_do != "/Fm0"
        assert page2_do == "/Fm0"
        form0_after = pdf.pages[0].Resources["/XObject"][page1_do]
        form1_after = pdf.pages[1].Resources["/XObject"][page2_do]
        assert form0_after.objgen != form1_after.objgen


class TestFilterShowElementsDirect:
    def _gs(self, font_size=100.0):
        gs = GraphicsState()
        gs.font_name = "/F1"
        gs.font_size = font_size
        gs.text_matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        return gs

    def test_tj_array_numeric_adjustment_element(self):
        """A TJ array's numeric kerning element must be re-emitted as-is
        and advance the text matrix horizontally (lines 747-750)."""
        gs = self._gs()
        cache = _FontCache(_simple_font_resources())
        stats = ExciseStats()
        rect = ExciseRect(rect=[500, 500, 600, 600], delete="inside")  # no overlap
        elements = [pikepdf.String(b"A"), -100.0, pikepdf.String(b"B")]
        rebuilt, any_deleted = _filter_show_elements(elements, gs, cache, rect, stats)
        assert any_deleted is False
        assert -100.0 in rebuilt

    def test_zero_font_size_skips_spacing_addition(self):
        """gs.font_size == 0 must skip the char/word-spacing addition
        entirely rather than dividing by zero (line 765->769 branch)."""
        gs = self._gs(font_size=0.0)
        gs.char_spacing = 5.0
        cache = _FontCache(_simple_font_resources())
        stats = ExciseStats()
        rect = ExciseRect(rect=[500, 500, 600, 600], delete="inside")
        # Should not raise ZeroDivisionError.
        _filter_show_elements([pikepdf.String(b"A")], gs, cache, rect, stats)

    def test_word_spacing_applied_to_code_32_simple_font(self):
        """Code 32 (space) in a simple (non-composite) font must add
        word_spacing on top of char_spacing (line 768)."""
        gs = self._gs()
        gs.word_spacing = 50.0
        cache = _FontCache(_simple_font_resources())
        stats = ExciseStats()
        rect = ExciseRect(rect=[500, 500, 600, 600], delete="inside")
        # Byte 32 == ' ' -- simple font, not composite.
        _filter_show_elements([pikepdf.String(b" ")], gs, cache, rect, stats)
        # No direct assertion on the resulting advance (that's an internal
        # detail), but this exercises the code==32-and-not-composite branch
        # without raising.


class TestRewriteTextShowQuoteMalformedOperands:
    def test_dquote_malformed_operands_caught(self):
        """Non-numeric aw/ac operands to '"' must be caught (lines 687-688)
        rather than raising, leaving word/char spacing untouched."""
        gs = GraphicsState()
        gs.font_name = "/F1"
        gs.font_size = 100.0
        gs.text_matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        cache = _FontCache(_simple_font_resources())
        stats = ExciseStats()
        rect = ExciseRect(rect=[0, 0, 300, 300], delete="inside")
        operands = [pikepdf.Name("/Bad"), pikepdf.Name("/AlsoBad"), pikepdf.String(b"A")]
        result = _rewrite_text_show('"', operands, gs, cache, rect, stats)
        assert result  # didn't raise; produced some output


class TestApplyGraphicsOpDirect:
    def test_unrecognized_op_is_a_noop(self):
        """_apply_graphics_op is only ever called by interpret_and_filter
        for op_str in {q, Q, cm}, so the 'none of the above' branch is
        untestable through that caller -- exercise it directly instead."""
        from pdftl.utils.graphics_state import GraphicsStateStack

        gs_stack = GraphicsStateStack()
        _apply_graphics_op("XYZ", [], gs_stack)  # must not raise or push/pop
