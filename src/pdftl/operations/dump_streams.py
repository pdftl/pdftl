# src/pdftl/operations/dump_streams.py

"""Dump page content streams (and Form XObject streams) for inspection,
mirroring what the `replace` operation operates on."""

import logging
from typing import TYPE_CHECKING

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.operations.helpers.stream_annotator import annotate_stream
from pdftl.operations.helpers.pretty_printers import pretty_format_pdf_obj
from pdftl.utils.keyval_parser import parse_keyval_list
from pdftl.utils.normalize import get_normalized_page_content_stream
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

    if normalize:
        return get_normalized_page_content_stream(page), warnings
    if "/Contents" not in page:
        return None, warnings
    if isinstance(page.Contents, pikepdf.Array):
        sub_streams = list(page.Contents)
        objgens = ", ".join(f"{x.objgen[0]}:{x.objgen[1]}" for x in sub_streams)
        warnings.append(
            f"Page /Contents is an array of {len(sub_streams)} streams "
            f"({objgens}), concatenated below."
        )
        return b"".join(x.read_bytes() for x in sub_streams), warnings
    return page.Contents.read_bytes(), warnings


def _scan_xobject_resources(resources, page_num: int, seen: set, objgen_to_pages: dict) -> None:
    """Recursively record Form XObject objgens reachable from *resources*."""
    if "/XObject" not in resources:
        return
    for _, xobj in resources.XObject.items():
        if xobj.objgen in seen:
            continue
        if xobj.get("/Subtype") != "/Form":
            continue
        seen.add(xobj.objgen)
        objgen_to_pages.setdefault(xobj.objgen, [])
        if page_num not in objgen_to_pages[xobj.objgen]:
            objgen_to_pages[xobj.objgen].append(page_num)
        if "/Resources" in xobj:
            _scan_xobject_resources(xobj.Resources, page_num, seen, objgen_to_pages)


def _build_xobject_page_map(pdf: "pikepdf.Pdf", target_page_nums: list[int]) -> dict:
    """
    Build a map of XObject objgen -> list of page numbers (from target_page_nums)
    that reference it, recursively. Used to warn when an XObject is shared.
    """
    objgen_to_pages: dict[tuple, list[int]] = {}
    for page_num in target_page_nums:
        page = pdf.pages[page_num - 1]
        if "/Resources" in page:
            _scan_xobject_resources(page.Resources, page_num, set(), objgen_to_pages)
    return objgen_to_pages


def _read_xobject_stream(xobj, normalize: bool) -> bytes:
    """Return the content bytes for a Form XObject, normalized or raw."""
    import pikepdf

    if not normalize:
        return xobj.read_bytes()
    try:
        return pikepdf.unparse_content_stream(pikepdf.parse_content_stream(xobj))
    except (pikepdf.PdfError, ValueError, TypeError) as e:
        logger.warning(
            "Could not normalize Form XObject stream: %s. Falling back to raw bytes.", e
        )
        return xobj.read_bytes()


def _xobject_shared_warnings(xobj, page_num: int, xobject_page_map: dict) -> list[str]:
    """Return warnings if this XObject is shared across multiple pages."""
    other_pages = [p for p in xobject_page_map.get(xobj.objgen, []) if p != page_num]
    if not other_pages:
        return []
    pages_str = ", ".join(str(p) for p in sorted(other_pages))
    return [
        f"Shared XObject: also appears on page(s): {pages_str}",
        "Changes via `replace` will affect all referencing pages.",
    ]


def _xobject_content_warnings(content: bytes | None) -> list[str]:
    """Return warnings for missing or empty stream content."""
    if content is None:
        return ["No stream content!"]
    if len(content) == 0:
        return ["Empty stream content!"]
    return []


def _recurse_and_collect(
    resources,
    page_num: int,
    seen_objgens: set,
    collected: list,
    annotate: bool,
    xobject_page_map: dict,
    dump_resources: bool,
    normalize: bool,
) -> None:
    """
    Recursively collect Form XObject streams into *collected*.
    Form XObjects are normalized if requested (normalize=True), mirroring `replace` behavior.
    """
    if "/XObject" not in resources:
        return

    for name, xobj in resources.XObject.items():
        if xobj.objgen in seen_objgens:
            continue
        if xobj.get("/Subtype") != "/Form":
            continue

        seen_objgens.add(xobj.objgen)
        obj_num, gen_num = xobj.objgen
        header = f"Page {page_num} / XObject {name} ({obj_num}:{gen_num})"

        content = _read_xobject_stream(xobj, normalize)
        warnings = _xobject_shared_warnings(
            xobj, page_num, xobject_page_map
        ) + _xobject_content_warnings(content)

        if annotate and content:
            content = annotate_stream(
                content, xobj.get("/Resources"), MIN_COMMENT_COL, MAX_COMMENT_COL
            )

        collected.append((header, content or b"", warnings))

        if dump_resources and "/Resources" in xobj:
            res_content = "\n".join(pretty_format_pdf_obj(xobj.Resources)).encode("latin-1")
            res_header = f"Page {page_num} / XObject {name} ({obj_num}:{gen_num}) / Resources"
            collected.append((res_header, res_content, []))

        if "/Resources" in xobj:
            _recurse_and_collect(
                xobj.Resources,
                page_num,
                seen_objgens,
                collected,
                annotate,
                xobject_page_map,
                dump_resources,
                normalize,
            )


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


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
        print(content.decode("latin-1"), file=out)
    print("", file=out)


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
  skipping Form XObjects. Mirrors the same flag on `replace`.
