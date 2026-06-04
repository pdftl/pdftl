# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/text_templates.py

"""
Shared utilities for {variable} template expansion in PDF text operations.

This module centralises the context-building, tokenising, and rendering
logic used by add_text, add_bookmarks, and any future operation that needs
to expand dynamic variables into strings.

Public API
----------
build_static_context(pdf) -> dict
    Build the parts of the context that are constant across all pages:
    filename, total page count, document metadata, and the current
    timestamp. Call this once per operation.

build_page_context(static_context, page, page_num) -> dict
    Merge a static context with per-page source metadata (stashed by the
    pipeline during cat/shuffle etc.). Falls back gracefully when source
    metadata is absent — i.e. when the PDF was opened directly rather
    than assembled through the pipeline.

render_template(template, context) -> str
    Render a template string containing {variable} expressions against a
    context dict.  Supports the full variable syntax:
      - Simple:       {page}, {filename}, {total}, ...
      - Arithmetic:   {page+100}, {page-1}
      - Formatting:   {page:06d}, {page+5000:06d}
      - Complex:      {total-page}
      - Metadata:     {meta:Title}
      - Count:        {count} / {n}  (1-based ordinal within current spec)
      - Global count: {global_count} (ever-incrementing, never resets)
      - Escaping:     {{literal braces}}

reset_global_count() -> None
    Reset the global counter to 0. Call at the start of any test that
    uses {global_count} to ensure a predictable starting value.
"""

import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pikepdf

import pdftl.core.constants as c

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Global counter
# ---------------------------------------------------------------------------
# Incremented every time {global_count} is expanded. Never resets
# automatically — it persists for the lifetime of the process, so each
# pdftl CLI invocation starts at 0. Tests must call reset_global_count()
# before exercising {global_count} to ensure deterministic values.

_global_count: int = 0
_global_count_lock = threading.Lock()


def reset_global_count() -> None:
    """Reset the global counter to 0. Exposed primarily for testing."""
    global _global_count
    with _global_count_lock:
        _global_count = 0


def _get_and_increment_global_count() -> int:
    global _global_count
    with _global_count_lock:
        _global_count += 1
        return _global_count


# ---------------------------------------------------------------------------
# Variable sets
# ---------------------------------------------------------------------------

# Variables to which arithmetic (+/-) may be applied
NUMERIC_VARS = {
    "page",
    "total",
    "source_page",
    "source_rotation",
    "source_width",
    "source_height",
    "count",
    "n",  # backward-compat alias for count
    "global_count",
}

# All recognised variable names (excluding special-cased ones like
# total-page and meta:* which are handled by their own regexes)
KNOWN_VARS = {
    "page",
    "total",
    "filename",
    "filename_base",
    "filepath",
    "date",
    "time",
    "datetime",
    "count",
    "n",  # backward-compat alias for count
    "global_count",
    # Source metadata variables (stashed by the pipeline)
    "source_filename",
    "source_path",
    "source_page",
    "source_rotation",
    "source_width",
    "source_height",
    "source_orientation",
    "source_cropbox",
    "source_mediabox",
    "source_filesize",
}


# ---------------------------------------------------------------------------
# Compiled regexes
# ---------------------------------------------------------------------------

# Captures either an escaped block {{...}} OR a variable block {...}
# Intentionally disallows nested braces inside a single token to avoid
# cross-brace overmatching on malformed templates.
TOKEN_REGEX = re.compile(r"(\{\{[^{}]*\}\}|\{[^{}]*\})")

LINK_REGEX = re.compile(r"\[([^\]\\]*(?:\\.[^\]\\]*)*)\]\(([^)]*)\)")

# Matches: {total-page}
COMPLEX_VAR_REGEX = re.compile(r"^\s*(total-page)\s*$")

# Matches: {meta:SomeKey}
META_VAR_REGEX = re.compile(r"^\s*(meta:\w+)\s*$", re.IGNORECASE)

# Master variable regex — handles simple vars, optional arithmetic, optional format spec.
# Examples: "page", "page+1", "page:06d", "page+5000:06d"
MASTER_VAR_REGEX = re.compile(
    r"^\s*(?P<var>[a-zA-Z_]\w*)"  # variable name
    r"(?:\s*(?P<op>[+-])\s*(?P<num>\d+))?"  # optional arithmetic (+/- int)
    r"(?::(?P<fmt>.+))?"  # optional format specifier
    r"\s*$"
)


# ---------------------------------------------------------------------------
# Variable parsing and evaluation
# ---------------------------------------------------------------------------


