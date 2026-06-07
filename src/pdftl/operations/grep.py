# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/grep.py

"""Search text content in a PDF using precise native mapping."""

from __future__ import annotations

import bisect
import io
import logging
import re
from typing import Any

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError
from pdftl.output.dump import dump
from pdftl.utils.dependencies import ensure_dependencies
from pdftl.utils.keyval_parser import parse_keyval_list
from pdftl.utils.page_specs import page_numbers_matching_page_specs
from pdftl.utils.string_utils import compact_json_string

logger = logging.getLogger(__name__)

_GREP_LONG_DESC = r"""
The `grep` operation searches the text content of a PDF for a specified regular
expression or literal string. It outputs a structured JSON array detailing matches,
page locations, context snippets, and precise coordinate bounding boxes.

Arguments:
  * `<pattern>`: The regular expression or text string to search for.
  * `[pages...]`: Optional page ranges (e.g., `1-5`, `9-end`) to restrict the search.
    If omitted, the entire document is searched.

Search Options:
  * `regex=<b>`: If true, treats the pattern as a Python-compatible regular expression.
    If false, matches the pattern as a plain literal string. (Default: true)
  * `ignore_case=<b>` or `i=<b>`: If true, performs a case-insensitive search. (Default: false)
  * `multiline=<b>` or `m=<b>`: If true, `^` and `$` match the start and end of lines.
    (Default: true)
  * `dotall=<b>` or `s=<b>`: If true, the `.` special character matches any character,
    including newlines. (Default: false)
  * `max_count=<N>`: Stop searching and parsing after locating `<N>` total matches.

Context Options:
  * `context=<N>`: Number of surrounding lines of text to include before and after each match.
    (Default: 0)
  * `before_context=<N>`: Number of surrounding lines to include strictly before the match.
  * `after_context=<N>`: Number of surrounding lines to include strictly after the match.

Typographic Filtering:
  You can restrict matches to text that meets specific visual criteria.
  * `min_size=<F>`, `max_size=<F>`: Only match text within a given point-size range.
  * `font_match=<S>`: Only match if the font name contains this substring (e.g., "Bold").
  * `require_bold=<b>`: Only match if the text is explicitly bold.
  * `require_italic=<b>`: Only match if the text is explicitly italicized.
  * `fonts=<b>`: Always extract and output font metadata for matches. Automatically enabled
    if any typographic filters are used. (Default: false)

Output Format:
  The results are written as a JSON object containing global metadata, a match metrics summary
  block (`count`), and a list of `hits`. Each hit contains:
  * `page`, `line`: 1-indexed page and line numbers where the match begins.
  * `text`: The exact string matching the main query.
  * `bboxes`: Coordinate bounding boxes `[x0, y0, x1, y1]` grouped per line.
  * `context_match`: The full string of the line(s) containing the match.
  * `match_start_idx`, `match_end_idx`: 0-indexed character offsets marking where the
    match resides within `context_match`.
  * `context_before`, `context_after`: Arrays of surrounding context lines (if requested).
  * `captures`: If the regex utilizes capture groups (e.g., `Invoice:\s*(\d+)`), this
    array automatically populates with the `group` number, exact `text`, and precise `bboxes`
    for every distinct captured sub-pattern.
"""

_GREP_EXAMPLES = [
    {
        "cmd": r"in.pdf grep 'Total:\s*(\$\d+\.\d{2})'",
        "desc": "Find the phrase and automatically extract the monetary value's bounding box.",
    },
    {
        "cmd": "in.pdf grep '.' regex=true min_size=18 require_bold=true",
        "desc": "Extract all text on the page formatted as a large bold heading.",
    },
]

# --- Argument Parsing Helpers ---


def _parse_bool(kv: dict, key: str, default: bool, alias: str | None = None) -> bool:
    if key in kv:
        return kv[key].lower() in ("true", "1", "yes")
    if alias and alias in kv:
        return kv[alias].lower() in ("true", "1", "yes")
    return default


def _parse_positive_int(kv: dict, key: str) -> int | None:
    if key not in kv:
        return None
    try:
        val = int(kv[key])
        if val <= 0:
            raise ValueError
        return val
    except ValueError as exc:
        raise InvalidArgumentError(f"grep: {key} must be a positive integer.") from exc


def _parse_non_negative_int(kv: dict, key: str, default: int) -> int:
    if key not in kv:
        return default
    try:
        val = int(kv[key])
        if val < 0:
            raise ValueError
        return val
    except ValueError as exc:
        raise InvalidArgumentError(f"grep: {key} must be a non-negative integer.") from exc


