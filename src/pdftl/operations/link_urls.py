# src/pdftl/operations/link_urls.py
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/link_urls.py

"""Auto-detect URLs and email addresses in page text and create /Link
annotations for them.

This is the "derive links from text" counterpart to `stamp`/`overlay`
(which place pre-existing content) and `modify_annots`/`delete_annots`
(which operate on annotations that already exist). `link_urls` instead
*creates* new Link annotations by regex-scanning each page's extracted
text for URL- and email-shaped substrings, mapping each match back to
its on-page bounding box(es) via the same text/bbox machinery `grep`
uses, and writing a `/Link` annotation with a `/URI` action for each
surviving match.

Two dedup concerns are handled explicitly, since naive regex scanning
over-produces:

1. **Overlap with existing links.** A PDF may already have a real
   Link annotation covering text that also happens to look like a
   URL (common in ebooks/reports). We skip a candidate match if its
   bbox overlaps an existing Link annotation's /Rect beyond a
   configurable fractional threshold, rather than only exact-matching,
   since OCR/kerning make exact bbox equality unreliable.

2. **Nested pattern overlap.** A string like `mailto:person@site.com`
   or a bare `person@site.com` embedded in a URL match will trigger
   both the URL and the email pattern. When two candidate matches
   overlap in *character* span (not bbox), we keep the longer match
   and discard the fully-contained shorter one, rather than emitting
   both as separate (broken, overlapping) annotations.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pikepdf import Pdf

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.dependencies import ensure_dependencies
from pdftl.utils.keyval_parser import parse_keyval_list
from pdftl.utils.page_specs import page_numbers_matching_page_specs

logger = logging.getLogger(__name__)

##################################################
# Patterns
#
# Deliberately conservative (favor missed matches over garbage matches,
# since a false-positive Link annotation is a worse user experience than
# a missed one -- the user can always add missed links manually via
# `modify_annots`, but stripping a bogus one requires `delete_annots`).

_URL_PATTERN = re.compile(
    r"""(?xi)
    \b
    (?:https?://|www\.)
    [a-z0-9\-._~%]+
    (?::\d+)?
    (?:/[a-z0-9\-._~%!$&'()*+,;=:@/]*)?
    (?:\?[a-z0-9\-._~%!$&'()*+,;=:@/?]*)?
    (?:\#[a-z0-9\-._~%!$&'()*+,;=:@/?]*)?
    """
)

_EMAIL_PATTERN = re.compile(
    r"""(?xi)
    \b
    [a-z0-9._%+\-]+
    @
    [a-z0-9.\-]+\.[a-z]{2,}
    \b
    """
)

# Trailing punctuation that regularly gets swept up by the URL/email
# patterns above when the match sits at the end of a sentence, e.g.
# "see https://example.com." or "(https://example.com)".
_TRAILING_STRIP_CHARS = ".,;:!?)\"'”’"


##################################################
# Long description / examples

_LINK_URLS_LONG_DESC = """

Scans the text content of each page for URL- and email-shaped
substrings and creates `/Link` annotations pointing at them
(`/URI` actions; email matches become `mailto:` links).

By default, matches are skipped if they overlap an existing Link
annotation beyond a threshold fraction of the candidate's own area,
so pages that already have live hyperlinks are not double-linked.

Arguments:
  * `[pages...]`: Optional page ranges (e.g. `1-5`, `9-end`) to
    restrict detection to. If omitted, the whole document is scanned.

Options:
  * `urls=<b>`: Detect and link URLs (`http://`, `https://`, `www.`).
    (Default: true)
  * `emails=<b>`: Detect and link email addresses as `mailto:` links.
    (Default: true)
  * `overlap_threshold=<F>`: Fraction (0.0-1.0) of a candidate match's
    bbox area that must overlap an existing Link annotation's /Rect
    before the candidate is skipped as a duplicate. (Default: 0.5)
  * `padding=<F>`: Points to expand the clickable bounding box on all sides,
    making the link slightly larger than the raw text. (Default: 0.0)
  * `border_width=<F>`: Width of the visible border. Set to 0.0 for an
    invisible border, which matches most PDF viewers. (Default: 0.0)
  * `border_color=<R,G,B>`: RGB color for the border (0.0 to 1.0 scale),
    e.g., `0,0,1` for blue. (Default: omitted)

This operation is read/scan-then-append: it never modifies or removes
any existing annotation. Use `modify_annots`/`delete_annots` for that.
"""

_LINK_URLS_EXAMPLES = [
    {
        "cmd": "in.pdf link_urls output out.pdf",
        "desc": "Detect and link all URLs and email addresses in the document.",
    },
    {
        "cmd": "in.pdf link_urls 1-10 emails=false output out.pdf",
        "desc": "Link only URLs (not emails) on pages 1-10.",
    },
    {
        "cmd": "in.pdf link_urls overlap_threshold=0.8 output out.pdf",
        "desc": "Be more permissive about linking near existing links.",
    },
]


##################################################
# Argument parsing


def _parse_bool(kv: dict, key: str, default: bool) -> bool:
    if key not in kv:
        return default
    return kv[key].lower() in ("true", "1", "yes")


def _parse_fraction(kv: dict, key: str, default: float) -> float:
    if key not in kv:
        return default
    try:
        val = float(kv[key])
    except ValueError as exc:
        raise InvalidArgumentError(f"link_urls: {key} must be a number.") from exc
    if not 0.0 <= val <= 1.0:
        raise InvalidArgumentError(f"link_urls: {key} must be between 0.0 and 1.0.")
    return val


def _parse_float(kv: dict, key: str, default: float) -> float:
    if key not in kv:
        return default
    try:
        return float(kv[key])
    except ValueError as exc:
        raise InvalidArgumentError(f"link_urls: {key} must be a number.") from exc


def _parse_color(kv: dict, key: str) -> tuple[float, float, float] | None:
    if key not in kv:
        return None
    parts = kv[key].split(",")
    if len(parts) != 3:
        raise InvalidArgumentError(f"link_urls: {key} must be R,G,B (e.g. 0,0,1).")
    try:
        return tuple(float(p.strip()) for p in parts)  # type: ignore
    except ValueError as exc:
        raise InvalidArgumentError(f"link_urls: {key} components must be numbers.") from exc


def _parse_args(args: list) -> tuple:
    bare_tokens: list[str] = []
    kv = parse_keyval_list(
        args or [],
        bare_tokens=bare_tokens,
        allowed_keys=[
            "urls",
            "emails",
            "overlap_threshold",
            "padding",
            "border_width",
            "border_color",
        ],
        context="link_urls",
    )

    detect_urls = _parse_bool(kv, "urls", True)
    detect_emails = _parse_bool(kv, "emails", True)
    overlap_threshold = _parse_fraction(kv, "overlap_threshold", 0.5)
    padding = _parse_float(kv, "padding", 0.0)
    border_width = _parse_float(kv, "border_width", 0.0)
    border_color = _parse_color(kv, "border_color")

    if not detect_urls and not detect_emails:
        raise InvalidArgumentError("link_urls: at least one of urls/emails must be enabled.")

    return (
        bare_tokens,
        detect_urls,
        detect_emails,
        overlap_threshold,
        padding,
        border_width,
        border_color,
    )


##################################################
# Candidate match model


class _Candidate:
    """A single URL/email match, prior to bbox resolution or dedup."""

    __slots__ = ("start", "end", "text", "kind")

    def __init__(self, start: int, end: int, text: str, kind: str):
        self.start = start
        self.end = end
        self.text = text
        self.kind = kind  # "url" or "email"

    @property
    def span(self) -> tuple[int, int]:
        return (self.start, self.end)


def _strip_trailing_punct(match_text: str, end: int) -> tuple[str, int]:
    """Trims common sentence-boundary punctuation off the end of a raw
    regex match, adjusting `end` to match. Balances trailing `)`/`"` only
    if there's no corresponding unbalanced opener inside the match, so a
    URL that legitimately ends in a closing paren (rare, but valid per
    RFC 3986) isn't mis-trimmed."""
    while match_text and match_text[-1] in _TRAILING_STRIP_CHARS:
        if match_text[-1] == ")" and match_text.count("(") > match_text.count(")") - 1:
            break
        match_text = match_text[:-1]
        end -= 1
    return match_text, end


def _find_candidates(text: str, detect_urls: bool, detect_emails: bool) -> list[_Candidate]:
    raw: list[_Candidate] = []
    if detect_urls:
        for m in _URL_PATTERN.finditer(text):
            stripped, end = _strip_trailing_punct(m.group(0), m.end())
            if stripped:
                raw.append(_Candidate(m.start(), end, stripped, "url"))
    if detect_emails:
        for m in _EMAIL_PATTERN.finditer(text):
            stripped, end = _strip_trailing_punct(m.group(0), m.end())
            if stripped:
                raw.append(_Candidate(m.start(), end, stripped, "email"))
    return _suppress_nested_matches(raw)


def _suppress_nested_matches(candidates: list[_Candidate]) -> list[_Candidate]:
    """Where one match's span is fully contained within another's (e.g. a
    bare email matched separately from a `mailto:`-prefixed URL match
    covering the same text), keep only the longer/outer match."""
    candidates.sort(key=lambda c: (c.start, -c.end))
    kept: list[_Candidate] = []
    for cand in candidates:
        if any(
            other.start <= cand.start and cand.end <= other.end and other is not cand
            for other in kept
        ):
            continue
        kept.append(cand)
    return kept


##################################################
# Geometry: bbox overlap against existing Link annotations


def _bbox_area(bbox: tuple[float, float, float, float]) -> float:
    x0, y0, x1, y1 = bbox
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _bbox_overlap_area(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    return max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)


def _get_existing_link_rects(page: Any) -> list[tuple[float, float, float, float]]:
    """Returns /Rect boxes for existing Link annotations on this page,
    normalized to (x0, y0, x1, y1) with x0<x1, y0<y1."""
    import pikepdf

    if "/Annots" not in page:
        return []

    rects = []
    for annot in page.Annots:
        if not isinstance(annot, pikepdf.Object) or "/Rect" not in annot:
            continue
        subtype = annot.get(pikepdf.Name.Subtype)
        if subtype != pikepdf.Name.Link:
            continue
        try:
            r = [float(x) for x in annot.Rect]
        except (TypeError, ValueError):
            continue
        rects.append((min(r[0], r[2]), min(r[1], r[3]), max(r[0], r[2]), max(r[1], r[3])))
    return rects


def _overlaps_existing_link(
    candidate_bbox: tuple[float, float, float, float],
    existing_rects: list[tuple[float, float, float, float]],
    threshold: float,
) -> bool:
    cand_area = _bbox_area(candidate_bbox)
    if cand_area <= 0:
        return False
    for rect in existing_rects:
        overlap = _bbox_overlap_area(candidate_bbox, rect)
        if overlap / cand_area >= threshold:
            return True
    return False


def _union_bbox(
    bboxes: list[tuple[float, float, float, float]],
) -> tuple[float, float, float, float] | None:
    """Merges a candidate's per-line bboxes (a match may span a line
    wrap) into one enclosing /Rect."""
    if not bboxes:
        return None
    x0 = min(b[0] for b in bboxes)
    y0 = min(b[1] for b in bboxes)
    x1 = max(b[2] for b in bboxes)
    y1 = max(b[3] for b in bboxes)
    return (x0, y0, x1, y1)


##################################################
# Annotation construction


def _make_uri(candidate: _Candidate) -> str:
    if candidate.kind == "email":
        return f"mailto:{candidate.text}"
    if candidate.text.lower().startswith("www."):
        return f"http://{candidate.text}"
    return candidate.text


def _build_link_annot(
    pdf: Pdf,
    rect: tuple[float, float, float, float],
    uri: str,
    border_width: float,
    border_color: tuple[float, float, float] | None,
):
    import pikepdf

    annot_dict: dict[str, Any] = {
        "/Type": pikepdf.Name.Annot,
        "/Subtype": pikepdf.Name.Link,
        "/Rect": pikepdf.Array(list(rect)),
        "/Border": pikepdf.Array([0, 0, border_width]),
        "/A": pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name.Action,
                "/S": pikepdf.Name.URI,
                "/URI": uri,
            }
        ),
    }
    if border_color is not None:
        annot_dict["/C"] = pikepdf.Array(list(border_color))

    return pdf.make_indirect(pikepdf.Dictionary(annot_dict))


##################################################
# Per-page processing


def _process_page(
    pdf: Pdf,
    page_1_indexed: int,
    tp: Any,
    detect_urls: bool,
    detect_emails: bool,
    overlap_threshold: float,
    padding: float,
    border_width: float,
    border_color: tuple[float, float, float] | None,
) -> int:
    import pikepdf

    page = pdf.pages[page_1_indexed - 1]
    page_idx = page_1_indexed - 1

    text = tp.get_text(page_idx)
    if not text:
        return 0

    candidates = _find_candidates(text, detect_urls, detect_emails)
    if not candidates:
        return 0

    existing_rects = _get_existing_link_rects(page)
    created = 0
    new_annots = []

    for cand in candidates:
        bboxes = tp.get_bboxes_for_lines(page_idx, cand.start, cand.end)
        rect = _union_bbox(bboxes)
        if rect is None or _bbox_area(rect) <= 0:
            # Degenerate/zero-area bbox: either get_bboxes_for_lines hit its
            # _ZERO_RECT fallback (start==end, or a pdfium extraction error),
            # or the match landed on whitespace-only charboxes. Either way
            # there's no real on-page geometry to anchor a Link annotation to.
            continue

        if padding != 0.0:
            rect = (rect[0] - padding, rect[1] - padding, rect[2] + padding, rect[3] + padding)

        if _overlaps_existing_link(rect, existing_rects, overlap_threshold):
            continue

        uri = _make_uri(cand)
        annot = _build_link_annot(pdf, rect, uri, border_width, border_color)
        new_annots.append(annot)
        # Newly created links also count toward future overlap checks
        # within the SAME page, so two overlapping candidate matches
        # (e.g. a URL pattern and an email pattern both surviving
        # nested-suppression by sitting adjacent rather than nested)
        # don't both get linked on top of each other.
        existing_rects.append(rect)
        created += 1

    if new_annots:
        current = list(page.Annots) if "/Annots" in page else []
        page.Annots = pikepdf.Array(current + new_annots)

    return created


##################################################
# Operation registration


def link_urls_cli_hook(result: OpResult, stage, _pipeline):
    logger.info("link_urls: created %d link annotation(s).", result.meta.get("created_count", 0))


@register_operation(
    "link_urls",
    tags=["in_place", "annotations", "text"],
    cli_hook=link_urls_cli_hook,
    type="single input operation",
    desc="Auto-create links from URLs/emails found in page text",
    long_desc=_LINK_URLS_LONG_DESC,
    usage="<input> link_urls [pages...] [key=val...] output <output>",
    examples=_LINK_URLS_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def link_urls(pdf: Pdf, operation_args: list) -> OpResult:
    """Detect URLs/emails in page text and create /Link annotations."""
    ensure_dependencies(feature_name="link_urls", dependencies=["pypdfium2"], extra_tag="grep")
    import io

    import pypdfium2 as pdfium

    from pdftl.utils.pdf_text.text_provider import TextProvider

    (
        page_specs,
        detect_urls,
        detect_emails,
        overlap_threshold,
        padding,
        border_width,
        border_color,
    ) = _parse_args(operation_args)

    num_pages = len(pdf.pages)
    target_pages = (
        sorted(page_numbers_matching_page_specs(page_specs, num_pages))
        if page_specs
        else list(range(1, num_pages + 1))
    )

    logger.debug("Synchronizing current PDF stream to pdfium for link_urls...")
    pdf_bytes = io.BytesIO()
    pdf.save(pdf_bytes)
    pdf_bytes.seek(0)

    tp = TextProvider(pdf_path="", opened_pdfium_doc=pdfium.PdfDocument(pdf_bytes))

    total_created = 0
    try:
        for page_1_indexed in target_pages:
            total_created += _process_page(
                pdf,
                page_1_indexed,
                tp,
                detect_urls,
                detect_emails,
                overlap_threshold,
                padding,
                border_width,
                border_color,
            )
            tp.clear_page_cache(page_1_indexed - 1)
    finally:
        tp.close()

    logger.info(
        "link_urls: created %d link annotation(s) across %d page(s).",
        total_created,
        len(target_pages),
    )

    return OpResult(success=True, pdf=pdf, meta={"created_count": total_created})
