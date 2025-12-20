# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/pages/outlines.py

"""
Utilities for rebuilding document outlines (bookmarks).

This module implements the "chunk-based" algorithm for merging document
outlines. It iterates through the input page specifications as "chunks"
(e.g., A, B1-3, A6-8) and appends the corresponding section of each
source document's outline tree.

This ensures that "cat A B A" results in the outline for A, followed by
the outline for B, followed by the outline for A again.
"""

import logging
from collections import namedtuple

logger = logging.getLogger(__name__)
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Pdf

from pdftl.pages.link_remapper import LinkRemapper
from pdftl.pages.links import RebuildLinksPartialContext

# A helper to store info about each "chunk" of pages.
# page_count = total number of pages in this chunk
# source_page_map = {source_page_idx: first_chunk_page_idx}
ChunkData = namedtuple(
    "ChunkData",
    [
        "pdf",  # Source pikepdf.Pdf object
        "source_page_map",  # dict: {source_page_idx: chunk_page_idx}
        "output_start_page",  # 1-based page index in output PDF
        "instance_num",  # the instance_num for this chunk
    ],
)


# pylint: disable=too-few-public-methods
class OutlineCopier:
    """Class for copying an outline intelligently"""

    def __init__(self, remapper):
        self.remapper = remapper
        self.new_dests_list = []

    def copy_item(self, source_item, new_parent_list):
        """
        Recursively copies a source outline item, remaps its destination,
        and prunes it if it's no longer valid.

        This function uses the LinkRemapper to handle all destination
        types (explicit, named, action) and coordinate transformations.
        """
        from pikepdf import OutlineItem

        logger.debug("  source_item title is '%s'", source_item.title)
        # --- 1. Get/Create a GoTo Action Dictionary ---
        source_action = _get_source_action(source_item)
        final_destination = None  # This will be passed to the constructor
        is_valid_destination = False

        # --- 2. Remap the Action ---
        if source_action:
            # This single call handles all cases:
            # - Resolves named destinations (using the dest_caches)
            # - Finds the remapped page (using page_map and rev_maps)
            # - Applies coordinate transforms (using page_transforms)
            # - Prunes invalid links
            new_action, new_named_dest = self.remapper.remap_goto_action(source_action)

            # --- Capture the new destination ---
            if new_named_dest:
                # _new_named_dest is a (name_str, dest_array) tuple
                # We use .extend() to add them as flat items ['name', dest]
                self.new_dests_list.extend(new_named_dest)

            if new_action:
                # Success! The new_action.D is the remapped destination
                # (either an Array or a new Name/String).
                is_valid_destination = True
                final_destination = new_action.D

        # --- 3. Create the new item ---
        new_item = OutlineItem(title=source_item.title, destination=final_destination)

        # --- 4. Recurse on children ---
        for source_child in source_item.children:
            self.copy_item(source_child, new_item.children)

        # --- 5. Pruning and Appending ---
        if is_valid_destination or new_item.children:
            new_parent_list.append(new_item)


def _get_source_action(source_item):
    from pikepdf import Dictionary, Name

    source_action = None
    if source_item.destination:
        # Case 1: Has .destination. Wrap it in a /GoTo action.
        source_action = Dictionary(S=Name.GoTo, D=source_item.destination)
    elif source_item.action:
        # Case 2: Has .action. Use it, but only if it's /GoTo.
        if source_item.action.S == Name.GoTo:
            source_action = source_item.action
    return source_action