def _parse_args(args: list) -> tuple:
    bare_tokens: list[str] = []
    kv = parse_keyval_list(
        args or [],
        bare_tokens=bare_tokens,
        allowed_keys=[
            "regex",
            "ignore_case",
            "i",
            "max_count",
            "context",
            "before_context",
            "after_context",
            "multiline",
            "m",
            "dotall",
            "s",
            "fonts",
            "min_size",
            "max_size",
            "font_match",
            "require_bold",
            "require_italic",
        ],
        context="grep",
    )

    if not bare_tokens:
        raise InvalidArgumentError("grep: Missing search pattern argument.")

    use_regex = _parse_bool(kv, "regex", True)
    ignore_case = _parse_bool(kv, "ignore_case", False, "i")
    multiline = _parse_bool(kv, "multiline", True, "m")
    dotall = _parse_bool(kv, "dotall", False, "s")

    has_filters = any(
        k in kv for k in ["min_size", "max_size", "font_match", "require_bold", "require_italic"]
    )
    fonts = True if has_filters else _parse_bool(kv, "fonts", False)

    max_count = _parse_positive_int(kv, "max_count")
    base_context = _parse_non_negative_int(kv, "context", 0)
    before_context = _parse_non_negative_int(kv, "before_context", base_context)
    after_context = _parse_non_negative_int(kv, "after_context", base_context)

    return (
        bare_tokens[0],
        bare_tokens[1:],
        use_regex,
        ignore_case,
        max_count,
        before_context,
        after_context,
        multiline,
        dotall,
        fonts,
        kv,
    )


# --- Extraction Helpers ---


def _compile_regex(
    pattern: str, use_regex: bool, ignore_case: bool, multiline: bool, dotall: bool
) -> re.Pattern:
    flags = 0
    if ignore_case:
        flags |= re.IGNORECASE
    if multiline:
        flags |= re.MULTILINE
    if dotall:
        flags |= re.DOTALL
    try:
        return re.compile(pattern if use_regex else re.escape(pattern), flags)
    except re.error as exc:
        raise InvalidArgumentError(f"grep: Invalid regular expression pattern: {exc}") from exc


def _build_line_map(text: str) -> tuple[list[str], list[int]]:
    lines = text.split("\n")
    line_starts = []
    current_offset = 0
    for line in lines:
        line_starts.append(current_offset)
        current_offset += len(line) + 1
    return lines, line_starts


def _passes_font_filters(font_info: dict, kv: dict) -> bool:
    if "min_size" in kv and font_info["font_size"] < float(kv["min_size"]):
        return False
    if "max_size" in kv and font_info["font_size"] > float(kv["max_size"]):
        return False
    if "font_match" in kv and kv["font_match"].lower() not in font_info["font_name"].lower():
        return False
    if "require_bold" in kv and font_info["bold"] != _parse_bool(kv, "require_bold", False):
        return False
    if "require_italic" in kv and font_info["italic"] != _parse_bool(kv, "require_italic", False):
        return False
    return True


def _evaluate_fonts(
    tp: Any, page_num: int, start: int, end: int, extract_fonts: bool, kv: dict
) -> tuple[bool, dict | None]:
    has_filters = any(
        k in kv for k in ["min_size", "max_size", "font_match", "require_bold", "require_italic"]
    )
    needs_fonts = extract_fonts or has_filters

    if not needs_fonts:
        return True, None

    font_info = tp.get_font_info(page_num, start, end)
    if font_info and has_filters and not _passes_font_filters(font_info, kv):
        return False, None

    return True, font_info


def _get_line_bounds(
    start_idx: int, end_idx: int, line_starts: list[int], num_lines: int
) -> tuple[int, int]:
    start_line_idx = bisect.bisect_right(line_starts, start_idx) - 1
    start_line_idx = max(0, min(start_line_idx, num_lines - 1))

    end_line_idx = (
        bisect.bisect_right(line_starts, end_idx - 1) - 1
        if end_idx > start_idx
        else start_line_idx
    )
    end_line_idx = max(start_line_idx, min(end_line_idx, num_lines - 1))

    return start_line_idx, end_line_idx


def _extract_captures(match: re.Match, page_num: int, tp: Any) -> list[dict]:
    if match.re.groups == 0:
        return []

    captures = []
    for i in range(1, match.re.groups + 1):
        try:
            c_start, c_end = match.start(i), match.end(i)
            if c_start != -1 and c_start != c_end:
                captures.append(
                    {
                        "group": i,
                        "text": match.group(i),
                        "bboxes": tp.get_bboxes_for_lines(page_num, c_start, c_end),
                    }
                )
        except IndexError:
            continue
    return captures


