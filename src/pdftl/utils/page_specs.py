# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/page_specs.py

"""Methods to parse and deal with page specs
(range-like specifications of collections of pages)

Public:

PageTransform
PageSpec

expand_specs_to_pages(specs, aliases=None, inputs=None, opened_pdfs=None)
  -> [PageTransform]
parse_page_spec(spec, total_pages) -> PageSpec
page_number_matches_page_spec(n, page_spec_str, total_pages) -> bool
page_numbers_matching_page_spec(page_spec, total_pages) -> [int]
page_numbers_matching_page_specs(specs, total_pages) -> [int]

"""

import logging
import math
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Pdf

from pdftl.core.registry import register_help_topic
from pdftl.exceptions import InvalidArgumentError, UserCommandLineError


@dataclass
class PageTransform:
    """A dataclass for passing page transformation data around"""

    pdf: "Pdf"
    index: int
    rotation: (int | float, bool)
    scale: float


@dataclass(frozen=True)
class PageSpec:
    """A structured representation of a parsed page specification."""

    start: int
    end: int
    rotate: tuple[int, bool]
    scale: float
    qualifiers: set[str]
    omissions: list[tuple[int, int]]

    def __tuple__(self):
        return (
            self.start,
            self.end,
            self.rotate,
            self.scale,
            self.qualifiers,
            self.omissions,
        )


# Maps rotation keywords to their (angle, is_relative) tuple.
ROTATION_MAP = {
    "north": (0, False),
    "east": (90, False),
    "south": (180, False),
    "west": (270, False),
    "left": (-90, True),
    "right": (90, True),
    "down": (180, True),
}

# Set of supported page qualifiers.
QUALIFIER_MAP = {"even", "odd"}

