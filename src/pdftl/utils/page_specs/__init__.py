"""
Methods to parse and deal with page specs
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
from collections.abc import Generator

from .help import _help_topic_page_specs
from .parser import SPEC_REGEX  # Imported for testing/patching visibility
from .parser import (  # Internals exposed for tests
    SpecParser,
    _expand_square_brackets,
    _flatten_spec_list,
)
from .resolver import (  # Internals exposed for tests
    _create_page_tuples_from_numbers,
    _filter_page_numbers,
    _handle_no_specs,
    _new_tuples_from_spec_str,
    _resolve_alias_and_spec,
)
from .spec_types import PageSpec, PageTransform

logger = logging.getLogger(__name__)


# --- Facade API Implementation ---


def parse_sub_page_spec(spec, total_pages) -> PageSpec:
    """
    Parses a SINGLE atomic pdftk-style page specification.
    Returns: a PageSpec object.

    NOTE: We explicitly inject SPEC_REGEX here. This ensures that if a test
    patches 'pdftl.utils.page_specs.SPEC_REGEX', the parser uses the mock.
    """
    parser = SpecParser(total_pages, spec_regex=SPEC_REGEX)
    return parser.parse(spec)


def parse_compound_page_spec(spec_str: str) -> list[str]:
    """
    Parses a single potentially complex spec string (like "[1,2]x2" or "1,5")
    into a flat list of atomic spec strings.
    """
    grouped = _expand_square_brackets([spec_str])
    return _flatten_spec_list(grouped)


def parse_specs(specs: list[str], total_pages: int) -> Generator[PageSpec, None, None]:
    """
    The Smart Funnel.
    Takes a list of raw spec strings and yields parsed PageSpec objects.
    """
    grouped_specs = _expand_square_brackets(specs)
    flattened_specs = _flatten_spec_list(grouped_specs)

    for spec_str in flattened_specs:
        yield parse_sub_page_spec(spec_str, total_pages)


def expand_specs_to_pages(
    specs, aliases=None, inputs=None, opened_pdfs=None
) -> list[PageTransform]:
    """
    Expand pdftk-style page specs into an array of PageTransform objects.

    NOTE: Implemented here (composition root) to ensure it uses the
    _handle_no_specs visible in this module (which might be patched by tests).
    """
    aliases = aliases or {}
    opened_pdfs = opened_pdfs or []

    if not inputs and not opened_pdfs:
        raise ValueError("no inputs or opened pdfs were passed to expand_specs_to_pages")

    if not specs:
        # This call uses the imported _handle_no_specs in THIS namespace.
        # This allows test patches on 'pdftl.utils.page_specs._handle_no_specs' to work.
        return _handle_no_specs(inputs, opened_pdfs)

    default_alias = "DEFAULT"
    aliases[default_alias] = 0
    opened_pdfs_by_alias = {alias: opened_pdfs[idx] for alias, idx in aliases.items()}

    grouped_specs = _expand_square_brackets(specs)
    flattened_specs = _flatten_spec_list(grouped_specs)

    page_tuples = []
    for spec_str in flattened_specs:
        page_tuples.extend(
            _new_tuples_from_spec_str(spec_str, opened_pdfs_by_alias, default_alias)
        )

    return page_tuples


# --- Query Helpers ---


def page_number_matches_page_spec(n, page_spec_str, total_pages) -> bool:
    specs = parse_compound_page_spec(page_spec_str)
    for s in specs:
        p = parse_sub_page_spec(s, total_pages)
        logger.debug("step=%s", p.step)
        start, end = (p.start, p.end) if p.start <= p.end else (p.end, p.start)

        if (
            ("even" in p.qualifiers and n % 2 == 1)
            or ("odd" in p.qualifiers and n % 2 == 0)
            or (n - p.start) % p.step != 0
            or n < start
            or n > end
            or any(omission[0] <= n <= omission[1] for omission in p.omissions)
        ):
            continue

        return True

    return False


def page_numbers_matching_page_spec(page_spec, total_pages) -> list[int]:
    return page_numbers_matching_page_specs([page_spec], total_pages)


def page_numbers_matching_page_specs(specs, total_pages) -> list[int]:
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


# --- Legacy Wrappers (Restored for Backward Compatibility with Tests) ---


def _resolve_page_token(token_str, is_reverse, total_pages):
    # Inject SPEC_REGEX to ensure test patching works if applicable
    return SpecParser(total_pages, spec_regex=SPEC_REGEX)._resolve_page_token(
        token_str, is_reverse
    )


def _parse_range_part(spec, total_pages):
    # This wrapper is critical for tests patching SPEC_REGEX
    return SpecParser(total_pages, spec_regex=SPEC_REGEX)._parse_range_part(spec)


def _parse_qualifiers(modifier_str):
    return SpecParser(1, spec_regex=SPEC_REGEX)._parse_qualifiers(modifier_str)


def _parse_rotation(modifier_str):
    return SpecParser(1, spec_regex=SPEC_REGEX)._parse_rotation(modifier_str)


def _parse_scaling(modifier_str):
    return SpecParser(1, spec_regex=SPEC_REGEX)._parse_scaling(modifier_str)


def _parse_omissions(modifier_str, total_pages):
    return SpecParser(total_pages, spec_regex=SPEC_REGEX)._parse_omissions(modifier_str)


__all__ = [
    "PageTransform",
    "PageSpec",
    "parse_specs",
    "parse_compound_page_spec",
    "parse_sub_page_spec",
    "expand_specs_to_pages",
    "page_number_matches_page_spec",
    "page_numbers_matching_page_spec",
    "page_numbers_matching_page_specs",
    "_help_topic_page_specs",
    # Internals exposed for tests
    "_expand_square_brackets",
    "_flatten_spec_list",
    "_handle_no_specs",
    "_resolve_alias_and_spec",
    "_filter_page_numbers",
    "_create_page_tuples_from_numbers",
    "_new_tuples_from_spec_str",
    "_resolve_page_token",
    "_parse_range_part",
    "_parse_qualifiers",
    "_parse_rotation",
    "_parse_scaling",
    "_parse_omissions",
    "SPEC_REGEX",
]
