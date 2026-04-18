# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/set_props.py

"""Set document-level properties, viewer preferences, and page labels."""

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

import pdftl.core.constants as c
from pdftl.core.registry import register_operation
from pdftl.core.types import OpResult
from pdftl.exceptions import OperationError

logger = logging.getLogger(__name__)

_SET_LONG_DESC = """

The `set` operation allows you to modify document-level properties, viewer
preferences, logical page labels, and open actions without altering page content.

You can provide one or more `key=value` pairs.

### Supported Properties
* **lang**: The language identifier of the document (e.g., `en-US`). Important for accessibility.
* **layout**: The page layout to use when opened.
    *(SinglePage, OneColumn, TwoColumnLeft, TwoColumnRight, TwoPageLeft, TwoPageRight)*
* **mode**: The document view mode when opened.
    *(UseNone, UseOutlines, UseThumbs, FullScreen, UseOC, UseAttachments)*

### Viewer Preferences (Booleans)
These control how the PDF reader UI is presented:
* **hide_toolbar**: `true` or `false`
* **hide_menubar**: `true` or `false`
* **hide_windowui**: `true` or `false`
* **fit_window**: `true` or `false`
* **center_window**: `true` or `false`
* **display_title**: `true` or `false` (Displays the Document Title instead of filename)

### Open Action (`open_action`)
Determines the page and zoom level the PDF viewer initially opens to.
Syntax: `PageNum[,ViewType,Arg1,Arg2...]`
* `open_action=5` (Opens to page 5, leaving the zoom level up to the viewer)
* `open_action=2,XYZ,null,null,2.5` (Opens to page 2, zoomed in 2.5x)
* `open_action=5,XYZ` (Opens to page 5, trailing nulls are automatically appended)
* `open_action=1,Fit` (Opens to page 1, fitting it to the window)
* `open_action=""` (Removes the open action entirely)

### Page Labels (`pagelabels`)
Logical page labels determine how page numbers are displayed in the PDF viewer
(e.g., i, ii, iii, 1, 2, 3, A-1).

Syntax: A comma-separated list of rules.
Rule format: `<StartPage>[ (Prefix) ][Style][StartNum]`
* **StartPage**: 1-indexed physical page number.
* **Prefix**: Optional text prefix enclosed in parentheses.
* **Style**: Optional numbering style:
  `r` (roman), `R` (ROMAN), `a` (letters), `A` (LETTERS), `D` (Arabic).
* **StartNum**: Optional starting number (defaults to 1).

To completely remove custom page labels, use `pagelabels=""`.
"""

_SET_EXAMPLES = [
    {
        "cmd": "in.pdf set lang=en-US display_title=true output out.pdf",
        "desc": "Set the document language and tell the viewer to display the metadata Title.",
    },
    {
        "cmd": "in.pdf set layout=TwoColumnRight mode=UseOutlines output out.pdf",
        "desc": "Force the PDF to open in a two-column view with the bookmarks panel open.",
    },
    {
        "cmd": "in.pdf set open_action=5,XYZ,null,null,2.5 output out.pdf",
        "desc": "Force the PDF to automatically open to Page 5 at a 2.5x zoom level.",
    },
    {
        "cmd": "in.pdf set 'pagelabels=1r,5D,8(A-)D8' output out.pdf",
        "desc": (
            "Set complex page labels: pages 1-4 as roman numerals, "
            "pages 5-7 as arabic starting at 1, "
            "and pages 8 onwards as arabic prefixed with 'A-' and starting at 8."
        ),
    },
]

# Map our mini-syntax chars to raw PDF style Names
_STYLE_MAP = {
    "r": "/r",
    "R": "/R",
    "a": "/a",
    "A": "/A",
    "D": "/D",
}

# Map view types to their expected number of arguments for auto-padding
_VIEW_EXPECTED_ARGS = {
    "XYZ": 3,
    "FIT": 0,
    "FITH": 1,
    "FITV": 1,
    "FITR": 4,
    "FITB": 0,
    "FITBH": 1,
    "FITBV": 1,
}

# Regex to parse the labels mini-syntax:
_LABEL_REGEX = re.compile(r"^(\d+)(?:\((.*?)\))?([rRaAD])?(\d+)?$")


def _parse_kwargs(op_args: list[str]) -> dict:
    """Parse the raw CLI arguments into a dictionary of key-value pairs."""
    kwargs = {}
    for arg in op_args:
        if "=" not in arg:
            raise OperationError(f"Invalid argument '{arg}'. Expected format: key=value")
        key, value = arg.split("=", 1)
        kwargs[key.lower().strip()] = value.strip()
    return kwargs


def _parse_labels(pikepdf, labels_str: str) -> dict:
    """Parse the labels string and return a dictionary of page indices to pikepdf.Dictionary."""
    rules = {}

    for part in labels_str.split(","):
        part = part.strip()
        if not part:
            continue

        match = _LABEL_REGEX.match(part)
        if not match:
            raise OperationError(f"Invalid page label syntax: '{part}'")

        start_page, prefix, style_char, start_num = match.groups()
        page_idx = int(start_page) - 1

        rule_args = {}

        if prefix is not None:
            rule_args["P"] = prefix

        if style_char:
            rule_args["S"] = pikepdf.Name(_STYLE_MAP[style_char])

        if start_num is not None:
            rule_args["St"] = int(start_num)
        elif style_char:
            rule_args["St"] = 1

        rules[page_idx] = pikepdf.Dictionary(**rule_args)

    return rules


