# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/test_delete_tags.py

import io
import logging

import pikepdf
import pypdfium2
import pytest

from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.delete_tags import delete_tags

TEXT = b"BT /F1 12 Tf 10 50 Td (Hi) Tj ET"
BOX = b"0 0 1 rg 20 20 30 30 re f"


def _norm(data: bytes) -> bytes:
    """Canonical spelling of hand-written content, to compare token streams."""
    pdf = pikepdf.new()
    return pikepdf.unparse_content_stream(pikepdf.parse_content_stream(pdf.make_stream(data)))


def _content(page) -> bytes:
    return _norm(pikepdf.Page(page).obj.Contents.read_bytes())


def _tagged_pdf(content: bytes) -> pikepdf.Pdf:
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(100, 100))
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica
        )
    )
    page.obj.Resources = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(F1=font),
        Properties=pikepdf.Dictionary(
            oc1=pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.OCG, Name="L"))
        ),
    )
    page.obj.Contents = pdf.make_stream(content)
    page.obj.StructParents = 0
    page.obj.Tabs = pikepdf.Name.S
    elem = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.StructElem, S=pikepdf.Name.P, K=0, Pg=page.obj)
    )
    pdf.Root.StructTreeRoot = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.StructTreeRoot,
            K=elem,
            ParentTree=pikepdf.Dictionary(Nums=pikepdf.Array([0, pikepdf.Array([elem])])),
        )
    )
    pdf.Root.MarkInfo = pikepdf.Dictionary(Marked=True)
    return pdf


def test_structure_tree_and_links_to_it_are_removed():
    pdf = _tagged_pdf(b"/P <</MCID 0>> BDC " + TEXT + b" EMC")
    link = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Link, Rect=[0, 0, 9, 9], StructParent=1
        )
    )
    direct = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Square, Rect=[0, 0, 5, 5], StructParent=2
    )
    pdf.pages[0].obj.Annots = pikepdf.Array([link, direct])
    image = pdf.make_stream(
        b"\xff",
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Image,
        Width=1,
        Height=1,
        BitsPerComponent=8,
        ColorSpace=pikepdf.Name.DeviceGray,
        StructParent=3,
    )
    pdf.pages[0].obj.Resources.XObject = pikepdf.Dictionary(Im0=image)

    delete_tags(pdf, [])

    assert "/StructTreeRoot" not in pdf.Root and "/MarkInfo" not in pdf.Root
    page = pdf.pages[0].obj
    assert "/StructParents" not in page and "/Tabs" not in page
    annots = list(page.Annots)
    assert [str(a.Subtype) for a in annots] == ["/Link", "/Square"]  # annotations kept
    assert all("/StructParent" not in a for a in annots)
    assert "/StructParent" not in page.Resources.XObject.Im0
    assert _content(page) == _norm(TEXT)


def test_other_tab_orders_are_kept():
    pdf = _tagged_pdf(TEXT)
    pdf.pages[0].obj.Tabs = pikepdf.Name.R
    delete_tags(pdf, [])
    assert pdf.pages[0].obj.Tabs == "/R"


@pytest.mark.parametrize(
    "before, after",
    [
        (b"/Artifact BMC " + BOX + b" EMC", BOX),
        (b"/Artifact <</Type /Pagination>> BDC " + BOX + b" EMC", BOX),
        (b"/OC /oc1 BDC " + BOX + b" EMC", b"/OC /oc1 BDC " + BOX + b" EMC"),
        (b"/Tx BMC " + TEXT + b" EMC", b"/Tx BMC " + TEXT + b" EMC"),
        (b"/Span BMC " + TEXT + b" EMC", b"/Span BMC " + TEXT + b" EMC"),
        (b"/P /MC0 BDC " + TEXT + b" EMC", b"/P /MC0 BDC " + TEXT + b" EMC"),
        (
            b"/Span <</Lang (en)>> BDC " + TEXT + b" EMC",
            b"/Span <</Lang (en)>> BDC " + TEXT + b" EMC",
        ),
        (
            b"/Span <</MCID 3 /ActualText (ff)>> BDC " + TEXT + b" EMC",
            b"/Span <</ActualText (ff)>> BDC " + TEXT + b" EMC",
        ),
        (  # nested: the kept mark keeps its EMC, the dropped ones lose theirs
            b"/Sect <</MCID 0>> BDC /OC /oc1 BDC /P <</MCID 1>> BDC " + TEXT + b" EMC EMC EMC",
            b"/OC /oc1 BDC " + TEXT + b" EMC",
        ),
        (b"EMC " + BOX, b"EMC " + BOX),  # an unbalanced EMC is left alone
        (b"/P <</MCID 0>> BDC " + BOX, BOX),  # never closed
        (b"/P BDC " + BOX + b" EMC", b"/P BDC " + BOX + b" EMC"),  # malformed, kept balanced
    ],
)
def test_marked_content(before, after):
    pdf = _tagged_pdf(before)
    delete_tags(pdf, [])
    assert _content(pdf.pages[0].obj) == _norm(after)


def test_an_untouched_stream_keeps_its_bytes():
    raw = b"0 0 1 rg   20 20 30 30 re f\n%comment\n"
    pdf = _tagged_pdf(raw)
    delete_tags(pdf, [])
    assert pdf.pages[0].obj.Contents.read_bytes() == raw


def test_a_mark_may_span_the_streams_of_a_page():
    pdf = _tagged_pdf(b"")
    parts = pikepdf.Array(
        [pdf.make_stream(b"/P <</MCID 0>> BDC " + BOX), pdf.make_stream(TEXT + b" EMC")]
    )
    pdf.pages[0].obj.Contents = parts
    second = pdf.add_blank_page(page_size=(100, 100))
    second.obj.Contents = parts
    delete_tags(pdf, [])
    first, other = (p.obj.Contents for p in pdf.pages)
    assert isinstance(first, pikepdf.Stream)
    assert first.objgen == other.objgen  # a shared array stays shared
    assert _norm(first.read_bytes()) == _norm(BOX + b" " + TEXT)


