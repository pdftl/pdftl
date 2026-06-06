# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/dump_colorspaces.py

"""Dump color space usage information for a PDF file."""

import json
import logging
import re
from typing import TYPE_CHECKING

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.utils.hooks import from_result_meta
from pdftl.utils.io_helpers import smart_open_maybe_dash
from pdftl.utils.page_specs import page_numbers_matching_page_specs

# Import from the newly extracted utilities
from pdftl.utils.colorspaces import effective_family
from pdftl.utils.stream_walker import walk_page

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)


_DUMP_COLORSPACES_LONG_DESC = """
The `dump_colorspaces` operation inspects all color spaces used in a PDF
document — across raster images, vector paths, and text — and reports them
as structured JSON.

It walks every page's content stream (recursing into Form XObjects), tracks
the graphics state stack, and collects every color-setting operator. ICC
profiles are reported with both their tag and resolved family. Separation
and DeviceN spot colors are reported with their colorant names and alternate
color space.

**Output levels** (controlled by optional keywords):

* *(default)* — `summary` only: the set of all color space families seen
  across the whole document, plus any ICC profiles encountered.
* `pages` — adds a per-page summary (families seen on each page).
* `full` — adds per-page element-level detail: images, strokes, fills,
  patterns, and shadings broken out individually. This level includes rich
  traceability, reporting the inline `operator` used, the named `resource_id`,
  and the exact `pdf_object_id` to pinpoint rogue color spaces.

**Color space families reported** (in the `"family"` field):

| PDF color space | Reported family |
|---|---|
| /DeviceGray | `gray` |
| /DeviceRGB, /CalRGB | `rgb` |
| /DeviceCMYK, /CalGray (4-component) | `cmyk` |
| /Lab | `lab` |
| /ICCBased | `icc` (+ `family` from profile) |
| /Separation, /DeviceN | `spot` |
| /Indexed | resolved to base |
| /Pattern | `pattern` |
"""

_DUMP_COLORSPACES_EXAMPLES = [
    {
        "cmd": "in.pdf dump_colorspaces",
        "desc": "Report color spaces used in in.pdf (summary only)",
    },
    {
        "cmd": "in.pdf dump_colorspaces pages",
        "desc": "Report color spaces per page",
    },
    {
        "cmd": "in.pdf dump_colorspaces full output cs.json",
        "desc": "Full element-level color space report saved to cs.json",
    },
    {
        "cmd": "in.pdf dump_colorspaces 1 3-5",
        "desc": "Report color spaces for pages 1, 3, 4, and 5 (summary)",
    },
]

# ---------------------------------------------------------------------------
# Per-page extraction
# ---------------------------------------------------------------------------


def _extract_page_info(page, page_num: int, pikepdf, *, full: bool) -> dict:
    """Return a dict describing color space usage for one page."""
    detail: dict = {
        "images": [],
        "strokes": [],
        "fills": [],
        "patterns": [],
    }

    try:
        resources = page.get("/Resources")
        walk_page(
            page,
            resources,
            pikepdf,
            detail=detail,
            seen_stream_ids=set(),
            full=full,
        )
    except AttributeError as err:
        logger.debug("Page %d skipped: %s", page_num, err)

    all_cs: list[dict] = (
        detail["images"] + detail["strokes"] + detail["fills"] + detail["patterns"]
    )
    families = sorted({effective_family(cs) for cs in all_cs} - {"unknown"})
    icc_profiles = _collect_icc_profiles(all_cs)

    page_info: dict = {
        "page": page_num,
        "families": families,
    }
    if icc_profiles:
        page_info["icc_profiles"] = icc_profiles

    if full:
        page_info["detail"] = {
            "images": [_serialize_cs(cs) for cs in detail["images"]],
            "strokes": [_serialize_cs(cs) for cs in detail["strokes"]],
            "fills": [_serialize_cs(cs) for cs in detail["fills"]],
            "patterns": [_serialize_cs(cs) for cs in detail["patterns"]],
        }

    return page_info