* `resources=true` — pretty-print the associated structural dictionary mapping
  for each Page and Form XObject. Very helpful to inspect Font and Form maps.
* `annotate` — append a PDF-style `%` comment to each operator line
  explaining what the operator does (e.g. `% show/text: Show text`).
  Particularly useful when learning the PDF content stream format or
  hunting for the right operator to target with `replace`.

### Output format

Each content stream is preceded by a labelled header block:

    ================
    === Page <N>
    ================

For Form XObjects:

    ============================================
    === Page <N> / XObject <name> (<obj>:<gen>)
    ============================================

When an XObject is shared across multiple pages, a warning appears in
the header identifying the other pages that reference it.

Stream content follows as decoded text (latin-1). Annotation comments,
when requested, use standard PDF `%` comment syntax so the output
remains valid PDF content stream text.

### Page specification

Standard page specs are supported (e.g. `1`, `2-4`, `1 3-5`).
Default is all pages.

### Relationship to `replace`

`dump_streams` intentionally mirrors `replace`'s behavior:

| Behavior                          | `replace`           | `dump_streams`      |
|-----------------------------------|---------------------|---------------------|
| Normalizes page streams           | yes                 | yes (default)       |
| Normalizes XObject streams        | yes                 | yes (default)       |
| Recurses into Form XObjects       | yes (default)       | yes (default)       |

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
        for header, content, warnings in result.data:
            _write_stream_block(f, header, content, warnings)


@register_operation(
    "dump_streams",
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
def dump_streams(pdf: "pikepdf.Pdf", specs, output_file=None) -> OpResult:
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

    xobject_page_map = _build_xobject_page_map(pdf, all_target_pages) if recurse else {}
    collected: list[tuple[str, bytes, list[str]]] = []

    for spec in page_specs:
        for page_num in page_numbers_matching_page_spec(spec, num_pages):
            page = pdf.pages[page_num - 1]
            collected.extend(
                _process_page(
                    page,
                    page_num,
                    normalize,
                    annotate,
                    dump_resources,
                    set(),
                    xobject_page_map,
                    recurse,
                )
            )

    return OpResult(
        success=True,
        data=collected,
        meta={c.META_OUTPUT_FILE: output_file},
    )


##################################################


def _process_page_resources(page, page_num: int) -> tuple[str, bytes]:
    """Return the (header, content) entry for a page's resource block."""
    res_content = "\n".join(pretty_format_pdf_obj(page.Resources)).encode("latin-1")
    return f"Page {page_num} / Resources", res_content


def _process_page(
    page,
    page_num: int,
    normalize: bool,
    annotate: bool,
    dump_resources: bool,
    seen_objgens: set,
    xobject_page_map: dict,
    recurse: bool,
) -> list[tuple[str, bytes, list[str]]]:
    """Collect all stream entries for a single page."""
    entries: list[tuple[str, bytes, list[str]]] = []

    content, warnings = _collect_page_stream(page, normalize)

    if content is None:
        content = b""
        logger.warning("Page %d: no content found (missing /Contents)", page_num)
        warnings.append("No content stream found (missing /Contents)")
    elif annotate and normalize:
        content = annotate_stream(
            content, page.get("/Resources"), MIN_COMMENT_COL, MAX_COMMENT_COL
        )
    elif annotate and not normalize:
        logger.warning(
            "Page %d: annotation is only reliable on normalized streams; "
            "skipping annotation because normalize=false",
            page_num,
        )

    contents_obj = page.get("/Contents")
    contents_str = (
        f"({contents_obj.objgen[0]}:{contents_obj.objgen[1]})" if contents_obj else "None"
    )
    entries.append((f"Page {page_num} / Contents {contents_str}", content, warnings))

    if dump_resources and "/Resources" in page:
        header, res_content = _process_page_resources(page, page_num)
        entries.append((header, res_content, []))

    if recurse and "/Resources" in page:
        _recurse_and_collect(
            page.Resources,
            page_num,
            seen_objgens,
            entries,
            annotate,
            xobject_page_map,
            dump_resources,
            normalize,
        )

    return entries
