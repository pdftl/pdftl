# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/utils/test_mrc_classify.py

import pikepdf
import pytest

from pdftl.utils.mrc import classify

PAGE_W, PAGE_H = 612, 792
IMG_W, IMG_H = 850, 1100
FULL = f"q {PAGE_W} 0 0 {PAGE_H} 0 0 cm /Im0 Do Q\n"

NOT_SCAN = "this page is not a scanned image"
MORE = "this page draws more than a scanned image"


def _image(pdf, width=IMG_W, height=IMG_H, **extra):
    img = pikepdf.Stream(pdf, b"\x00" * 16)
    img.Type = pikepdf.Name.XObject
    img.Subtype = pikepdf.Name.Image
    img.Width = width
    img.Height = height
    img.ColorSpace = pikepdf.Name.DeviceRGB
    img.BitsPerComponent = 8
    for key, value in extra.items():
        img[f"/{key}"] = value
    return pdf.make_indirect(img)


def _form(pdf, content, resources=None, matrix=None):
    form = pikepdf.Stream(pdf, content.encode())
    form.Type = pikepdf.Name.XObject
    form.Subtype = pikepdf.Name.Form
    form.BBox = [0, 0, PAGE_W, PAGE_H]
    if resources is not None:
        form.Resources = resources
    if matrix is not None:
        form.Matrix = matrix
    return pdf.make_indirect(form)


def _page(pdf, content, xobjects=None, *, resources=None, **boxes):
    if resources is None:
        resources = pikepdf.Dictionary()
        if xobjects is not None:
            resources.XObject = pikepdf.Dictionary(xobjects)
    page_dict = pikepdf.Dictionary(
        Type=pikepdf.Name.Page,
        MediaBox=boxes.pop("MediaBox", [0, 0, PAGE_W, PAGE_H]),
        Contents=pdf.make_stream(content.encode()),
    )
    if resources is not False:
        page_dict.Resources = resources
    for key, value in boxes.items():
        page_dict[f"/{key}"] = value
    pdf.pages.append(pikepdf.Page(pdf.make_indirect(page_dict)))
    return pdf.pages[-1]


def _scan(pdf, content=FULL, image=None, extra_xobjects=None, **boxes):
    xobjects = {"/Im0": image if image is not None else _image(pdf)}
    xobjects.update(extra_xobjects or {})
    return _page(pdf, content, xobjects, **boxes)


@pytest.fixture
def pdf():
    with pikepdf.Pdf.new() as doc:
        yield doc


def _reason(pdf, page):
    candidate, reason = classify.classify_page(pdf, page, 1)
    if reason == "mrc":
        assert candidate is not None
    else:
        assert candidate is None
    return reason