def rebuild_outlines(
    new_pdf: "Pdf",
    source_pages_to_process: list,
    call_context: RebuildLinksPartialContext,
    remapper: LinkRemapper,
) -> list:
    """
    Rebuilds the document outline (bookmarks) for the new PDF.

    Args:
        new_pdf: The destination pikepd.Pdf object.
        source_pages_to_process: The flat list of PageTransform objects.
        call_context: The RebuildLinksPartialContext from PASS 1.
        pdf_to_input_index (dict): Maps source PDF ids to input order indices.

    Returns:
        list: a flat list of [name, dest, ...] for all new dests.
    """
    logger.debug("rebuild_outlines called. Processing %s pages.", len(source_pages_to_process))
    chunks = _build_outline_chunks(call_context.processed_page_info)
    logger.debug("_build_outline_chunks created %s chunks.", len(chunks))

    if not chunks:
        logger.debug("no chunks found. exiting")
        return []

    # # Build the same caches that rebuild_links() uses.
    # # We need this to resolve named destinations.
    # rev_maps, dest_caches, include_instance, include_pdf_id = _build_link_caches(
    #     call_context.processed_page_info, call_context.unique_source_pdfs
    # )

    # # --- Create a LinkRemapper for this chunk ---
    # remapper_context = RemapperContext(
    #     page_map=call_context.page_map,
    #     rev_maps=rev_maps,
    #     dest_caches=dest_caches,
    #     pdf_to_input_index=pdf_to_input_index,
    #     page_transforms=call_context.page_transforms,
    #     include_instance=include_instance,
    #     include_pdf_id=include_pdf_id,
    # )
    # remapper = LinkRemapper(remapper_context)

    new_dests_from_outlines = []

    with new_pdf.open_outline() as new_outline:
        for chunk in chunks:
            remapper.set_call_context(new_pdf, chunk.pdf, chunk.instance_num)
            _process_chunk(chunk, remapper, new_outline)

    return new_dests_from_outlines


##################################################


@dataclass
class _OutlineChunkState:
    pdf: "Pdf"
    chunks: list
    chunk_map: dict
    page_in_chunk_idx: int
    output_start_page: int
    instance_num: int
    last_src_idx: int


def _build_outline_chunks(processed_page_info: list) -> [ChunkData]:
    """
    Builds a list of "outline chunks" from the processed_page_info.

    A new chunk is created whenever the source PDF, the instance number,
    or page contiguity changes. This fixes the `cat A A` bug.
    """
    chunks = []
    if not processed_page_info:
        return []

    # Start the first chunk
    try:
        current_pdf, first_src_idx, first_inst_num = processed_page_info[0]
    except (IndexError, TypeError, ValueError):
        logger.warning(
            "Could not build outline chunks: processed_page_info is empty or malformed."
        )
        return []

    state = _OutlineChunkState(
        pdf=current_pdf,
        chunks=chunks,
        chunk_map={first_src_idx: 0},  # {source_idx: chunk_idx}
        page_in_chunk_idx=1,
        output_start_page=1,  # 1-based
        instance_num=first_inst_num,
        last_src_idx=first_src_idx,
    )

    for i, data in enumerate(processed_page_info[1:], 1):
        state = _build_outline_chunks_helper(i, data, state)

    # Append the final chunk
    _append_to_chunk_data(state)
    return state.chunks


def _append_to_chunk_data(state: _OutlineChunkState):
    state.chunks.append(
        ChunkData(state.pdf, state.chunk_map, state.output_start_page, state.instance_num)
    )


def _build_outline_chunks_helper(
    i: int, data: tuple, state: _OutlineChunkState
) -> _OutlineChunkState:
    output_page_num = i + 1  # 1-based

    pdf, src_idx, inst_num = data

    is_new_chunk = (
        pdf is not state.pdf or inst_num != state.instance_num or src_idx != state.last_src_idx + 1
    )

    if is_new_chunk:
        _append_to_chunk_data(state)
        state = _OutlineChunkState(
            pdf=pdf,
            chunk_map={src_idx: 0},
            chunks=state.chunks,
            output_start_page=output_page_num,
            instance_num=inst_num,
            page_in_chunk_idx=1,
            last_src_idx=src_idx,
        )
    else:
        state.chunk_map[src_idx] = state.page_in_chunk_idx
        state.page_in_chunk_idx += 1
        state.last_src_idx = src_idx

    return state


def _process_chunk(chunk, remapper: LinkRemapper, new_outline):
    from pikepdf import Name

    source_pdf = chunk.pdf

    # --- Get instance_num from chunk ---
    logger.debug(
        "Processing outline chunk: start_page=%s, instance_num=%s",
        chunk.output_start_page,
        chunk.instance_num,
    )

    has_outlines = bool(source_pdf.Root.get(Name.Outlines))
    logger.debug("Processing chunk. Source PDF has outlines: %s", has_outlines)

    if not has_outlines:
        logger.debug("short-circuiting _process_chunk")
        return

    copier = OutlineCopier(remapper)

    with source_pdf.open_outline() as source_outline:
        root_items = list(source_outline.root)
        logger.debug("[DEBUG] Source outline has %s root items.", len(root_items))
        for source_item in source_outline.root:
            copier.copy_item(
                source_item,
                new_outline.root,  # Append to the new root
            )
