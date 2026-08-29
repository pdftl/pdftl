# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/helpers/marks_render.py

# Portions derived from printer_marks.py (Spectra-PDF) by Jason Ulbright.
# Used under the MIT License (see NOTICES.md)

# MIT License
#
# Copyright (c) 2026 Jason Ulbright
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""Builds and applies printer-marks content and box growth for one page.

Idempotent by strip-then-redraw: a page already carrying marks from a
previous add_marks call has them -- and the box growth that add caused --
undone before this call's own marks are drawn, so re-running add_marks
never compounds growth or duplicates marks. Unlike a byte-exact restore,
this always takes the surgical strip-the-draw-out-of-current-content path:
simpler, and still correct even if something else edited the page's content
since a previous add_marks call.
"""

from __future__ import annotations

from pdftl.operations.helpers.marks_geometry import (
    MAX_PAGE_EXTENT,
    circle_ops,
    crop_mark_segments,
    grow_box,
    n,
    read_box,
    registration_centres,
    resolve_bleed,
    resolve_trim,
    star_target_segments,
)
from pdftl.operations.helpers.marks_types import MarksConfig

RECORD_KEY = "/PdftlAddMarksOriginalBoxes"
MARK_XOBJECT = "/PdftlAddMarks"
COLORBAR_X = 3
PAGEINFO_X = 3


def _make_all_space(pdf):
    """`[/Separation /All /DeviceCMYK tint]`"""
    from pikepdf import Array, Dictionary, Name

    fn = pdf.make_indirect(
        Dictionary(
            FunctionType=2,
            Domain=Array([0, 1]),
            N=1,
            C0=Array([0, 0, 0, 0]),
            C1=Array([1, 1, 1, 1]),
            Range=Array([0, 1, 0, 1, 0, 1, 0, 1]),
        )
    )
    return pdf.make_indirect(Array([Name.Separation, Name.All, Name.DeviceCMYK, fn]))


def _process_patches() -> list[tuple[float, float, float, float]]:
    """Process CMYK solids and their 75/50/25% tints, in plate order. Fixed
    and document-independent -- this deliberately matches Acrobat's own
    "Add Printer Marks" colour bar, which is likewise a generic swatch strip
    rather than one that inspects the document for spot inks."""
    base = ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1))
    out = []
    for components in base:
        for tint in (1.0, 0.75, 0.5, 0.25):
            out.append(tuple(v * tint for v in components))
    return out


def _find_drop_indices(instructions) -> set[int]:
    drop: set[int] = set()
    for index, instruction in enumerate(instructions):
        if str(instruction.operator) != "Do" or not instruction.operands:
            continue
        if str(instruction.operands[0]) != MARK_XOBJECT:
            continue
        drop.add(index)
        # The add draws marks inside their own q/Q frame; dropping the
        # frame along with the draw keeps the stream balanced.
        if index > 0 and str(instructions[index - 1].operator) == "q":
            drop.add(index - 1)
        if index + 1 < len(instructions) and str(instructions[index + 1].operator) == "Q":
            drop.add(index + 1)
    return drop


def _prune_page_resources(pdf, page) -> None:
    from pikepdf import Dictionary, Name

    resources = page.obj.get("/Resources")
    if resources is None:
        return
    xobjects = resources.get("/XObject")
    if xobjects is None:
        return
    pruned = Dictionary()
    for key, val in xobjects.items():
        if str(key) != MARK_XOBJECT:
            pruned[key] = val
    fresh = Dictionary()
    for key, val in resources.items():
        fresh[key] = val
    fresh[Name("/XObject")] = pruned
    page.obj[Name("/Resources")] = pdf.make_indirect(fresh)


def _strip_marks(pdf, page) -> None:
    """Remove any previous add_marks draw call and its XObject entry from
    whatever the page carries now, leaving every other edit in place."""
    import pikepdf

    if page.obj.get("/Contents") is None:
        return

    instructions = list(pikepdf.parse_content_stream(page))
    drop = _find_drop_indices(instructions)
    if not drop:
        return

    kept = [instr for i, instr in enumerate(instructions) if i not in drop]
    page.obj[pikepdf.Name("/Contents")] = pdf.make_stream(pikepdf.unparse_content_stream(kept))
    _prune_page_resources(pdf, page)


def _restore_original_boxes(page) -> None:
    """Put /MediaBox and /CropBox back to what they were before any
    previous add_marks growth. A page with no recorded growth is untouched."""
    from pikepdf import Dictionary, Name

    record = page.obj.get(RECORD_KEY)
    if not isinstance(record, Dictionary):
        return
    media = record.get("/MediaBox")
    if media is not None:
        page.obj[Name("/MediaBox")] = media
    crop = record.get("/CropBox")
    if crop is not None:
        page.obj[Name("/CropBox")] = crop
    elif "/CropBox" in page.obj:
        del page.obj[Name("/CropBox")]
    del page.obj[Name(RECORD_KEY)]


def _build_mark_form(
    pdf,
    trim,
    media_box,
    config: MarksConfig,
    bleed: float,
    filename: str,
    page_index: int,
    total_pages: int,
):
    """One Form XObject drawing every requested mark, in page user space."""
    from pikepdf import Array, Dictionary, Name
    import datetime

    content: list[bytes] = []
    resources = Dictionary()

    all_space = _make_all_space(pdf)
    resources[Name("/ColorSpace")] = Dictionary(All0=all_space)
    content.append(f"q /All0 CS 1 SCN /All0 cs 1 scn {n(config.weight)} w 0 J 0 j".encode("ascii"))

    if config.cropmarks:
        for x0, y0, x1, y1 in crop_mark_segments(
            trim, config.offset, config.length, config.cropmarks, bleed
        ):
            content.append(f"{n(x0)} {n(y0)} m {n(x1)} {n(y1)} l S".encode("ascii"))

    if config.registration or config.colorbars:
        reg_centres = registration_centres(trim, config.offset, config.length)

    if config.registration:
        for cx, cy, r in reg_centres:
            content.append(circle_ops(cx, cy, r) + b" S")
            content.append(circle_ops(cx, cy, r * 0.45) + b" f")
            content.append(
                f"{n(cx - r * 1.4)} {n(cy)} m {n(cx + r * 1.4)} {n(cy)} l S "
                f"{n(cx)} {n(cy - r * 1.4)} m {n(cx)} {n(cy + r * 1.4)} l S".encode("ascii")
            )

    if config.colorbars:
        patch_w = max(1.0, config.length * 0.9)
        bar_h = max(1.0, config.length * 0.5)
        bar_y = trim[3] + config.offset
        x = trim[0] + COLORBAR_X
        for c, m, y, k in _process_patches():
            rect = f"{n(x)} {n(bar_y)} {n(patch_w)} {n(bar_h)} re"
            content.append(f"q {n(c)} {n(m)} {n(y)} {n(k)} k {rect} f Q".encode("ascii"))
            x += patch_w

        rc0 = reg_centres[0]
        x = rc0[0] + rc0[2] * 1.4 + COLORBAR_X
        # Overprint control patches (magenta over cyan)
        op_gs = pdf.make_indirect(Dictionary(Type=Name.ExtGState, OP=True, op=True, OPM=1))
        resources[Name("/ExtGState")] = Dictionary(OPon=op_gs)
        rect = f"{n(x)} {n(bar_y)} {n(patch_w)} {n(bar_h)} re"
        content.append(f"q 1 0 0 0 k {rect} f".encode("ascii"))
        x += patch_w
        content.append(
            f"/OPon gs 0 1 0 0 k {n(x)} {n(bar_y)} {n(patch_w)} {n(bar_h)} re f Q".encode("ascii")
        )

    if config.startarget:
        # Top-left corner, sized to sit inside the offset+length margin so
        # it doesn't collide with the corner's own crop-mark arms.
        margin = config.offset + config.length
        radius = margin * 0.4
        cx = trim[0] - margin / 2.0
        cy = trim[3] + margin / 2.0
        content.append(b"q")
        for x0, y0, x1, y1 in star_target_segments(cx, cy, radius):
            content.append(f"{n(x0)} {n(y0)} m {n(x1)} {n(y1)} l S".encode("ascii"))
        content.append(b"Q")

    if config.pageinfo:
        font_dict = Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)
        # resources has no /Font key so far, so we create it
        resources[Name("/Font")] = Dictionary()
        resources[Name("/Font")][Name("/F0")] = pdf.make_indirect(font_dict)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        label = f"{filename}  .  {page_index}/{total_pages}  .  {timestamp}"
        size = min(config.length * 0.4, 7.0)
        baseline = trim[1] - config.offset - size
        encoded_label = (
            label.encode("ascii", "replace")
            .replace(b"\\", b"\\\\")
            .replace(b"(", b"\\(")
            .replace(b")", b"\\)")
        )
        x = n(trim[0] + PAGEINFO_X)
        content.append(
            f"q BT /F0 {n(size)} Tf 1 0 0 1 {x} {n(baseline)} Tm ".encode("ascii")
            + b"("
            + encoded_label
            + b") Tj ET Q"
        )

    content.append(b"Q")
    form = pdf.make_stream(b"\n".join(content))
    form.Type = Name.XObject
    form.Subtype = Name.Form
    form.FormType = 1
    form.BBox = Array(list(media_box))
    form.Resources = resources
    return pdf.make_indirect(form)


def apply_marks_to_page(
    pdf, page, config: MarksConfig, filename: str = "", page_index: int = 1, total_pages: int = 1
) -> dict:
    """Applies `config` to one page in-place: strips any existing add_marks
    output, grows the page to hold the new marks (if any are requested),
    and draws them. Returns a small report dict for logging.

    A config requesting nothing (`wants_anything` False) leaves the page
    with marks removed and boxes restored -- the same end state as never
    having run add_marks on it.
    """
    from pikepdf import Array, Dictionary, Name

    _strip_marks(pdf, page)
    _restore_original_boxes(page)

    if not config.wants_anything:
        return {"marked": False}

    media = read_box(page, "/MediaBox")
    if media is None:
        return {"marked": False, "skipped_reason": "no media box"}

    margin = config.offset + config.length
    new_media = grow_box(media, margin)
    if (
        new_media[2] - new_media[0] > MAX_PAGE_EXTENT
        or new_media[3] - new_media[1] > MAX_PAGE_EXTENT
    ):
        raise ValueError(
            "add_marks: requested offset/length would grow the page past the "
            f"{MAX_PAGE_EXTENT:.0f}-point page-extent limit."
        )

    trim, trim_source = resolve_trim(page)
    bleed = resolve_bleed(page, trim) if config.cropmarks == "japanese" else 0.0

    # Own (non-inherited) box arrays only -- an inherited box has nothing
    # of the page's own to restore, and _restore_original_boxes above
    # already handles the "delete the own key" case when this is None.
    record = Dictionary()
    own_media = page.obj.get("/MediaBox")
    if own_media is not None:
        record[Name("/MediaBox")] = own_media
    own_crop = page.obj.get("/CropBox")
    if own_crop is not None:
        record[Name("/CropBox")] = own_crop

    form = _build_mark_form(pdf, trim, new_media, config, bleed, filename, page_index, total_pages)

    resources = page.obj.get("/Resources")
    fresh_resources = Dictionary()
    if resources is not None:
        for key in resources.keys():
            fresh_resources[key] = resources[key]
    xobjects = fresh_resources.get("/XObject")
    pruned = Dictionary()
    if xobjects is not None:
        for key in xobjects.keys():
            pruned[key] = xobjects[key]
    pruned[Name(MARK_XOBJECT)] = form
    fresh_resources[Name("/XObject")] = pruned
    page.obj[Name("/Resources")] = pdf.make_indirect(fresh_resources)

    contents = page.obj.get("/Contents")
    if contents is None:
        streams = []
    elif isinstance(contents, Array):
        streams = list(contents)
    else:
        streams = [contents]
    pre = pdf.make_stream(b"q\n")
    post = pdf.make_stream(f"\nQ\nq\n{MARK_XOBJECT} Do\nQ\n".encode("ascii"))
    page.obj[Name("/Contents")] = Array([pre] + streams + [post])

    page.obj[Name("/MediaBox")] = Array(list(new_media))
    crop = read_box(page, "/CropBox")
    if crop is not None:
        page.obj[Name("/CropBox")] = Array(list(grow_box(crop, margin)))

    page.obj[Name(RECORD_KEY)] = record

    return {
        "marked": True,
        "trim_source": trim_source,
        "media_before": list(media),
        "media_after": list(new_media),
    }
