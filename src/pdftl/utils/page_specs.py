# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/page_specs.py

"""Methods to parse and deal with page specs
(range-like specifications of collections of pages)

Public:

PageTransform
PageSpec

parse_specs(specs, total_pages) -> Generator[PageSpec]
parse_compound_page_spec(spec_str) -> list[str]
parse_sub_page_spec(spec, total_pages) -> PageSpec

expand_specs_to_pages(specs, aliases=None, inputs=None, opened_pdfs=None)
  -> [PageTransform]

page_number_matches_page_spec(n, page_spec_str, total_pages) -> bool
page_numbers_matching_page_spec(page_spec, total_pages) -> [int]
page_numbers_matching_page_specs(specs, total_pages) -> [int]
"""

import logging
import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING
from collections.abc import Generator

if TYPE_CHECKING:
    from pikepdf import Pdf

from pdftl.core.registry import register_help_topic
from pdftl.exceptions import InvalidArgumentError, UserCommandLineError

logger = logging.getLogger(__name__)


@dataclass
class PageTransform:
    """A dataclass for passing page transformation data around"""

    pdf: "Pdf"
    index: int
    rotation: tuple[int | float, bool]
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

SPEC_REGEX = re.compile(
    r"""
    ^                     # Anchor to the start of the string
    (?:                   # Start optional non-capturing group for whole range
        (r(?!ight))?      # CAPTURE GROUP 1: Optional 'r', reverse start page
        (end|\d+)?        # CAPTURE GROUP 2: The start page number or 'end'
        (?:               # Start optional non-capturing group for end of range
            -             # literal hyphen separator
            (r(?!ight))?  # CAPTURE GROUP 3: Optional 'r' for reverse end page
            (end|\d+)?    # CAPTURE GROUP 4: end page number or 'end'
        )?                # End of optional end-of-range group
    )?                    # End of optional page-range group
    (.*)                  # CAPTURE GROUP 5: Greedily capture rest as modifiers
    """,
    re.IGNORECASE | re.VERBOSE,
)


# --- Internal Parsing Helpers ---


def _expand_square_brackets(specs: list[str]) -> list[str]:
    """
    Expands Group Syntax: `[A,B]mod` -> `Amod, Bmod`.
    Raises UserCommandLineError if the spec is ambiguous (e.g. `[1,2]x2,3`).
    """
    expanded = []
    # Matches [content]suffix
    group_re = re.compile(r"^\[([^\]]+)\](.*)$")

    for spec in specs:
        if spec is None:
            continue
        spec = spec.strip()
        match = group_re.match(spec)

        if match:
            content, suffix = match.groups()

            # Guardrail: If the suffix contains a comma, the user likely forgot a space.
            if "," in suffix:
                raise UserCommandLineError(
                    f"Invalid page spec: '{spec}'.\n"
                    f"Found a comma after the closing bracket (in '{suffix}').\n"
                    "Please separate distinct page specifications with spaces.\n"
                    "Example: Use '[1,2]x3 6x2' instead of '[1,2]x3,6x2'."
                )

            # 1. Split the inner content by comma
            sub_specs = [s.strip() for s in content.split(",") if s.strip()]

            # 2. Distribute the suffix to every item
            for sub in sub_specs:
                expanded.append(f"{sub}{suffix}")
        else:
            expanded.append(spec)

    return expanded


def _flatten_spec_list(specs: list[str]) -> list[str]:
    """
    Takes a list of spec strings (which may contain commas) and returns
    a flat list of atomic spec strings.
    e.g. ["1,3", "5-7"] -> ["1", "3", "5-7"]
    """
    flat = []
    for s in specs:
        if s is None:
            continue

        # If the string is empty or just whitespace, we treat it as an empty spec
        # (which parse_sub_page_spec interprets as 'all pages').
        if s.strip() == "":
            flat.append("")
            continue

        # Split by comma and strip whitespace
        parts = [p.strip() for p in s.split(",") if p.strip()]
        flat.extend(parts)
    return flat


def _resolve_page_token(token_str, is_reverse, total_pages):
    if token_str is None:
        return None
    is_end_token = token_str.lower() == "end"
    if is_reverse:
        if is_end_token:
            return 1  # 'rend' means page 1
        return total_pages - int(token_str) + 1
    if is_end_token:
        return total_pages
    return int(token_str)


