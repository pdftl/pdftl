# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/info/info_types.py

"""Types for the info modules and operations"""

from dataclasses import asdict, dataclass, field, fields

from pdftl.utils.type_helpers import safe_create

# --- 1. Serialization Factory ---

# Maps internal Python names to the specific keys pdftk/update_info expects
FIELD_MAPPINGS = {
    "ids": "PdfID",  # 'Ids' != 'PdfID'
    "doc_info": "Info",  # 'DocInfo' != 'Info'
    "pages": "NumberOfPages",  # 'Pages' != 'NumberOfPages'
}


def camel_case_dict_factory(data):
    """
    Used by asdict to serialize dataclasses.
    1. Filters out None values.
    2. Renames fields to match pdftk expectations (using FIELD_MAPPINGS).
    3. Auto-converts remaining snake_case keys to PascalCase (e.g. media_rect -> MediaRect).
    """
    # breakpoint()

    clean_dict = {}
    for k, v in data:
        if v is None:
            continue

        # Determine the export key
        if k in FIELD_MAPPINGS:
            new_key = FIELD_MAPPINGS[k]
        else:
            # Auto-convert snake_case to PascalCase (e.g. crop_rect -> CropRect)
            # This handles media_rect -> MediaRect, trim_rect -> TrimRect automatically
            new_key = "".join(word.title() for word in k.split("_"))

        clean_dict[new_key] = v
    return clean_dict


# --- 2. Helper Functions ---


def _fuzzy_create(cls, data: dict):
    """
    Instantiates 'cls' matching keys insensitively, with support for
    legacy aliases (e.g., 'Number' -> 'page_number').
    """
    if not isinstance(data, dict):
        return data

    # Map {normalized_name: actual_field_name}
    field_map = {f.name.lower().replace("_", ""): f.name for f in fields(cls)}

    # Map {input_alias: target_normalized_field}
    # This handles inconsistent naming in PDF tools (e.g. Number vs PageNumber)
    aliases = {
        "number": "pagenumber",  # Maps 'Number' input to 'page_number' field
        "rect": "mediarect",  # Maps 'Rect' input to 'media_rect' field
    }

    init_kwargs = {}
    for k, v in data.items():
        norm_key = k.lower().replace("_", "")

        # 1. Try direct match
        if norm_key in field_map:
            init_kwargs[field_map[norm_key]] = v

        # 2. Try alias match
        elif norm_key in aliases:
            target_field = aliases[norm_key]
            if target_field in field_map:
                init_kwargs[field_map[target_field]] = v

    return safe_create(cls, init_kwargs)


# --- 3. Data Classes ---


@dataclass
class PageLabelEntry:
    # If the dump key is "NewIndex" or "Index", adjust this name to match fuzzily.
    # If dump has "Start", 'start' matches.
    new_index: int
    start: int = 1
    prefix: str | None = None
    num_style: str | None = None

    def to_dict(self):
        return asdict(self, dict_factory=camel_case_dict_factory)


@dataclass
class DocInfoEntry:
    key: str
    value: str
    # No to_dict needed here; these are flattened manually in PdfInfo.to_dict


@dataclass
class BookmarkEntry:
    title: str
    level: int
    page_number: int  # Automatically becomes "PageNumber" via factory
    children: list["BookmarkEntry"] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict):
        """Recursively create BookmarkEntries."""
        d = data.copy()

        # Handle Recursion (children)
        # We still need this because _fuzzy_create doesn't know how to
        # instantiate lists of objects automatically.
        kids_key = None
        if "children" in d:
            kids_key = "children"
        elif "Children" in d:
            kids_key = "Children"

        if kids_key and isinstance(d[kids_key], list):
            d[kids_key] = [cls.from_dict(c) if isinstance(c, dict) else c for c in d[kids_key]]

        # Now we just rely on fuzzy matching!
        # "PageNumber" -> matches field "page_number" automatically.
        return _fuzzy_create(cls, d)

    def to_dict(self):
        return asdict(self, dict_factory=camel_case_dict_factory)