class TestEligible:
    def test_full_page_scan_candidate_fields(self, pdf):
        img = _image(pdf)
        page = _scan(pdf, image=img)
        candidate, reason = classify.classify_page(pdf, page, 7)
        assert reason == "mrc"
        assert candidate.page_number == 7
        assert candidate.xobj_name == "/Im0"
        assert candidate.xobj.objgen == img.objgen
        assert candidate.rect == (0, 0, 612, 792)
        assert candidate.matrix == (612, 0, 0, 792, 0, 0)
        assert (candidate.width, candidate.height) == (850, 1100)
        # 850 px over 612 pt = 8.5 in -> 100 dpi.
        assert candidate.source_dpi == 100

    def test_offset_placement_with_nested_cm(self, pdf):
        # 1 0 0 1 10 20 then 600 0 0 780: image spans x 10..610, y 20..800.
        content = "q 1 0 0 1 10 20 cm q 600 0 0 780 0 0 cm /Im0 Do Q Q"
        candidate, reason = classify.classify_page(pdf, _scan(pdf, content), 1)
        assert reason == "mrc"
        assert candidate.rect == (10, 20, 610, 800)
        assert candidate.matrix == (600, 0, 0, 780, 10, 20)
        # 850 px over 600 pt -> 102 dpi.
        assert candidate.source_dpi == 102

    def test_invisible_ocr_text_is_allowed(self, pdf):
        content = FULL + "BT 3 Tr /F1 12 Tf 100 650 Td (ocr) Tj [(a)] TJ (b) ' 1 2 (c) \" ET"
        assert _reason(pdf, _scan(pdf, content)) == "mrc"

    def test_tr_state_restored_by_Q(self, pdf):
        # 3 Tr set outside BT survives only until BT resets it; here the
        # Q restores render mode 3 set inside q, but no text follows.
        content = FULL + "q 3 Tr Q BT 3 Tr (x) Tj ET"
        assert _reason(pdf, _scan(pdf, content)) == "mrc"

    def test_ignored_operators_and_stray_Q(self, pdf):
        content = "Q Q 0 0 1 rg 5 w 0 0 m 10 10 l n " + FULL
        assert _reason(pdf, _scan(pdf, content)) == "mrc"

    def test_clip_released_by_Q(self, pdf):
        content = "q 0 0 10 10 re W n Q " + FULL
        assert _reason(pdf, _scan(pdf, content)) == "mrc"

    def test_clipped_second_image_is_ignored(self, pdf):
        content = FULL + "q 0 0 10 10 re W* n 10 0 0 10 0 0 cm /Im1 Do Q"
        page = _scan(pdf, content, extra_xobjects={"/Im1": _image(pdf)})
        assert _reason(pdf, page) == "mrc"

    def test_blank_form_is_allowed(self, pdf):
        form = _form(pdf, "q Q")
        page = _scan(pdf, FULL + "/Fm0 Do", extra_xobjects={"/Fm0": form})
        assert _reason(pdf, page) == "mrc"

    def test_ninety_percent_coverage_is_enough(self, pdf):
        # 612 * 712.8 / (612 * 792) = 0.9 exactly.
        content = "q 612 0 0 712.8 0 0 cm /Im0 Do Q"
        assert _reason(pdf, _scan(pdf, content)) == "mrc"

    def test_tiny_skew_within_tolerance(self, pdf):
        # 0.5 / 792 is below the 1e-3 tolerance.
        content = "q 612 0.5 0.5 792 0 0 cm /Im0 Do Q"
        assert _reason(pdf, _scan(pdf, content)) == "mrc"

    def test_image_colour_spaces_that_are_fine(self, pdf):
        icc = pdf.make_stream(b"", N=3)
        for cs in (pikepdf.Array([pikepdf.Name.ICCBased, icc]), pikepdf.Array()):
            page = _scan(pdf, image=_image(pdf, ColorSpace=cs))
            assert _reason(pdf, page) == "mrc"

    def test_image_without_colour_space(self, pdf):
        img = _image(pdf)
        del img["/ColorSpace"]
        assert _reason(pdf, _scan(pdf, image=img)) == "mrc"

    def test_16_bit_image_is_fine(self, pdf):
        page = _scan(pdf, image=_image(pdf, BitsPerComponent=16))
        assert _reason(pdf, page) == "mrc"

    def test_unparseable_bpc_treated_as_8(self, pdf):
        page = _scan(pdf, image=_image(pdf, BitsPerComponent=pikepdf.Name.Eight))
        assert _reason(pdf, page) == "mrc"

    def test_dpi_floor_is_one(self, pdf):
        # 2 px over 612 pt rounds to 0 dpi, floored at 1.
        candidate, reason = classify.classify_page(
            pdf, _scan(pdf, image=_image(pdf, width=2, height=2)), 1
        )
        assert reason == "mrc"
        assert candidate.source_dpi == 1