# FIXME: can "right" be confused with "r" here?
# generate test cases
SPEC_REGEX = re.compile(
    r"""
    ^               # Anchor to the start of the string
    (?:             # Start optional non-capturing group for whole range
        (r)?        # CAPTURE GROUP 1: Optional 'r', reverse start page
        (end|\d+)?  # CAPTURE GROUP 2: The start page number or 'end'
        (?:         # Start optional non-capturing group for end of range
            -       # literal hyphen separator
            (r)?    # CAPTURE GROUP 3: Optional 'r' for reverse end page
            (end|\d+)?# CAPTURE GROUP 4: end page number or 'end'
        )?          # End of optional end-of-range group
    )?              # End of optional page-range group
    (.*)            # CAPTURE GROUP 5: Greedily capture rest as modifiers
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _handle_no_specs(inputs, opened_pdfs) -> [PageTransform]:
    """
    Generates a list of PageTransform s for all pages of all inputs when no spec is given.
    """
    page_tuples = []
    for input_idx in range(len(inputs)):
        pdf = opened_pdfs[input_idx]
        for i in range(len(pdf.pages)):
            # Append a 4-item tuple with default rotation and scale
            page_tuples.append(PageTransform(pdf=pdf, index=i, rotation=(0, False), scale=1.0))
    return page_tuples


def _resolve_alias_and_spec(spec, opened_pdfs_by_alias, default_alias):
    """
    Determines the correct PDF object and page spec string from a full spec string.
    e.g., "A1-5" -> (pdf_A, "1-5", "A")
    """
    if spec and spec.startswith("_"):
        alias = default_alias
        page_spec_full = spec[1:]
    elif spec and spec[0].isalpha() and spec[0].upper() in opened_pdfs_by_alias:
        alias = spec[0].upper()
        page_spec_full = spec[1:]
    else:
        alias = default_alias
        page_spec_full = spec

    if not alias or alias not in opened_pdfs_by_alias:
        raise UserCommandLineError(f"Cannot determine a valid alias for spec '{spec}'")

    pdf = opened_pdfs_by_alias[alias]
    return pdf, page_spec_full, alias


def _filter_page_numbers(page_numbers, qualifiers, omissions):
    """
    Applies even/odd and omission filters to a list of 1-based page numbers.
    """
    if "even" in qualifiers:
        page_numbers = [p for p in page_numbers if p % 2 == 0]
    if "odd" in qualifiers:
        page_numbers = [p for p in page_numbers if p % 2 != 0]

    for om_start, om_end in omissions:
        page_numbers = [p for p in page_numbers if not om_start <= p <= om_end]
    return page_numbers


def _create_page_tuples_from_numbers(
    page_numbers, pdf, rotate, scale, spec_for_error
) -> [PageTransform]:
    """
    Validates page numbers (1-based) and creates the final list of page tuples.
    """
    new_tuples = []
    total_pages = len(pdf.pages)
    pdf_filename = (
        pdf.filename
        if hasattr(pdf, "filename") and pdf.filename != "empty PDF"
        else "pipeline PDF"
    )

    for page_num in page_numbers:
        if not 1 <= page_num <= total_pages:
            raise UserCommandLineError(
                f"Invalid page.\n  "
                f"Page spec '{spec_for_error}' includes page {page_num} but "
                f"there are only {total_pages} pages in {pdf_filename}"
            )
        # Convert 1-based page_num to 0-based index for pikepdf and append
        new_tuples.append(PageTransform(pdf=pdf, index=page_num - 1, rotation=rotate, scale=scale))
    return new_tuples


def expand_specs_to_pages(specs, aliases=None, inputs=None, opened_pdfs=None) -> [PageTransform]:
    """
    Expand pdftk-style page specs into an array of PageTransform
    """
    aliases = aliases or {}
    opened_pdfs = opened_pdfs or {}

    logger.debug("specs=%s, aliases=%s, inputs=%s", specs, aliases, inputs)

    if not inputs:
        raise ValueError("inputs were not passed in expand_specs_to_pages")

    # The opened_pdfs dict maps an input index to a pikepdf.Pdf object.
    # The aliases dict maps a string (e.g., 'A') to an input index (e.g., 0).
    default_alias = "DEFAULT"
    aliases[default_alias] = 0
    opened_pdfs_by_alias = {alias: opened_pdfs[idx] for alias, idx in aliases.items()}

    # Handle the simple case of no specs first and exit early.
    if not specs:
        page_tuples = _handle_no_specs(inputs, opened_pdfs)
        logger.debug("No specs provided, expanded to %s pages.", len(page_tuples))
        return page_tuples

    page_tuples = []
    for spec_str in specs:
        page_tuples.extend(
            _new_tuples_from_spec_str(spec_str, opened_pdfs_by_alias, default_alias)
        )

    logger.debug("Total specs expanded to %s pages.", len(page_tuples))
    return page_tuples


def _new_tuples_from_spec_str(spec_str, opened_pdfs_by_alias, default_alias) -> [PageTransform]:
    # Step 1: Isolate the logic for determining the PDF and page spec.
    pdf, page_spec_full, alias = _resolve_alias_and_spec(
        spec_str, opened_pdfs_by_alias, default_alias
    )

    # Step 2: Parse the page spec string into its component parts.
    page_spec = parse_page_spec(page_spec_full, len(pdf.pages))

    # Step 3: Generate the initial list of 1-based page numbers for the full range.
    step = 1 if page_spec.start <= page_spec.end else -1
    initial_page_numbers = list(range(page_spec.start, page_spec.end + step, step))

    # Step 4: Isolate the filtering logic.
    final_page_numbers = _filter_page_numbers(
        initial_page_numbers, page_spec.qualifiers, page_spec.omissions
    )

    # Step 5: Isolate the validation and final tuple creation.
    new_tuples = _create_page_tuples_from_numbers(
        final_page_numbers, pdf, page_spec.rotate, page_spec.scale, spec_str
    )
    logger.debug(
        "Spec '%s' expanded to %s pages from alias '%s'.",
        spec_str,
        len(new_tuples),
        alias,
    )
    return new_tuples


def _resolve_page_token(token_str, is_reverse, total_pages):
    """Converts a string token ('end', '5') into an absolute page number.
    If None is passed, return None."""
    if token_str is None:
        return None
    is_end_token = token_str.lower() == "end"
    if is_reverse:
        if is_end_token:
            return 1  # 'rend' means page 1, just as 'r1' means last page
        return total_pages - int(token_str) + 1
    if is_end_token:
        return total_pages
    return int(token_str)


def _parse_range_part(spec, total_pages):
    """Parses the 'start-end' part of the spec string."""
    range_match = SPEC_REGEX.match(spec)
    if not range_match:
        raise InvalidArgumentError(f"Invalid page spec format: {spec}")

    start_is_rev, start_str, end_is_rev, end_str, modifier_str = range_match.groups()

    if start_str is not None or end_str is not None:
        # pdftk.java seems to default to 0, so we replicate that behavior.
        start = _resolve_page_token(start_str, start_is_rev, total_pages) or 0
        end = _resolve_page_token(end_str, end_is_rev, total_pages) or start
    else:
        # If no range is specified, it applies to all pages.
        start, end = 1, total_pages

    if start <= 0:
        raise InvalidArgumentError(
            f"Parsed invalid starting page {start} from the range spec {spec}. "
            "Valid page numbers start at 1."
        )

    return start, end, modifier_str


def _parse_qualifiers(modifier_str):
    """Extracts 'even' or 'odd' qualifiers from the modifier string."""
    qualifiers = set()
    # Using a loop with replace ensures we find all occurrences, same as original
    for qual in QUALIFIER_MAP:
        if qual in modifier_str:
            qualifiers.add(qual)
            modifier_str = modifier_str.replace(qual, "", 1)
    return qualifiers, modifier_str


def _parse_rotation(modifier_str):
    """Extracts a rotation modifier from the string."""

    # The original debug helper function

    for key, value in ROTATION_MAP.items():
        if key in modifier_str:
            return value, modifier_str.replace(key, "", 1)
    return (0, False), modifier_str  # Default value


def _parse_scaling(modifier_str):
    """Extracts 'x' and 'z' scaling modifiers and combines them."""
    scale = 1.0

    # Find 'x' scaling
    scale_re = re.compile(r"x([+-]?\d*\.?\d+)")
    scale_match = scale_re.search(modifier_str)
    if scale_match:
        scaling_val = float(scale_match.group(1))
        if scaling_val <= 0:
            raise InvalidArgumentError(f"Invalid scaling: {scaling_val}")
        scale *= scaling_val
        modifier_str = scale_re.sub("", modifier_str, 1)

    # Find 'z' zoom scaling
    zoom_re = re.compile(r"z([+-]?\d*\.?\d+)")
    zoom_match = zoom_re.search(modifier_str)
    if zoom_match:
        zoom_val = float(zoom_match.group(1))
        scale *= math.pow(math.sqrt(2), zoom_val)
        modifier_str = zoom_re.sub("", modifier_str, 1)

    return scale, modifier_str


def _parse_omissions(modifier_str, total_pages):
    """Parses and resolves page omission sub-specs (e.g., '~1-5')."""
    omissions = []
    omit_re = re.compile(r"^(~([^~]*))")

    remaining_str = modifier_str
    while remaining_str:
        omit_match = omit_re.match(remaining_str)
        if not omit_match:
            raise InvalidArgumentError(
                f"Invalid part '{remaining_str}' should start with ~ " f"while parsing omissions."
            )

        omit_range_str = omit_match.group(2)
        if omit_range_str:
            # Recursive call to the main public function, preserving original behavior
            omit_page_spec = parse_page_spec(omit_range_str, total_pages)
            omissions.append(tuple(sorted((omit_page_spec.start, omit_page_spec.end))))

        remaining_str = omit_re.sub("", remaining_str, 1)

    return omissions, remaining_str


def parse_page_spec(spec, total_pages) -> PageSpec:
    """
    Parses a pdftk-style page specification for page ranges, rotation,
    scaling (including x and z modifiers), and qualifiers, including reverse
    page numbers (e.g., r1, r3-r1).

    Returns: a PageSpec, with fields:
    start, end, rotate, scale, qualifiers, omissions
    """

    logger.debug("spec=%s, total_pages=%s", spec, total_pages)

    # 1. Parse the primary page range (e.g., '1-10', 'r5-end')
    start, end, modifier_str = _parse_range_part(spec, total_pages)

    # 2. Sequentially parse all modifiers from the remaining string.
    #    The order of these operations is critical and preserved from the original.
    qualifiers, modifier_str = _parse_qualifiers(modifier_str.lower())
    rotate, modifier_str = _parse_rotation(modifier_str)
    scale, modifier_str = _parse_scaling(modifier_str)
    omissions, modifier_str = _parse_omissions(modifier_str, total_pages)

    logger.debug("finally, modifier_str=%s", modifier_str)
    logger.debug(
        "start=%s, end=%s, rotate=%s, scale=%s, qualifiers=%s, omissions=%s",
        start,
        end,
        rotate,
        scale,
        qualifiers,
        omissions,
    )

    return PageSpec(
        start=start,
        end=end,
        rotate=rotate,
        scale=scale,
        qualifiers=qualifiers,
        omissions=omissions,
    )


def page_number_matches_page_spec(n, page_spec_str, total_pages) -> bool:
    """
    Does page n fall within the given pdftk-style page specification?
    See parse_page_spec for details of the page spec format.

    Returns:
    True or False
    """
    p = parse_page_spec(page_spec_str, total_pages)

    (start, end) = (p.start, p.end) if p.start <= p.end else (p.end, p.start)

    if "even" in p.qualifiers and n % 2 == 1:
        return False
    if "odd" in p.qualifiers and n % 2 == 0:
        return False
    if n < start or n > end:
        return False
    return all(n < omission[0] or n > omission[1] for omission in p.omissions)


def page_numbers_matching_page_spec(page_spec, total_pages) -> [int]:
    """
    Return all page numbers which fall within the given
    pdftk-style page specification.

    See parse_page_spec for details of the page spec format.

    Returns:
    an array of page numbers (starting at 1)

    """
    return page_numbers_matching_page_specs([page_spec], total_pages)


def page_numbers_matching_page_specs(specs, total_pages) -> [int]:
    """
    Return all page numbers which fall within any of the given
    pdftk-style page specifications.

    See parse_page_spec for details of the page spec format.

    Returns:
    an array of page numbers (starting at 1)

    """
    return [
        n
        for n in range(1, total_pages + 1)
        if any(page_number_matches_page_spec(n, page_spec, total_pages) for page_spec in specs)
    ]


@register_help_topic(
    "page_specs",
    title="page specification syntax",
    desc="Specifying collections of pages and transformations",
)
def _help_topic_page_specs():
    """The page specification syntax is a powerful mechanism
    used by commands like `cat`, `delete`, and `rotate` to
    select pages and optionally apply transformations to them as
    they are processed.

    A complete page specification string combines up to three
    optional components in the following order:

    1. Page range: Which pages to select.

    2. Qualifiers and omissions: Filtering the selected pages by
    parity (even/odd) and omitted ranges.

    3. Transformation modifiers: Applying rotation or scaling to
    the selected pages. This is ignored by some operations.

    ### 1. Page Ranges

    A page range defines the starting and ending page
    numbers. If omitted, the specification applies to all pages.

    A page identifier can be:

      an integer (e.g., `5`) representing that page (numbered
      from page 1, the first page of the PDF file, regardless of
      any page labelling),

      the keyword `end`,

      or `r` followed by one of the two above types,
      representing reverse numbering. So `r1` means the same as
      `end`, and `rend` means the same as `1`.

    The following page range formats are supported:

    `<I>`: A single page identifier

    `<I>-<J>`: A range of pages (e.g., `1-5`). If the start page
    number is higher than the end page number (e.g., `5-1`),
    then the pages are treated in reverse order.

    ### 2. Page qualifiers and omissions

    #### Parity qualifiers

    Parity qualifiers filter the selected pages based on their
    number. They are added immediately after the page
    range. Valid qualifiers are:

    `even`: selects only even-numbered pages in the range (e.g.,
    `1-10even`).

    `odd`: selects only odd-numbered pages in the range (e.g.,
    `odd` alone selects all odd pages).

    #### Omissions

    The `~` operator is used to exclude pages from the selection
    defined by the preceding page range and qualifiers.

    `~<N>-<M>`: Omits a range of pages (e.g., `1-end~5-10` selects
    all pages except 5 through 10).

    `~<N>`: Omits a single page (e.g., `1-10~5` selects all pages
    from 1 to 10 except page 5).

    `~r<N>`: Omits a single page counting backwards from the end
    (e.g., `~r1` omits the last page).

    ### 3. Transformation Modifiers


    These optional modifiers can be chained after the range and
    qualifiers to apply changes to the page content.

    #### Rotation (relative)

    These modifiers adjust the page's current rotation property
    by adding or subtracting degrees.

    right: Rotates 90 degrees clockwise (+90),

    left: Rotates 90 degrees counter-clockwise (-90),

    down: Rotates 180 degrees (+180).

    #### Rotation (absolute)

    These modifiers reset and set the page's rotation property
    to a fixed orientation (0, 90, 180, or 270 degrees) relative
    to the page's natural (unrotated) state.

    `north`: Resets rotation to the natural page orientation,

    `east`: Sets rotation to 90 degrees clockwise,

    `south`: Sets rotation to 180 degrees,

    `west`: Sets rotation to 270 degrees clockwise or 90 degrees
    counter-clockwise.

    #### Scale and zoom

    `x<N>`: Scales the page content by factor N. N is typically an
    integer or decimal (e.g., `x2` doubles the size, `x0.5`
    halves it).

    `z<N>`: Zoom in by N steps (or out if N is negative), where a
    zoom of 1 step corresponds to enlarging A4 paper to A3. More
    technically, we scale by factor of 2^(N/2). (N can be any
    number). For example, z1 will scale A4 pages up to A3, and
    `z-1` scales A4 pages down to A5.

    ### Examples

    `1-5eastx2` selects pages 1 through 5, rotating them 90
    degrees clockwise (east) and scaling them by 2x.


    `oddleftz-1` selects only the odd pages from the beginning
    to the end, rotating them 90 degrees counter-clockwise
    (left) and applying a zoom factor of z-1.


    `1-end~3-5` or equivalently `~3-5` selects all pages except
    pages 3-5.

    `~2downz1` selects all pages except page 2, rotating them by
    180 degrees and zooming in 1 step. This will likely need to
    be quoted to prevent your shell misinterpreting it. (The
    same goes for `~3-5`).

    `end-r4` selects the last 4 pages, in reverse order.

    """
