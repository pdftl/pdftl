# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/catalog/catalog_types.py

"""Types for the PDF Catalog (Root) dictionary based on ISO 32000-1:2008"""

import json
from dataclasses import asdict, dataclass, field, fields

import pikepdf

from pdftl.utils.destinations import get_named_destinations, resolve_dest_to_page_num
from pdftl.utils.type_helpers import safe_create

# --- 1. Serialization Factory ---

# src/pdftl/catalog/catalog_types.py


def catalog_dict_factory(data):
    """Serializes Catalog dataclasses to PDF-compatible PascalCase keys."""
    clean_dict = {}
    for k, v in data:
        if v is None:
            continue

        # Mapping specific acronyms
        if k == "hide_window_u_i":
            new_key = "HideWindowUI"
        else:
            new_key = "".join(word.title() for word in k.split("_"))

        # Presentation: Convert lists to space-separated strings with 'null'
        if isinstance(v, list):
            # 1. Convert None -> "null"
            # 2. Format floats to strip .0 (consistent with 612 vs 612.0)
            formatted_vals = []
            for x in v:
                if x is None:
                    formatted_vals.append("null")
                elif isinstance(x, float) and x.is_integer():
                    formatted_vals.append(str(int(x)))
                else:
                    formatted_vals.append(str(x))
            v = " ".join(formatted_vals)

        clean_dict[new_key] = v
    return clean_dict


# --- 2. Helper Functions ---


def _val(obj):
    """
    Strictly converts pikepdf types to native Python types.
    Raises TypeError if an unsupported pikepdf object is passed.
    """
    import pikepdf

    # 1. Handle pikepdf Names and Strings
    if isinstance(obj, pikepdf.Name):
        return str(obj).lstrip("/")
    if isinstance(obj, pikepdf.String):
        return str(obj)

    # 2. Handle native scalars (pikepdf 10.x returns these for simple types)
    if isinstance(obj, (bool, int, float, str)) or obj is None:
        return obj

    # 3. Handle specific numeric types that might come from older pikepdf/decimal
    from decimal import Decimal

    if isinstance(obj, Decimal):
        return float(obj)

    # 4. Fail loudly if a Dictionary/Array/Stream is passed here.
    # These should be handled by the reconstruction logic, not leaf conversion.
    raise TypeError(
        f"Unexpected type {type(obj)} for leaf value. "
        "Containers must be decomposed before calling _val."
    )


def _fuzzy_create_catalog(cls, data: dict):
    """Instantiates Catalog types with insensitive key matching."""
    if not isinstance(data, dict):
        return data

    field_map = {f.name.lower().replace("_", ""): f.name for f in fields(cls)}
    init_kwargs = {}

    for k, v in data.items():
        norm_key = k.lower().replace("_", "").replace("/", "")
        if norm_key in field_map:
            init_kwargs[field_map[norm_key]] = v

    return safe_create(cls, init_kwargs)


# --- 3. Data Classes ---


@dataclass
class ViewerPreferences:
    """Table 150 – Entries in a viewer preferences dictionary"""

    hide_toolbar: bool | None = None
    hide_menubar: bool | None = None
    hide_window_u_i: bool | None = None
    fit_window: bool | None = None
    center_window: bool | None = None
    display_doc_title: bool | None = None
    non_full_screen_page_mode: str | None = None
    direction: str | None = None
    view_area: str | None = None
    view_clip: str | None = None
    print_area: str | None = None
    print_clip: str | None = None
    print_scaling: str | None = None

    def to_dict(self):
        return asdict(self, dict_factory=catalog_dict_factory)


@dataclass
class MarkInfo:
    """Table 321 – Entries in the mark information dictionary"""

    marked: bool | None = None
    user_properties: bool | None = None
    suspects: bool | None = None

    def to_dict(self):
        return asdict(self, dict_factory=catalog_dict_factory)


@dataclass
class OpenAction:
    page: int
    dest_type: str = "XYZ"
    # We allow list (for code) or str (for Stanza)
    args: list | str = field(default_factory=list)


@dataclass
class OpenAction:
    page: int
    dest_type: str = "XYZ"
    args: list | str = field(default_factory=list)

    @classmethod
    def from_stanza_dict(cls, data: dict):
        """
        Handles the conversion from Stanza strings to proper types.
        Ensures page is an int and args is a list.
        """
        # 1. Fix the 'page' type (The fix for your Pdb error)
        if "Page" in data and isinstance(data["Page"], str):
            try:
                data["Page"] = int(data["Page"])
            except ValueError:
                logger.warning("Invalid page number in Stanza: %s", data["Page"])

        # 2. Fix the 'args' type (The list 'trick')
        raw_args = data.get("Args", "")
        if isinstance(raw_args, str):
            processed = []
            for item in raw_args.split():
                if item.lower() in ("null", "none"):
                    processed.append(None)
                else:
                    try:
                        val = float(item)
                        processed.append(int(val) if val.is_integer() else val)
                    except ValueError:
                        processed.append(item)
            data["Args"] = processed

        return _fuzzy_create_catalog(cls, data)


