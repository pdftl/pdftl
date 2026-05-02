# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/delete_blank.py

"""Delete blank or near-blank pages based on ink coverage or pixel deviation."""

import logging
from dataclasses import dataclass

import pdftl.core.constants as c
from pdftl.core.registry import register_operation
from pdftl.core.types import OpResult
from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.keyval_parser import parse_keyval_string
from pdftl.utils.page_specs import page_numbers_matching_page_spec

logger = logging.getLogger(__name__)

#: DPI used for blank detection when none is specified.
#: Low enough to be fast; high enough to catch fine hairlines.
_DEFAULT_DETECTION_DPI = 30.0

#: Default ink coverage threshold (fraction of non-white pixels, 0.0–1.0).
_DEFAULT_THRESHOLD = 0.005  # 0.5%

#: Default maximum stddev of pixel values (0–255 scale).
_DEFAULT_STDDEV = 5.0

_THRESHOLD_KW = "threshold"
_STDDEV_KW = "stddev"
_DPI_KW = "dpi"

_DELETE_BLANK_LONG_DESC = """
The `delete_blank` operation removes pages that are blank or visually uniform
by rendering each candidate page at low resolution and analysing its pixels.

**Detection methods:**

Two complementary methods are available. If both are specified, a page must
satisfy *both* criteria to be deleted.

- **`threshold`** — ink coverage: the fraction of pixels that differ from
  pure white (0.0–1.0, default `0.005`). Use `threshold=0` for
  pure-white-only detection.

- **`stddev`** — pixel standard deviation: how uniform the page is regardless
  of its base colour. A solid white *or* solid coloured page both score near
  zero (default `5.0`, on a 0–255 scale). Useful for detecting pages with
  coloured backgrounds.

- **`mode`** — colour space for analysis: `grey` (default) or `rgb`.
  `rgb` is more accurate for coloured backgrounds; `grey` is faster.

If neither `threshold` nor `stddev` is specified, both defaults are applied.

**DPI:**

The `dpi` parameter controls the render resolution used for detection
(default: `30`). Higher values catch finer marks but are slower.

**Selector:**

An optional page selector (e.g. `1-5`, `odd`) restricts which pages are
*candidates* for deletion. Non-candidate pages are always kept.

The syntax is `[selector](Key=Value, ...)`, where all parameters are optional.

### Parameters

| Parameter   | Description                                             | Default | Example      |
| :---------- | :------------------------------------------------------ | :------ | :----------- |
| `threshold` | Max ink coverage fraction to consider blank (0.0–1.0)  | `0.005` | `threshold=0` |
| `stddev`    | Max pixel stddev to consider uniform (0–255)            | `5.0`   | `stddev=2.5` |
| `mode`      | Colour space: `grey` or `rgb`                           | `grey`  | `mode=rgb`   |
| `dpi`       | Render resolution for detection                         | `30`    | `dpi=72`     |

"""

_DELETE_BLANK_KEYS = [_THRESHOLD_KW, _STDDEV_KW, "mode", _DPI_KW]

_DELETE_BLANK_EXAMPLES = [
    {
        "cmd": "in.pdf delete_blank output out.pdf",
        "desc": "Delete blank pages using default threshold and stddev detection.",
    },
    {
        "cmd": "in.pdf delete_blank '(threshold=0)' output out.pdf",
        "desc": "Delete only perfectly white pages (zero ink coverage).",
    },
    {
        "cmd": "in.pdf delete_blank '(stddev=3,mode=rgb)' output out.pdf",
        "desc": "Delete pages that are visually uniform in any colour, using RGB analysis.",
    },
    {
        "cmd": "in.pdf delete_blank 'even(threshold=0.01,dpi=72)' output out.pdf",
        "desc": "On even pages only, delete near-blank pages rendered at 72 dpi.",
    },
]


# ---------------------------------------------------------------------------
# Parameter parsing
# ---------------------------------------------------------------------------


@dataclass
class _BlankSpec:
    """Resolved, validated parameters for one spec."""

    candidate_indices: set[int]  # 0-based
    dpi: float
    mode: str
    threshold: float
    stddev: float
    use_threshold: bool
    use_stddev: bool


def _parse_float(value: str, name: str, min_val: float = None, max_val: float = None) -> float:
    """Parse a float param, raising InvalidArgumentError on bad input."""
    try:
        result = float(value)
    except ValueError:
        raise InvalidArgumentError(f"Invalid value '{value}' for '{name}': expected a number.")
    if min_val is not None and result < min_val:
        raise InvalidArgumentError(f"Invalid value '{value}' for '{name}': must be >= {min_val}.")
    if max_val is not None and result > max_val:
        raise InvalidArgumentError(f"Invalid value '{value}' for '{name}': must be <= {max_val}.")
    return result


def _extract_params(spec: str) -> tuple[str, dict[str, str]]:
    if "(" not in spec or not spec.endswith(")"):
        return spec, {}
    selector, params_str = spec[:-1].split("(", 1)
    return selector, parse_keyval_string(
        params_str,
        allowed_keys=_DELETE_BLANK_KEYS,
        bare_tokens=True,  # silently skip tokens without '='
        context="delete_blank",
    )


