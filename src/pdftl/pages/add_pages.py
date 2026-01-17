# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/pages/add_pages.py

"""Utilities for adding pages to a PDF"""

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pikepdf

    from pdftl.utils.page_specs import PageTransform

logger = logging.getLogger(__name__)

import pdftl.core.constants as c
from pdftl.pages.forms import handle_page_widgets, rebuild_acroform_index
from pdftl.pages.link_remapper import create_link_remapper
from pdftl.pages.links import (
    RebuildLinksPartialContext,
    rebuild_links,
    write_named_dests,
)
from pdftl.pages.outlines import rebuild_outlines
from pdftl.utils.scale import apply_scaling


def _apply_rotation(page, source_page, rotation):
    """
    Applies the specified rotation to a page object.

    Args:
        page: The destination pikepdf.Page object to modify.
        source_page: The original source pikepdf.Page object.
        rotation: A tuple (angle, absolute) specifying the rotation.
    """
    from pikepdf import Name

    angle, absolute = rotation
    if absolute or angle != 0:
        current_rotation = source_page.get(Name.Rotate, 0)
        page.Rotate = angle if absolute else current_rotation + angle


def add_pages(
    new_pdf: "pikepdf.Pdf",
    opened_pdfs: list["pikepdf.Pdf"],
    source_pages_to_process: list["PageTransform"],
):
    """
    Add pages to the opened pdf file new_pdf.

    Args:
        new_pdf: The destination PDF object.
        opened_pdfs: List of all open source PDF objects (used for indexing).
        source_pages_to_process: A list of PageTransform instances.

    """
    # --- PASS 1: Copy page structure, content, and apply transformations. ---
    logger.debug("--- PASS 1: Assembling %s pages... ---", len(source_pages_to_process))
    rebuild_context, widget_queue = process_source_pages(new_pdf, source_pages_to_process)

    # --- PASS 2: Rebuild links and destinations. ---
    logger.debug("--- PASS 2: Rebuilding links and destinations... ---")

    # The link rebuilder needs a map from a PDF's memory address to its
    # original index in the input list.
    pdf_to_input_index = {id(pdf): i for i, pdf in enumerate(opened_pdfs)}

    remapper = create_link_remapper(
        page_map=rebuild_context.page_map,
        page_transforms=rebuild_context.page_transforms,
        processed_page_info=rebuild_context.processed_page_info,
        unique_source_pdfs=rebuild_context.unique_source_pdfs,
        pdf_to_input_index=pdf_to_input_index,
    )

    # Pass 2a: Get all destinations from link annotations
    all_dests = rebuild_links(new_pdf, rebuild_context.processed_page_info, remapper)

    # Pass 2b: Get all destinations from outlines
    outline_dests = rebuild_outlines(new_pdf, source_pages_to_process, rebuild_context, remapper)
    all_dests.extend(outline_dests)

    # Pass 2c: Write all collected destinations to the NameTree
    if all_dests:
        write_named_dests(new_pdf, all_dests)

    logger.debug("--- PASS 3: widget wrangling ---")
    for args in widget_queue:
        handle_page_widgets(*args)

    rebuild_acroform_index(new_pdf)