def test_a_stream_array_without_marks_is_kept():
    pdf = _tagged_pdf(b"")
    parts = pikepdf.Array([pdf.make_stream(BOX), pdf.make_stream(TEXT)])
    pdf.pages[0].obj.Contents = parts
    delete_tags(pdf, [])
    assert isinstance(pdf.pages[0].obj.Contents, pikepdf.Array)


def test_forms_patterns_and_appearances_are_unmarked():
    pdf = _tagged_pdf(b"/Fm0 Do /Pattern cs /P0 scn 0 0 100 100 re f")
    marked = b"/P <</MCID 0>> BDC " + BOX + b" EMC"
    form = pdf.make_stream(
        marked,
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Form,
        BBox=[0, 0, 100, 100],
        StructParents=4,
    )
    pattern = pdf.make_stream(
        marked, PatternType=1, PaintType=1, TilingType=1, BBox=[0, 0, 10, 10], XStep=10, YStep=10
    )
    appearance = pdf.make_stream(
        b"/Tx BMC " + marked + b" EMC",
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Form,
        BBox=[0, 0, 9, 9],
    )
    other = pdf.make_stream(marked)  # not painted content: left alone
    pdf.Root.Other = other
    res = pdf.pages[0].obj.Resources
    res.XObject = pikepdf.Dictionary(Fm0=form)
    res.Pattern = pikepdf.Dictionary(P0=pattern)
    pdf.pages[0].obj.Annots = pikepdf.Array(
        [
            pdf.make_indirect(
                pikepdf.Dictionary(
                    Subtype=pikepdf.Name.Widget,
                    Rect=[0, 0, 9, 9],
                    AP=pikepdf.Dictionary(N=appearance),
                )
            )
        ]
    )

    delete_tags(pdf, [])

    assert _norm(form.read_bytes()) == _norm(BOX)
    assert "/StructParents" not in form
    assert _norm(pattern.read_bytes()) == _norm(BOX)
    assert _norm(appearance.read_bytes()) == _norm(b"/Tx BMC " + BOX + b" EMC")
    assert other.read_bytes() == marked


def _render(pdf) -> list[bytes]:
    buf = io.BytesIO()
    pdf.save(buf)
    doc = pypdfium2.PdfDocument(buf.getvalue())
    return [page.render(scale=1).to_pil().tobytes() for page in doc]


def test_pages_look_the_same():
    content = (
        b"/Artifact BMC " + BOX + b" EMC /P <</MCID 0>> BDC " + TEXT + b" EMC "
        b"/OC /oc1 BDC 0 1 0 rg 60 60 20 20 re f EMC"
    )
    before = _render(_tagged_pdf(content))
    pdf = _tagged_pdf(content)
    delete_tags(pdf, [])
    assert _render(pdf) == before
    assert before != _render(_tagged_pdf(b""))  # the check can see a difference


def test_unparsable_stream_is_left_with_a_warning(monkeypatch, caplog):
    pdf = _tagged_pdf(b"/P <</MCID 0>> BDC " + BOX + b" EMC")

    def fail(_):
        raise pikepdf.PdfError("broken")

    monkeypatch.setattr(pikepdf, "parse_content_stream", fail)
    with caplog.at_level(logging.WARNING):
        delete_tags(pdf, [])
    assert "could not parse" in caplog.text
    assert b"MCID" in pdf.pages[0].obj.Contents.read_bytes()
    assert "/StructTreeRoot" not in pdf.Root


def test_untagged_pdf_is_fine():
    pdf = pikepdf.new()
    pdf.add_blank_page()
    result = delete_tags(pdf, [])
    assert result.success and result.pdf is pdf


def test_arguments_are_rejected():
    with pytest.raises(InvalidArgumentError, match="no arguments"):
        delete_tags(_tagged_pdf(TEXT), ["1-3"])


@pytest.mark.parametrize(
    "fields, claim",
    [
        ({"pdfuaid:part": "1"}, "PDF/UA"),
        ({"pdfaid:part": "2", "pdfaid:conformance": "A"}, "PDF/A level A"),
        (
            {"pdfuaid:part": "1", "pdfaid:part": "1", "pdfaid:conformance": "a"},
            "PDF/UA and PDF/A level A",
        ),
        ({"pdfaid:part": "2", "pdfaid:conformance": "B"}, None),
        ({}, None),
    ],
)
def test_lost_conformance_is_warned(fields, claim, caplog):
    pdf = _tagged_pdf(TEXT)
    with pdf.open_metadata() as meta:
        for key, value in fields.items():
            meta[key] = value
    with caplog.at_level(logging.WARNING):
        delete_tags(pdf, [])
    if claim is None:
        assert "claims" not in caplog.text
    else:
        assert f"claims {claim}, which" in caplog.text


def test_odd_shapes_are_passed_over():
    pdf = _tagged_pdf(BOX)
    blank = pdf.add_blank_page()
    del blank.obj["/Contents"]
    form = pdf.make_stream(
        BOX, Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form, BBox=[0, 0, 9, 9]
    )
    pdf.Root.Extra = pdf.make_indirect(pikepdf.Array([form]))
    pdf.pages[0].obj.Annots = pikepdf.Array([None])
    delete_tags(pdf, [])
    assert form.read_bytes() == BOX
    assert "/Contents" not in pdf.pages[1].obj
    assert list(pdf.pages[0].obj.Annots) == [None]