@dataclass
class PageMediaEntry:
    page_number: int
    rotation: int | None = None

    # Internal names match the requested keys (snake_case -> PascalCase)
    media_rect: list[float] | None = None  # Maps to MediaRect
    dimensions: tuple[str, str] | None = None
    crop_rect: list[float] | None = None  # Maps to CropRect
    trim_rect: list[float] | None = None  # Maps to TrimRect
    bleed_rect: list[float] | None = None  # BleedRect
    art_rect: list[float] | None = None  # ArtRect

    def to_dict(self):
        return asdict(self, dict_factory=camel_case_dict_factory)


@dataclass
class PdfInfo:
    pages: int | None = None
    ids: list[str] | None = None
    doc_info: list[DocInfoEntry] | None = None
    bookmarks: list[BookmarkEntry] | None = None
    page_media: list[PageMediaEntry] | None = None
    page_labels: list[PageLabelEntry] | None = None
    file_path: str | None = None
    version: str | None = None
    encrypted: bool | None = None

    @classmethod
    def from_dict(cls, data: dict):
        d = data.copy()

        # 1. Flatten "Info" dictionary -> doc_info list
        if "Info" in d and isinstance(d["Info"], dict):
            d["doc_info"] = [DocInfoEntry(k, v) for k, v in d["Info"].items()]
            del d["Info"]

        # 2. Map IDs
        # Case A: Legacy Stanza (PdfID0, PdfID1)
        if "PdfID0" in d:
            d["ids"] = [d.pop("PdfID0", None), d.pop("PdfID1", None)]
        # Case B: JSON (PdfID: {"0": "...", "1": "..."})
        elif "PdfID" in d and isinstance(d["PdfID"], dict):
            pdf_id = d.pop("PdfID")
            # Sort by keys '0', '1' to ensure order
            d["ids"] = [pdf_id.get(k) for k in sorted(pdf_id.keys())]

        # 3. Map NumberOfPages -> pages
        if "NumberOfPages" in d:
            d["pages"] = d.pop("NumberOfPages")

        # 4. Process Lists
        # We just map the Container Name (JSON) -> Field Name (Python)
        list_mappings = [
            ("PageMediaList", "page_media", PageMediaEntry),
            ("PageLabelList", "page_labels", PageLabelEntry),
        ]

        # A. Bookmarks (needs .from_dict for recursion)
        if "BookmarkList" in d:
            raw_list = d.pop("BookmarkList")
            d["bookmarks"] = [BookmarkEntry.from_dict(b) for b in raw_list]

        # B. Simple Lists (use fuzzy create)
        for json_key, field_name, item_cls in list_mappings:
            if json_key in d:
                raw_list = d.pop(json_key)
                d[field_name] = [_fuzzy_create(item_cls, item) for item in raw_list]

        # Handle case where JSON output used 'PageMedia' but legacy read expects 'PageMediaList'
        # The factory outputs 'PageMedia'.
        if "PageMedia" in d:
            raw_list = d.pop("PageMedia")
            d["page_media"] = [_fuzzy_create(PageMediaEntry, item) for item in raw_list]

        if "PageLabels" in d:
            raw_list = d.pop("PageLabels")
            d["page_labels"] = [_fuzzy_create(PageLabelEntry, item) for item in raw_list]

        if "Bookmarks" in d:
            raw_list = d.pop("Bookmarks")
            d["bookmarks"] = [BookmarkEntry.from_dict(b) for b in raw_list]

        return _fuzzy_create(cls, d)

    def to_json(self, indent=2):
        import json

        return json.dumps(self.to_dict(), indent=indent)

    def to_dict(self):
        # 1. Use factory for bulk work (renaming, filtering, recursion)
        d = asdict(self, dict_factory=camel_case_dict_factory)

        # 2. Patch the structural mismatches manually

        # Patch Info: Convert list of dicts [{'Key': 'Title', 'Value': 'A'}] -> {'Title': 'A'}
        # Factory turns DocInfoEntry(key="Title", value="A") into {"Key": "Title", "Value": "A"}
        if "Info" in d and isinstance(d["Info"], list):
            d["Info"] = {item["Key"]: item["Value"] for item in d["Info"]}

        # Patch PdfID: Convert list ['id1', 'id2'] -> {'0': 'id1', '1': 'id2'}
        if "PdfID" in d and isinstance(d["PdfID"], list):
            d["PdfID"] = {str(i): val for i, val in enumerate(d["PdfID"])}

        return d
