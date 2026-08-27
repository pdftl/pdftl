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


import pdftl.core.constants as c
from pdftl.pages.forms import handle_page_widgets, rebuild_acroform_index
from pdftl.pages.link_remapper import create_link_remapper
from pdftl.pages.links import RebuildLinksPartialContext, rebuild_links, write_named_dests
from pdftl.pages.outlines import rebuild_outlines
from pdftl.utils.progress import get_track_progress
from pdftl.utils.scale import apply_scaling, scale_annotations_in_page

logger = logging.getLogger(__name__)


def _apply_rotation(page, source_page, rotation):
    """
    Applies the specified rotation to a page object.
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
    """
    # --- PASS 1: Copy page structure, content, and apply transformations. ---
    logger.debug("--- PASS 1: Assembling %s pages... ---", len(source_pages_to_process))
    rebuild_context, widget_queue = process_source_pages(new_pdf, source_pages_to_process)

    # --- PASS 2: Rebuild links and destinations. ---
    logger.debug("--- PASS 2: Rebuilding links and destinations... ---")

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

    # scale annotations here
    for objgen, transform in rebuild_context.page_transforms.items():
        _rotate, scale = transform
        if scale != 1.0:
            scale_annotations_in_page(new_pdf.get_object(objgen), scale)

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


def _compute_source_page_meta(src, page_idx, src_page) -> tuple:
    """
    Compute the metadata fields derived purely from a source page.
    These are invariant across all output instances of the same source page,
    so callers should cache the result keyed on (id(src), page_idx).
    """
    src_filename = getattr(src, "filename", "")
    src_basename = os.path.basename(src_filename) if src_filename else ""
    src_abspath = os.path.abspath(src_filename) if src_filename else ""
    orig_rotation = int(src_page.rotation)
    mediabox = src_page.mediabox
    width = float(mediabox[2] - mediabox[0])
    height = float(mediabox[3] - mediabox[1])
    if orig_rotation % 180 != 0:
        width, height = height, width
    orientation = "portrait" if height >= width else "landscape"
    return src_basename, src_abspath, orig_rotation, width, height, orientation


def process_source_pages(
    new_pdf, source_pages_to_process: list["PageTransform"]
) -> tuple[RebuildLinksPartialContext, list]:
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

        A widget queue for later processing.

    OPTIMIZED: Uses a "Clean Master" strategy to allow independent transformations
    of identical source pages without re-importing resources.

    Source-page metadata (filename, dimensions, orientation) is computed lazily
    and cached per (source_pdf, page_index) so it is never recomputed across
    multiple output instances of the same source page.
    """
    from pikepdf import Dictionary, Page

    ret = RebuildLinksPartialContext()
    widget_queue = []

    instance_counts: dict[tuple, int] = {}

    # Map (pdf_id, page_index) -> CLEAN MASTER OBJECT (Indirect Object in new_pdf)
    # This master object holds the page dictionary *before* any rotation/scaling
    # is applied in the new PDF.
    clean_masters_map = {}

    # Pre-cache source pages
    unique_sources = {p.pdf for p in source_pages_to_process}
    source_pages_cache = {}
    for src in unique_sources:
        source_pages_cache[id(src)] = list(src.pages)

    # Lazy cache for per-source-page metadata (filename, dimensions, orientation).
    # Keyed on (id(src), page_idx). Populated on first encounter of each source page.
    # This avoids recomputing os.path.abspath, MediaBox, and /Rotate for every
    # output instance of the same source page (e.g. in `cat A A`).
    source_page_meta_cache: dict[tuple, tuple] = {}

    new_pdf_pages_append = new_pdf.pages.append

    track = get_track_progress(interactive=True)

    for page_data in track(
        source_pages_to_process, description="Processing pages", transient=True
    ):
        pdf_id = id(page_data.pdf)
        source_page = source_pages_cache[pdf_id][page_data.index]
        page_identity = (page_data.pdf, page_data.index)
        page_key = (pdf_id, page_data.index)

        ret.unique_source_pdfs.add(page_data.pdf)
        new_pdf_make_indirect = new_pdf.make_indirect

        if page_key not in clean_masters_map:
            # --- FIRST ENCOUNTER ---
            # 1. Append source directly. pikepdf handles the import.
            new_pdf_pages_append(source_page)

            # 2. Get the new native page object
            new_page = new_pdf.pages[-1]

            # 3. Create the "Clean Master" immediately
            # We make a shallow copy of the dictionary *before* we modify new_page.
            # This ensures we have a pristine reference for future clones.
            master_vars = new_page.obj.copy()
            del master_vars["/Parent"]

            # Store as an indirect object in new_pdf (orphaned from page tree, but valid)
            clean_master = new_pdf_make_indirect(Dictionary(master_vars))
            clean_masters_map[page_key] = clean_master

        else:
            # --- REPEAT ENCOUNTER ---
            # 1. Retrieve the Clean Master (unmodified state)
            clean_master = clean_masters_map[page_key]

            # 2. Clone it
            clone_vars = dict(clean_master)

            # 3. Create new indirect object and wrap as Page
            indirect_copy = new_pdf_make_indirect(Dictionary(clone_vars))
            new_page = Page(indirect_copy)
            new_pdf_pages_append(new_page)

        # --- COMMON POST-PROCESSING ---

        instance_num = instance_counts.get(page_key, 0)
        instance_counts[page_key] = instance_num + 1

        # Lazily compute and cache source-page-invariant metadata.
        # abspath, basename, MediaBox, and /Rotate are computed at most once
        # per unique (source_pdf, page_index) pair regardless of how many
        # output instances are produced.
        if page_key not in source_page_meta_cache:
            source_page_meta_cache[page_key] = _compute_source_page_meta(
                page_data.pdf, page_data.index, source_page
            )
        source_meta = source_page_meta_cache[page_key]

        _stash_page_source_data(new_page, source_meta, page_data, instance_num)

        widget_queue.append((new_pdf, new_page, source_page, instance_num))

        # Store metadata for PASS 2
        ret.page_map[(*page_key, instance_num)] = new_page
        ret.processed_page_info.append((*page_identity, instance_num))

        ret.page_transforms[new_page.obj.objgen] = (page_data.rotation, page_data.scale)

        if page_data.rotation != (0, False):
            _apply_rotation(new_page, source_page, page_data.rotation)
        if page_data.scale != 1.0:
            apply_scaling(new_page, page_data.scale, scale_annotations=False)

    return ret, widget_queue


def _stash_page_source_data(new_page, source_meta, page_data, instance_num):
    """
    Write per-output-page source metadata into the PDF page dictionary.

    source_meta is a pre-computed tuple from _compute_source_page_meta,
    containing fields that are invariant across output instances of the
    same source page. page_data and instance_num vary per output page.
    """
    from pikepdf import Dictionary

    src_basename, src_abspath, orig_rotation, width, height, orientation = source_meta

    info_dict = Dictionary(
        {
            # User-facing variable data
            "/source_filename": src_basename,
            "/source_path": src_abspath,
            "/source_page": page_data.index + 1,
            "/source_rotation": orig_rotation,
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
    )

    new_page["/" + c.PDFTL_SOURCE_INFO_KEY] = info_dict
