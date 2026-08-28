# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/usage.py

"""Report which part of a PDF is spending its bytes, by category."""

import io
import json
import logging

from typing import TYPE_CHECKING

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.utils.dump import get_json_flag
from pdftl.utils.io_helpers import smart_open_maybe_dash
from pdftl.utils.space_usage import analyze_space_usage
from pdftl.utils.string_utils import compact_json_string

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)

_USAGE_LONG_DESC = """
The `usage` operation reports which part of a PDF file is spending its
bytes, broken down by category: images, fonts, content streams,
annotations, forms, embedded files, bookmarks, named destinations, tagged
structure, document structure, metadata, javascript, other objects, and a
residual `overhead` category (cross-reference tables, unreferenced
objects, and other structural bytes).

Categories with zero bytes are omitted from the default tabular output
(a document with no embedded fonts simply has no `fonts` row). The
`json` format always includes every category, at zero, for programmatic
consumers that want a fixed schema.

Every byte of the file is attributed to exactly one category: `overhead` is a
residual (file size minus every other category), so the category totals
always sum to the exact file size.

Where an object is reachable in more than one way (e.g. a font also
embedded as an appearance resource), it is attributed to whichever
category comes first in the report order above.

### Output formats

* *(default)* — Formatted text table sorted by size (largest first), sizes
  shown human-readable (e.g. `512.4 KB`).
* `bytes` — Formatted text table sorted by size, sizes shown as raw byte counts.
* `json` — Structured JSON, always in raw byte counts, including
  the largest objects in each category (capped) for further digging.

### Notes

If `usage` runs after earlier pipeline operations that modified the document
in memory, it analyzes a fresh save of the modified PDF state.
"""

_USAGE_EXAMPLES = [
    {"cmd": "in.pdf usage", "desc": "Human-readable byte breakdown of in.pdf"},
    {"cmd": "in.pdf usage bytes", "desc": "Byte breakdown with raw byte counts"},
    {"cmd": "in.pdf usage json output usage.json", "desc": "Save a JSON breakdown"},
]

_HUMAN_UNITS = ("B", "KB", "MB", "GB", "TB")


def _human_bytes(n: int) -> str:
    value = float(n)
    for unit in _HUMAN_UNITS:
        if value < 1024.0 or unit == _HUMAN_UNITS[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} {unit}"
        value /= 1024.0
    return f"{n} B"  # pragma: no cover - unreachable, satisfies type checkers


def _get_pdf_source_bytes_and_pdf(
    pdf_filename: str | None, pdf: "pikepdf.Pdf"
) -> tuple[bytes, "pikepdf.Pdf"]:
    """Return the exact byte payload and a corresponding pikepdf object.

    If `pdf` has been modified by earlier pipeline stages, or if there is no real
    file on disk (e.g. stdin `_`), save `pdf` to an in-memory buffer and reopen it
    so that `pdf.get_xref_table()` offsets match `source_bytes` exactly.
    """
    import pikepdf

    if not pdf_filename or pdf_filename == "_":
        buf = io.BytesIO()
        pdf.save(buf)
        source_bytes = buf.getvalue()
        refreshed_pdf = pikepdf.open(io.BytesIO(source_bytes))
        return source_bytes, refreshed_pdf

    with open(pdf_filename, "rb") as f:
        source_bytes = f.read()
    return source_bytes, pdf


def _format_size(nbytes: int, human: bool) -> str:
    return str(nbytes) if not human else _human_bytes(nbytes)


def _active_categories(data: dict) -> list[dict]:
    """Categories with nonzero bytes, largest first."""
    flattened = []
    for r in data.get("categories", []):
        if r.get("residual") and r.get("detail"):
            for sub in r["detail"]:
                if sub.get("bytes", 0) > 0:
                    flattened.append(
                        {
                            "id": f"{sub['kind']} overhead",
                            "bytes": sub["bytes"],
                            "objects": sub.get("objects", 0),
                        }
                    )
        elif r.get("bytes", 0) > 0:
            flattened.append(r)
    return sorted(flattened, key=lambda r: r.get("bytes", 0), reverse=True)