class TestNotAScan:
    def test_blank_page(self, pdf):
        assert _reason(pdf, _page(pdf, "")) == NOT_SCAN

    def test_two_images(self, pdf):
        content = FULL + FULL.replace("/Im0", "/Im1")
        page = _scan(pdf, content, extra_xobjects={"/Im1": _image(pdf)})
        assert _reason(pdf, page) == NOT_SCAN

    def test_only_image_is_clipped(self, pdf):
        assert _reason(pdf, _scan(pdf, "q 0 0 612 792 re W n " + FULL + "Q")) == NOT_SCAN

    def test_clip_persists_until_Q(self, pdf):
        content = "q 0 0 612 792 re W n q " + FULL + "Q Q"
        assert _reason(pdf, _scan(pdf, content)) == NOT_SCAN

    def test_low_coverage(self, pdf):
        # 612 * 700 / (612 * 792) ~ 0.88.
        assert _reason(pdf, _scan(pdf, "q 612 0 0 700 0 0 cm /Im0 Do Q")) == NOT_SCAN

    def test_image_without_cm_is_one_point(self, pdf):
        assert _reason(pdf, _scan(pdf, "/Im0 Do")) == NOT_SCAN

    @pytest.mark.parametrize("w, h", [(1, 1100), (850, 1), (0, 0)])
    def test_tiny_image(self, pdf, w, h):
        assert _reason(pdf, _scan(pdf, image=_image(pdf, width=w, height=h))) == NOT_SCAN

    def test_image_without_dimensions(self, pdf):
        img = _image(pdf)
        del img["/Width"]
        del img["/Height"]
        assert _reason(pdf, _scan(pdf, image=img)) == NOT_SCAN

    def test_missing_xobject_resource(self, pdf):
        assert _reason(pdf, _page(pdf, FULL, {"/Other": _image(pdf)})) == NOT_SCAN

    def test_no_xobject_dictionary(self, pdf):
        assert _reason(pdf, _page(pdf, FULL)) == NOT_SCAN

    def test_no_resources_anywhere(self, pdf):
        assert _reason(pdf, _page(pdf, FULL, resources=False)) == NOT_SCAN

    def test_xobject_entry_not_a_dictionary(self, pdf):
        res = pikepdf.Dictionary(XObject=5)
        assert _reason(pdf, _page(pdf, FULL, resources=res)) == NOT_SCAN

    def test_do_without_operand(self, pdf):
        assert _reason(pdf, _scan(pdf, "q 612 0 0 792 0 0 cm Do Q")) == NOT_SCAN


class TestPlacement:
    @pytest.mark.parametrize(
        "matrix",
        [
            "0 792 -612 0 612 0",  # rotated 90
            "-612 0 0 792 612 0",  # mirrored horizontally
            "612 0 0 -792 0 792",  # flipped vertically
            "612 10 0 792 0 0",  # skewed
            "612 0 10 792 0 0",  # skewed the other way
        ],
    )
    def test_not_upright(self, pdf, matrix):
        page = _scan(pdf, f"q {matrix} cm /Im0 Do Q")
        assert _reason(pdf, page) == "the page image placement is rotated or skewed"

    def test_malformed_cm_is_ignored(self, pdf):
        # The bad cm is skipped, so the image is drawn at 1x1 pt.
        page = _scan(pdf, "q /a /b /c /d /e /f cm /Im0 Do Q")
        assert _reason(pdf, page) == NOT_SCAN

    def test_malformed_cm_keeps_earlier_ctm(self, pdf):
        page = _scan(pdf, "q 612 0 0 792 0 0 cm /x cm /Im0 Do Q")
        assert _reason(pdf, page) == "mrc"


