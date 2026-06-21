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
    from pikepdf import Pdf, OutlineItem

from pdftl.pages.link_remapper import LinkRemapper
from pdftl.pages.links import RebuildLinksPartialContext

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


@dataclass
class CachedOutlineItem:
    """Pure Python representation of an outline item to avoid lazy-loading loops."""

    title: str
    action: Any
    is_closed: bool
    obj: Any
    children: list["CachedOutlineItem"]


def _cache_outline_tree(items) -> list[CachedOutlineItem]:
    """Recursively converts a pikepdf Outline tree into an eager Python cache."""
    from pikepdf import Name

    cached = []
    for item in items:
        # /Count *should* always be present
        # But if not, viewers seems to assume the item is closed. So we do that too.
        # The pikepdf default is 'open' so we need to set it explicitly in this case.
        cached.append(
            CachedOutlineItem(
                title=item.title,
                action=_get_source_action(item),
                is_closed=item.is_closed or Name.Count not in item.obj,
                obj=item.obj,
                children=_cache_outline_tree(item.children),
            )
        )
    return cached


# pylint: disable=too-few-public-methods
class OutlineCopier:
    """Class for copying an outline intelligently"""

    def __init__(self, remapper):
        self.remapper = remapper
        self.new_dests_list = []

    def _remap_item_action(self, action) -> tuple[Any, bool]:
        """Remaps the item action and extracts the destination."""
        if not action:
            return None, False

        new_action, new_named_dest = self.remapper.remap_goto_action(action)
        if new_named_dest:
            # remap_goto_action returns a 2-tuple (name, dest)
            # .extend() intentionally flattens this into a flat list for write_named_dests
            self.new_dests_list.extend(new_named_dest)

        if new_action:
            return new_action.D, True
        return None, False

    def _apply_cached_meta(self, cached_item: "CachedOutlineItem", new_item: "OutlineItem"):
        """Extracts and sets formatting metadata manually onto the item."""
        from pikepdf import Name

        if cached_item.obj is None:
            return

        if Name.C in cached_item.obj:
            new_item._cached_color = [float(c) for c in cached_item.obj[Name.C]]
        if Name.F in cached_item.obj:
            new_item._cached_flags = int(cached_item.obj[Name.F])

        new_item.is_closed = cached_item.is_closed

    def copy_item(self, cached_item: "CachedOutlineItem") -> "OutlineItem | None":
        """
        Recursively copies a source outline item, remaps its destination,
        and prunes it if it's no longer valid.
        """
        from pikepdf import OutlineItem

        # --- 1. Remap Destination Actions ---
        final_dest, is_valid_dest = self._remap_item_action(cached_item.action)

        # --- 2. Recurse on children (Bottom-Up) ---
        valid_children = [
            new_child
            for child in cached_item.children
            if (new_child := self.copy_item(child)) is not None
        ]

        # --- 3. Pruning ---
        if not is_valid_dest and not valid_children:
            return None

        # --- 4. Instantiate and apply stashed attributes ---
        new_item = OutlineItem(title=cached_item.title, destination=final_dest)
        self._apply_cached_meta(cached_item, new_item)
        new_item.children.extend(valid_children)

        return new_item


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


def _apply_formatting_and_state(item):
    """Post-processing layout step executed after outline tree closes."""
    from pikepdf import Name

    if hasattr(item, "obj") and item.obj is not None:
        if hasattr(item, "_cached_color"):
            item.obj[Name.C] = item._cached_color
        if hasattr(item, "_cached_flags"):
            item.obj[Name.F] = item._cached_flags

    for child in item.children:
        _apply_formatting_and_state(child)


def rebuild_outlines(
    new_pdf: "Pdf",
    source_pages_to_process: list,
    call_context: RebuildLinksPartialContext,
    remapper: LinkRemapper,
) -> list:
    logger.debug("rebuild_outlines called. Processing %s pages.", len(source_pages_to_process))
    chunks = _build_outline_chunks(call_context.processed_page_info)
    logger.debug("_build_outline_chunks created %s chunks.", len(chunks))

    if not chunks:
        logger.debug("no chunks found. exiting")
        return []

    new_dests_list: list[Any] = []
    source_outline_caches = {}
    final_root_items = []

    with new_pdf.open_outline() as outline:
        from pikepdf.models.outlines import Outline

        for chunk in chunks:
            remapper.set_call_context(new_pdf, chunk.pdf, chunk.instance_num)

            if chunk.pdf not in source_outline_caches:
                source_outline_caches[chunk.pdf] = _cache_outline_tree(Outline(chunk.pdf).root)
            cached_root_items = source_outline_caches[chunk.pdf]

            chunk_dests, new_items = _process_chunk(chunk, remapper, cached_root_items)
            new_dests_list.extend(chunk_dests)
            outline.root.extend(new_items)
            final_root_items.extend(new_items)

    # --- Post-Processing: Run AFTER the outline context manager closes ---
    for root_item in final_root_items:
        _apply_formatting_and_state(root_item)

    return new_dests_list


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


def _process_chunk(chunk, remapper: LinkRemapper, cached_root_items: list) -> tuple[list, list]:
    """
    Process a single outline chunk entirely in pure Python memory.
    """
    logger.debug(
        "Processing outline chunk: start_page=%s, instance_num=%s",
        chunk.output_start_page,
        chunk.instance_num,
    )
    logger.debug("Source outline has %s cached root items.", len(cached_root_items))

    if not cached_root_items:
        return [], []

    copier = OutlineCopier(remapper)
    new_items = []

    for cached_item in cached_root_items:
        new_item = copier.copy_item(cached_item)
        if new_item is not None:
            new_items.append(new_item)

    return copier.new_dests_list, new_items
