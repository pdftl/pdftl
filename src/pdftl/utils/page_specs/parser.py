import logging
import math
import re

from pdftl.exceptions import InvalidArgumentError

from .spec_types import PageSpec

logger = logging.getLogger(__name__)

# --- Constants ---

SPEC_REGEX = re.compile(
    r"""
    ^                     # Anchor to the start of the string
    (?:                   # Start optional non-capturing group for whole range
        (r(?!ight|ep))?   # CAPTURE GROUP 1: Optional 'r', reverse start page (not right or rep)
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
QUALIFIER_MAP = {"even", "odd", "portrait", "landscape"}


# --- Parser Class ---


class SpecParser:
    """Encapsulates parsing logic for a specific context (total_pages)."""

    def __init__(self, total_pages: int, spec_regex=None):
        self.total_pages = total_pages
        # Allow injection of regex for testing (mocking SPEC_REGEX)
        self.spec_regex = spec_regex or SPEC_REGEX

    def parse(self, spec_str: str) -> PageSpec:
        """Parses a single atomic spec string into a PageSpec object.

        Raises pdftl.exceptions.InvalidArgumentError on failure.
        """
        logger.debug("spec=%s, total_pages=%s", spec_str, self.total_pages)
        # Clean up any accidental internal or surrounding user whitespaces
        if isinstance(spec_str, str):
            cleaned_spec_str = spec_str.replace(" ", "")
            if spec_str != cleaned_spec_str:
                logger.debug("cleaned spaces: spec=%s", spec_str)
                spec_str = cleaned_spec_str

        # 1. Parse the primary page range
        start, end, modifier_str = self._parse_range_part(spec_str)

        # 2. Sequentially parse modifiers
        # Note: Order matters. We consume parts of the string.
        qualifiers, modifier_str = self._parse_qualifiers(modifier_str.lower())
        step, modifier_str = self._parse_step(modifier_str)
        block_rep, modifier_str = self._parse_block_rep(modifier_str)
        rep, modifier_str = self._parse_rep(modifier_str)
        rotate, modifier_str = self._parse_rotation(modifier_str)
        scale, modifier_str = self._parse_scaling(modifier_str)

        # 3. Check for unrecognised modifier before attempting omissions
        if modifier_str and not modifier_str.startswith("~"):
            raise InvalidArgumentError(
                f"Invalid page spec modifier '{modifier_str}' in '{spec_str}'. "
                f"Did you mean one of: even, odd, north, east, south, west, left, right, down, "
                f"stepN, copyN, repN, xN, zN, or ~N for omissions?"
            )

        omissions, modifier_str = self._parse_omissions(modifier_str)

        return PageSpec(
            start=start,
            end=end,
            step=step,
            block_rep=block_rep,
            rep=rep,
            rotate=rotate,
            scale=scale,
            qualifiers=qualifiers,
            omissions=omissions,
        )

    def _resolve_page_token(self, token_str, is_reverse):
        if token_str is None:
            return None
        is_end_token = token_str.lower() == "end"
        if is_end_token:
            if is_reverse:
                return 1  # 'rend' means page 1
            else:
                return self.total_pages
        try:
            token_int = int(token_str)
        except ValueError as e:
            raise InvalidArgumentError(
                f"Could not parse page token '{token_str}' as an integer"
            ) from e
        if is_reverse:
            return self.total_pages - token_int + 1
        return token_int

    def _parse_range_part(self, spec):
        range_match = self.spec_regex.match(spec)
        if not range_match:
            raise InvalidArgumentError(f"Invalid page spec format: {spec}")

        start_is_rev, start_str, end_is_rev, end_str, modifier_str = range_match.groups()

        if start_str is not None or end_str is not None:
            start = self._resolve_page_token(start_str, start_is_rev) or 0
            end = self._resolve_page_token(end_str, end_is_rev) or start
        else:
            start, end = 1, self.total_pages

        if start <= 0:
            raise InvalidArgumentError(
                f"Parsed invalid starting page {start} from the range spec {spec}. "
                "Valid page numbers start at 1."
            )

        return start, end, modifier_str

    def _parse_qualifiers(self, modifier_str):
        """Parse simple boolean keyword modifiers, like even, odd"""
        qualifiers = set()
        for qual in QUALIFIER_MAP:
            if qual in modifier_str:
                qualifiers.add(qual)
                modifier_str = modifier_str.replace(qual, "", 1)
        return qualifiers, modifier_str

    def _parse_step(self, modifier_str):
        step_re = re.compile(r"((step|by|every) *[=]? *([+-]? *\d*))")
        step_match = step_re.search(modifier_str)
        if not step_match:
            return 1, modifier_str
        match_groups = step_match.groups()
        kw_match = match_groups[1]  # (step|by|every)
        matched_digits = match_groups[2]
        logger.debug("kw_match='%s', matched_digits='%s'", kw_match, matched_digits)
        if not matched_digits:
            raise InvalidArgumentError(
                f"Empty {kw_match} value. Example: use {kw_match}3 to select every 3rd page"
            )
        step_val = int(matched_digits)
        logger.debug("step_val=%s", step_val)
        if not step_val >= 1:
            raise InvalidArgumentError(
                f"Invalid {kw_match} value {step_val}. Should be at least 1."
            )

        modifier_str = step_re.sub("", modifier_str, 1)

        return step_val, modifier_str

    def _parse_rep_like_modifier(self, modifier_str, keyword):
        rep_re = re.compile(rf"({keyword} *(\d+))")
        rep_match = rep_re.search(modifier_str)
        if not rep_match:
            return 1, modifier_str

        rep_val = int(rep_match.group(2))

        if rep_val < 1:
            raise InvalidArgumentError(f"Invalid {keyword} value {rep_val}. Should be at least 1.")

        modifier_str = rep_re.sub("", modifier_str, 1)
        return rep_val, modifier_str

    def _parse_block_rep(self, modifier_str):
        return self._parse_rep_like_modifier(modifier_str, "copy")

    def _parse_rep(self, modifier_str):
        return self._parse_rep_like_modifier(modifier_str, "rep")

    def _parse_rotation(self, modifier_str):
        for key, value in ROTATION_MAP.items():
            if key in modifier_str:
                return value, modifier_str.replace(key, "", 1)
        return (0, False), modifier_str

    def _parse_scaling(self, modifier_str):
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
            if abs(zoom_val) > 1000:
                raise InvalidArgumentError(f"Zoom value out of bounds: {zoom_val}")
            scale *= math.pow(math.sqrt(2), zoom_val)
            modifier_str = zoom_re.sub("", modifier_str, 1)

        return scale, modifier_str

    def _parse_omissions(self, modifier_str):
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
                # Recursive call using the same class logic (passing self.spec_regex)
                omit_parser = SpecParser(self.total_pages, spec_regex=self.spec_regex)
                omit_page_spec = omit_parser.parse(omit_range_str)
                omissions.append(tuple(sorted((omit_page_spec.start, omit_page_spec.end))))

            remaining_str = omit_re.sub("", remaining_str, 1)

        return omissions, remaining_str


# --- Internal Helpers (Exposed for Resolver and Tests) ---


def _expand_square_brackets(specs: list[str]) -> list[str]:
    """
    Expands Group Syntax: `[A,B]mod` -> `Amod, Bmod`.
    Raises InvalidArgumentError if the spec is ambiguous (e.g. `[1,2]x2,3`).
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
                raise InvalidArgumentError(
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

        if s.strip() == "":
            flat.append("")
            continue

        parts = [p.strip() for p in s.split(",") if p.strip()]
        flat.extend(parts)
    return flat