class TestPageBox:
    def test_cropbox_preferred_and_normalised(self, pdf):
        # CropBox given reversed; covers the image exactly.
        page = _scan(
            pdf,
            "q 300 0 0 400 0 0 cm /Im0 Do Q",
            CropBox=[300, 400, 0, 0],
            MediaBox=[0, 0, 612, 792],
        )
        assert classify.page_box(page) == (0, 0, 300, 400)
        assert _reason(pdf, page) == "mrc"

    def test_mediabox_used_without_cropbox(self, pdf):
        page = _page(pdf, "", MediaBox=[0, 0, 100, 200])
        assert classify.page_box(page) == (0, 0, 100, 200)

    def test_malformed_box_falls_back_to_letter(self, pdf):
        page = _page(pdf, "", MediaBox=[0, 0, pikepdf.Name.x, 1])
        assert classify.page_box(page) == (0, 0, 612, 792)

    def test_missing_box_falls_back_to_letter(self, pdf):
        page = _page(pdf, "")
        del page.obj["/MediaBox"]
        assert classify.page_box(page) == (0, 0, 612, 792)


class TestImageRefusal:
    def test_soft_mask(self, pdf):
        smask = _image(pdf, ColorSpace=pikepdf.Name.DeviceGray)
        page = _scan(pdf, image=_image(pdf, SMask=smask))
        assert _reason(pdf, page) == "the page image carries a soft mask"

    def test_mask(self, pdf):
        page = _scan(pdf, image=_image(pdf, Mask=pikepdf.Array([0, 10, 0, 10, 0, 10])))
        assert _reason(pdf, page) == "the page image is already masked"

    def test_one_bit(self, pdf):
        page = _scan(pdf, image=_image(pdf, BitsPerComponent=1))
        assert _reason(pdf, page) == "the page image is already 1-bit"

    def test_four_bit(self, pdf):
        page = _scan(pdf, image=_image(pdf, BitsPerComponent=4))
        assert _reason(pdf, page) == "the page image is already 1-bit"

    def test_image_mask(self, pdf):
        img = _image(pdf, ImageMask=True)
        del img["/BitsPerComponent"]
        del img["/ColorSpace"]
        assert _reason(pdf, _scan(pdf, image=img)) == "the page image is already 1-bit"

    def test_indexed(self, pdf):
        cs = pikepdf.Array([pikepdf.Name.Indexed, pikepdf.Name.DeviceRGB, 1, b"\x00" * 6])
        page = _scan(pdf, image=_image(pdf, ColorSpace=cs))
        assert _reason(pdf, page) == "the page image uses an indexed colour space"

    def test_refusal_checked_before_visible_content(self, pdf):
        page = _scan(pdf, FULL + "0 0 10 10 re f", image=_image(pdf, BitsPerComponent=1))
        assert _reason(pdf, page) == "the page image is already 1-bit"


