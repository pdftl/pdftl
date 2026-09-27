# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/mrc/classify.py

"""Page eligibility for MRC compression.

A page qualifies when it draws exactly one upright, page-covering image and
nothing else visible. Invisible OCR text (render mode 3) is allowed.

Any image drawn while a `W`/`W*` clip is active counts as clipped and is
ignored. This may skip some eligible pages but never admits a clipped one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

IDENTITY: tuple[float, ...] = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

#: The scan must cover this much of the page for the page to be a scan.
MIN_COVERAGE = 0.90
#: Placement skew/rotation tolerance, as a fraction of the placement's scale.
PLACEMENT_TOLERANCE = 1e-3
#: Form XObject nesting beyond this depth counts as visible content.
_DEPTH_LIMIT = 4

_PAINT_OPS = frozenset({"S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "sh"})
_TEXT_SHOW_OPS = frozenset({"Tj", "TJ", "'", '"'})


@dataclass
class Candidate:
    """A page that classified as a scan, with what the pass needs to act."""

    page_number: int  # 1-based
    xobj: object  # the qualifying pikepdf image XObject (indirect)
    xobj_name: str  # its resource name in the page's own content stream
    rect: tuple[float, float, float, float]
    matrix: tuple[float, ...]
    width: int
    height: int
    source_dpi: int


def _multiply(m1: tuple[float, ...], m2: tuple[float, ...]) -> tuple[float, ...]:
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def _bbox(ctm: tuple[float, ...]) -> tuple[float, float, float, float]:
    a, b, c, d, e, f = ctm
    xs = (e, a + e, c + e, a + c + e)
    ys = (f, b + f, d + f, b + d + f)
    return min(xs), min(ys), max(xs), max(ys)


def page_box(page) -> tuple[float, float, float, float]:
    box = page.obj.get("/CropBox", page.obj.get("/MediaBox"))
    try:
        x0, y0, x1, y1 = (float(v) for v in box)
    except (TypeError, ValueError):
        x0, y0, x1, y1 = 0.0, 0.0, 612.0, 792.0
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def resolve_resources(page):
    """Walk the inheritable /Pages tree to find this page's /Resources."""
    node = page.obj
    seen: set = set()  # objgens: only indirect objects can form a cycle
    while True:
        res = node.get("/Resources")
        if res is not None:
            return res
        parent = node.get("/Parent")
        if parent is None or (parent.is_indirect and parent.objgen in seen):
            break
        if parent.is_indirect:
            seen.add(parent.objgen)
        node = parent
    return None


def lookup_xobject(name, resources, fallback=None):
    """Look up a `/Do` operand name in `resources`, falling back to `fallback`."""
    if resources is not None:
        xobjects = resources.get("/XObject")
        if xobjects is not None:
            try:
                if name in xobjects:
                    return xobjects[name]
            except TypeError:
                pass
    if fallback is not None:
        return lookup_xobject(name, fallback, None)
    return None


def _placement_is_upright(matrix: tuple[float, ...]) -> bool:
    a, b, c, d, _e, _f = matrix
    scale = max(abs(a), abs(d), 1e-9)
    return (
        abs(b) <= PLACEMENT_TOLERANCE * scale
        and abs(c) <= PLACEMENT_TOLERANCE * scale
        and a > 0
        and d > 0
    )


def _image_refusal(xobj) -> str | None:
    """Why this image XObject cannot be separated, or None."""
    import pikepdf

    if xobj.get("/SMask") is not None:
        return "the page image carries a soft mask"
    if xobj.get("/Mask") is not None:
        return "the page image is already masked"
    try:
        bpc = int(xobj.get("/BitsPerComponent", 8))
    except (TypeError, ValueError):
        bpc = 8
    if bool(xobj.get("/ImageMask", False)) or bpc < 8:
        return "the page image is already 1-bit"
    cs = xobj.get("/ColorSpace")
    if cs is not None and isinstance(cs, pikepdf.Array) and len(cs) and str(cs[0]) == "/Indexed":
        return "the page image uses an indexed colour space"
    return None


@dataclass
class _Frame:
    """Per-content-stream walking state (one instance per recursion level)."""

    ctm: tuple[float, ...]
    ctm_stack: list = field(default_factory=list)
    clip_stack: list = field(default_factory=list)
    render_mode_stack: list = field(default_factory=list)
    clip_active: bool = False
    # Reset to 0 at each BT. OCR layers set `3 Tr` inside every text
    # object, so this only risks skipping a page, never admitting one.
    render_mode: int = 0