def _collect_icc_profiles(cs_list: list[dict]) -> list[dict]:
    """Extract unique ICC profile descriptors from a list of resolved CS dicts."""
    seen = set()
    profiles = []
    for cs in cs_list:
        if cs.get("family") != "icc":
            continue
        key = (
            cs.get("icc_family", "unknown"),
            cs.get("components"),
            cs.get("profile_name"),
        )
        if key not in seen:
            seen.add(key)
            entry: dict = {"icc_family": cs.get("icc_family", "unknown")}
            if cs.get("components") is not None:
                entry["components"] = cs["components"]
            if cs.get("profile_name"):
                entry["profile_name"] = cs["profile_name"]
            if cs.get("alternate"):
                entry["alternate"] = cs["alternate"]
            profiles.append(entry)
    return profiles


def _serialize_cs(cs: dict) -> dict:
    """Remove any redundant/internal keys before JSON output."""
    return {k: v for k, v in cs.items() if v is not None}


# ---------------------------------------------------------------------------
# Main extraction entry point
# ---------------------------------------------------------------------------


def _extract_colorspace_info(pdf: "pikepdf.Pdf", specs: list | None, *, level: str) -> dict:
    """Walk the PDF and return the colorspace report dict."""
    import pikepdf

    num_pages = len(pdf.pages)
    include_pages = level in ("pages", "full")
    include_full = level == "full"

    if not specs:
        target_pages = list(range(1, num_pages + 1))
    else:
        target_pages = sorted(page_numbers_matching_page_specs(specs, num_pages))

    all_families: set[str] = set()
    all_icc: list[dict] = []
    pages_output: list[dict] = []

    for page_num in target_pages:
        page = pdf.pages[page_num - 1]
        page_info = _extract_page_info(page, page_num, pikepdf, full=include_full)

        all_families.update(page_info["families"])
        all_icc.extend(page_info.get("icc_profiles", []))

        if include_pages:
            pages_output.append(page_info)

    # De-duplicate ICC profiles in the summary
    seen_icc_keys: set = set()
    unique_icc: list[dict] = []
    for profile in all_icc:
        key = (profile.get("icc_family"), profile.get("components"), profile.get("profile_name"))
        if key not in seen_icc_keys:
            seen_icc_keys.add(key)
            unique_icc.append(profile)

    summary: dict = {
        "families": sorted(all_families),
    }
    if unique_icc:
        summary["icc_profiles"] = unique_icc

    result: dict = {"summary": summary}
    if include_pages:
        result["pages"] = pages_output

    return result


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


def _parse_colorspaces_args(specs_and_keywords: list) -> tuple[list, str]:
    """Split the raw OPERATION_ARGS list into page specs and verbosity level."""
    level = "summary"
    page_specs = []
    for token in specs_and_keywords or []:
        if token == "full":
            level = "full"
        elif token == "pages":
            if level != "full":
                level = "pages"
        else:
            page_specs.append(token)
    return page_specs, level


def dump_colorspaces_cli_hook(result: OpResult, stage, _pipeline):
    """Write the colorspace report to stdout or a file as JSON."""
    output_file = from_result_meta(result, c.META_OUTPUT_FILE)
    compact_json = re.sub(
        r"\[\s+([^\[\]\{\}]+?)\s+\]",
        lambda match: "[" + re.sub(r"\s+", " ", match.group(1)) + "]",
        json.dumps({"colorspaces": result.data}, indent=2),
    )
    with smart_open_maybe_dash(output_file) as f:
        f.write(compact_json + "\n")


@register_operation(
    "dump_colorspaces",
    tags=["info", "metadata", "color", "prepress"],
    type="single input operation",
    desc="Report color spaces used",
    long_desc=_DUMP_COLORSPACES_LONG_DESC,
    examples=_DUMP_COLORSPACES_EXAMPLES,
    cli_hook=dump_colorspaces_cli_hook,
    usage="<input> dump_colorspaces [pages] [full] [<page_spec>...] [output <output>]",
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {"output_file": c.OUTPUT}),
    skip_pipeline_save=True,
)
def dump_colorspaces(pdf, specs, output_file=None) -> OpResult:
    """Report color spaces used throughout a PDF document."""
    page_specs, level = _parse_colorspaces_args(specs)
    data = _extract_colorspace_info(pdf, page_specs or None, level=level)
    return OpResult(
        success=True,
        data=data,
        meta={c.META_OUTPUT_FILE: output_file},
    )
