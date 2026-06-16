# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/pdf_text/global_stream_mapper.py

import bisect
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pypdfium2


class GlobalStreamMapper:
    """Consolidates page streams while tracking text coordinates to strip margins on the fly."""

    def __init__(
        self,
        text_provider: Any,
        num_pages: int,
        doc: "pypdfium2.PdfDocument",
        margins: dict[str, float],
    ):
        self.tp = text_provider
        self.num_pages = num_pages
        self.margins = margins
        self.page_offsets: list[int] = []
        self.global_text: list[str] = []
        self.page_sizes: list[tuple[float, float]] = []

        self._build_stream_map(doc)

    def _build_stream_map(self, doc: Any) -> None:
        """Processes each page and populates the global text stream map."""
        current_offset = 0

        for page_num in range(self.num_pages):
            self.page_offsets.append(current_offset)
            page_text = self.tp.get_text(page_num)
            self.global_text.append(page_text)
            self.page_sizes.append(doc[page_num].get_size())
            current_offset += len(page_text)

        self.full_stream = "".join(self.global_text)

    def _is_inside_margin(
        self,
        bbox: tuple[float, float, float, float],
        page_width: float,
        page_height: float,
    ) -> bool:
        """Evaluates layout coordinates using clean partial overlap checking."""
        x0, y0, x1, y1 = bbox
        margins = self.margins

        if margins["top"] > 0 and y1 >= (page_height - margins["top"]):
            return True
        if margins["bottom"] > 0 and y0 <= margins["bottom"]:
            return True
        if margins["left"] > 0 and x0 <= margins["left"]:
            return True
        if margins["right"] > 0 and x1 >= (page_width - margins["right"]):
            return True

        return False

    def _compute_local_bounds(
        self, page_num: int, global_start: int, global_end: int, end_page: int
    ) -> tuple[int, int]:
        """Calculates page-confined local boundaries."""
        page_start_offset = self.page_offsets[page_num]
        local_start = max(0, global_start - page_start_offset)

        if page_num == end_page:
            return local_start, global_end - page_start_offset

        next_page_offset = self.page_offsets[page_num + 1]
        return local_start, next_page_offset - page_start_offset

    def resolve_span(self, global_start: int, global_end: int) -> list[tuple[int, int, int]]:
        """Takes a global start/end index and splits it into a list of page-specific spans."""
        if global_start >= global_end:
            return []

        start_page = bisect.bisect_right(self.page_offsets, global_start) - 1
        end_page = bisect.bisect_right(self.page_offsets, global_end - 1) - 1

        spans = []
        for page_num in range(start_page, end_page + 1):
            local_start, local_end = self._compute_local_bounds(
                page_num, global_start, global_end, end_page
            )
            spans.append((page_num, local_start, local_end))

        return spans
