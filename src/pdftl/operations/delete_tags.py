# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/delete_tags.py

"""Delete the structure tree (tags) from a PDF."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)

_LONG_DESC = """
Delete the structure tree (tags) of a tagged PDF.

Tags describe a document's logical structure for screen readers,
reflow and accessibility checkers. In some files they take up a large
share of the bytes. Deleting them does not change how the pages look.

Removed:

- the catalog's /StructTreeRoot and /MarkInfo;

- /StructParents on pages and /StructParent(s) on annotations and
  XObjects, and a page's /Tabs entry when it orders by structure;

- the marked-content wrappers that link page content to the tree (those
  with an /MCID), and /Artifact wrappers. The content inside them stays.
  A wrapper that also carries /ActualText, /Alt, /Lang or /E keeps those
  and loses only its /MCID.

Kept: optional content (/OC) marks, form-field text (/Tx) marks, and
marked content that refers to a named property.

The result is no longer accessible. A file that claims PDF/UA, or PDF/A
level A, loses that conformance; pdftl warns but leaves the claim in
the metadata.
"""

_EXAMPLES = [
    {
        "cmd": "in.pdf delete_tags output out.pdf",
        "desc": "Delete the structure tree",
    },
]

_KEPT_TAGS = {"/OC", "/Tx"}
_STRUCT_KEYS = ("/StructParent", "/StructParents")


def _plain_props(props) -> pikepdf.Dictionary | None:
    """`props` without /MCID, or None if nothing else is left in it."""
    import pikepdf

    rest = {k: v for k, v in props.items() if k != "/MCID"}
    return pikepdf.Dictionary(rest) if rest else None


def _open_mark(operands, operator: str):
    """(drop, replacement) for a BMC/BDC; replacement is None to keep it as is."""
    import pikepdf

    if len(operands) != (1 if operator == "BMC" else 2):
        return False, None
    tag = str(operands[0])
    if tag in _KEPT_TAGS:
        return False, None
    if tag == "/Artifact":
        return True, None
    if operator != "BDC" or not isinstance(operands[1], pikepdf.Dictionary):
        return False, None
    if "/MCID" not in operands[1]:
        return False, None
    rest = _plain_props(operands[1])
    if rest is None:
        return True, None
    return False, pikepdf.ContentStreamInstruction([operands[0], rest], pikepdf.Operator("BDC"))


def _step(inst, open_marks: list[bool]):
    """(instruction to keep or None, whether it changed) for one instruction."""
    operator = str(getattr(inst, "operator", ""))
    if operator in ("BMC", "BDC"):
        drop, replacement = _open_mark(inst.operands, operator)
        open_marks.append(drop)
        if drop:
            return None, True
        return (inst, False) if replacement is None else (replacement, True)
    if operator == "EMC" and open_marks and open_marks.pop():
        return None, True
    return inst, False


def _unmark(instructions) -> list | None:
    """`instructions` without structure marks, or None if there were none."""
    out = []
    open_marks: list[bool] = []
    changed = False
    for inst in instructions:
        kept, touched = _step(inst, open_marks)
        changed = changed or touched
        if kept is not None:
            out.append(kept)
    return out if changed else None


def _unmarked_bytes(source) -> bytes | None:
    import pikepdf

    try:
        instructions = pikepdf.parse_content_stream(source)
    except pikepdf.PdfError as exc:
        logger.warning("delete_tags: could not parse a content stream, left as is: %s", exc)
        return None
    kept = _unmark(instructions)
    return None if kept is None else pikepdf.unparse_content_stream(kept)


def _unmark_array(pdf, page, contents, rewritten: dict) -> None:
    """Replace a page's stream array by one unmarked stream; shared arrays share it."""
    key = tuple(s.objgen for s in contents)
    if key not in rewritten:
        data = _unmarked_bytes(page)
        rewritten[key] = None if data is None else pdf.make_stream(data)
    if rewritten[key] is not None:
        page.Contents = rewritten[key]


def _unmark_pages(pdf) -> None:
    import pikepdf

    rewritten: dict[tuple, pikepdf.Stream | None] = {}
    for page in pdf.pages:
        contents = page.obj.get("/Contents")
        if isinstance(contents, pikepdf.Stream):
            data = _unmarked_bytes(contents)
            if data is not None:
                contents.write(data)
        elif isinstance(contents, pikepdf.Array):
            _unmark_array(pdf, page.obj, contents, rewritten)


def _is_painted_stream(obj) -> bool:
    return obj.get("/Subtype") == "/Form" or obj.get("/PatternType") == 1


def _unmark_forms(pdf) -> None:
    import pikepdf

    for obj in pdf.objects:
        if isinstance(obj, pikepdf.Stream) and _is_painted_stream(obj):
            data = _unmarked_bytes(obj)
            if data is not None:
                obj.write(data)


def _drop_struct_keys(obj) -> None:
    for key in _STRUCT_KEYS:
        if key in obj:
            del obj[key]


def _drop_links(pdf) -> None:
    import pikepdf

    for obj in pdf.objects:
        if isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
            _drop_struct_keys(obj)
    for page in pdf.pages:
        if page.obj.get("/Tabs") == "/S":
            del page.obj["/Tabs"]
        for annot in page.obj.get("/Annots", []):
            if isinstance(annot, pikepdf.Dictionary):
                _drop_struct_keys(annot)


def lost_conformance(pdf) -> list[str]:
    """The conformance claims deleting `pdf`'s tags would break."""
    if "/StructTreeRoot" not in pdf.Root:
        return []
    meta = pdf.open_metadata(set_pikepdf_as_editor=False)
    claims = []
    if meta.get("pdfuaid:part"):
        claims.append("PDF/UA")
    if str(meta.get("pdfaid:conformance", "")).upper() == "A":
        claims.append("PDF/A level A")
    return claims


def warn_lost_conformance(claims: list[str]) -> None:
    if claims:
        logger.warning(
            "delete_tags: the file claims %s, which it no longer meets", " and ".join(claims)
        )


@register_operation(
    "delete_tags",
    tags=["in_place", "accessibility", "structure", "tags", "delete"],
    type="single input operation",
    desc="Delete the structure tree (tags)",
    long_desc=_LONG_DESC,
    usage="<input> delete_tags output <file> [<option>...]",
    examples=_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def delete_tags(pdf: pikepdf.Pdf, op_args: list) -> OpResult:
    """Delete the structure tree and the marks that link content to it."""
    if op_args:
        raise InvalidArgumentError(f"delete_tags takes no arguments, got {op_args[0]!r}")
    claims = lost_conformance(pdf)
    for key in ("/StructTreeRoot", "/MarkInfo"):
        if key in pdf.Root:
            del pdf.Root[key]
    _drop_links(pdf)
    _unmark_pages(pdf)
    _unmark_forms(pdf)
    warn_lost_conformance(claims)
    return OpResult(success=True, pdf=pdf)
