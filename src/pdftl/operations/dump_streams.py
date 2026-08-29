# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/dump_streams.py

"""Dump page content streams (and Form XObject streams) for inspection,
mirroring what the `replace` operation operates on."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.operations.helpers.stream_annotator import annotate_stream
from pdftl.operations.helpers.xobject_helpers import read_xobject_stream
from pdftl.operations.helpers.pretty_printers import pretty_format_pdf_obj
from pdftl.utils.keyval_parser import parse_keyval_list
from pdftl.utils.normalize import get_normalized_page_content_stream
from pdftl.utils.pdf_resources import get_resources, walk_content_streams, StreamContext
from pdftl.utils.page_specs import page_numbers_matching_page_spec
from pdftl.utils.io_helpers import smart_open_maybe_dash

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)


MIN_COMMENT_COL = 40
MAX_COMMENT_COL = 80


# ---------------------------------------------------------------------------
# Stream collection
# ---------------------------------------------------------------------------


def _collect_page_stream(page, normalize: bool) -> tuple[bytes | None, list[str]]:
    """Return the content stream for a page (normalized or raw) and any warnings."""
    import pikepdf

    warnings = []

    if "/Contents" not in page:
        return None, warnings

    if isinstance(page.Contents, pikepdf.Array):
        sub_streams = list(page.Contents)
        objgens = ", ".join(f"{x.objgen[0]}:{x.objgen[1]}" for x in sub_streams)
        warnings.append(
            f"Page /Contents is an array of {len(sub_streams)} streams "
            f"({objgens}), concatenated below."
        )
        if normalize:
            return get_normalized_page_content_stream(page), warnings
        return b"".join(x.read_bytes() for x in sub_streams), warnings

    if normalize:
        return get_normalized_page_content_stream(page), warnings
    return page.Contents.read_bytes(), warnings


def _build_stream_page_map(pdf: pikepdf.Pdf, target_page_nums: list[int]) -> dict:
    """
    Build a map of stream objgen -> list of page numbers (from target_page_nums)
    that reference it, recursively. Used to warn when an XObject is shared.

    Covers all four walkable kinds (Form, Pattern, SMask group, annotation
    appearance stream), not just Forms.
    """
    objgen_to_pages: dict[tuple, list[int]] = {}
    for page_num in target_page_nums:
        for stream_obj, ctx in walk_content_streams(pdf, [page_num], yield_duplicates=True):
            if ctx.kind == "page":
                continue
            objgen_to_pages.setdefault(stream_obj.objgen, []).append(page_num)
    return objgen_to_pages


def _stream_shared_warnings(stream_obj, page_num: int, stream_page_map: dict) -> list[str]:
    """Return warnings if this stream is shared across multiple pages."""
    other_pages = [p for p in stream_page_map.get(stream_obj.objgen, []) if p != page_num]
    if not other_pages:
        return []
    pages_str = ", ".join(str(p) for p in sorted(other_pages))
    return [
        f"Shared resource: also appears on page(s): {pages_str}",
        "Changes via `replace` will affect all referencing pages.",
    ]


def _xobject_content_warnings(content: bytes | None) -> list[str]:
    """Return warnings for missing or empty stream content."""
    if content is None:
        return ["No stream content!"]
    if len(content) == 0:
        return ["Empty stream content!"]
    return []


def _breadcrumb_segment(ctx: StreamContext) -> str:
    """Render the single path segment for one non-page StreamContext.

    "smask" and "annotation" segments each embed their own internal
    " / "-joined sub-parts (e.g. "ExtGState /GS1 / SMask"), since the
    walker treats them as one traversal level despite having two or three
    named components. See _IMPORT_STREAMS_LONG_DESC / this module's
    long-desc for the full path grammar.
    """
    if ctx.kind == "form":
        return f"XObject {ctx.owner_key}"
    if ctx.kind == "pattern":
        return f"Pattern {ctx.owner_key}"
    if ctx.kind == "smask":
        return f"ExtGState {ctx.owner_key} / SMask"
    if ctx.kind == "annotation":
        segment = f"Annot {ctx.annot_index + 1} / AP {ctx.ap_key}"
        if ctx.ap_state is not None:
            segment += f" / State {ctx.ap_state}"
        return segment
    return ""  # kind == "page": handled separately by the caller


def _collect_walked_streams(
    pdf: pikepdf.Pdf,
    page_num: int,
    normalize: bool,
    annotate: bool,
    dump_resources: bool,
    seen_objgens: dict[tuple, str],
    stream_page_map: dict,
) -> list[tuple[str, bytes, list[str]]]:
    """
    Collect all non-page streams (Forms, Patterns, SMask groups, annotation
    appearance streams) reachable from one page, via the canonical walker.

    Path segments are reconstructed from ctx.depth using a stack: the
    walker yields in strict depth-first pre-order (parent before its own
    children, at depth+1), so popping stack entries whose recorded depth
    is >= the current entry's depth always leaves exactly the correct
    ancestor chain in place.
    """
    entries: list[tuple[str, bytes, list[str]]] = []
    path_stack: list[tuple[int, str]] = []  # (depth, segment)

    for stream_obj, ctx in walk_content_streams(pdf, [page_num], yield_duplicates=True):
        if ctx.kind == "page":
            continue  # handled separately by the page's own /Contents entry

        while path_stack and path_stack[-1][0] >= ctx.depth:
            path_stack.pop()
        path_stack.append((ctx.depth, _breadcrumb_segment(ctx)))
        full_path = f"Page {page_num} / " + " / ".join(seg for _, seg in path_stack)

        objgen = stream_obj.objgen
        if objgen in seen_objgens:
            canonical_path = seen_objgens[objgen]
            entries.append((full_path, f"% ALIAS OF: {canonical_path}\n".encode("latin-1"), []))
            continue
        seen_objgens[objgen] = full_path

        content = read_xobject_stream(stream_obj, normalize)
        warnings = _stream_shared_warnings(
            stream_obj, page_num, stream_page_map
        ) + _xobject_content_warnings(content)

        if annotate and content:
            content = annotate_stream(content, ctx.resources, MIN_COMMENT_COL, MAX_COMMENT_COL)

        entries.append((full_path, content or b"", warnings))

        if dump_resources and ctx.resources is not None:
            res_content = "\n".join(pretty_format_pdf_obj(ctx.resources)).encode("latin-1")
            entries.append((f"{full_path} / Resources", res_content, []))

    return entries


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _escape_content_line(line: str) -> str:
    """Escape a content line that would otherwise be misread by `import_streams`
    as a structural '===' header, or as an escape sequence itself.

    The on-disk format treats any line starting with '===' as structural
    regardless of where it falls inside a stream's body, so a content line
    that happens to start with '=' (or with the escape character '\\') must
    be escaped with a leading backslash. `import_streams._unescape_content_line`
    reverses this on read.
    """
    if line.startswith("=") or line.startswith("\\"):
        return "\\" + line
    return line


def _write_stream_block(
    out, header: str, content: bytes, warnings: list[str] | None = None
) -> None:
    """Write a single labelled stream block."""
    all_lines = [f"=== {header}"] + [f"=== {w}" for w in (warnings or [])]
    width = max(max(len(line) for line in all_lines) + 4, MIN_COMMENT_COL)
    separator = "=" * width
    print(separator, file=out)
    for line in all_lines:
        print(line, file=out)
    print(separator, file=out)
    if content:
        decoded = content.decode("latin-1")
        escaped = "\n".join(_escape_content_line(line) for line in decoded.split("\n"))
        print(escaped, file=out)
    print("", file=out)


def format_stream_blocks(collected: list[tuple[str, bytes, list[str]]]) -> str:
    """Render collected (header, content, warnings) stream entries to text,
    the same block format `dump_streams_cli_hook` writes to stdout/file."""
    import io

    buf = io.StringIO()
    for header, content, warnings in collected:
        _write_stream_block(buf, header, content, warnings)
    return buf.getvalue()


def _api_serialize_stream_blocks(data, _meta):
    """api_serializer: renders .data as text for the server/API path,
    where no cli_hook ever runs to format it."""
    return None, {"kind": "text", "text": format_stream_blocks(data)}


# ---------------------------------------------------------------------------
# Long description and examples
# ---------------------------------------------------------------------------

_DUMP_STREAMS_LONG_DESC = """
The `dump_streams` operation outputs page content streams in the same form
that the `replace` operation operates on: by default normalized (one PDF
operator per line), with Form XObjects recursively included.

