# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/text_templates.py

"""
Shared utilities for {variable} template expansion in PDF text operations.

This module centralises the context-building and rendering logic used by
add_text, add_bookmarks, and any future operation that needs to expand
dynamic variables into strings.

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
      - Simple:      {page}, {filename}, {total}, ...
      - Arithmetic:  {page+100}, {page-1}
      - Formatting:  {page:06d}, {page+5000:06d}
      - Complex:     {total-page}
      - Metadata:    {meta:Title}
      - Escaping:    {{literal braces}}
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pikepdf

import pdftl.core.constants as c

logger = logging.getLogger(__name__)


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
    # --- Document metadata ---
    try:
        metadata = {str(k).lstrip("/"): str(v) for k, v in pdf.docinfo.items()}
    except (AttributeError, TypeError, ValueError):
        logger.warning("Could not read PDF metadata for variable substitution.")
        metadata = {}

    # --- Filename ---
    filename = ""
    filename_base = ""
    filepath = ""
    if pdf.filename:
        p = Path(pdf.filename)
        filename = p.name
        filename_base = p.stem
        filepath = str(p)

    # --- Timestamp (frozen once per operation) ---
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
    the current page's own properties are used as a fallback so that
    {filename}, {source_page}, etc. still produce sensible values.

    Args:
        static_context: The dict returned by build_static_context.
        page:           The pikepdf.Page being processed (0-based internally,
                        but page_num is 1-based as seen by the user).
        page_num:       1-based page number.

    Returns:
        A merged context dict ready for render_template.
    """
    ctx = {**static_context, "page": page_num}

    # Attempt to read source metadata stashed by the pipeline
    source_meta = getattr(page, c.PDFTL_SOURCE_INFO_KEY, None)

    if source_meta:
        # Keys are stored with a leading "/" in the PDF dict; strip it for
        # the template context so {source_filename} works as expected.
        ctx.update({k.lstrip("/"): v for k, v in source_meta.items()})
    else:
        # Fallback: treat the current file as its own source.
        rotation = int(page.get("/Rotate", 0)) % 360

        # Physical dimensions from the page box
        box = page.trimbox
        phys_w = float(box[2] - box[0])
        phys_h = float(box[3] - box[1])

        # Visual dimensions (swap for 90/270 rotation)
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


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------


def render_template(template: str, context: dict) -> str:
    """
    Render a template string against a context dict.

    Delegates to the tokeniser and renderer in add_text_parser, so the
    full variable syntax is supported:

      {page}              simple variable
      {page+100}          arithmetic offset
      {page:06d}          Python format spec
      {page+5000:06d}     combined
      {total-page}        complex expression
      {meta:Title}        document metadata lookup
      {{literal}}         escaped braces

    Hyperlink syntax ([text](url)) is also parsed; only the display text
    portion is included in the returned string (the URL is discarded),
    which is appropriate for bookmark titles.

    Args:
        template: A template string, e.g. "{filename_base} - p.{page}".
        context:  A context dict as returned by build_page_context.

    Returns:
        The rendered string.

    Raises:
        ValueError: If the template contains an unknown variable or a
                    formatting error (e.g. applying arithmetic to a
                    non-numeric variable).
    """
    from pdftl.operations.parsers.add_text_parser import (
        _default_renderer,
        _tokenize_text_string,
    )

    parts = _tokenize_text_string(template)
    return _default_renderer(parts, context)
