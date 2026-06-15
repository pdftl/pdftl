# src/pdftl/operations/dump_tables.py

"""Dump tables extracted from a PDF file."""

import io
import json
import logging
from typing import TYPE_CHECKING

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.utils.dependencies import ensure_dependencies
from pdftl.utils.hooks import from_result_meta
from pdftl.utils.io_helpers import smart_open_maybe_dash
from pdftl.utils.page_specs import page_numbers_matching_page_specs
from pdftl.utils.string_utils import compact_json_string

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)


_DUMP_TABLES_LONG_DESC = """
The `dump_tables` operation extracts tabular data from a PDF file and
outputs it as structured JSON.

It uses the `tablers` library for table detection and extraction. Tables
are identified by their line/rectangle borders (lattice-style detection).

**Note:** This operation works only with native text-based PDFs. Scanned
PDFs or PDFs where tables are rendered as images will not yield results.

### Filtering

* `min_rows=N` — exclude tables with fewer than N rows (e.g. `min_rows=2`)
* `min_cols=N` — exclude tables with fewer than N columns
* `min_area=N` — exclude tables whose bounding box area is less than N square points
* `no_empty` — exclude tables where every cell is empty

### Output Schema

The output JSON contains a `tables` list. Each entry corresponds to a
detected table and includes:

* **page**: The 1-indexed page number containing the table.
* **table_index**: The 0-indexed position of this table among all tables
  on that page.
* **bbox**: Bounding box of the table `[x1, y1, x2, y2]` in PDF points.
* **rows**: Number of rows detected.
* **cols**: Number of columns detected.
* **data**: A list of rows, each a list of cell objects with:
    * **text**: The cell's text content, or `null` for merged continuation
      slots.
    * **merged_left**: `true` if this slot continues a cell from the left.
    * **merged_top**: `true` if this slot continues a cell from above.

### Output Formats

By default, output is JSON. Pass `csv` to output each table as CSV blocks
separated by a `---` delimiter line. Pass `markdown` to output tables in
Markdown format.

### Dependency note

Table extraction requires the `tablers` library. Install it with:

    pip install pdftl[dump-tables]

or directly:

    pip install tablers
"""

_DUMP_TABLES_EXAMPLES = [
    {
        "cmd": "in.pdf dump_tables",
        "desc": "Print tables from in.pdf as JSON to stdout",
    },
    {
        "cmd": "in.pdf dump_tables output tables.json",
        "desc": "Save table data from in.pdf to tables.json",
    },
    {
        "cmd": "in.pdf dump_tables csv output tables.csv",
        "desc": "Save tables from in.pdf as CSV",
    },
    {
        "cmd": "in.pdf dump_tables markdown",
        "desc": "Print tables from in.pdf as Markdown",
    },
    {
        "cmd": "in.pdf dump_tables 1 3-5",
        "desc": "Extract tables from pages 1, 3, 4, and 5",
    },
    {
        "cmd": "in.pdf dump_tables min_rows=2 min_cols=2 no_empty",
        "desc": "Skip likely-spurious tables",
    },
]


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def _table_to_dict(table, page_num: int, table_index: int) -> dict:
    """Convert a tablers Table object to a JSON-serialisable dict."""
    rows_data = []
    for row in table.to_list():
        row_data = []
        for cell in row:
            row_data.append(
                {
                    "text": cell.text,
                    "merged_left": cell.merged_left,
                    "merged_top": cell.merged_top,
                }
            )
        rows_data.append(row_data)

    return {
        "page": page_num,
        "table_index": table_index,
        "bbox": list(table.bbox),
        "rows": len(table.rows),
        "cols": len(table.columns),
        "data": rows_data,
    }


def _extract_tables(pdf: "pikepdf.Pdf", specs: list | None, filters: dict) -> list[dict]:
    """Iterate target pages and extract all tables using tablers."""
    import tablers

    num_pages = len(pdf.pages)
    if not specs:
        target_pages = list(range(1, num_pages + 1))
    else:
        target_pages = sorted(page_numbers_matching_page_specs(specs, num_pages))

    all_tables = []

    # tablers operates on its own Document, so we serialise via bytes
    buf = io.BytesIO()
    pdf.save(buf)
    pdf_bytes = buf.getvalue()

    with tablers.Document(bytes=pdf_bytes) as doc:
        for page_num in target_pages:
            page = doc.get_page(page_num - 1)  # tablers is 0-indexed
            try:
                tables = tablers.find_tables(
                    page,
                    extract_text=True,
                    text_use_text_flow=True,
                    min_columns=filters["min_cols"],
                    min_rows=filters["min_rows"],
                )
            except (ValueError, RuntimeError, IndexError) as exc:
                logger.warning("Page %d: table extraction failed: %s", page_num, exc)
                continue

            for idx, table in enumerate(tables):
                try:
                    all_tables.append(_table_to_dict(table, page_num, idx))
                except ValueError as exc:
                    logger.warning(
                        "Page %d, table %d: could not serialise table: %s", page_num, idx, exc
                    )

    return all_tables


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _clean_cell_text(text: str | None) -> str:
    if text is None:
        return ""
    cleaned = "".join(ch for ch in text if ch.isprintable() or ch in ("\n", "\t"))
    return cleaned.strip()


