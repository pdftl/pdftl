# tests/operations/test_add_marks.py
from unittest.mock import PropertyMock, patch
import pikepdf
import pytest

from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.add_marks import add_marks_to_pdf
from pdftl.operations.helpers.marks_render import MARK_XOBJECT


def _new_pdf(num_pages=3, size=(200, 300)):
    pdf = pikepdf.new()
    for _ in range(num_pages):
        pdf.add_blank_page(page_size=size)
    return pdf


class TestAddMarksOperation:
    def test_default_call_marks_every_page(self):
        pdf = _new_pdf(3)
        result = add_marks_to_pdf(pdf, [])
        assert result.success is True
        for page in pdf.pages:
            xobjects = page.obj["/Resources"]["/XObject"]
            assert MARK_XOBJECT in [str(k) for k in xobjects.keys()]

    def test_spec_restricts_to_named_pages(self):
        pdf = _new_pdf(3)
        add_marks_to_pdf(pdf, ["2"])
        assert "/XObject" not in pdf.pages[0].obj.get("/Resources", {})
        xobjects = pdf.pages[1].obj["/Resources"]["/XObject"]
        assert MARK_XOBJECT in [str(k) for k in xobjects.keys()]
        assert "/XObject" not in pdf.pages[2].obj.get("/Resources", {})

    def test_multiple_specs_apply_independently(self):
        pdf = _new_pdf(3, size=(200, 300))
        add_marks_to_pdf(pdf, ["1", "3(offset=1cm,length=2cm)"])
        media1 = [float(v) for v in pdf.pages[0].obj["/MediaBox"]]
        media3 = [float(v) for v in pdf.pages[2].obj["/MediaBox"]]
        assert media1 != media3  # page 3 grew by a different margin

    def test_invalid_option_raises_invalid_argument_error(self):
        pdf = _new_pdf(1)
        with pytest.raises(InvalidArgumentError):
            add_marks_to_pdf(pdf, ["1(cropmarks=nonsense)"])

    def test_rerunning_whole_operation_does_not_compound(self):
        pdf = _new_pdf(1, size=(200, 300))
        add_marks_to_pdf(pdf, [])
        first_media = [float(v) for v in pdf.pages[0].obj["/MediaBox"]]
        add_marks_to_pdf(pdf, [])
        second_media = [float(v) for v in pdf.pages[0].obj["/MediaBox"]]
        assert first_media == second_media


# top-level operation entry point ---


class TestAddMarksSkipsPagesWithNoMediaBox:
    def test_page_without_media_box_is_skipped_with_warning(self):
        pdf = _new_pdf(2)
        del pdf.pages[0].obj["/MediaBox"]

        result = add_marks_to_pdf(pdf, [])

        assert result.success is True
        resources = pdf.pages[0].obj.get("/Resources", {})
        assert "/XObject" not in resources
        xobjects = pdf.pages[1].obj["/Resources"]["/XObject"]
        assert MARK_XOBJECT in [str(k) for k in xobjects.keys()]


# (a strip-only/'remove' page's report -- {'marked': False}, no
# 'skipped_reason' -- followed by another page in the same call) and
# integration coverage for the 'remove' spec end-to-end ---


class TestAddMarksRemove:
    def test_remove_strips_previously_added_marks(self):
        pdf = _new_pdf(1, size=(200, 300))
        add_marks_to_pdf(pdf, [])
        assert MARK_XOBJECT in [str(k) for k in pdf.pages[0].obj["/Resources"]["/XObject"].keys()]

        add_marks_to_pdf(pdf, ["1(remove)"])

        resources = pdf.pages[0].obj.get("/Resources", {})
        xobjects = resources.get("/XObject", {})
        assert MARK_XOBJECT not in [str(k) for k in xobjects.keys()]
        media = [float(v) for v in pdf.pages[0].obj["/MediaBox"]]
        assert media == [0.0, 0.0, 200.0, 300.0]

    def test_remove_on_page_with_no_existing_marks_is_a_noop(self):
        pdf = _new_pdf(1, size=(200, 300))
        result = add_marks_to_pdf(pdf, ["1(remove)"])
        assert result.success is True
        media = [float(v) for v in pdf.pages[0].obj["/MediaBox"]]
        assert media == [0.0, 0.0, 200.0, 300.0]

    def test_remove_page_followed_by_another_page_in_same_call(self):
        # Exercises the loop-continuation branch after a strip-only report
        # ({'marked': False}, no 'skipped_reason'): page 1 is a 'remove'
        # (wants_anything False, no skip reason), page 2 gets default marks,
        # so the loop must proceed past page 1's report to page 2.
        pdf = _new_pdf(2, size=(200, 300))
        result = add_marks_to_pdf(pdf, ["1(remove)", "2"])

        assert result.success is True
        page1_resources = pdf.pages[0].obj.get("/Resources", {})
        assert MARK_XOBJECT not in [str(k) for k in page1_resources.get("/XObject", {}).keys()]
        page2_xobjects = pdf.pages[1].obj["/Resources"]["/XObject"]
        assert MARK_XOBJECT in [str(k) for k in page2_xobjects.keys()]


class TestAddMarksDefaultFilename:
    def test_missing_filename_falls_back_to_document_pdf(self):
        pdf = _new_pdf(1, size=(200, 300))
        with patch.object(type(pdf), "filename", new_callable=PropertyMock, return_value=""):
            add_marks_to_pdf(pdf, ["1(pageinfo=true)"])

        form = pdf.pages[0].obj["/Resources"]["/XObject"][MARK_XOBJECT]
        stream_bytes = form.read_bytes()
        assert b"document.pdf" in stream_bytes