This is the primary tool for crafting a regular expression to pass to
`replace`. Instead of reaching for `mutool show` or an external PDF
inspector, run `dump_streams` to see exactly the text that `replace` will
match against.

### Options

* `normalize=false` — output the raw, un-normalized stream bytes as stored
  in the PDF, instead of the normalized form. Annotation is suppressed when
  normalization is disabled.
* `recurse=false` — restrict output to top-level page content streams only,
  skipping Form XObjects, tiling Patterns, ExtGState soft-mask groups, and
  annotation appearance streams. Mirrors the same flag on `replace`.
* `resources=true` — pretty-print the associated structural dictionary mapping
  for each Page and Form XObject. Very helpful to inspect Font and Form maps.
* `annotate` — append a PDF-style `%` comment to each operator line
  explaining what the operator does (e.g. `% show/text: Show text`).
  Particularly useful when learning the PDF content stream format or
  hunting for the right operator to target with `replace`.

### Page specification

Standard page specs are supported (e.g. `1`, `2-4`, `1 3-5`).
Default is all pages.

### Output format

Each content stream is preceded by a semantic breadcrumb path block:

    ================
    === Page <N> / Contents
    ================

For nested Form XObjects:

    ============================================
    === Page <N> / XObject <name1> [ / XObject <name2> ]...
    ============================================

