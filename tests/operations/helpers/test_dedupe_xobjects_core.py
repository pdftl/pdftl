# tests/operations/helpers/test_dedupe_xobjects_core.py

from __future__ import annotations

import io

import pikepdf
import pymupdf
import pytest
from pikepdf import Array, Dictionary, Name

from pdftl.operations.helpers.dedupe_xobjects_core import deduplicate_form_xobjects

SQUARE = b"0 0 1 rg 10 10 30 30 re f"


@pytest.fixture
def pdf():
    p = pikepdf.Pdf.new()
    yield p
    p.close()


def _form(pdf, data=SQUARE, **extra):
    extra.setdefault("Resources", Dictionary())
    extra.setdefault("BBox", Array([0, 0, 100, 100]))
    return pdf.make_indirect(
        pikepdf.Stream(pdf, data, Type=Name.XObject, Subtype=Name.Form, **extra)
    )


def _page(pdf, xobjects, content=b"/Fm0 Do"):
    page = pdf.add_blank_page(page_size=(100, 100))
    page.Resources = Dictionary(XObject=Dictionary(xobjects))
    page.Contents = pdf.make_stream(content)
    return page


def _og(page, name="/Fm0"):
    return page.Resources.XObject[name].objgen


def _renders(pdf):
    buf = io.BytesIO()
    pdf.save(buf)
    doc = pymupdf.open(stream=buf.getvalue(), filetype="pdf")
    return [page.get_pixmap(dpi=72).samples for page in doc]


# --- merged ---


def test_identical_drawn_forms_merge_and_render_the_same(pdf):
    pages = [_page(pdf, {"/Fm0": _form(pdf)}) for _ in range(3)]
    before = _renders(pdf)

    result = deduplicate_form_xobjects(pdf)

    assert result == {"merged": 2, "bytes_saved": 2 * len(SQUARE)}
    assert len({_og(p) for p in pages}) == 1
    assert _renders(pdf) == before
    assert before[0] != _renders_blank()  # the square really is drawn


def _renders_blank():
    blank = pikepdf.new()
    blank.add_blank_page(page_size=(100, 100))
    return _renders(blank)[0]


def test_nested_forms_merge_at_both_levels(pdf):
    pages = []
    for _ in range(2):
        inner = _form(pdf, b"1 0 0 rg 0 0 5 5 re f")
        outer = _form(pdf, b"/In Do", Resources=Dictionary(XObject=Dictionary(In=inner)))
        pages.append(_page(pdf, {"/Fm0": outer}))
    before = _renders(pdf)

    assert deduplicate_form_xobjects(pdf)["merged"] == 2

    outers = {p.Resources.XObject.Fm0.objgen for p in pages}
    inners = {p.Resources.XObject.Fm0.Resources.XObject.In.objgen for p in pages}
    assert len(outers) == len(inners) == 1
    assert _renders(pdf) == before


def test_resources_inherited_from_pages_node_count_as_page_owners(pdf):
    page1 = _page(pdf, {"/Fm0": _form(pdf)})
    del page1.obj["/Resources"]
    pdf.Root.Pages.Resources = Dictionary(XObject=Dictionary(Fm0=_form(pdf)))
    page2 = _page(pdf, {"/Fm0": _form(pdf)})

    assert deduplicate_form_xobjects(pdf)["merged"] == 1
    assert pdf.Root.Pages.Resources.XObject.Fm0.objgen == _og(page2)


def test_indirect_resources_and_xobject_dicts(pdf):
    shared_xobjects = pdf.make_indirect(Dictionary(Fm0=_form(pdf)))
    page1 = _page(pdf, {})
    page1.Resources = pdf.make_indirect(Dictionary(XObject=shared_xobjects))
    page2 = _page(pdf, {"/Fm0": _form(pdf)})

    assert deduplicate_form_xobjects(pdf)["merged"] == 1
    assert _og(page1) == _og(page2)


def test_threshold_skips_small_forms(pdf):
    pages = [_page(pdf, {"/Fm0": _form(pdf)}) for _ in range(2)]
    assert deduplicate_form_xobjects(pdf, threshold=len(SQUARE) + 1)["merged"] == 0
    assert len({_og(p) for p in pages}) == 2