class TestVisibleContent:
    @pytest.mark.parametrize("op", sorted(classify._PAINT_OPS - {"sh"}))
    def test_vector_paint(self, pdf, op):
        page = _scan(pdf, FULL + f"0 0 10 10 re {op}")
        assert _reason(pdf, page) == MORE

    def test_shading(self, pdf):
        assert _reason(pdf, _scan(pdf, FULL + "/Sh0 sh")) == MORE

    def test_inline_image(self, pdf):
        content = FULL + "BI /W 1 /H 1 /CS /G /BPC 8 ID \x80 EI"
        assert _reason(pdf, _scan(pdf, content)) == MORE

    @pytest.mark.parametrize("mode", [0, 1, 2, 4, 7])
    def test_visible_render_modes(self, pdf, mode):
        page = _scan(pdf, FULL + f"BT {mode} Tr (x) Tj ET")
        assert _reason(pdf, page) == MORE

    def test_default_render_mode_is_visible(self, pdf):
        assert _reason(pdf, _scan(pdf, FULL + "BT (x) Tj ET")) == MORE

    @pytest.mark.parametrize("show", ["[(x)] TJ", "(x) '", '1 2 (x) "'])
    def test_other_show_operators(self, pdf, show):
        assert _reason(pdf, _scan(pdf, FULL + f"BT {show} ET")) == MORE

    def test_bt_resets_render_mode(self, pdf):
        page = _scan(pdf, FULL + "3 Tr BT (x) Tj ET")
        assert _reason(pdf, page) == MORE

    def test_Q_restores_visible_render_mode(self, pdf):
        page = _scan(pdf, FULL + "BT q 3 Tr Q (x) Tj ET")
        assert _reason(pdf, page) == MORE

    @pytest.mark.parametrize("tr", ["Tr", "/x Tr"])
    def test_malformed_tr_is_ignored(self, pdf, tr):
        page = _scan(pdf, FULL + f"BT 3 Tr {tr} (x) Tj ET")
        assert _reason(pdf, page) == "mrc"
        page = _scan(pdf, FULL + f"BT {tr} (x) Tj ET")
        assert _reason(pdf, page) == MORE

    def test_postscript_xobject(self, pdf):
        ps = pdf.make_indirect(
            pikepdf.Stream(pdf, b"", Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.PS)
        )
        page = _scan(pdf, FULL + "/P0 Do", extra_xobjects={"/P0": ps})
        assert _reason(pdf, page) == MORE

    def test_xobject_without_subtype(self, pdf):
        odd = pdf.make_indirect(pikepdf.Stream(pdf, b""))
        page = _scan(pdf, FULL + "/X0 Do", extra_xobjects={"/X0": odd})
        assert _reason(pdf, page) == MORE

    def test_unparseable_page_content(self, pdf):
        page = _scan(pdf)
        page.obj.Contents = 5
        assert _reason(pdf, page) == NOT_SCAN

    def test_unparseable_form_content(self, pdf):
        form = pdf.make_indirect(pikepdf.Dictionary(Subtype=pikepdf.Name.Form))
        page = _scan(pdf, FULL + "/Fm0 Do", extra_xobjects={"/Fm0": form})
        assert _reason(pdf, page) == MORE


class TestForms:
    def test_image_inside_form(self, pdf):
        img = _image(pdf)
        form = _form(pdf, FULL, pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=img)))
        page = _page(pdf, "/Fm0 Do", {"/Fm0": form})
        assert _reason(pdf, page) == "the page image is drawn inside a form"

    def test_form_falls_back_to_page_resources(self, pdf):
        # The form has no /Resources; /Im0 comes from the page.
        form = _form(pdf, FULL)
        page = _page(pdf, "/Fm0 Do", {"/Fm0": form, "/Im0": _image(pdf)})
        assert _reason(pdf, page) == "the page image is drawn inside a form"

    def test_form_resources_fall_back_to_page(self, pdf):
        # The form has /Resources without /Im0; lookup falls back to the page.
        form = _form(pdf, FULL, pikepdf.Dictionary())
        page = _page(pdf, "/Fm0 Do", {"/Fm0": form, "/Im0": _image(pdf)})
        assert _reason(pdf, page) == "the page image is drawn inside a form"

    def test_visible_content_inside_form(self, pdf):
        form = _form(pdf, "0 0 10 10 re f")
        page = _scan(pdf, FULL + "/Fm0 Do", extra_xobjects={"/Fm0": form})
        assert _reason(pdf, page) == MORE

    def test_visible_text_in_nested_form(self, pdf):
        inner = _form(pdf, "BT (x) Tj ET")
        outer = _form(pdf, "/Fm1 Do", pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fm1=inner)))
        page = _scan(pdf, FULL + "/Fm0 Do", extra_xobjects={"/Fm0": outer})
        assert _reason(pdf, page) == MORE

    def _chain(self, pdf, levels):
        # `levels` nested empty forms; each form calls the next one.
        form = _form(pdf, "")
        for _ in range(levels - 1):
            form = _form(pdf, "/Fm Do", pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fm=form)))
        return _scan(pdf, FULL + "/Fm Do", extra_xobjects={"/Fm": form})

    def test_four_levels_of_empty_forms_allowed(self, pdf):
        assert _reason(pdf, self._chain(pdf, 4)) == "mrc"

    def test_five_levels_of_forms_count_as_visible(self, pdf):
        assert _reason(pdf, self._chain(pdf, 5)) == MORE

    def test_self_recursive_form_terminates(self, pdf):
        form = _form(pdf, "/Fm Do")
        form.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fm=form))
        page = _scan(pdf, FULL + "/Fm Do", extra_xobjects={"/Fm": form})
        assert _reason(pdf, page) == MORE

    def test_form_matrix_applies_to_nested_image(self, pdf):
        img = _image(pdf)
        form = _form(
            pdf,
            "/Im0 Do",
            pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=img)),
            matrix=[612, 0, 0, 792, 0, 0],
        )
        page = _page(pdf, "q 1 0 0 1 5 6 cm /Fm0 Do Q", {"/Fm0": form})
        walker = classify._Walker()
        walker.walk(pdf, page, classify.resolve_resources(page), None, classify.IDENTITY, 0)
        (placement,) = walker.placements
        assert placement["matrix"] == (612, 0, 0, 792, 5, 6)
        assert placement["rect"] == (5, 6, 617, 798)
        assert placement["nested"] is True
        assert placement["clipped"] is False

    def test_malformed_form_matrix_is_identity(self, pdf):
        img = _image(pdf)
        form = _form(
            pdf,
            "/Im0 Do",
            pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=img)),
            matrix=[pikepdf.Name.a, 0, 0, 1, 0, 0],
        )
        page = _page(pdf, "q 2 0 0 3 0 0 cm /Fm0 Do Q", {"/Fm0": form})
        walker = classify._Walker()
        walker.walk(pdf, page, classify.resolve_resources(page), None, classify.IDENTITY, 0)
        (placement,) = walker.placements
        assert placement["matrix"] == (2, 0, 0, 3, 0, 0)