def _parse_var_expression(expr: str) -> tuple:
    """
    Parse the inner content of a {variable} block into a token tuple.

    Returns one of:
      ("total-page", None, {})
      ("meta:<Key>", None, {})
      (var_name, "master", (offset_int, fmt_str_or_None))

    Raises ValueError for unknown or malformed expressions.
    """
    # 1. Complex variables
    if COMPLEX_VAR_REGEX.fullmatch(expr):
        return ("total-page", None, {})

    # 2. Metadata variables
    if match := META_VAR_REGEX.fullmatch(expr):
        meta_key = match.group(1).split(':', 1)[1].lower()
        return (f"meta:{meta_key}", None, {})

    # 3. Master regex: simple / arithmetic / formatting
    if match := MASTER_VAR_REGEX.fullmatch(expr):
        groups = match.groupdict()
        var = groups["var"].lower()
        if var not in KNOWN_VARS:
            raise ValueError(f"Unknown variable: {{{var}}}")

        op_val = int(groups["num"]) if groups["num"] else 0
        if groups["op"] == "-":
            op_val = -op_val

        if op_val != 0 and var not in NUMERIC_VARS:
            raise ValueError(f"Cannot apply arithmetic to non-numeric variable: {var}")

        fmt_spec = groups["fmt"]
        return (var, "master", (op_val, fmt_spec))

    raise ValueError(f"Unknown variable expression: {{{expr}}}")


def _evaluate_token(token: tuple, context: dict):
    """
    Evaluate a single parsed token against the runtime context.

    Special cases:
      - total-page:   computed from context
      - meta:<Key>:   looked up in context["metadata"]
      - global_count: side-effecting — increments the module-level counter
      - n:            backward-compat alias, resolved to context["count"]
    """
    var, op, param = token

    # --- Special logic variables ---
    if var == "total-page":
        return context.get("total", 0) - context.get("page", 0)

    if var.startswith("meta:"):
        meta_key = var[5:]
        return context.get("metadata", {}).get(meta_key, "")

    # --- Global counter (side-effecting) ---
    if var == "global_count":
        base_value = _get_and_increment_global_count()

    # --- count is an alias for n ---
    elif var in ("n", "count"):
        base_value = context.get("count", context.get("n", 1))

    # --- Standard context lookup ---
    else:
        base_value = context.get(var, "")

    # --- Apply arithmetic and/or formatting ---
    if op == "master":
        offset, fmt_spec = param

        final_val = base_value
        if offset != 0:
            if isinstance(base_value, int | float):
                final_val = base_value + offset
            else:
                raise ValueError(f"Cannot apply arithmetic to non-numeric variable: {var}")

        if fmt_spec:
            try:
                return "{:{}}".format(final_val, fmt_spec)
            except (ValueError, TypeError) as e:
                raise ValueError(f"Formatting error for {{{var}:{fmt_spec}}}: {e}") from e

        return final_val

    return base_value


# ---------------------------------------------------------------------------
# Tokeniser
# ---------------------------------------------------------------------------


def _find_unit(input_str: str) -> str | None:
    """Return the first matching unit suffix from UNITS, or None."""
    for unit_name in c.UNITS:
        if input_str.endswith(unit_name):
            return unit_name
    return None


def _tokenize_plain_segment(text_str: str) -> list:
    """Tokenise a plain (non-link) segment into literals and variable tokens."""
    parts = []
    for i, part in enumerate(TOKEN_REGEX.split(text_str)):
        if not part:
            continue
        if i % 2 == 0:
            parts.append(part.replace(r"\[", "[").replace(r"\]", "]"))
        elif part.startswith("{{"):
            parts.append(part[1:-1])
        else:
            parts.append(_parse_var_expression(part[1:-1]))
    return parts


def tokenize_text_string(text_str: str) -> list:
    """
    Split a text string into literals, variable tokens, and link tokens.

    Link tokens have the form: ("link", display_parts, url_parts)
    Markdown syntax: [display](url). {variables} are supported in both parts.
    Escaped brackets: \\[ and \\] are treated as literals.
    """
    parts = []
    segments = LINK_REGEX.split(text_str)
    i = 0
    while i < len(segments):
        if segments[i]:
            parts.extend(_tokenize_plain_segment(segments[i]))
        if i + 2 < len(segments):
            parts.append(
                (
                    "link",
                    _tokenize_plain_segment(segments[i + 1]),
                    _tokenize_plain_segment(segments[i + 2]),
                )
            )
            i += 3
        else:
            i += 1
    return parts


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


def render_parts_to_string(parts: list, context: dict) -> str:
    """
    Render a tokenised parts list to a plain string.

    For link tokens, only the display text is included; the URL is discarded.
    This is the correct behaviour for contexts like bookmark titles where
    hyperlinks are not meaningful.
    """
    result = []
    for part in parts:
        if isinstance(part, str):
            result.append(part)
        elif isinstance(part, tuple) and part[0] == "link":
            result.append(render_parts_to_string(part[1], context))
        else:
            result.append(str(_evaluate_token(part, context)))
    return "".join(result)


