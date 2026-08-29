# tests/operations/helpers/test_marks_render.py
import pikepdf
import pytest

from pdftl.operations.helpers.marks_render import (
    MARK_XOBJECT,
    RECORD_KEY,
    apply_marks_to_page,
)
from pdftl.operations.helpers.marks_types import MarksConfig


def _new_pdf_with_page(size=(200, 300)):
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=size)
    return pdf, pdf.pages[0]


class TestApplyMarksToPage:
    def test_grows_media_box_by_offset_plus_length(self):
        pdf, page = _new_pdf_with_page((200, 300))
        config = MarksConfig(offset=9.0, length=18.0)
        report = apply_marks_to_page(pdf, page, config)

        assert report["marked"] is True
        media = page.obj["/MediaBox"]
        assert float(media[0]) == pytest.approx(-27.0)
        assert float(media[1]) == pytest.approx(-27.0)
        assert float(media[2]) == pytest.approx(227.0)
        assert float(media[3]) == pytest.approx(327.0)

    def test_writes_mark_xobject_and_draw_call(self):
        pdf, page = _new_pdf_with_page()
        apply_marks_to_page(pdf, page, MarksConfig())
        xobjects = page.obj["/Resources"]["/XObject"]
        assert MARK_XOBJECT in [str(k) for k in xobjects.keys()]
        content = pikepdf.parse_content_stream(page)
        do_ops = [i for i in content if str(i.operator) == "Do"]
        assert any(str(op.operands[0]) == MARK_XOBJECT for op in do_ops)

    def test_records_original_box_for_restore(self):
        pdf, page = _new_pdf_with_page((200, 300))
        apply_marks_to_page(pdf, page, MarksConfig())
        record = page.obj[RECORD_KEY]
        assert [float(v) for v in record["/MediaBox"]] == [0.0, 0.0, 200.0, 300.0]

    def test_rerun_does_not_compound_growth(self):
        pdf, page = _new_pdf_with_page((200, 300))
        config = MarksConfig(offset=9.0, length=18.0)
        apply_marks_to_page(pdf, page, config)
        first_media = [float(v) for v in page.obj["/MediaBox"]]

        apply_marks_to_page(pdf, page, config)
        second_media = [float(v) for v in page.obj["/MediaBox"]]

        assert first_media == second_media

    def test_rerun_does_not_duplicate_xobject_draw_calls(self):
        pdf, page = _new_pdf_with_page()
        apply_marks_to_page(pdf, page, MarksConfig())
        apply_marks_to_page(pdf, page, MarksConfig())
        content = pikepdf.parse_content_stream(page)
        do_ops = [
            i for i in content if str(i.operator) == "Do" and str(i.operands[0]) == MARK_XOBJECT
        ]
        assert len(do_ops) == 1

    def test_wants_nothing_strips_and_restores(self):
        pdf, page = _new_pdf_with_page((200, 300))
        apply_marks_to_page(pdf, page, MarksConfig())
        report = apply_marks_to_page(
            pdf, page, MarksConfig(cropmarks=None, registration=False, colorbars=False)
        )
        assert report["marked"] is False
        media = [float(v) for v in page.obj["/MediaBox"]]
        assert media == [0.0, 0.0, 200.0, 300.0]
        assert RECORD_KEY not in page.obj

    def test_colorbars_draw_patches_when_enabled(self):
        pdf, page = _new_pdf_with_page()
        apply_marks_to_page(pdf, page, MarksConfig(colorbars=True))
        xobjects = page.obj["/Resources"]["/XObject"]
        form = xobjects[MARK_XOBJECT]
        stream_bytes = form.read_bytes()
        assert b" k " in stream_bytes  # DeviceCMYK fill operator present

    def test_refuses_extreme_growth_past_page_extent_limit(self):
        pdf, page = _new_pdf_with_page((200, 300))
        huge = MarksConfig(offset=9000.0, length=9000.0)
        with pytest.raises(ValueError, match="page-extent limit"):
            apply_marks_to_page(pdf, page, huge)


# restore variants, and apply_marks_to_page's less-common resource/content
# shapes ---

from pdftl.operations.helpers.marks_render import _strip_marks


