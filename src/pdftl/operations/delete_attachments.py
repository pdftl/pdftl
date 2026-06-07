# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/delete_attachments.py

"""Delete attachments from a PDF based on specific filters."""

import logging

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.keyval_parser import parse_keyval_string
from pdftl.utils.page_specs import page_numbers_matching_page_spec

logger = logging.getLogger(__name__)

_DELETE_ATTACHMENTS_LONG_DESC = """

The `delete_attachments` operation removes attached files from the PDF.
By default, it will permanently delete all attachments from the document,
but it can be heavily customized to target specific files based on where
they are located, what they are named, how large they are, or their relationship.

**Global, Page-Based, or Document-Level:**
- **Global (Default):** If you omit the selector or use `-`, the operation will
  delete matching attachments everywhere (both document-level and page-level).
- **Document-Level (`doc`):** If you use the selector `doc`, it will only look at
  attachments that exist purely in the document's global attachment panel and
  are *not* visually attached to any specific page.
- **Page-Based (`1-5`, `odd`, etc.):** If you provide a specific page range, it
  will only delete attachments that are linked as clickable annotations on those pages.

The syntax is `[selector](Key=Value, ...)`, where:
  - `selector` is optional (`1-5`, `doc`, `-`). Omitting it acts globally.
  - `Key=Value` pairs define the filter criteria an attachment must meet to be deleted.

### Filter Parameters

| Parameter  | Description | Example |
| :--- | :--- | :--- |
| `name`     | Match the attachment's full filename (case insensitive). | `name=draft.txt` |
| `namehas`  | Match a substring of the full filename (case insensitive). | `namehas=draft` |
| `ext`      | File extension match (with or without the dot). | `ext=csv`, `ext=.jpg` |
| `minbytes` | Min file size. Supports `k`, `m`, `g` suffixes. | `500k`, `2m` |
| `maxbytes` | Max file size. Supports `k`, `m`, `g` suffixes. | `10000`, `1.5m` |
| `relation` | Exact match against relationship type (e.g. `Source`, `Data`). | `relation=Data` |

*Note: If multiple parameters are provided, an attachment must meet ALL criteria to be deleted.*

"""

_DELETE_ATTACHMENTS_KEYS = ["name", "namehas", "ext", "minbytes", "maxbytes", "relation"]

_DELETE_ATTACHMENTS_EXAMPLES = [
    {
        "cmd": "in.pdf delete_attachments output out.pdf",
        "desc": "Globally delete all attachments from the PDF.",
    },
    {
        "cmd": "in.pdf delete_attachments '(ext=pdf)' output out.pdf",
        "desc": "Globally delete all attachments that are PDF files.",
    },
    {
        "cmd": "in.pdf delete_attachments 'doc(minbytes=1m)' output out.pdf",
        "desc": (
            "Delete document-level attachments (not on specific pages) that are larger than 1MB."
        ),
    },
    {
        "cmd": "in.pdf delete_attachments '1-5(relation=Source,ext=zip)' output out.pdf",
        "desc": "On pages 1 through 5, delete ZIP attachments designated as 'Source'.",
    },
]


def _parse_size_str(size_str: str) -> int:
    """Converts '100k', '1M' to an integer. Raises ValueError on bad input."""
    size_str = size_str.lower().strip()
    multipliers = {"k": 1024, "m": 1024**2, "g": 1024**3}
    for unit, mult in multipliers.items():
        if size_str.endswith(unit):
            return int(float(size_str[:-1]) * mult)
    return int(size_str)


def _get_params(params_str: str) -> dict:
    params = parse_keyval_string(
        params_str,
        allowed_keys=_DELETE_ATTACHMENTS_KEYS,
        lowercase_values=True,
        context="delete_attachments",
    )
    for k, v in params.items():
        if k in ("minbytes", "maxbytes"):
            try:
                _parse_size_str(v)
            except ValueError as exc:
                raise InvalidArgumentError(
                    f"Invalid value '{v}' for parameter '{k}'. Expected a size (e.g., '100k')."
                ) from exc
    return params


def _get_attachment_size(attachment) -> int:
    """Attempts to read size from metadata first to save memory, falls back to bytes."""
    try:
        size = int(attachment.obj.get("/EF", {}).get("/F", {}).get("/Length", -1))
        if size > -1:
            return size
    except (AttributeError, ValueError, KeyError):
        pass

    # Fallback to reading the file stream into memory
    return len(attachment.get_file().read_bytes())


def _matches_name_and_ext(fname: str, params: dict) -> bool:
    if "name" in params and params["name"] != fname.lower():
        return False
    if "namehas" in params and params["namehas"] not in fname.lower():
        return False
    if "ext" in params:
        ext = params["ext"]
        if not ext.startswith("."):
            ext = f".{ext}"
        if not fname.lower().endswith(ext):
            return False
    return True


def _matches_relation(attachment, params: dict) -> bool:
    if "relation" not in params:
        return True
    rel = "unspecified"
    if attachment.relationship:
        rel = str(attachment.relationship).strip("/").lower()
    return params["relation"] == rel


