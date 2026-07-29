# tests/visual/test_visual_excise.py
"""Visual regression test for the shared-Form-XObject excise bug: a Form
XObject referenced by object identity from multiple pages must, after
excise deletes content from just one of those pages, still resolve and
render correctly in a real PDF renderer -- not just in pikepdf's own
object model. See git history / roadmap notes for the original repro
(reproduced manually via evince during development; this pins it down
as an automated regression test rendered via pypdfium2)."""

import pikepdf
import pytest

import pdftl.api


@pytest.fixture
def shared_form_two_page_pdf():
    """Two pages, each pointing '/Fm0' at the SAME underlying Form
    XObject stream object (simulating chop-style content sharing). The
    Form draws two disjoint stroke segments so a partial (not
    whole-Form) deletion is possible and visually distinguishable."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(300, 300))
    pdf.add_blank_page(page_size=(300, 300))

    # 20pt line width -- deliberately thick, not a hairline default (1pt),
    # so both surviving/deleted segments are unambiguous in the rasterized
    # comparison image. A near-invisible default-width stroke risks
    # antialiasing away to near-nothing at typical render DPI, which would
    # silently defeat the whole point of this regression test.
    shared_form = pdf.make_stream(b"20 w 10 10 m 50 10 l 220 10 m 260 10 l S")
    shared_form.Type = pikepdf.Name("/XObject")
    shared_form.Subtype = pikepdf.Name("/Form")
    shared_form.BBox = pikepdf.Array([0, 0, 300, 300])

    for page in pdf.pages:
        page.Resources = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/Fm0": shared_form})}
        )
        page.Contents = pdf.make_stream(b"/Fm0 Do")

    return pdf


def test_visual_excise_shared_form_partial_deletion(shared_form_two_page_pdf, assert_pdf_match):
    """Page 1: rect covers only the FIRST stroke segment -- the SECOND
    segment must still visibly render (this is exactly the case that
    silently rendered blank under the pre-fix bug, since the rewritten
    Do operand pointed at a resource key invisible to a real renderer).
    Page 2: rect misses the Form entirely -- both segments must survive,
    completely unaffected by page 1's excision of the shared object."""
    result = pdftl.api.excise(
        pdf=shared_form_two_page_pdf,
        operation_args=["1(abs,0,0,100,50)"],
    )
    assert_pdf_match(result)