# --- not merged ---


@pytest.mark.parametrize(
    "extra",
    [
        {"Resources": Dictionary(ExtGState=Dictionary(G=Dictionary(CA=0.5)))},
        {"Group": Dictionary(S=Name.Transparency, K=True)},
        {"Matrix": Array([2, 0, 0, 2, 0, 0])},
        {"BBox": Array([0, 0, 50, 50])},
    ],
    ids=["resources", "group", "matrix", "bbox"],
)
def test_forms_differing_in_their_dictionary_stay_apart(pdf, extra):
    page1 = _page(pdf, {"/Fm0": _form(pdf)})
    page2 = _page(pdf, {"/Fm0": _form(pdf, **extra)})
    assert deduplicate_form_xobjects(pdf) == {"merged": 0, "bytes_saved": 0}
    assert _og(page1) != _og(page2)


def test_forms_with_different_bytes_stay_apart(pdf):
    page1 = _page(pdf, {"/Fm0": _form(pdf)})
    page2 = _page(pdf, {"/Fm0": _form(pdf, SQUARE.replace(b"30 30", b"31 29"))})
    assert deduplicate_form_xobjects(pdf)["merged"] == 0
    assert _og(page1) != _og(page2)


@pytest.mark.parametrize("key", ["/StructParent", "/StructParents"])
def test_forms_in_the_structure_tree_stay_apart(pdf, key):
    pages = [_page(pdf, {"/Fm0": _form(pdf, **{key[1:]: 0})}) for _ in range(2)]
    assert deduplicate_form_xobjects(pdf)["merged"] == 0
    assert len({_og(p) for p in pages}) == 2


def _widget(pdf, page, appearance):
    annot = pdf.make_indirect(
        Dictionary(
            Type=Name.Annot,
            Subtype=Name.Widget,
            Rect=Array([0, 0, 100, 100]),
            AP=Dictionary(N=appearance),
        )
    )
    page.Annots = pdf.make_indirect(Array([annot]))
    return annot


def _link(pdf, page, ap):
    annot = pdf.make_indirect(
        Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=Array([0, 0, 100, 100]), AP=ap)
    )
    page.Annots = pdf.make_indirect(Array([annot]))
    return annot


def _pdfium_renders(pdf):
    import pypdfium2

    buf = io.BytesIO()
    pdf.save(buf)
    doc = pypdfium2.PdfDocument(buf.getvalue())
    try:  # MuPDF does not draw Link appearances; pdfium does
        return [p.render(draw_annots=True).to_pil().tobytes() for p in doc]
    finally:
        doc.close()


def test_link_appearances_merge_and_render_the_same(pdf):
    links = []
    for _ in range(3):
        page = _page(pdf, {}, content=b"")
        links.append(_link(pdf, page, Dictionary(N=_form(pdf))))
    before = _pdfium_renders(pdf)
    assert deduplicate_form_xobjects(pdf)["merged"] == 2
    assert len({link.AP.N.objgen for link in links}) == 1
    assert _pdfium_renders(pdf) == before
    blank = pikepdf.new()
    blank.add_blank_page(page_size=(100, 100))
    assert before[0] != _pdfium_renders(blank)[0]  # the appearance really is drawn


def test_link_appearance_states_merge(pdf):
    links = []
    for _ in range(2):
        page = _page(pdf, {}, content=b"")
        states = Dictionary(On=_form(pdf), Off=_form(pdf, b"0 g 0 0 5 5 re f"))
        links.append(_link(pdf, page, Dictionary(N=states)))
    assert deduplicate_form_xobjects(pdf)["merged"] == 2
    assert links[0].AP.N.On.objgen == links[1].AP.N.On.objgen
    assert links[0].AP.N.Off.objgen == links[1].AP.N.Off.objgen


def test_link_with_an_indirect_appearance_dictionary_is_not_merged(pdf):
    links = []
    for _ in range(2):
        page = _page(pdf, {}, content=b"")
        links.append(_link(pdf, page, pdf.make_indirect(Dictionary(N=_form(pdf)))))
    assert deduplicate_form_xobjects(pdf)["merged"] == 0