def _matches_size(attachment, params: dict) -> bool:
    minbytes_val = params.get("minbytes")
    maxbytes_val = params.get("maxbytes")
    if minbytes_val is None and maxbytes_val is None:
        return True

    size = _get_attachment_size(attachment)
    if minbytes_val is not None and size < _parse_size_str(minbytes_val):
        return False
    if maxbytes_val is not None and size > _parse_size_str(maxbytes_val):
        return False
    return True


def _attachment_matches(fname: str, attachment, params: dict) -> bool:
    """Evaluates if an attachment matches the provided key=value filters."""
    if not params:
        return True
    return (
        _matches_name_and_ext(fname, params)
        and _matches_relation(attachment, params)
        and _matches_size(attachment, params)
    )


def _evaluate_spec(pdf, spec_str: str, annot_map: dict) -> set[str]:
    """Parses a spec and returns a set of filenames to delete."""
    selector, params_str = spec_str, ""
    if "(" in spec_str and spec_str.endswith(")"):
        selector, params_str = spec_str[:-1].split("(", 1)

    params = _get_params(params_str)
    selector = selector.strip().lower()

    is_doc_only = selector == "doc"
    target_pages = None

    if selector and selector not in ("-", "doc"):
        target_pages = set(page_numbers_matching_page_spec(selector, len(pdf.pages)))

    matched_filenames = set()

    for fname, attachment in pdf.attachments.items():
        objgen = attachment.obj.objgen
        pages_attached = annot_map.get(objgen, set())

        # Check Selector Criteria
        if is_doc_only and pages_attached:
            continue  # It is on a page, so skip it
        if target_pages is not None and not pages_attached.intersection(target_pages):
            continue  # It is not on the target pages

        # Check Filter Criteria
        if _attachment_matches(fname, attachment, params):
            matched_filenames.add(fname)

    return matched_filenames


def _extract_page_attachments(page, p_num: int, annot_map: dict) -> None:
    if "/Annots" not in page:
        return
    for annot in page.Annots:
        if annot.get("/Subtype") == "/FileAttachment":
            fs = annot.get("/FS")
            if fs is not None:
                annot_map.setdefault(fs.objgen, set()).add(p_num)


def _map_attachment_annotations(pdf) -> dict:
    annot_map = {}
    for p_num, page in enumerate(pdf.pages, start=1):
        _extract_page_attachments(page, p_num, annot_map)
    return annot_map


def _delete_from_nametree(pdf, filenames_to_delete: set) -> set:
    deleted_objgens = set()
    for fname in filenames_to_delete:
        if fname in pdf.attachments:
            deleted_objgens.add(pdf.attachments[fname].obj.objgen)
            del pdf.attachments[fname]
    return deleted_objgens


def _should_keep_annot(annot, deleted_objgens: set) -> bool:
    if annot.get("/Subtype") != "/FileAttachment":
        return True
    fs = annot.get("/FS")
    if fs is not None and fs.objgen in deleted_objgens:
        return False
    return True


def _clean_single_page_annots(page, deleted_objgens: set) -> None:
    import pikepdf

    if "/Annots" not in page:
        return

    new_annots = pikepdf.Array()
    for annot in page.Annots:
        if _should_keep_annot(annot, deleted_objgens):
            new_annots.append(annot)

    page.Annots = new_annots
    if len(page.Annots) == 0:
        del page["/Annots"]


def _scrub_page_annotations(pdf, deleted_objgens: set) -> None:
    for page in pdf.pages:
        _clean_single_page_annots(page, deleted_objgens)


@register_operation(
    "delete_attachments",
    tags=["in_place", "attachments", "optimization", "delete"],
    type="single input operation",
    desc="Delete file attachments based on criteria",
    long_desc=_DELETE_ATTACHMENTS_LONG_DESC,
    usage="<input> delete_attachments [[spec](params)...] output <output>",
    examples=_DELETE_ATTACHMENTS_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def delete_attachments(pdf, specs) -> OpResult:
    """
    Deletes attachments matching criteria from the document NameTree
    and scrubs corresponding page annotations.
    """
    if not specs:
        specs = [""]  # Empty string triggers global deletion with no filters

    if not pdf.attachments:
        logger.debug("No attachments found in document.")
        return OpResult(success=True, pdf=pdf)

    # 1. Map attachments to the pages they appear on
    annot_map = _map_attachment_annotations(pdf)

    # 2. Evaluate all specs to build the final list of attachment filenames to delete
    filenames_to_delete = set()
    for spec in specs:
        filenames_to_delete.update(_evaluate_spec(pdf, spec, annot_map))

    if not filenames_to_delete:
        logger.info("No attachments matched the deletion criteria.")
        return OpResult(success=True, pdf=pdf)

    # 3. Perform Deletions
    deleted_objgens = _delete_from_nametree(pdf, filenames_to_delete)
    _scrub_page_annotations(pdf, deleted_objgens)

    logger.info("Permanently deleted %d attachment(s).", len(filenames_to_delete))
    return OpResult(success=True, pdf=pdf)