class _Walker:
    """Single pass over a content stream, recursing into Form XObjects.

    Records every image placement and whether anything else visible is
    drawn. Images found inside forms are marked `nested`.
    """

    def __init__(self):
        self.placements: list[dict] = []
        self.visible = False

    def walk(self, pdf, page_or_xobj, resources, fallback, ctm, depth) -> None:
        import pikepdf

        try:
            instructions = list(pikepdf.parse_content_stream(page_or_xobj))
        except (TypeError, pikepdf.PdfError, pikepdf.DataDecodingError):  # assume visible
            self.visible = True
            return

        frame = _Frame(ctm=ctm)
        for operands, operator in instructions:
            handler = _OP_HANDLERS.get(str(operator))
            if handler is not None:
                handler(self, frame, operands, pdf, resources, fallback, depth)

    # Per-operator handlers, dispatched via _OP_HANDLERS.
    def _op_q(self, frame, _operands, _pdf, _resources, _fallback, _depth):
        frame.ctm_stack.append(frame.ctm)
        frame.clip_stack.append(frame.clip_active)
        frame.render_mode_stack.append(frame.render_mode)

    def _op_Q(self, frame, _operands, _pdf, _resources, _fallback, _depth):
        if frame.ctm_stack:
            frame.ctm = frame.ctm_stack.pop()
        if frame.clip_stack:
            frame.clip_active = frame.clip_stack.pop()
        if frame.render_mode_stack:
            frame.render_mode = frame.render_mode_stack.pop()

    def _op_cm(self, frame, operands, _pdf, _resources, _fallback, _depth):
        try:
            m = tuple(float(v) for v in operands)
        except (TypeError, ValueError):
            return
        frame.ctm = _multiply(m, frame.ctm)

    def _op_clip(self, frame, _operands, _pdf, _resources, _fallback, _depth):
        frame.clip_active = True

    def _op_tr(self, frame, operands, _pdf, _resources, _fallback, _depth):
        try:
            frame.render_mode = int(operands[0])
        except (IndexError, TypeError, ValueError):
            pass

    def _op_bt(self, frame, _operands, _pdf, _resources, _fallback, _depth):
        frame.render_mode = 0

    def _op_text_show(self, frame, _operands, _pdf, _resources, _fallback, _depth):
        if frame.render_mode != 3:
            self.visible = True

    def _op_paint(self, _frame, _operands, _pdf, _resources, _fallback, _depth):
        self.visible = True

    def _op_do(self, frame, operands, pdf, resources, fallback, depth):
        if not operands:
            return
        name = operands[0]
        xobj = lookup_xobject(name, resources, fallback)
        if xobj is None:
            return
        subtype = str(xobj.get("/Subtype", ""))
        if subtype == "/Image":
            self.placements.append(
                {
                    "name": str(name),
                    "xobj": xobj,
                    "matrix": frame.ctm,
                    "rect": _bbox(frame.ctm),
                    "clipped": frame.clip_active,
                    "nested": depth > 0,
                }
            )
            return
        if subtype == "/Form":
            self._recurse_form(frame, xobj, pdf, resources, depth)
            return
        # Other subtypes (e.g. /PS) count as visible.
        self.visible = True

    def _recurse_form(self, frame, xobj, pdf, resources, depth):
        if depth >= _DEPTH_LIMIT:
            self.visible = True
            return
        form_matrix = IDENTITY
        if "/Matrix" in xobj:
            try:
                form_matrix = tuple(float(v) for v in xobj.Matrix)
            except (TypeError, ValueError):
                form_matrix = IDENTITY
        form_ctm = _multiply(form_matrix, frame.ctm)
        form_res = xobj.get("/Resources")
        self.walk(
            pdf,
            xobj,
            form_res if form_res is not None else resources,
            resources,
            form_ctm,
            depth + 1,
        )


_OP_HANDLERS = {
    "q": _Walker._op_q,
    "Q": _Walker._op_Q,
    "cm": _Walker._op_cm,
    "W": _Walker._op_clip,
    "W*": _Walker._op_clip,
    "Tr": _Walker._op_tr,
    "BT": _Walker._op_bt,
    "Do": _Walker._op_do,
    **{op: _Walker._op_text_show for op in _TEXT_SHOW_OPS},
    **{op: _Walker._op_paint for op in _PAINT_OPS},
    "INLINE IMAGE": _Walker._op_paint,  # pikepdf's single operator for BI...ID...EI
}


def classify_page(pdf, page, page_number: int) -> tuple[Candidate | None, str]:
    """`(candidate, reason)` -- a candidate, or None with the reason why not."""
    resources = resolve_resources(page)
    walker = _Walker()
    walker.walk(pdf, page, resources, None, IDENTITY, 0)

    images = [p for p in walker.placements if not p["clipped"]]
    if len(images) != 1:
        return None, "this page is not a scanned image"
    placement = images[0]
    if placement["nested"]:
        return None, "the page image is drawn inside a form"
    if not _placement_is_upright(placement["matrix"]):
        return None, "the page image placement is rotated or skewed"

    px0, py0, px1, py1 = page_box(page)
    page_area = max((px1 - px0) * (py1 - py0), 1e-6)
    rx0, ry0, rx1, ry1 = placement["rect"]
    if ((rx1 - rx0) * (ry1 - ry0)) / page_area < MIN_COVERAGE - 1e-9:
        return None, "this page is not a scanned image"

    xobj = placement["xobj"]
    refusal = _image_refusal(xobj)
    if refusal is not None:
        return None, refusal
    if walker.visible:
        return None, "this page draws more than a scanned image"

    width = int(xobj.get("/Width", 0) or 0)
    height = int(xobj.get("/Height", 0) or 0)
    if width < 2 or height < 2:
        return None, "this page is not a scanned image"
    dpi = int(round(width * 72.0 / max(rx1 - rx0, 1e-6)))

    return (
        Candidate(
            page_number=page_number,
            xobj=xobj,
            xobj_name=placement["name"],
            rect=(rx0, ry0, rx1, ry1),
            matrix=placement["matrix"],
            width=width,
            height=height,
            source_dpi=max(1, dpi),
        ),
        "mrc",
    )