def process_source_pages(
    new_pdf, source_pages_to_process: list["PageTransform"]
) -> RebuildLinksPartialContext:
    """Handles PASS 1: Assembling pages and applying transformations.

    This function iterates through source pages, copies them to the new PDF,
    applies transformations (rotation/scaling), and builds the necessary data
    structures for link rebuilding in PASS 2.

    It implements an optimized resource deduplication strategy:
    1.  **First Encounter:** The page is appended normally. This imports all
        resources (images, fonts) into the new PDF. The resulting page object
        is independent, so transformations can be applied without affecting
        the source.
    2.  **Repeat Encounter:** A new blank page is created, and the source
        dictionary keys (Content, Resources, MediaBox, etc.) are shallow-copied.
        This ensures the new page shares the heavy resources already imported
        during the first encounter, while remaining a distinct object that can
        be rotated or scaled independently.

    Args:
        new_pdf: The pikepdf.Pdf object being built.
        source_pages_to_process: A list of PageTransform instances defining
            the source page and the transformations to apply.

    Returns:
        A RebuildLinksPartialContext instance containing the mapping of
        (source_page, instance_index) -> new_page_object, needed for
        resolving destinations in PASS 2.
    """
    import pikepdf

    ret = RebuildLinksPartialContext()
    widget_queue = []

    instance_counts: dict[tuple, int] = {}
    seen_pages = set()

    # Pre-cache source pages to avoid repeated attribute access/hashing
    unique_sources = {p.pdf for p in source_pages_to_process}
    source_pages_cache = {}
    for src in unique_sources:
        source_pages_cache[id(src)] = list(src.pages)

    # Local variable lookups for speed inside the loop
    new_pdf_pages_append = new_pdf.pages.append
    new_pdf_add_blank = new_pdf.add_blank_page
    new_pdf_copy_foreign = new_pdf.copy_foreign

    for page_data in source_pages_to_process:
        pdf_id = id(page_data.pdf)

        # Fast lookup from cache
        source_page = source_pages_cache[pdf_id][page_data.index]

        page_identity = (page_data.pdf, page_data.index)
        page_key = (pdf_id, page_data.index)

        ret.unique_source_pdfs.add(page_data.pdf)

        if page_identity not in seen_pages:
            # --- FIRST ENCOUNTER ---

            # Make an explicit copy instead of standard append so that
            # we get the handle without an expensive lookup.
            new_page_obj = new_pdf_copy_foreign(source_page.obj)
            new_page = pikepdf.Page(new_page_obj)
            new_pdf_pages_append(new_page)
            seen_pages.add(page_identity)

        else:
            # --- REPEAT ENCOUNTER ---
            # We need a fresh object to allow unique rotation/scaling,
            # but it must share the underlying heavy resources (Images/Fonts).
            new_page = new_pdf_add_blank()

            # Shallow copy keys to point to existing resources in new_pdf
            src_obj = source_page.obj
            for k, v in src_obj.items():
                if k != "/Parent":
                    # The simplest types in PDFs are
                    # directly represented as Python types:
                    # int, bool, and None stand for PDF
                    # integers, booleans and the
                    # “null”. Decimal is used for floating
                    # point numbers in PDFs. If a value in a
                    # PDF is assigned a Python float,
                    # pikepdf will convert it to Decimal.
                    #
                    # Types that are not directly
                    # convertible to Python are represented
                    # as pikepdf.Object, a compound object
                    # that offers a superset of possible
                    # methods, some of which only if the
                    # underlying type is suitable. Use the
                    # EAFP idiom, or isinstance to determine
                    # the type more precisely. This partly
                    # reflects the fact that the PDF
                    # specification allows many data fields
                    # to be one of several types.

                    try:
                        new_val = new_pdf_copy_foreign(page_data.pdf.make_indirect(v))
                    except TypeError:
                        new_val = k
                    new_page[k] = new_val

        # --- COMMON POST-PROCESSING ---

        instance_num = instance_counts.get(page_key, 0)
        instance_counts[page_key] = instance_num + 1

        _stash_page_source_data(new_page, source_page, page_data, instance_num)

        widget_queue.append((new_pdf, new_page, source_page, instance_num))

        # Store metadata for PASS 2
        ret.page_map[(*page_key, instance_num)] = new_page
        ret.processed_page_info.append((*page_identity, instance_num))

        # Record transforms (needed for link coordinate recalculation)
        ret.page_transforms[new_page.obj.objgen] = (page_data.rotation, page_data.scale)

        # Apply visual transformations
        _apply_rotation(new_page, source_page, page_data.rotation)
        apply_scaling(new_page, page_data.scale)

    return ret, widget_queue


def _stash_page_source_data(new_page, source_page, page_data, instance_num):
    # Calculate metadata for variable expansion
    filename = getattr(page_data.pdf, "filename", "")
    # Get original page rotation and dimensions
    orig_rotation = source_page.get("/Rotate", 0)
    # MediaBox is typically [x0, y0, x1, y1]
    mediabox = source_page.MediaBox
    width = float(mediabox[2] - mediabox[0])
    height = float(mediabox[3] - mediabox[1])

    # Handle rotation for width/height (if page is rotated 90/270, swap w/h)
    if orig_rotation % 180 != 0:
        width, height = height, width

    orientation = "portrait" if height >= width else "landscape"

    # Inject comprehensive source data into the PDF page object.
    # NOTE: We only store serializable data here (strings, numbers).
    # We do NOT store the pikepdf.Pdf object itself, as it cannot be
    # serialized to a PDF dictionary.
    # Internal tools (like Link Rebuilding) use the returned
    # RebuildLinksPartialContext to access the PDF objects.
    # Downstream tools (like add_text) use this dictionary for variables.
    info_dict = {
        # User-facing variable data
        "/source_filename": os.path.basename(filename) if filename else "",
        "/source_path": os.path.abspath(filename) if filename else "",
        "/source_page": page_data.index + 1,
        "/source_rotation": int(orig_rotation),
        "/source_width": width,
        "/source_height": height,
        "/source_orientation": orientation,
        # Transformation data (serializable)
        "/applied_rotation_angle": page_data.rotation[0],
        "/applied_rotation_absolute": bool(page_data.rotation[1]),
        "/applied_scale": float(page_data.scale),
        "/original_index": page_data.index,
        "/instance_num": instance_num,
    }
    # Store in a custom key in the PDF Page Dictionary
    new_page["/" + c.PDFTL_SOURCE_INFO_KEY] = info_dict