Tiling Patterns, ExtGState soft-mask groups, and annotation appearance
streams use their own path segments, and can nest further Form XObjects
beneath them the same way a page can:

    Page <N> / Pattern <name>
    Page <N> / ExtGState <name> / SMask
    Page <N> / Annot <index> / AP <key>
    Page <N> / Annot <index> / AP <key> / State <state>

`<index>` is 1-based, matching `Page <N>`. `<key>` is one of `/N`, `/D`,
`/R`. `/ State <state>` only appears when the annotation's appearance
entry is itself a sub-dictionary keyed by appearance state (e.g. `/On`,
`/Off`) rather than a direct stream.

Path segments are joined with " / " (space-slash-space); resource names
themselves start with their own literal slash (e.g. `/Fm1`), so a full
segment reads e.g. `XObject /Fm1` — the leading slash belongs to the
name, not the delimiter. Avoid introducing a literal " / " sequence when
editing a resource name with text tools.

When an XObject is shared across multiple pages, a warning appears in
the header identifying the other pages that reference it. Subsequent references
to the same underlying PDF object will output a lightweight `% ALIAS OF:` stub
pointing back to the original canonical path.

Stream content follows as decoded text (latin-1). Annotation comments,
when requested, use standard PDF `%` comment syntax so the output
remains valid PDF content stream text. Any content line that would
otherwise be misread as a structural `===` header (or as an escape
sequence) is escaped with a leading backslash; `import_streams` reverses
this automatically.

### Relationship to `replace`

`dump_streams` intentionally mirrors `replace`'s behavior:

| Behavior                           | `replace`           | `dump_streams`      |
|-----------------------------------|---------------------|---------------------|
| Normalizes page streams           | yes                 | yes (default)       |
| Normalizes XObject streams        | yes                 | yes (default)       |
| Recurses into Forms/Patterns/etc. | yes (default)       | yes (default)       |

