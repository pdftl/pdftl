# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/types/info_types.py

"""Data types for PDF info"""

from dataclasses import dataclass, field


@dataclass
class Bookmark:
    title: str
    page_number: int
    level: int


@dataclass
class PageLabel:
    index: int  # 0-based index
    start: int = 1
    style: str | None = None
    prefix: str | None = None


@dataclass
class PageMedia:
    page_number: int  # 1-based (as per standard dumps)
    rotation: int | None = None
    rect: list[float] | None = None  # MediaBox
    crop_rect: list[float] | None = None  # CropBox
    trim_rect: list[float] | None = None  # TrimBox (Added)


@dataclass
class InfoSpec:
    """
    Specification for updating PDF info.
    Fields set to None are ignored (preserve existing).
    Fields set to empty lists [] will explicitly clear that data from the PDF.
    """

    info: dict[str, str] = field(default_factory=dict)

    bookmarks: list[Bookmark] | None = None
    page_labels: list[PageLabel] | None = None
    page_media: list[PageMedia] | None = None

    pdf_id: list[str] | None = None