class TestStripMarksDirect:
    def test_noop_when_page_has_no_contents(self):
        pdf, page = _new_pdf_with_page()
        del page.obj["/Contents"]
        _strip_marks(pdf, page)  # must not raise
        assert "/Contents" not in page.obj

    def test_skips_do_calls_for_other_xobjects(self):
        pdf, page = _new_pdf_with_page()
        page.obj["/Contents"] = pdf.make_stream(b"q /OtherForm Do Q")
        _strip_marks(pdf, page)
        content = list(pikepdf.parse_content_stream(page))
        do_ops = [i for i in content if str(i.operator) == "Do"]
        assert len(do_ops) == 1
        assert str(do_ops[0].operands[0]) == "/OtherForm"

    def test_removes_unframed_mark_do_call(self):
        pdf, page = _new_pdf_with_page()
        page.obj["/Contents"] = pdf.make_stream(f"{MARK_XOBJECT} Do".encode("ascii"))
        _strip_marks(pdf, page)
        content = list(pikepdf.parse_content_stream(page))
        assert len(content) == 0

    def test_noop_when_no_resources(self):
        pdf, page = _new_pdf_with_page()
        page.obj["/Contents"] = pdf.make_stream(f"q\n{MARK_XOBJECT} Do\nQ".encode("ascii"))
        if "/Resources" in page.obj:
            del page.obj["/Resources"]
        _strip_marks(pdf, page)  # must not raise
        assert "/Resources" not in page.obj

    def test_noop_when_resources_has_no_xobject(self):
        pdf, page = _new_pdf_with_page()
        page.obj["/Contents"] = pdf.make_stream(f"q\n{MARK_XOBJECT} Do\nQ".encode("ascii"))
        page.obj["/Resources"] = pikepdf.Dictionary()
        _strip_marks(pdf, page)  # must not raise
        assert "/XObject" not in page.obj["/Resources"]

    def test_preserves_other_xobject_entries(self):
        pdf, page = _new_pdf_with_page()
        other_form = pdf.make_stream(b"q Q")
        other_form.Type = pikepdf.Name.XObject
        other_form.Subtype = pikepdf.Name.Form

        xobj_dict = pikepdf.Dictionary()
        xobj_dict[MARK_XOBJECT] = pdf.make_indirect(other_form)
        xobj_dict["/OtherForm"] = pdf.make_indirect(other_form)
        resources = pikepdf.Dictionary()
        resources["/XObject"] = xobj_dict

        page.obj["/Contents"] = pdf.make_stream(f"q\n{MARK_XOBJECT} Do\nQ".encode("ascii"))
        page.obj["/Resources"] = resources

        _strip_marks(pdf, page)
        xobjects = page.obj["/Resources"]["/XObject"]
        keys = [str(k) for k in xobjects.keys()]
        assert "/OtherForm" in keys
        assert MARK_XOBJECT not in keys


class TestRestoreCropBoxVariants:
    def test_own_cropbox_is_recorded_and_restored(self):
        pdf, page = _new_pdf_with_page((200, 300))
        page.obj["/CropBox"] = pikepdf.Array([0, 0, 200, 300])

        apply_marks_to_page(pdf, page, MarksConfig())
        grown_crop = [float(v) for v in page.obj["/CropBox"]]
        assert grown_crop != [0.0, 0.0, 200.0, 300.0]

        apply_marks_to_page(
            pdf, page, MarksConfig(cropmarks=None, registration=False, colorbars=False)
        )
        restored_crop = [float(v) for v in page.obj["/CropBox"]]
        assert restored_crop == [0.0, 0.0, 200.0, 300.0]

    def test_inherited_only_cropbox_is_removed_on_restore(self):
        pdf, page = _new_pdf_with_page((200, 300))
        pdf.Root.Pages["/CropBox"] = pikepdf.Array([0, 0, 200, 300])
        assert "/CropBox" not in page.obj

        apply_marks_to_page(pdf, page, MarksConfig())
        assert "/CropBox" in page.obj  # growth added an own copy

        apply_marks_to_page(
            pdf, page, MarksConfig(cropmarks=None, registration=False, colorbars=False)
        )
        # Nothing was ever recorded as the page's OWN CropBox (it only ever
        # inherited one), so restore removes the own copy growth added
        # rather than trying to set it back to a value that never existed.
        assert "/CropBox" not in page.obj


class TestRestoreMediaBoxInheritedOnly:
    def test_inherited_only_mediabox_is_left_grown_on_restore(self):
        pdf, page = _new_pdf_with_page((200, 300))
        del page.obj["/MediaBox"]
        pdf.Root.Pages["/MediaBox"] = pikepdf.Array([0, 0, 200, 300])

        report = apply_marks_to_page(pdf, page, MarksConfig())
        assert report["marked"] is True

        apply_marks_to_page(
            pdf, page, MarksConfig(cropmarks=None, registration=False, colorbars=False)
        )
        # Known limitation: unlike /CropBox, /MediaBox restore has no
        # delete-if-never-owned branch, so an inherited-only MediaBox is
        # left at the grown size after restore rather than reverting to
        # pure inheritance. Documented here, not silently "fixed".
        assert "/MediaBox" in page.obj