def _category_row(row: dict, file_size: int, human: bool) -> tuple[str, str, str, str]:
    b_count = row["bytes"]
    pct = (b_count / file_size * 100.0) if file_size > 0 else 0.0
    return (row["id"], _format_size(b_count, human), str(row["objects"]), f"{pct:.1f}%")


def _build_table_rows(data: dict, human: bool) -> list[tuple[str, str, str, str]]:
    file_size = data.get("file_size", 0)
    return [_category_row(row, file_size, human) for row in _active_categories(data)]


def _column_widths(
    headers: tuple[str, ...], rows: list[tuple[str, str, str, str]]
) -> tuple[int, ...]:
    return tuple(
        max(len(headers[col]), max(len(r[col]) for r in rows)) for col in range(len(headers))
    )


def _print_table_rows(
    rows: list[tuple[str, str, str, str]], widths: tuple[int, ...], file_obj
) -> None:
    w_cat, w_bytes, w_objs, w_pct = widths
    for cat, b_str, obj_str, pct_str in rows:
        print(
            f"{cat:<{w_cat}}  {b_str:>{w_bytes}}  {obj_str:>{w_objs}}  {pct_str:>{w_pct}}",
            file=file_obj,
        )


def _write_table_output(data: dict, file_obj, human: bool) -> None:
    table_rows = _build_table_rows(data, human)
    if not table_rows:
        return

    total_row = (
        "total",
        _format_size(data.get("file_size", 0), human),
        str(data.get("objects", 0)),
        "100%",
    )

    headers = ("CATEGORY", "BYTES", "OBJECTS", "% FILE")
    widths = _column_widths(headers, table_rows + [total_row])
    header_line = "  ".join(
        f"{headers[i]:<{widths[i]}}" if i == 0 else f"{headers[i]:>{widths[i]}}"
        for i in range(len(headers))
    )

    print(header_line, file=file_obj)
    print("-" * len(header_line), file=file_obj)
    _print_table_rows(table_rows, widths, file_obj)
    print("-" * len(header_line), file=file_obj)
    _print_table_rows([total_row], widths, file_obj)


def usage_cli_hook(result: OpResult, stage, _pipeline):
    """Write the usage report to stdout or a file as a table or JSON."""
    output_file = result.meta.get(c.META_OUTPUT_FILE)
    json_output = result.meta.get(c.META_JSON_OUTPUT, False)
    human = result.meta.get("human", True)

    with smart_open_maybe_dash(output_file) as f:
        if json_output:
            compact = compact_json_string(
                json.dumps({"usage": result.data}, indent=2), fold_dicts=False
            )
            f.write(compact + "\n")
        else:
            _write_table_output(result.data, f, human)


@register_operation(
    "usage",
    tags=["info", "diagnostic", "metadata"],
    type="single input operation",
    desc="Per-category byte breakdown of a PDF",
    long_desc=_USAGE_LONG_DESC,
    examples=_USAGE_EXAMPLES,
    cli_hook=usage_cli_hook,
    usage="<input> usage [bytes] [json] [output <output>]",
    args=(
        [c.OPERATION_NAME, c.INPUT_FILENAME, c.INPUT_PDF, c.OPERATION_ARGS],
        {"output_file": c.OUTPUT},
    ),
    skip_pipeline_save=True,
)
def usage(op_name, input_filename, pdf, op_args, output_file=None) -> OpResult:
    """Report per-category byte usage for a PDF file."""
    bytes_output = "bytes" in (op_args or [])
    json_args = [a for a in (op_args or []) if a != "bytes"]
    json_output = get_json_flag(json_args, op_name)

    source_bytes, pdf = _get_pdf_source_bytes_and_pdf(input_filename, pdf)
    data = analyze_space_usage(pdf, source_bytes)

    return OpResult(
        success=True,
        data=data,
        pdf=pdf,
        is_discardable=True,
        meta={
            c.META_OUTPUT_FILE: output_file,
            c.META_JSON_OUTPUT: json_output,
            "human": not bytes_output,
        },
    )
