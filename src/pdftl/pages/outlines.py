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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pikepdf import Pdf

from pdftl.pages.link_remapper import LinkRemapper
from pdftl.pages.links import RebuildLinksPartialContext
from pdftl.utils.progress import get_track_progress

logger = logging.getLogger(__name__)

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
        new_pdf: The destination pikepdf.Pdf object.
        source_pages_to_process: The flat list of PageTransform objects.
        call_context: The RebuildLinksPartialContext from PASS 1.
        remapper: The pre-configured LinkRemapper instance.

    Returns:
        list: a flat list of [name, dest, ...] for all new dests.
    """
    logger.debug("rebuild_outlines called. Processing %s pages.", len(source_pages_to_process))
    chunks = _build_outline_chunks(call_context.processed_page_info)
    logger.debug("_build_outline_chunks created %s chunks.", len(chunks))

    if not chunks:
        logger.debug("no chunks found. exiting")
        return []

    new_dests_from_outlines: list[Any] = []

    track = get_track_progress(interactive=True)
    with new_pdf.open_outline() as new_outline:
        for chunk in track(chunks, description="Bookmark chunk handling", transient=True):
            remapper.set_call_context(new_pdf, chunk.pdf, chunk.instance_num)

            # Instantiate without using as a context manager — we are reading only.
            # The context manager's __exit__ unconditionally calls _save(), which
            # re-serialises the entire outline tree even if nothing was modified.
            # Accessing .root directly populates the cache without triggering write-back.
            from pikepdf.models.outlines import Outline

            source_outline = Outline(chunk.pdf)
            root_items = list(source_outline.root)

            chunk_dests = _process_chunk(chunk, remapper, new_outline, root_items)
            new_dests_from_outlines.extend(chunk_dests)

    return new_dests_from_outlines


##################################################


@dataclass
class _OutlineChunkState:
    pdf: "Pdf"
    chunks: list[ChunkData]
    chunk_map: dict
    page_in_chunk_idx: int
    output_start_page: int
    instance_num: int
    last_src_idx: int


def _build_outline_chunks(processed_page_info: list) -> list[ChunkData]:
    """
    Builds a list of "outline chunks" from the processed_page_info.

    A new chunk is created whenever the source PDF, the instance number,
    or page contiguity changes. This fixes the `cat A A` bug.
    """
    chunks: list[ChunkData] = []
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


def _process_chunk(chunk, remapper: LinkRemapper, new_outline, root_items: list) -> list:
    """
    Process a single outline chunk, copying its items into new_outline.

    Args:
        chunk: ChunkData describing the source PDF and page range.
        remapper: Active LinkRemapper for this chunk.
        new_outline: The destination Outline object (open context manager).
        root_items: Pre-loaded list of OutlineItem objects from the source PDF.
            Passed in rather than loaded here so that callers control the
            Outline lifecycle and tests can inject items directly.

    Returns:
        list: Flat list of (name, dest) pairs for new named destinations.
    """
    logger.debug(
        "Processing outline chunk: start_page=%s, instance_num=%s",
        chunk.output_start_page,
        chunk.instance_num,
    )
    logger.debug("Source outline has %s root items.", len(root_items))

    if not root_items:
        return []

    copier = OutlineCopier(remapper)

    # Capture new_outline.root once — accessing it repeatedly re-parses the
    # outline tree on each call.
    new_root = new_outline.root
    for source_item in root_items:
        copier.copy_item(source_item, new_root)

    return copier.new_dests_list