def _parse_range_part(spec, total_pages):
    range_match = SPEC_REGEX.match(spec)
    if not range_match:
        raise InvalidArgumentError(f"Invalid page spec format: {spec}")

    start_is_rev, start_str, end_is_rev, end_str, modifier_str = range_match.groups()

    if start_str is not None or end_str is not None:
        start = _resolve_page_token(start_str, start_is_rev, total_pages) or 0
        end = _resolve_page_token(end_str, end_is_rev, total_pages) or start
    else:
        start, end = 1, total_pages

    if start <= 0:
        raise InvalidArgumentError(
            f"Parsed invalid starting page {start} from the range spec {spec}. "
            "Valid page numbers start at 1."
        )

    return start, end, modifier_str


def _parse_qualifiers(modifier_str):
    qualifiers = set()
    for qual in QUALIFIER_MAP:
        if qual in modifier_str:
            qualifiers.add(qual)
            modifier_str = modifier_str.replace(qual, "", 1)
    return qualifiers, modifier_str


def _parse_rotation(modifier_str):
    for key, value in ROTATION_MAP.items():
        if key in modifier_str:
            return value, modifier_str.replace(key, "", 1)
    return (0, False), modifier_str


def _parse_scaling(modifier_str):
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
    omissions = []
    omit_re = re.compile(r"^(~([^~]*))")

    remaining_str = modifier_str
    while remaining_str:
        omit_match = omit_re.match(remaining_str)
        if not omit_match:
            raise InvalidArgumentError(
                f"Invalid part '{remaining_str}' should start with ~ while parsing omissions."
            )

        omit_range_str = omit_match.group(2)
        if omit_range_str:
            # Recursive call to the atomic parser
            omit_page_spec = parse_sub_page_spec(omit_range_str, total_pages)
            omissions.append(tuple(sorted((omit_page_spec.start, omit_page_spec.end))))

        remaining_str = omit_re.sub("", remaining_str, 1)

    return omissions, remaining_str


# --- Public API ---


def parse_compound_page_spec(spec_str: str) -> list[str]:
    """
    Parses a single potentially complex spec string (like "[1,2]x2" or "1,5")
    into a flat list of atomic spec strings (like ["1x2", "2x2"] or ["1", "5"]).
    """
    # Wrap in list because _expand_square_brackets expects a list of args
    grouped = _expand_square_brackets([spec_str])
    return _flatten_spec_list(grouped)


def parse_sub_page_spec(spec, total_pages) -> PageSpec:
    """
    Parses a SINGLE atomic pdftk-style page specification.

    WARNING: This does NOT handle commas or brackets.
    Use parse_specs or parse_compound_page_spec for full support.

    Returns: a PageSpec object.
    """
    logger.debug("spec=%s, total_pages=%s", spec, total_pages)

    # 1. Parse the primary page range
    start, end, modifier_str = _parse_range_part(spec, total_pages)

    # 2. Sequentially parse modifiers
    qualifiers, modifier_str = _parse_qualifiers(modifier_str.lower())
    rotate, modifier_str = _parse_rotation(modifier_str)
    scale, modifier_str = _parse_scaling(modifier_str)
    omissions, modifier_str = _parse_omissions(modifier_str, total_pages)

    return PageSpec(
        start=start,
        end=end,
        rotate=rotate,
        scale=scale,
        qualifiers=qualifiers,
        omissions=omissions,
    )


def parse_specs(specs: list[str], total_pages: int) -> Generator[PageSpec, None, None]:
    """
    The Smart Funnel.

    Takes a list of raw spec strings (from CLI arguments), handles
    group expansion ([1,2]x2) and comma splitting (1,3), and yields
    parsed PageSpec objects one by one.

    This is the preferred entry point for commands like rotate, spin, etc.
    """
    # 1. Expand Groups: [1,2]x2 -> 1x2, 2x2
    grouped_specs = _expand_square_brackets(specs)

    # 2. Flatten commas: "1,3" -> "1", "3"
    flattened_specs = _flatten_spec_list(grouped_specs)

    for spec_str in flattened_specs:
        yield parse_sub_page_spec(spec_str, total_pages)


def expand_specs_to_pages(
    specs, aliases=None, inputs=None, opened_pdfs=None
) -> list[PageTransform]:
    """
    Expand pdftk-style page specs into an array of PageTransform objects.
    Used primarily by the 'cat' command.
    """
    aliases = aliases or {}
    opened_pdfs = opened_pdfs or {}

    if not inputs:
        raise ValueError("inputs were not passed in expand_specs_to_pages")

    default_alias = "DEFAULT"
    aliases[default_alias] = 0
    opened_pdfs_by_alias = {alias: opened_pdfs[idx] for alias, idx in aliases.items()}

    if not specs:
        return _handle_no_specs(inputs, opened_pdfs)

    # Reuse the logic of expanding groups and flattening lists
    grouped_specs = _expand_square_brackets(specs)
    flattened_specs = _flatten_spec_list(grouped_specs)

    page_tuples = []
    for spec_str in flattened_specs:
        page_tuples.extend(
            _new_tuples_from_spec_str(spec_str, opened_pdfs_by_alias, default_alias)
        )

    return page_tuples


