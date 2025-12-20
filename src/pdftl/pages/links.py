# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/pages/links.py
"""
Link rebuilding utilities for reconstructed or merged PDF documents.

When PDF pages from multiple sources are stitched together, link annotations
(/Annots) and named destinations (/Dests) must be reconstructed so that all
internal hyperlinks continue to function correctly. This module provides a
coordinated pipeline to copy, remap, and attach annotations using pikepdf.

It relies on :class:`pdftl.pages.link_remapper.LinkRemapper` for destination
coordinate and name remapping.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

from pdftl.pages.action_handlers import ACTION_HANDLERS, DEFAULT_ACTION_HANDLER
from pdftl.pages.link_remapper import LinkRemapper

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class RebuildLinksPartialContext:
    """
    Holds contextual data required by :func:`rebuild_links`.

    This class groups together per-run information about page mappings,
    transformation matrices, and the source PDFs involved.

    Attributes:
        page_map (dict): Maps (source_pdf_id, page_index, instance_num)
            → target page object.
        page_transforms (dict): Maps target_page.obj.objgen → ((rotation, relative), scale).
        processed_page_info (list): List of (source_pdf, page_index, instance_num)
            describing the source pages processed.
        unique_source_pdfs (set): Set of unique PDF objects contributing pages.
    """

    page_map: dict = field(default_factory=dict)
    page_transforms: dict = field(default_factory=dict)
    processed_page_info: list = field(default_factory=list)
    unique_source_pdfs: set = field(default_factory=set)


def _process_annotation(original_annot, page_idx, remapper: LinkRemapper):
    """
    Copy and remap a single annotation from a source page.

    Attempts to duplicate the annotation in the destination PDF, remap
    its link actions via the appropriate handler, and attach any new
    named destinations if necessary.

    Args:
        original_annot (Dictionary): The annotation dictionary to copy.
        page_idx (int): Index of the source page.
        remapper (LinkRemapper): Active remapper for this PDF context.

    Returns:
        tuple: (new_annotation, new_named_dest_data)
            - new_annotation (Dictionary | None): The copied annotation.
            - new_named_dest_data (list | None): A flat list of (name, dest) pairs.
    """
    from pikepdf import ForeignObjectError, Name

    try:
        new_annot = remapper.pdf.copy_foreign(remapper.source_pdf.make_indirect(original_annot))
    except (ForeignObjectError, ValueError, RuntimeError) as e:
        logger.warning(
            "Skipping potentially corrupt annotation from source page %s. "
            "Reason: %s\nAnnotation: %s",
            page_idx,
            e,
            original_annot,
        )
        return None, None

    if new_annot.get(Name.Subtype) != Name.Link or Name.A not in new_annot:
        # Non-link or non-action annotations are copied as-is
        return new_annot, None

    original_action = original_annot.A
    action_type = original_action.get(Name.S)
    handler = ACTION_HANDLERS.get(action_type, DEFAULT_ACTION_HANDLER)
    new_action, new_named_dest_data = handler(remapper, original_action)

    if new_action:
        new_annot.A = new_action

    return new_annot, new_named_dest_data


def _rebuild_annotations_for_page(
    new_page, source_page, page_idx, remapper: LinkRemapper, pikepdf: Any
):
    """
    Rebuild all annotations for a given page.

    This clears any preexisting annotations, processes each annotation
    from the corresponding source page, remaps its links, and reattaches
    them to the new page.

    Args:
        new_page (pikepdf.Page): The target page to populate.
        source_page (pikepdf.Page): The correpsonding source page.
        page_idx (int): The index of the corresponding source page.
        remapper (LinkRemapper): Configured remapper for this PDF.
        pikepdf: (Any): dependency injection of pikepdf module.

    Returns:
        list: A flat list of (name, dest) tuples for newly created named destinations.
    """
    Array = pikepdf.Array

    if "/Annots" not in source_page:
        return []

    # Ensure we don’t duplicate /Annots from a previous copy
    if "/Annots" in new_page:
        del new_page.Annots

    new_annots = Array()
    page_dests = []

    for annot in source_page.Annots:
        new_annot, new_named_dest = _process_annotation(annot, page_idx, remapper)

        if new_annot:
            new_annot.P = new_page.obj  # Set parent page reference
            new_annots.append(remapper.pdf.make_indirect(new_annot))

        if new_named_dest:
            page_dests.extend(new_named_dest)

    if new_annots:
        new_page.Annots = new_annots

    return page_dests


def write_named_dests(pdf, all_named_dests):
    """
    Attaches a list of (name_str, dest_array) tuples into the PDF's
    /Names /Dests NameTree, creating it if it doesn't exist.

    This function is now centralized and called by add_pages.py.

     Args:
        pdf: The destination PDF.
        all_named_dests (list): A list of (name_str, dest_array) tuples.
    """
    from pikepdf import Dictionary, Name, NameTree

    if not all_named_dests:
        return

    logger.debug("Building/updating NameTree with %s destinations.", len(all_named_dests))

    # Revert to handling the flat list that rebuild_links currently produces.
    # We will refactor this to tuples in a later step.
    assert len(all_named_dests) % 2 == 0
    dest_tree = NameTree.new(pdf)
    for key, value in zip(all_named_dests[0::2], all_named_dests[1::2]):
        dest_tree[str(key)] = value

    if Name.Names not in pdf.Root:
        pdf.Root.Names = Dictionary()

    pdf.Root.Names.Dests = dest_tree.obj


# ---------------------------------------------------------------------------
# Public orchestration function
# ---------------------------------------------------------------------------


def rebuild_links(pdf, processed_page_info: list, remapper: LinkRemapper) -> list:
    """
    Rebuilds link annotations and named destinations for a reconstructed PDF.

    This high-level function iterates over all source pages, remaps
    their annotations via :class:`LinkRemapper`, and attaches newly
    created named destinations back into the document.

    Args:
        pdf (Pdf): The destination PDF being rebuilt.
        processed_page_info (list): List of (source_pdf, page_index, instance_num).
        remapper (LinkRemapper): The pre-configured LinkRemapper instance.

    Returns:
        list: A list of (name_str, dest_array) tuples for all new dests.
    """
    import pikepdf

    logger.debug("--- Remapping links and named destinations ---")

    all_named_dests = []

    # --- 1. CACHE SOURCE PAGES (The Fix) ---
    # Create a map of { source_pdf_id: [page_object_0, page_object_1, ...] }
    # This turns the O(N) tree lookup into an O(1) list lookup.
    source_pages_cache = {}

    # Pre-fill the cache for all unique source PDFs
    unique_sources = {info[0] for info in processed_page_info}
    for src in unique_sources:
        source_pages_cache[id(src)] = list(src.pages)

    annots_key = pikepdf.Name("/Annots")

    # --- 2. LOOP ---
    for target_page, (src_pdf, page_idx, instance_num) in zip(pdf.pages, processed_page_info):
        source_page_obj = source_pages_cache[id(src_pdf)][page_idx]
        if annots_key not in source_page_obj:
            continue

        remapper.set_call_context(pdf, src_pdf, instance_num)

        new_page_dests = _rebuild_annotations_for_page(
            target_page,
            source_page_obj,
            page_idx,
            remapper,
            pikepdf,
        )
        all_named_dests.extend(new_page_dests)

    logger.debug("--- Finished remapping links (returning %s dests) ---", len(all_named_dests))
    return all_named_dests