def _build_hit(
    page_1_indexed: int,
    page_num: int,
    match: re.Match,
    lines: list[str],
    line_starts: list[int],
    before_context: int,
    after_context: int,
    tp: Any,
    extract_fonts: bool,
    kv: dict,
) -> dict | None:
    """Constructs the hit dictionary, processing targeted capture groups and filters."""
    primary_start, primary_end = match.start(0), match.end(0)

    if primary_start == primary_end:
        return None

    is_valid, font_info = _evaluate_fonts(
        tp, page_num, primary_start, primary_end, extract_fonts, kv
    )
    if not is_valid:
        return None

    start_line_idx, end_line_idx = _get_line_bounds(
        primary_start, primary_end, line_starts, len(lines)
    )

    context_match_str = "\n".join(lines[start_line_idx : end_line_idx + 1])
    context_start_offset = line_starts[start_line_idx]

    hit = {
        "page": page_1_indexed,
        "line": start_line_idx + 1,
        "text": match.group(0),
        "bboxes": tp.get_bboxes_for_lines(page_num, primary_start, primary_end),
        "context_match": context_match_str,  # .rstrip("\r"),
        "match_start_idx": primary_start - context_start_offset,
        "match_end_idx": primary_end - context_start_offset,
    }

    captures = _extract_captures(match, page_num, tp)
    if captures:
        hit["captures"] = captures

    if before_context > 0:
        hit["context_before"] = [
            line  # .rstrip("\r")
            for line in lines[max(0, start_line_idx - before_context) : start_line_idx]
        ]
    if after_context > 0:
        hit["context_after"] = [
            line  # .rstrip("\r")
            for line in lines[end_line_idx + 1 : end_line_idx + 1 + after_context]
        ]

    if extract_fonts and font_info:
        hit.update(font_info)

    return hit


def grep_cli_hook(result: OpResult, stage, _pipeline):
    from pdftl.utils.hooks import from_result_meta

    _write_json_output(result.data, from_result_meta(result, c.META_OUTPUT_FILE))


def _write_json_output(output_data, output_file):
    import json

    dump(
        compact_json_string(json.dumps(output_data, indent=2), fold_dicts=False), dest=output_file
    )


# --- Main Operation ---


@register_operation(
    "grep",
    tags=["text", "search", "utility"],
    cli_hook=grep_cli_hook,
    type="single input operation",
    desc="Match text patterns and get bounding boxes",
    long_desc=_GREP_LONG_DESC,
    usage="<input> grep <pattern> [pages...] [key=val...] [output <output>]",
    examples=_GREP_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {"output_file": c.OUTPUT}),
    skip_pipeline_save=True,
)
def grep(pdf, operation_args: list, output_file=None) -> OpResult:
    ensure_dependencies(feature_name="grep", dependencies=["pypdfium2"], extra_tag="grep")
    import pypdfium2 as pdfium
    from pdftl.utils.text_provider import TextProvider

    (
        pattern,
        page_specs,
        use_regex,
        ignore_case,
        max_count,
        before_context,
        after_context,
        multiline,
        dotall,
        extract_fonts,
        kv,
    ) = _parse_args(operation_args)

    num_pages = len(pdf.pages)
    target_pages = (
        sorted(list(page_numbers_matching_page_specs(page_specs, num_pages)))
        if page_specs
        else list(range(1, num_pages + 1))
    )

    logger.debug("Synchronizing current PDF stream to pdfium for search...")
    pdf_bytes = io.BytesIO()
    pdf.save(pdf_bytes)
    pdf_bytes.seek(0)

    tp = TextProvider(pdf_path="", opened_pdfium_doc=pdfium.PdfDocument(pdf_bytes))
    search_regex = _compile_regex(pattern, use_regex, ignore_case, multiline, dotall)

    results = []
    page_counts: dict[int, int] = {}
    total_hits = 0
    stop_searching = False

    for page_1_indexed in target_pages:
        if stop_searching:
            break

        page_num = page_1_indexed - 1
        text = tp.get_text(page_num)
        lines, line_starts = _build_line_map(text)

        for match in search_regex.finditer(text):
            if max_count is not None and total_hits >= max_count:
                stop_searching = True
                break

            hit = _build_hit(
                page_1_indexed,
                page_num,
                match,
                lines,
                line_starts,
                before_context,
                after_context,
                tp,
                extract_fonts,
                kv,
            )
            if hit is None:
                continue

            results.append(hit)
            page_counts[page_1_indexed] = page_counts.get(page_1_indexed, 0) + 1
            total_hits += 1

        tp.clear_page_cache(page_num)

    tp.close()

    output_data = {
        "query": pattern,
        "is_regex": use_regex,
        "ignore_case": ignore_case,
        "count": {
            "total": total_hits,
            "by_page": [[p, c] for p, c in sorted(page_counts.items())],
        },
        "hits": results,
    }
    return OpResult(success=True, data=output_data, meta={c.META_OUTPUT_FILE: output_file})