class TestApplyMarksEdgeCases:
    def test_no_media_box_anywhere_is_skipped(self):
        pdf, page = _new_pdf_with_page((200, 300))
        del page.obj["/MediaBox"]
        report = apply_marks_to_page(pdf, page, MarksConfig())
        assert report == {"marked": False, "skipped_reason": "no media box"}

    def test_only_colorbars_draws_without_crop_or_registration(self):
        pdf, page = _new_pdf_with_page()
        config = MarksConfig(cropmarks=None, registration=False, colorbars=True)
        report = apply_marks_to_page(pdf, page, config)
        assert report["marked"] is True
        form = page.obj["/Resources"]["/XObject"][MARK_XOBJECT]
        stream_bytes = form.read_bytes()
        assert b" k " in stream_bytes  # colorbar fill present
        # no crop/registration stroke op (avoid matching "SCN" in the
        # header's colorspace-selection line)
        assert b" S\n" not in stream_bytes and not stream_bytes.rstrip().endswith(b" S")

    def test_missing_resources_before_add_is_handled(self):
        pdf, page = _new_pdf_with_page()
        if "/Resources" in page.obj:
            del page.obj["/Resources"]
        apply_marks_to_page(pdf, page, MarksConfig())
        xobjects = page.obj["/Resources"]["/XObject"]
        assert MARK_XOBJECT in [str(k) for k in xobjects.keys()]

    def test_existing_xobject_entries_are_preserved(self):
        pdf, page = _new_pdf_with_page()
        other_form = pdf.make_stream(b"q Q")
        other_form.Type = pikepdf.Name.XObject
        other_form.Subtype = pikepdf.Name.Form
        xobj_dict = pikepdf.Dictionary()
        xobj_dict["/OtherForm"] = pdf.make_indirect(other_form)
        resources = pikepdf.Dictionary()
        resources["/XObject"] = xobj_dict
        page.obj["/Resources"] = resources

        apply_marks_to_page(pdf, page, MarksConfig())
        xobjects = page.obj["/Resources"]["/XObject"]
        keys = [str(k) for k in xobjects.keys()]
        assert "/OtherForm" in keys
        assert MARK_XOBJECT in keys

    def test_missing_contents_before_add_results_in_just_pre_and_post(self):
        pdf, page = _new_pdf_with_page()
        if "/Contents" in page.obj:
            del page.obj["/Contents"]
        apply_marks_to_page(pdf, page, MarksConfig())
        contents = page.obj["/Contents"]
        assert len(contents) == 2

    def test_existing_array_contents_are_preserved_and_wrapped(self):
        pdf, page = _new_pdf_with_page()
        stream_a = pdf.make_stream(b"1 0 0 RG")
        stream_b = pdf.make_stream(b"0 1 0 RG")
        page.obj["/Contents"] = pikepdf.Array([stream_a, stream_b])

        apply_marks_to_page(pdf, page, MarksConfig())
        contents = page.obj["/Contents"]
        assert len(contents) == 4


class TestPageInfoAndCropOnly:
    def test_pageinfo_draws_label_text(self):
        pdf, page = _new_pdf_with_page()
        config = MarksConfig(cropmarks=None, registration=False, colorbars=False, pageinfo=True)
        report = apply_marks_to_page(
            pdf, page, config, filename="report.pdf", page_index=2, total_pages=5
        )
        assert report["marked"] is True
        form = page.obj["/Resources"]["/XObject"][MARK_XOBJECT]
        stream_bytes = form.read_bytes()
        assert b"BT" in stream_bytes and b"Tj" in stream_bytes
        assert b"report.pdf" in stream_bytes
        assert b"2/5" in stream_bytes
        assert "/F0" in [
            str(k)
            for k in page.obj["/Resources"]["/XObject"][MARK_XOBJECT].Resources["/Font"].keys()
        ]

    def test_cropmarks_only_skips_registration_centres(self):
        pdf, page = _new_pdf_with_page()
        config = MarksConfig(cropmarks="western", registration=False, colorbars=False)
        report = apply_marks_to_page(pdf, page, config)
        assert report["marked"] is True
        form = page.obj["/Resources"]["/XObject"][MARK_XOBJECT]
        stream_bytes = form.read_bytes()
        assert b" l S" in stream_bytes  # crop mark stroke present
        assert b" k " not in stream_bytes  # no colorbar fill


class TestStarTarget:
    def test_startarget_draws_diameter_strokes(self):
        from pdftl.operations.helpers.marks_geometry import STAR_TARGET_DIAMETERS

        pdf, page = _new_pdf_with_page()
        config = MarksConfig(cropmarks=None, registration=False, colorbars=False, startarget=True)
        report = apply_marks_to_page(pdf, page, config)
        assert report["marked"] is True
        form = page.obj["/Resources"]["/XObject"][MARK_XOBJECT]
        stream_bytes = form.read_bytes()
        # 18 diameters -> 18 "m ... l S" stroke instructions
        assert stream_bytes.count(b" l S") == STAR_TARGET_DIAMETERS

    def test_startarget_off_by_default(self):
        pdf, page = _new_pdf_with_page()
        config = MarksConfig(cropmarks=None, registration=False, colorbars=True)
        apply_marks_to_page(pdf, page, config)
        form = page.obj["/Resources"]["/XObject"][MARK_XOBJECT]
        assert form.read_bytes().count(b" l S") == 0