def test_link_appearance_also_used_by_a_widget_is_not_merged(pdf):
    shared = _form(pdf)
    page1 = _page(pdf, {}, content=b"")
    link = _link(pdf, page1, Dictionary(N=shared))
    page2 = _page(pdf, {}, content=b"")
    _widget(pdf, page2, shared)
    page3 = _page(pdf, {}, content=b"")
    other = _link(pdf, page3, Dictionary(N=_form(pdf)))
    assert deduplicate_form_xobjects(pdf)["merged"] == 0
    assert link.AP.N.objgen != other.AP.N.objgen


def test_appearance_streams_are_never_merged(pdf):
    page1 = _page(pdf, {}, content=b"")
    page2 = _page(pdf, {}, content=b"")
    a1 = _widget(pdf, page1, _form(pdf))
    a2 = _widget(pdf, page2, _form(pdf))
    assert deduplicate_form_xobjects(pdf)["merged"] == 0
    assert a1.AP.N.objgen != a2.AP.N.objgen


def test_a_form_also_used_as_an_appearance_is_not_merged(pdf):
    shared = _form(pdf)
    page1 = _page(pdf, {"/Fm0": shared})
    _widget(pdf, page1, shared)
    page2 = _page(pdf, {"/Fm0": _form(pdf)})
    assert deduplicate_form_xobjects(pdf)["merged"] == 0
    assert _og(page1) != _og(page2)


def test_forms_drawn_inside_an_appearance_are_not_merged(pdf):
    nested = _form(pdf)
    appearance = _form(pdf, b"/Fm0 Do", Resources=Dictionary(XObject=Dictionary(Fm0=nested)))
    page1 = _page(pdf, {}, content=b"")
    _widget(pdf, page1, appearance)
    page2 = _page(pdf, {"/Fm0": _form(pdf)})
    assert deduplicate_form_xobjects(pdf)["merged"] == 0
    assert nested.objgen != _og(page2)


def test_resources_shared_by_a_page_and_an_appearance_are_not_merged(pdf):
    shared_resources = pdf.make_indirect(Dictionary(XObject=Dictionary(Fm0=_form(pdf))))
    page1 = _page(pdf, {})
    page1.Resources = shared_resources
    _widget(pdf, page1, _form(pdf, b"/Fm0 Do", Resources=shared_resources))
    page2 = _page(pdf, {"/Fm0": _form(pdf)})
    assert deduplicate_form_xobjects(pdf)["merged"] == 0
    assert _og(page1) != _og(page2)


def test_soft_mask_groups_are_not_merged(pdf):
    mask = _form(pdf, Group=Dictionary(S=Name.Transparency, CS=Name.DeviceGray))
    page1 = _page(pdf, {"/Fm0": _form(pdf)})
    page1.Resources.ExtGState = Dictionary(
        GS0=Dictionary(SMask=Dictionary(S=Name.Luminosity, G=mask))
    )
    page2 = _page(pdf, {"/Fm0": _form(pdf, Group=mask.Group)})
    assert deduplicate_form_xobjects(pdf)["merged"] == 0
    assert page1.Resources.ExtGState.GS0.SMask.G.objgen == mask.objgen
    assert _og(page2) != mask.objgen


def test_forms_drawn_from_a_pattern_are_not_merged(pdf):
    in_pattern = _form(pdf)
    pattern = pdf.make_indirect(
        pikepdf.Stream(
            pdf,
            b"/Fm0 Do",
            PatternType=1,
            PaintType=1,
            TilingType=1,
            BBox=Array([0, 0, 50, 50]),
            XStep=50,
            YStep=50,
            Resources=Dictionary(XObject=Dictionary(Fm0=in_pattern)),
        )
    )
    page1 = _page(pdf, {"/Fm0": _form(pdf)})
    page1.Resources.Pattern = Dictionary(P0=pattern)
    assert deduplicate_form_xobjects(pdf)["merged"] == 0
    assert in_pattern.objgen != _og(page1)