def _resolve_params(params: dict[str, str]) -> tuple[str, float, float, float, bool, bool]:
    """
    Validate and resolve raw string params into typed values.
    Returns (mode, dpi, threshold, stddev, use_threshold, use_stddev).
    """
    mode = params.get("mode", "grey")
    if mode not in ("grey", "rgb"):
        raise InvalidArgumentError(f"Invalid mode '{mode}': must be 'grey' or 'rgb'.")

    dpi = (
        _parse_float(params[_DPI_KW], _DPI_KW, min_val=1.0)
        if _DPI_KW in params
        else _DEFAULT_DETECTION_DPI
    )

    use_threshold = _THRESHOLD_KW in params
    use_stddev = _STDDEV_KW in params
    if not use_threshold and not use_stddev:
        use_threshold = True
        use_stddev = True

    threshold = (
        _parse_float(params[_THRESHOLD_KW], _THRESHOLD_KW, min_val=0.0, max_val=1.0)
        if _THRESHOLD_KW in params
        else _DEFAULT_THRESHOLD
    )
    stddev = (
        _parse_float(params[_STDDEV_KW], _STDDEV_KW, min_val=0.0)
        if _STDDEV_KW in params
        else _DEFAULT_STDDEV
    )
    return mode, dpi, threshold, stddev, use_threshold, use_stddev


def _parse_spec(spec: str, total_pages: int) -> _BlankSpec:
    """
    Parse a spec string into a _BlankSpec.
    Examples: '', '-', 'odd', 'even(threshold=0.01,dpi=72)'
    """
    selector, params = _extract_params(spec)
    mode, dpi, threshold, stddev, use_threshold, use_stddev = _resolve_params(params)

    if not selector or selector == "-":
        candidate_1based = list(range(1, total_pages + 1))
    else:
        candidate_1based = page_numbers_matching_page_spec(selector, total_pages)

    return _BlankSpec(
        candidate_indices={p - 1 for p in candidate_1based},
        dpi=dpi,
        mode=mode,
        threshold=threshold,
        stddev=stddev,
        use_threshold=use_threshold,
        use_stddev=use_stddev,
    )


# ---------------------------------------------------------------------------
# Pixel analysis
# ---------------------------------------------------------------------------


def _compute_ink_coverage(image) -> float:
    """Return the fraction of pixels that are not pure white (0.0–1.0)."""
    import numpy as np

    arr = np.array(image.convert("L"))
    total = arr.size
    if total == 0:
        return 0.0
    return int((arr < 255).sum()) / total


def _compute_stddev(image, mode: str) -> float:
    """
    Return the standard deviation of pixel values (0–255 scale).

    For mode='grey', returns stddev of the greyscale image.
    For mode='rgb', returns the mean stddev across R, G, B channels.
    """
    import numpy as np

    if mode == "rgb":
        arr = np.array(image.convert("RGB")).astype(float)
        return float(np.mean([arr[:, :, ch].std() for ch in range(3)]))
    arr = np.array(image.convert("L")).astype(float)
    return float(arr.std())


def _page_is_blank(image, spec: _BlankSpec) -> bool:
    """Return True if the page image satisfies all active criteria in spec."""
    if spec.use_threshold and _compute_ink_coverage(image) >= spec.threshold:
        return False
    if spec.use_stddev and _compute_stddev(image, spec.mode) >= spec.stddev:
        return False
    return True


# ---------------------------------------------------------------------------
# Per-spec evaluation helper
# ---------------------------------------------------------------------------


def _find_blank_pages_for_spec(pdf, spec: _BlankSpec) -> set[int]:
    """
    Render candidate pages for one spec and return the set of blank 1-based
    page numbers. Serialises the PDF exactly once per call.
    """
    from pdftl.utils.page_images import iter_pages_as_pil

    blank = set()
    sorted_candidates = sorted(list(spec.candidate_indices))
    for page_index, image in iter_pages_as_pil(pdf, spec.dpi, page_indices=sorted_candidates):
        if _page_is_blank(image, spec):
            p_num = page_index + 1
            blank.add(p_num)
            logger.debug("Page %d marked as blank.", p_num)
    return blank


# ---------------------------------------------------------------------------
# Operation
# ---------------------------------------------------------------------------


@register_operation(
    "delete_blank",
    tags=["in_place", "pages", "optimization", "delete"],
    type="single input operation",
    desc="Delete blank or near-blank pages",
    long_desc=_DELETE_BLANK_LONG_DESC,
    usage="<input> delete_blank '[spec](params)' output <output>",
    examples=_DELETE_BLANK_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def delete_blank(pdf, specs) -> OpResult:
    """
    Delete pages that are blank or visually uniform.

    Each spec is evaluated independently. A page is deleted if any spec
    marks it as blank. Deletion happens in reverse page order so indices
    remain valid throughout.
    """
    from pdftl.utils.dependencies import ensure_dependencies

    ensure_dependencies("delete_blank", ["pypdfium2", "PIL", "numpy"], "delete_blank")

    if not specs:
        specs = [""]

    total_pages = len(pdf.pages)
    pages_to_delete: set[int] = set()

    for spec_str in specs:
        spec = _parse_spec(spec_str, total_pages)
        pages_to_delete |= _find_blank_pages_for_spec(pdf, spec)

    for p_num in sorted(pages_to_delete, reverse=True):
        del pdf.pages[p_num - 1]

    logger.info("Deleted %d blank page(s) from %d total.", len(pages_to_delete), total_pages)
    return OpResult(success=True, pdf=pdf)