def _render_part_to_run(part, context) -> tuple[str, str | None]:
    """Render a single part to a (text, url_or_None) run tuple."""
    if isinstance(part, str):
        return (part, None)
    if isinstance(part, tuple) and part[0] == "link":
        return (
            render_parts_to_string(part[1], context),
            render_parts_to_string(part[2], context),
        )
    return (str(_evaluate_token(part, context)), None)


def _render_runs(parts: list, context: dict) -> list[tuple[str, str | None]]:
    """Render all parts to a list of (text, url_or_None) run tuples."""
    return [run for part in parts if (run := _render_part_to_run(part, context)) and run[0]]


def compile_text_renderer(text_str: str):
    """
    Compile a text string into a render function.

    Returns a callable: context -> list of (text, url_or_None) tuples.
    Used by add_text's TextDrawer which needs URL information for hyperlinks.
    """
    parts = tokenize_text_string(text_str)
    return lambda context: _render_runs(parts, context)


# ---------------------------------------------------------------------------
# Public render_template
# ---------------------------------------------------------------------------


def render_template(template: str, context: dict) -> str:
    """
    Render a template string against a context dict.

    Supports the full variable syntax — see module docstring for details.

    Note: "count" / "n" are NOT automatically set — callers must inject
    them into the context dict before calling this function if they want
    per-spec ordinal numbering. "global_count" is handled internally and
    does not need to be in the context.

    Args:
        template: A template string, e.g. "{filename_base} - p.{page}".
        context:  A context dict as returned by build_page_context,
                  optionally augmented with "count" for per-spec ordinal.

    Returns:
        The rendered string.

    Raises:
        ValueError: If the template contains an unknown variable or a
                    formatting error.
    """
    parts = tokenize_text_string(template)
    return render_parts_to_string(parts, context)


# ---------------------------------------------------------------------------
# Static context
# ---------------------------------------------------------------------------


def build_static_context(pdf: "pikepdf.Pdf") -> dict:
    """
    Build the parts of the template context that are constant for all pages
    of a single operation invocation.

    Includes: filename/filepath, total page count, document metadata
    dictionary, and a frozen snapshot of the current date/time.

    Args:
        pdf: The open pikepdf.Pdf object being processed.

    Returns:
        A dict suitable for passing to build_page_context and render_template.
    """
    try:
        metadata = {str(k).lstrip("/"): str(v) for k, v in pdf.docinfo.items()}
    except (AttributeError, TypeError, ValueError):
        logger.warning("Could not read PDF metadata for variable substitution.")
        metadata = {}

    filename = ""
    filename_base = ""
    filepath = ""
    if pdf.filename:
        filepath = pdf.filename
        try:
            p = Path(pdf.filename)
            filename = p.name
            filename_base = p.stem
        except (TypeError, ValueError):
            filename = pdf.filename
            filename_base = pdf.filename

    now = datetime.now()

    return {
        "total": len(pdf.pages),
        "metadata": metadata,
        "filename": filename,
        "filename_base": filename_base,
        "filepath": filepath,
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "datetime": now.isoformat(),
    }


# ---------------------------------------------------------------------------
# Per-page context
# ---------------------------------------------------------------------------


def build_page_context(
    static_context: dict,
    page: "pikepdf.Page",
    page_num: int,
) -> dict:
    """
    Build a full template context for a single page by merging the static
    context with per-page source metadata.

    Source metadata is stashed on the page object by the pipeline
    (add_pages._stash_page_source_data) when pages are assembled via cat,
    shuffle, etc.  When it is absent — i.e. the PDF was opened directly —
    the current page's own properties are used as a fallback.

    Note: "count" and "n" are NOT set here — they are per-spec counters
    that callers inject into the returned dict before calling render_template.

    Args:
        static_context: The dict returned by build_static_context.
        page:           The pikepdf.Page being processed.
        page_num:       1-based page number.

    Returns:
        A merged context dict ready for render_template.
    """
    ctx = {**static_context, "page": page_num}

    source_meta = getattr(page, c.PDFTL_SOURCE_INFO_KEY, None)

    if source_meta:
        ctx.update({k.lstrip("/"): v for k, v in source_meta.items()})
    else:
        rotation = int(page.get("/Rotate", 0)) % 360
        box = page.trimbox
        phys_w = float(box[2] - box[0])
        phys_h = float(box[3] - box[1])

        if rotation in (90, 270):
            vis_w, vis_h = phys_h, phys_w
        else:
            vis_w, vis_h = phys_w, phys_h

        ctx.update(
            {
                "source_filename": static_context.get("filename", ""),
                "source_path": static_context.get("filepath", ""),
                "source_page": page_num,
                "source_rotation": rotation,
                "source_width": vis_w,
                "source_height": vis_h,
                "source_orientation": "Landscape" if vis_w > vis_h else "Portrait",
                "source_cropbox": str(list(page.cropbox)),
                "source_mediabox": str(list(page.mediabox)),
                "source_filesize": "",
            }
        )

    return ctx