def _tables_to_csv(tables: list[dict]) -> str:
    """Render extracted tables as CSV blocks separated by '---'."""
    import csv

    blocks = []
    for tbl in tables:
        buf = io.StringIO()
        writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
        for row in tbl["data"]:
            writer.writerow(
                [
                    _clean_cell_text(cell["text"]) if cell["text"] is not None else ""
                    for cell in row
                ]
            )
        header = f"# Page {tbl['page']}, table {tbl['table_index']}"
        blocks.append(f"{header}\n{buf.getvalue().rstrip()}")

    return "\n---\n".join(blocks)


def _tables_to_markdown(tables: list[dict]) -> str:
    """Render extracted tables as Markdown table blocks."""
    blocks = []
    for tbl in tables:
        rows = tbl["data"]
        if not rows:
            continue

        def cell_text(cell):
            return (cell["text"] or "").replace("|", "\\|").replace("\n", " ")

        lines = []
        header_row = rows[0]
        lines.append("| " + " | ".join(cell_text(c) for c in header_row) + " |")
        lines.append("| " + " | ".join("---" for _ in header_row) + " |")
        for row in rows[1:]:
            lines.append("| " + " | ".join(cell_text(c) for c in row) + " |")

        header = f"<!-- Page {tbl['page']}, table {tbl['table_index']} -->"
        blocks.append(f"{header}\n" + "\n".join(lines))

    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _parse_dump_tables_args(op_args: list) -> tuple[list, str, dict]:
    """Split raw OPERATION_ARGS into page specs, output format, and filters."""
    fmt = "json"
    page_specs = []
    filters = {
        "min_rows": None,
        "min_cols": None,
        "min_area": None,
        "no_empty": False,
    }
    for token in op_args or []:
        if token in ("csv", "markdown"):
            fmt = token
        elif token == "no_empty":
            filters["no_empty"] = True
        elif token.startswith("min_rows="):
            filters["min_rows"] = int(token.split("=")[1])
        elif token.startswith("min_cols="):
            filters["min_cols"] = int(token.split("=")[1])
        elif token.startswith("min_area="):
            filters["min_area"] = float(token.split("=")[1])
        else:
            page_specs.append(token)
    return page_specs, fmt, filters


def _passes_filters(tbl: dict, filters: dict) -> bool:
    """Return False if the table should be excluded based on filter settings."""
    if filters["min_rows"] and tbl["rows"] < filters["min_rows"]:
        return False
    if filters["min_cols"] and tbl["cols"] < filters["min_cols"]:
        return False
    if filters["min_area"] is not None:
        x1, y1, x2, y2 = tbl["bbox"]
        if (x2 - x1) * (y2 - y1) < filters["min_area"]:
            return False
    if filters["no_empty"]:
        all_empty = all(not _clean_cell_text(cell["text"]) for row in tbl["data"] for cell in row)
        if all_empty:
            return False
    return True


# ---------------------------------------------------------------------------
# CLI hook
# ---------------------------------------------------------------------------


def dump_tables_cli_hook(result: OpResult, stage, _pipeline):
    """Write table data to stdout or a file in the requested format."""
    output_file = from_result_meta(result, c.META_OUTPUT_FILE)
    fmt = (result.meta or {}).get("fmt", "json")
    tables = result.data

    with smart_open_maybe_dash(output_file) as f:
        if fmt == "csv":
            f.write(_tables_to_csv(tables) + "\n")
        elif fmt == "markdown":
            f.write(_tables_to_markdown(tables) + "\n")
        else:
            compact = compact_json_string(
                json.dumps({"tables": tables}, indent=2), fold_dicts=False
            )
            f.write(compact + "\n")


# ---------------------------------------------------------------------------
# Operation
# ---------------------------------------------------------------------------


@register_operation(
    "dump_tables",
    tags=["info", "tables", "text"],
    type="single input operation",
    desc="Extract tables to JSON, CSV, or Markdown",
    long_desc=_DUMP_TABLES_LONG_DESC,
    examples=_DUMP_TABLES_EXAMPLES,
    cli_hook=dump_tables_cli_hook,
    usage="<input> dump_tables [csv|markdown] [<page_spec>...] [output <output>]",
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {"output_file": c.OUTPUT}),
    skip_pipeline_save=True,
)
def dump_tables(pdf: "pikepdf.Pdf", specs, output_file=None) -> OpResult:
    """Extract tables from a PDF file."""
    ensure_dependencies(
        feature_name="dump_tables",
        dependencies={"tablers": "tablers"},
        extra_tag="dump-tables",
    )

    page_specs, fmt, filters = _parse_dump_tables_args(specs)
    tables = _extract_tables(pdf, page_specs or None, filters)
    tables = [t for t in tables if _passes_filters(t, filters)]

    logger.debug("Extracted %d table(s) total.", len(tables))

    return OpResult(
        success=True,
        data=tables,
        meta={
            c.META_OUTPUT_FILE: output_file,
            "fmt": fmt,
        },
    )