def _apply_standard_props(pdf, kwargs, pikepdf):
    """Apply standard metadata properties like lang, layout, and mode."""
    if "lang" in kwargs:
        pdf.Root.Lang = kwargs["lang"]

    if "layout" in kwargs:
        pdf.Root.PageLayout = pikepdf.Name(f"/{kwargs['layout']}")

    if "mode" in kwargs:
        pdf.Root.PageMode = pikepdf.Name(f"/{kwargs['mode']}")


def _apply_viewer_prefs(pdf, kwargs, pikepdf):
    """Apply Acrobat UI viewer preferences."""
    viewer_pref_keys = {
        "hide_toolbar": "HideToolbar",
        "hide_menubar": "HideMenubar",
        "hide_windowui": "HideWindowUI",
        "fit_window": "FitWindow",
        "center_window": "CenterWindow",
        "display_title": "DisplayDocTitle",
    }

    prefs_to_set = {}
    for kw_key, pdf_key in viewer_pref_keys.items():
        if kw_key in kwargs:
            val = kwargs[kw_key].lower()
            if val in ("true", "1", "yes"):
                prefs_to_set[pdf_key] = True
            elif val in ("false", "0", "no"):
                prefs_to_set[pdf_key] = False
            else:
                raise OperationError(f"Invalid boolean value for {kw_key}: '{kwargs[kw_key]}'")

    if prefs_to_set:
        if "/ViewerPreferences" not in pdf.Root:
            pdf.Root.ViewerPreferences = pikepdf.Dictionary()
        for pdf_key, bool_val in prefs_to_set.items():
            setattr(pdf.Root.ViewerPreferences, pdf_key, bool_val)


def _parse_page_idx(page_str: str, total_pages: int) -> int:
    """Parse and validate the page number for open_action."""
    try:
        page_num = int(page_str)
        page_idx = page_num - 1
        if page_idx < 0 or page_idx >= total_pages:
            raise OperationError(f"open_action page {page_num} is out of bounds.")
        return page_idx
    except ValueError as exc:
        raise OperationError(
            f"open_action must start with a valid page number, got: {page_str}"
        ) from exc


def _parse_view_arg(arg: str):
    """Cast a view argument to None, float, or int."""
    if arg.lower() == "null":
        return None
    try:
        return float(arg) if "." in arg else int(arg)
    except ValueError as exc:
        raise OperationError(f"Invalid open_action argument: {arg}") from exc


def _build_dest_array(parts: list[str], page_obj, pikepdf) -> list:
    """Construct the PDF destination array from parsed arguments."""
    dest_array = [page_obj]

    if len(parts) == 1:
        # Default to XYZ with nulls to preserve viewer's default zoom/position
        dest_array.extend([pikepdf.Name("/XYZ"), None, None, None])
        return dest_array

    view_type = parts[1]
    dest_array.append(pikepdf.Name(f"/{view_type}"))

    for arg in parts[2:]:
        dest_array.append(_parse_view_arg(arg))

    # Auto-pad missing arguments with None based on known view types
    expected_args = _VIEW_EXPECTED_ARGS.get(view_type.upper(), 0)
    missing = expected_args - len(parts[2:])
    if missing > 0:
        dest_array.extend([None] * missing)

    return dest_array


def _apply_open_action(pdf, kwargs, pikepdf):
    """Apply the document OpenAction (initial view state)."""
    if "open_action" not in kwargs:
        return

    val = kwargs["open_action"].strip()
    if not val:
        if "/OpenAction" in pdf.Root:
            del pdf.Root.OpenAction
        return

    parts = [p.strip() for p in val.split(",")]
    page_idx = _parse_page_idx(parts[0], len(pdf.pages))

    dest_array = _build_dest_array(parts, pdf.pages[page_idx].obj, pikepdf)
    pdf.Root.OpenAction = pikepdf.Array(dest_array)


def _apply_page_labels(pdf, kwargs, pikepdf):
    """Apply the logical page labels numbering rules."""
    if "pagelabels" not in kwargs:
        return

    labels_str = kwargs["pagelabels"].strip()
    if not labels_str:
        # Cleanly remove the PageLabels tree if an empty string is provided
        if "/PageLabels" in pdf.Root:
            del pdf.Root.PageLabels
    else:
        rules = _parse_labels(pikepdf, labels_str)
        # Create a brand new NumberTree to completely replace the old one
        nt = pikepdf.NumberTree.new(pdf)
        for page_idx, rule_dict in rules.items():
            logger.debug("Setting page_idx=%s pagelabel to rule_dict=%s", page_idx, rule_dict)
            nt[page_idx] = rule_dict
        pdf.Root.PageLabels = nt.obj


@register_operation(
    "set",
    tags=["metadata", "properties", "viewer"],
    type="single input operation",
    desc="Set document properties, viewer preferences, and page labels",
    usage="<input> set <key=value>... output <file>",
    long_desc=_SET_LONG_DESC,
    examples=_SET_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def set_props(pdf, op_args) -> OpResult:
    """Orchestrates setting various document-level metadata and preferences."""
    import pikepdf

    if not op_args:
        raise OperationError("The 'set' operation requires at least one key=value pair.")

    kwargs = _parse_kwargs(op_args)

    _apply_standard_props(pdf, kwargs, pikepdf)
    _apply_viewer_prefs(pdf, kwargs, pikepdf)
    _apply_open_action(pdf, kwargs, pikepdf)
    _apply_page_labels(pdf, kwargs, pikepdf)

    return OpResult(success=True, pdf=pdf)