def test_forms_in_a_structure_object_reference_are_not_merged(pdf):
    referenced = _form(pdf)
    page1 = _page(pdf, {"/Fm0": referenced})
    page2 = _page(pdf, {"/Fm0": _form(pdf)})
    pdf.Root.StructTreeRoot = Dictionary(
        Type=Name.StructTreeRoot,
        K=Array([Dictionary(Type=Name.OBJR, Obj=referenced, Pg=page1.obj)]),
    )
    assert deduplicate_form_xobjects(pdf)["merged"] == 0
    assert _og(page1) != _og(page2)


def test_a_self_referencing_form_is_left_alone(pdf):
    loop1 = _form(pdf, b"/Me Do")
    loop1.Resources = Dictionary(XObject=Dictionary(Me=loop1))
    loop2 = _form(pdf, b"/Me Do")
    loop2.Resources = Dictionary(XObject=Dictionary(Me=loop2))
    loop_owner = pdf.make_indirect(Dictionary(Resources=Dictionary(XObject=Dictionary(A=loop1))))
    pdf.Root.Unrelated = Array([loop_owner, loop2])
    assert deduplicate_form_xobjects(pdf)["merged"] == 0


def test_non_dictionary_resources_are_ignored(pdf):
    page1 = _page(pdf, {"/Fm0": _form(pdf)})
    page2 = _page(pdf, {"/Fm0": _form(pdf)})
    odd = _page(pdf, {})
    odd.Resources = Array([1])
    odd2 = _page(pdf, {})
    odd2.Resources.XObject = Array([1])
    assert deduplicate_form_xobjects(pdf)["merged"] == 1
    assert _og(page1) == _og(page2)


def test_only_forms_are_candidates(pdf):
    def image():
        return pdf.make_indirect(
            pikepdf.Stream(
                pdf,
                b"\x00" * 4,
                Type=Name.XObject,
                Subtype=Name.Image,
                Width=2,
                Height=2,
                BitsPerComponent=8,
                ColorSpace=Name.DeviceGray,
            )
        )

    page = _page(pdf, {"/Im0": image()}, content=b"/Im0 Do")
    page2 = _page(pdf, {"/Im0": image()}, content=b"/Im0 Do")
    assert deduplicate_form_xobjects(pdf)["merged"] == 0
    assert page.Resources.XObject.Im0.objgen != page2.Resources.XObject.Im0.objgen


def test_forms_drawn_from_a_type3_glyph_are_not_merged(pdf):
    in_glyph = _form(pdf)
    type3 = Dictionary(  # direct, inside the page's font resources
        Type=Name.Font,
        Subtype=Name.Type3,
        FontBBox=Array([0, 0, 100, 100]),
        FontMatrix=Array([0.01, 0, 0, 0.01, 0, 0]),
        CharProcs=Dictionary(a=pdf.make_stream(b"100 0 d0 /Fm0 Do")),
        Encoding=Dictionary(Differences=Array([97, Name.a])),
        FirstChar=97,
        LastChar=97,
        Widths=Array([100]),
        Resources=Dictionary(XObject=Dictionary(Fm0=in_glyph)),
    )
    page1 = _page(pdf, {"/Fm0": _form(pdf)})
    page1.Resources.Font = Dictionary(T3=type3)
    assert deduplicate_form_xobjects(pdf)["merged"] == 0
    assert in_glyph.objgen != _og(page1)


def test_indirect_scalars_are_skipped(pdf):
    pdf.Root.Count = pdf.make_indirect(7)
    pages = [_page(pdf, {"/Fm0": _form(pdf)}) for _ in range(2)]
    assert deduplicate_form_xobjects(pdf)["merged"] == 1
    assert _og(pages[0]) == _og(pages[1])


def test_forms_differing_only_in_their_name_merge_and_render_the_same(pdf):
    page1 = _page(pdf, {"/Fm0": _form(pdf, Name=Name.Fm0)})
    page2 = _page(pdf, {"/Fm0": _form(pdf, Name=Name.Fm1)})
    before = _renders(pdf)
    assert deduplicate_form_xobjects(pdf)["merged"] == 1
    assert _og(page1) == _og(page2)
    assert _renders(pdf) == before