# --- Internal Cat Helpers ---


def _handle_no_specs(inputs, opened_pdfs) -> list[PageTransform]:
    page_tuples = []
    for input_idx in range(len(inputs)):
        pdf = opened_pdfs[input_idx]
        for i in range(len(pdf.pages)):
            page_tuples.append(PageTransform(pdf=pdf, index=i, rotation=(0, False), scale=1.0))
    return page_tuples


def _resolve_alias_and_spec(spec, opened_pdfs_by_alias, default_alias):
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
    if "even" in qualifiers:
        page_numbers = [p for p in page_numbers if p % 2 == 0]
    if "odd" in qualifiers:
        page_numbers = [p for p in page_numbers if p % 2 != 0]

    for om_start, om_end in omissions:
        page_numbers = [p for p in page_numbers if not om_start <= p <= om_end]
    return page_numbers


def _create_page_tuples_from_numbers(
    page_numbers, pdf, rotate, scale, spec_for_error
) -> list[PageTransform]:
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
        new_tuples.append(PageTransform(pdf=pdf, index=page_num - 1, rotation=rotate, scale=scale))
    return new_tuples


def _new_tuples_from_spec_str(
    spec_str, opened_pdfs_by_alias, default_alias
) -> list[PageTransform]:
    pdf, page_spec_full, alias = _resolve_alias_and_spec(
        spec_str, opened_pdfs_by_alias, default_alias
    )

    # Use the atomic parser here
    page_spec = parse_sub_page_spec(page_spec_full, len(pdf.pages))

    step = 1 if page_spec.start <= page_spec.end else -1
    initial_page_numbers = list(range(page_spec.start, page_spec.end + step, step))

    final_page_numbers = _filter_page_numbers(
        initial_page_numbers, page_spec.qualifiers, page_spec.omissions
    )

    new_tuples = _create_page_tuples_from_numbers(
        final_page_numbers, pdf, page_spec.rotate, page_spec.scale, spec_str
    )
    return new_tuples


# --- Query Helpers ---


def page_number_matches_page_spec(n, page_spec_str, total_pages) -> bool:
    """
    Does page n fall within the given pdftk-style page specification?
    Supports comma-separated specs (e.g., "1,3,5").
    """
    # Use public helper to handle commas/brackets if any
    specs = parse_compound_page_spec(page_spec_str)

    for s in specs:
        p = parse_sub_page_spec(s, total_pages)
        (start, end) = (p.start, p.end) if p.start <= p.end else (p.end, p.start)

        if "even" in p.qualifiers and n % 2 == 1:
            continue
        if "odd" in p.qualifiers and n % 2 == 0:
            continue
        if n < start or n > end:
            continue
        if any(omission[0] <= n <= omission[1] for omission in p.omissions):
            continue

        return True  # Matched this sub-spec

    return False


def page_numbers_matching_page_spec(page_spec, total_pages) -> list[int]:
    """Return all page numbers which fall within the given spec."""
    return page_numbers_matching_page_specs([page_spec], total_pages)


def page_numbers_matching_page_specs(specs, total_pages) -> list[int]:
    """Return all page numbers which fall within any of the given specs."""
    # Flatten via internal or public helpers
    flattened_specs = []
    # If specs is just a list of strings, we can run them through expansion
    grouped = _expand_square_brackets(specs)
    flattened_specs = _flatten_spec_list(grouped)

    return [
        n
        for n in range(1, total_pages + 1)
        if any(
            page_number_matches_page_spec(n, page_spec, total_pages)
            for page_spec in flattened_specs
        )
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

    Multiple ranges can be separated by commas (e.g. `1,3,5-10`).

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

    #### Groups (Applies to all contents)

    `[<Range>, <Range>]<Modifier>`: Applies the modifier to the entire
    disjoint set of pages.

    Example: `[1-3, 5]x2` scales pages 1, 2, 3, and 5.

    **Note:** Group specifications must be distinct arguments. You cannot
    combine them with other ranges using commas (e.g. `[1,2]x3,6x2` is invalid).
    Use spaces instead: `[1,2]x3 6x2`.

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

    `1,3,5` selects pages 1, 3, and 5.

    `[1,3,5]x2` selects pages 1, 3, and 5 and scales them all.

    """