class TestResources:
    def _pages_node(self, pdf, **entries):
        return pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Pages, **entries))

    def test_inherited_from_parent(self, pdf):
        img = _image(pdf)
        res = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=img))
        page = _page(pdf, FULL, resources=False)
        page.obj.Parent = self._pages_node(pdf, Resources=res)
        assert _reason(pdf, page) == "mrc"

    def test_inherited_from_grandparent(self, pdf):
        res = pikepdf.Dictionary(Marker=1)
        page = _page(pdf, "", resources=False)
        page.obj.Parent = self._pages_node(pdf, Parent=self._pages_node(pdf, Resources=res))
        assert classify.resolve_resources(page).Marker == 1

    def test_inherited_from_great_grandparent(self, pdf):
        res = pikepdf.Dictionary(Marker=1)
        node = self._pages_node(pdf, Resources=res)
        for _ in range(2):
            node = self._pages_node(pdf, Parent=node)
        page = _page(pdf, "", resources=False)
        page.obj.Parent = node
        assert classify.resolve_resources(page) is not None

    def test_inherited_through_a_direct_parent(self, pdf):
        page = _page(pdf, "", resources=False)
        page.obj.Parent = pikepdf.Dictionary(Resources=pikepdf.Dictionary(Marker=2))
        assert classify.resolve_resources(page).Marker == 2

    def test_parent_cycle_terminates(self, pdf):
        node = self._pages_node(pdf)
        node.Parent = node
        page = _page(pdf, "", resources=False)
        page.obj.Parent = node
        assert classify.resolve_resources(page) is None

    def test_lookup_xobject_fallback_and_misses(self, pdf):
        img = _image(pdf)
        with_img = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=img))
        empty = pikepdf.Dictionary()
        assert classify.lookup_xobject("/Im0", with_img).objgen == img.objgen
        assert classify.lookup_xobject("/Im0", empty, with_img).objgen == img.objgen
        assert classify.lookup_xobject("/Im0", None, with_img).objgen == img.objgen
        assert classify.lookup_xobject("/Im0", empty) is None
        assert classify.lookup_xobject("/Im0", None) is None
