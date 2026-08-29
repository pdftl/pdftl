# src/pdftl/operations/parsers/add_marks_parser.py
"""Parses add_marks spec strings ('<pages>(key=val,...)') into a per-page
MarksConfig map, following the same page-spec + keyval grammar and
last-spec-wins merging convention as excise_parser and rebox_parser.
"""

from __future__ import annotations

from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.helpers.marks_types import MarksConfig
from pdftl.utils.dimensions import dim_str_to_pts
from pdftl.utils.keyval_parser import constrained_bool, constrained_choice, parse_keyval_list
from pdftl.utils.page_specs import page_numbers_matching_page_spec


def _dimension(val: str) -> float:
    """Schema adapter: dim_str_to_pts raises ValueError/TypeError on bad
    input, which parse_keyval_token already knows how to report."""
    try:
        return dim_str_to_pts(val, 0.0)
    except (ValueError, TypeError) as e:
        raise ValueError(str(e)) from e


_SCHEMA = {
    "cropmarks": constrained_choice("western", "japanese", "false"),
    "registration": constrained_bool(),
    "colorbars": constrained_bool(),
    "pageinfo": constrained_bool(),
    "startarget": constrained_bool(),
    "weight": constrained_choice("0.125", "0.25", "0.5"),
    "offset": _dimension,
    "length": _dimension,
}

# Sentinel config for a bare 'remove' spec: strips existing marks and
# restores boxes, draws nothing. Equivalent to wants_anything == False.
_REMOVE_CONFIG = MarksConfig(
    cropmarks=None, registration=False, colorbars=False, pageinfo=False, startarget=False
)


def _config_from_kwargs(kwargs: dict) -> MarksConfig:
    fields: dict = {}
    if "cropmarks" in kwargs:
        fields["cropmarks"] = None if kwargs["cropmarks"] == "false" else kwargs["cropmarks"]
    if "registration" in kwargs:
        fields["registration"] = kwargs["registration"]
    if "colorbars" in kwargs:
        fields["colorbars"] = kwargs["colorbars"]
    if "pageinfo" in kwargs:
        fields["pageinfo"] = kwargs["pageinfo"]
    if "startarget" in kwargs:
        fields["startarget"] = kwargs["startarget"]
    if "weight" in kwargs:
        fields["weight"] = float(kwargs["weight"])
    if "offset" in kwargs:
        fields["offset"] = kwargs["offset"]
    if "length" in kwargs:
        fields["length"] = kwargs["length"]
    try:
        return MarksConfig(**fields)
    except ValueError as e:
        raise InvalidArgumentError(f"add_marks: {e}") from e


def _parse_single(spec: str) -> tuple[str, MarksConfig]:
    if "(" in spec:
        page_spec, _, rest = spec.partition("(")
        if not rest.endswith(")"):
            raise InvalidArgumentError(f"add_marks: invalid spec '{spec}', missing closing ')'.")
        tokens = [t.strip() for t in rest[:-1].split(",") if t.strip()]
    else:
        page_spec = spec
        tokens = []

    bare: list[str] = []
    kwargs = parse_keyval_list(tokens, schema=_SCHEMA, context="add_marks", bare_tokens=bare)

    if bare:
        if bare != ["remove"]:
            raise InvalidArgumentError(
                f"add_marks: unrecognized token(s) {bare} in spec '{spec}'."
            )
        if kwargs:
            raise InvalidArgumentError(
                f"add_marks: 'remove' cannot be combined with other options, in spec '{spec}'."
            )
        return page_spec or "-", _REMOVE_CONFIG

    return page_spec or "-", _config_from_kwargs(kwargs)


def parse_add_marks_args(args: list[str], total_pages: int) -> dict[int, MarksConfig]:
    """{1-based page number: MarksConfig}. Later specs replace earlier ones
    wholesale for any page number they both touch (last spec wins), matching
    excise's and rebox's spec-merging convention -- a later spec is not
    layered on top of an earlier one's config, it replaces it.

    A bare `add_marks` with no specs at all applies the default MarksConfig
    to every page.
    """
    if not args:
        args = ["-"]

    page_configs: dict[int, MarksConfig] = {}
    for spec in args:
        page_spec, config = _parse_single(spec)
        for page_num in page_numbers_matching_page_spec(page_spec, total_pages):
            page_configs[page_num] = config
    return page_configs