@dataclass
class PdfCatalog:
    """Table 28 – Entries in the catalog dictionary (Leaf-only subset)."""

    version: str | None = None
    lang: str | None = None
    page_layout: str | None = None
    page_mode: str | None = None
    viewer_preferences: ViewerPreferences | None = None
    needs_rendering: bool | None = None
    mark_info: MarkInfo | None = None
    open_action: OpenAction | None = None

    @classmethod
    def from_dict(cls, data, pdf=None):
        """
        Decomposes a pikepdf Dictionary into native Python types.
        Only processes keys that match the PdfCatalog dataclass.
        """
        # 1. Normalize keys to strings without slashes
        raw_dict = {str(k).lstrip("/"): v for k, v in data.items()}

        clean_data = {}

        # 2. Extract specific known leaf keys
        leaf_keys = ["Lang", "Version", "PageLayout", "PageMode", "NeedsRendering"]
        for key in leaf_keys:
            if key in raw_dict:
                val = raw_dict[key]
                # Only convert if it's not a complex container
                if not isinstance(val, (pikepdf.Dictionary, pikepdf.Array)):
                    clean_data[key] = _val(val)

        # 3. Extract and decompose specific sub-dictionaries
        # Handle ViewerPreferences
        vp_raw = raw_dict.get("ViewerPreferences")
        if isinstance(vp_raw, pikepdf.Dictionary):
            vp_native = {
                str(k).lstrip("/"): _val(v)
                for k, v in vp_raw.items()
                if not isinstance(v, (pikepdf.Dictionary, pikepdf.Array))
            }
            clean_data["ViewerPreferences"] = _fuzzy_create_catalog(ViewerPreferences, vp_native)
        elif isinstance(vp_raw, dict):  # Handle dict from JSON/Stanza
            clean_data["ViewerPreferences"] = _fuzzy_create_catalog(ViewerPreferences, vp_raw)

        # Handle MarkInfo
        mi_raw = raw_dict.get("MarkInfo")
        if isinstance(mi_raw, pikepdf.Dictionary):
            mi_native = {
                str(k).lstrip("/"): _val(v)
                for k, v in mi_raw.items()
                if not isinstance(v, (pikepdf.Dictionary, pikepdf.Array))
            }
            clean_data["MarkInfo"] = _fuzzy_create_catalog(MarkInfo, mi_native)
        elif isinstance(mi_raw, dict):
            clean_data["MarkInfo"] = _fuzzy_create_catalog(MarkInfo, mi_raw)

        # 3. Handle OpenAction
        oa_raw = raw_dict.get("OpenAction")

        if isinstance(oa_raw, (pikepdf.Array, pikepdf.Dictionary)) and pdf is not None:
            # Case A: From PDF
            resolved = resolve_dest_to_page_num(oa_raw, pdf.pages, get_named_destinations(pdf))
            if resolved:
                clean_data["OpenAction"] = OpenAction(
                    resolved.page_num, resolved.dest_type, resolved.args
                )

        elif isinstance(oa_raw, dict):
            # Case B: From JSON/Dict
            clean_data["OpenAction"] = _fuzzy_create_catalog(OpenAction, oa_raw)

        else:
            # Case C: From Stanza (Flat keys like CatalogOpenActionArgs)
            oa_stanza_data = {}
            for k, v in raw_dict.items():
                if k.startswith("OpenAction") and k != "OpenAction":
                    oa_stanza_data[k[len("OpenAction") :]] = v

            if oa_stanza_data:
                # USE THE TRICK HERE:
                clean_data["OpenAction"] = OpenAction.from_stanza_dict(oa_stanza_data)

        # 4. Handle flat 'Catalog...' keys from Stanza format
        for k, v in raw_dict.items():
            if k.startswith("Catalog"):
                short_k = k[len("Catalog") :]
                # Only process if not already handled
                if short_k not in clean_data:
                    clean_data[short_k] = (
                        _val(v) if not isinstance(v, (pikepdf.Dictionary, pikepdf.Array)) else None
                    )

        return _fuzzy_create_catalog(cls, clean_data)

    def to_dict(self):
        return asdict(self, dict_factory=catalog_dict_factory)

    def to_json(self, indent=2):
        return json.dumps(self.to_dict(), indent=indent)


# --- 4. Logic for update_catalog ---


def merge_catalog_data(existing_root, new_catalog_obj: PdfCatalog, pdf=None):
    """Deep merges a PdfCatalog object into a pikepdf Root dictionary."""
    updates = new_catalog_obj.to_dict()

    def to_pdf_name(val):
        if not isinstance(val, str):
            return val
        return pikepdf.Name(val if val.startswith("/") else f"/{val}")

    for key, value in updates.items():
        # Skip the OpenAction here, handle it separately below
        if key == "OpenAction":
            continue

        pdf_key = f"/{key}"

        if key in ("PageMode", "PageLayout", "Version"):
            value = to_pdf_name(value)

        if isinstance(value, dict):
            if pdf_key not in existing_root:
                existing_root[pdf_key] = pikepdf.Dictionary()

            for sub_key, sub_val in value.items():
                if sub_key in (
                    "NonFullScreenPageMode",
                    "Direction",
                    "ViewArea",
                    "ViewClip",
                    "PrintArea",
                    "PrintClip",
                    "PrintScaling",
                ):
                    sub_val = to_pdf_name(sub_val)
                existing_root[pdf_key][f"/{sub_key}"] = sub_val
        else:
            existing_root[pdf_key] = value

    # --- Handle OpenAction ---
    if new_catalog_obj.open_action:
        oa = new_catalog_obj.open_action
        try:
            # 1. Get the page helper
            target_page_helper = pdf.pages[int(oa.page) - 1]

            # 2. THE TRICK: Create an indirect reference in the target PDF
            # This ensures the array contains '10 0 R' instead of a giant dict
            page_ref = pdf.make_indirect(target_page_helper.obj)

            # 3. Construct the array
            dest_array = pikepdf.Array([page_ref, pikepdf.Name(f"/{oa.dest_type}")])

            # 4. Add the args
            for arg in oa.args:
                dest_array.append(arg)

            existing_root["/OpenAction"] = dest_array

        except IndexError:
            logger.warning("OpenAction refers to non-existent page %s", oa.page)