"""

_DUMP_STREAMS_EXAMPLES = [
    {
        "cmd": "in.pdf dump_streams",
        "desc": "Print normalized content streams for all pages to stdout",
    },
    {
        "cmd": "in.pdf dump_streams resources=true",
        "desc": "Dump page content streams along with their pretty-printed resource blocks",
    },
    {
        "cmd": "in.pdf dump_streams 1-3 output streams.txt",
        "desc": "Dump normalized content streams for pages 1-3 to a file",
    },
    {
        "cmd": "in.pdf dump_streams annotate",
        "desc": "Dump streams with operator annotations to help write a replace spec",
    },
    {
        "cmd": "in.pdf dump_streams normalize=false 1",
        "desc": "Dump the raw (un-normalized) content stream for page 1",
    },
    {
        "cmd": "in.pdf dump_streams recurse=false",
        "desc": "Dump only top-level page content streams, skipping Form XObjects",
    },
]


# ---------------------------------------------------------------------------
# CLI hook and operation
# ---------------------------------------------------------------------------


def dump_streams_cli_hook(result: OpResult, stage, _pipeline) -> None:
    """Write collected stream blocks to stdout or a file."""
    from pdftl.utils.hooks import from_result_meta

    output_file = from_result_meta(result, c.META_OUTPUT_FILE)

    with smart_open_maybe_dash(output_file) as f:
        f.write(format_stream_blocks(result.data))


@register_operation(
    "dump_streams",
    api_serializer=_api_serialize_stream_blocks,
    tags=["info", "content_stream", "replace"],
    type="single input operation",
    desc="Dump page content streams as seen by `replace`",
    long_desc=_DUMP_STREAMS_LONG_DESC,
    examples=_DUMP_STREAMS_EXAMPLES,
    cli_hook=dump_streams_cli_hook,
    usage="<input> dump_streams [normalize=false] [recurse=false] [resources=true] [annotate] "
    "[<page_spec>...] [output <output>]",
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {"output_file": c.OUTPUT}),
    skip_pipeline_save=True,
)
def dump_streams(pdf: pikepdf.Pdf, specs, output_file=None) -> OpResult:
    """
    Dump page content streams in the form that `replace` operates on them.
    """
    page_specs: list[str] = []
    kvs = parse_keyval_list(
        specs,
        bare_tokens=page_specs,
        allowed_keys=["normalize", "recurse", "resources"],
        lowercase_values=True,
        context="dump_streams",
    )

    normalize = kvs.get("normalize", "true") != "false"
    recurse = kvs.get("recurse", "true") != "false"
    dump_resources = kvs.get("resources", "false") != "false"

    annotate = "annotate" in page_specs
    if annotate:
        page_specs.remove("annotate")

    if not page_specs:
        page_specs = ["1-end"]

    num_pages = len(pdf.pages)
    all_target_pages = sorted(
        {
            page_num
            for spec in page_specs
            for page_num in page_numbers_matching_page_spec(spec, num_pages)
        }
    )

    stream_page_map = _build_stream_page_map(pdf, all_target_pages) if recurse else {}
    collected: list[tuple[str, bytes, list[str]]] = []
    seen_objgens: dict[tuple, str] = {}

    for spec in page_specs:
        for page_num in page_numbers_matching_page_spec(spec, num_pages):
            page = pdf.pages[page_num - 1]
            collected.extend(
                _process_page(
                    pdf,
                    page,
                    page_num,
                    normalize,
                    annotate,
                    dump_resources,
                    seen_objgens,
                    stream_page_map,
                    recurse,
                )
            )

    return OpResult(
        success=True,
        data=collected,
        meta={c.META_OUTPUT_FILE: output_file},
    )


# ---------------------------------------------------------------------------
# Page processors
# ---------------------------------------------------------------------------


def _process_page_resources(page, page_num: int, resources) -> tuple[str, bytes]:
    """Return the (header, content) entry for a page's resource block."""
    res_content = "\n".join(pretty_format_pdf_obj(resources)).encode("latin-1")
    return f"Page {page_num} / Resources", res_content


def _process_page(
    pdf,
    page,
    page_num: int,
    normalize: bool,
    annotate: bool,
    dump_resources: bool,
    seen_objgens: dict[tuple, str],
    stream_page_map: dict,
    recurse: bool,
) -> list[tuple[str, bytes, list[str]]]:
    """Collect all stream entries for a single page."""
    entries: list[tuple[str, bytes, list[str]]] = []

    page_resources = get_resources(page)
    content, warnings = _collect_page_stream(page, normalize)

    if content is None:
        content = b""
        logger.warning("Page %d: no content found (missing /Contents)", page_num)
        warnings.append("No content stream found (missing /Contents)")
    elif annotate and normalize:
        content = annotate_stream(content, page_resources, MIN_COMMENT_COL, MAX_COMMENT_COL)
    elif annotate and not normalize:
        logger.warning(
            "Page %d: annotation is only reliable on normalized streams; "
            "skipping annotation because normalize=false",
            page_num,
        )

    entries.append((f"Page {page_num} / Contents", content, warnings))

    if dump_resources and page_resources is not None:
        header, res_content = _process_page_resources(page, page_num, page_resources)
        entries.append((header, res_content, []))

    if recurse:
        entries.extend(
            _collect_walked_streams(
                pdf, page_num, normalize, annotate, dump_resources, seen_objgens, stream_page_map
            )
        )

    return entries
