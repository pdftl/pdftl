# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/pages/add_pages.py

"""Utilities for adding pages to a PDF"""

import logging

logger = logging.getLogger(__name__)
from pikepdf import Name

from pdftl.pages.link_remapper import create_link_remapper
from pdftl.pages.links import (
    RebuildLinksPartialContext,
    rebuild_links,
    write_named_dests,
)
from pdftl.pages.outlines import rebuild_outlines
from pdftl.utils.page_specs import PageTransform
from pdftl.utils.scale import apply_scaling


def _apply_rotation(page, source_page, rotation):
    """
    Applies the specified rotation to a page object.

    Args:
        page: The destination pikepdf.Page object to modify.
        source_page: The original source pikepdf.Page object.
        rotation: A tuple (angle, absolute) specifying the rotation.
    """
    angle, absolute = rotation
    if absolute or angle != 0:
        current_rotation = source_page.get(Name.Rotate, 0)
        page.Rotate = angle if absolute else current_rotation + angle


def add_pages(new_pdf, opened_pdfs, source_pages_to_process: [PageTransform]):
    """
    Add pages opened pdf file new_pdf.
    Arguments:
        source_pages_to_process: A list of PageTransform instances

    """
    # --- PASS 1: Copy page structure, content, and apply transformations. ---
    logger.debug("--- PASS 1: Assembling %s pages... ---", len(source_pages_to_process))
    rebuild_context = process_source_pages(new_pdf, source_pages_to_process)

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
    outline_dests = rebuild_outlines(
        new_pdf, source_pages_to_process, rebuild_context, remapper
    )
    all_dests.extend(outline_dests)

    # Pass 2c: Write all collected destinations to the NameTree
    if all_dests:
        write_named_dests(new_pdf, all_dests)


def process_source_pages(
    new_pdf, source_pages_to_process: [PageTransform]
) -> RebuildLinksPartialContext:
    """
    Handles PASS 1: Assembling pages and applying transformations.

    This function iterates through source pages, copies them to the new PDF,
    applies transformations, and builds the necessary data structures for link
    rebuilding in PASS 2.

    It uses a "pristine copy" technique: for each unique source page, an
    unmodified copy is first added to ensure all necessary objects from the
    source PDF are available in the new document's context. The actual
    (potentially transformed) page is then added. Finally, all temporary
    pristine copies are deleted.

    Args:
        new_pdf: The pikepdf.Pdf object being built.
        source_pages_to_process: A list of PageTransform instances

    Returns:
        A RebuildLinksPartialContext instance
    """
    ret = RebuildLinksPartialContext()

    instance_counts = {}
    seen_pages = set()
    pristine_copy_indices = []

    for page_data in source_pages_to_process:
        ret.unique_source_pdfs.add(page_data.pdf)
        source_page = page_data.pdf.pages[page_data.index]
        page_key = (id(page_data.pdf), page_data.index)

        # If a page is seen for the first time, add a pristine (unmodified)
        # copy to establish object context. Its index is recorded for later
        # deletion.
        page_identity = (page_data.pdf, page_data.index)
        if page_identity not in seen_pages:
            new_pdf.pages.append(source_page)
            seen_pages.add(page_identity)
            pristine_copy_indices.append(len(new_pdf.pages) - 1)

        # Append the actual page that will be used and transformed.
        new_pdf.pages.append(source_page)
        new_page = new_pdf.pages[-1]

        # Track page instances for link rebuilding.
        instance_num = instance_counts.get(page_key, 0)
        instance_counts[page_key] = instance_num + 1

        # Populate data structures needed for link rebuilding in PASS 2.
        ret.page_map[(*page_key, instance_num)] = new_page
        ret.processed_page_info.append((*page_identity, instance_num))
        ret.page_transforms[new_page.obj.objgen] = (page_data.rotation, page_data.scale)

        # Apply transformations to the new page.
        _apply_rotation(new_page, source_page, page_data.rotation)
        apply_scaling(new_page, page_data.scale)

    # Clean up by deleting the temporary pristine copies in reverse order
    # to avoid index shifting issues.
    for idx in sorted(pristine_copy_indices, reverse=True):
        del new_pdf.pages[idx]

    return ret
