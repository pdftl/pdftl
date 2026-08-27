# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/delete_images.py

"""Delete images by replacing them with 1x1 transparent stubs."""

import logging

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.keyval_parser import parse_keyval_string
from pdftl.utils.page_specs import page_numbers_matching_page_spec
from pdftl.utils.pikepdf_helpers import get_inheritable

logger = logging.getLogger(__name__)

_DELETE_IMAGES_LONG_DESC = """

The `delete_images` operation permanently overwrites images matching specific criteria
with a 1x1 transparent pixel. This safely crushes large graphical bloat (like full-page ads)
while maintaining strict PDF structural validity.

**Global vs. Page-Based:**
- **Global (Default):** If you omit the page selector or use `-`, the operation scans the
  entire internal PDF object table. It will aggressively find and destroy matching images
  no matter where they hide (including inside annotations, patterns, and background forms).
- **Page-Based:** If you provide a specific page range (e.g., `1-5`), it will only scan
  those specific pages and the forms located on them.
  *(Note: Because the underlying data is overwritten, if a deleted image on page 1 is also
  shared on page 10, it will visually disappear from page 10 as well).*

The syntax is `[selector](Key=Value, ...)`, where:
  - `selector` is an optional page range (`1-5`, `odd`, `-`). If omitted, acts globally.
  - `Key=Value` pairs define the filter criteria an image must meet to be deleted.

If you only have one such selector block, you can replace it with separate arguments
without parentheses or commas, as a convenient shorthand.

### Filter Parameters

| Parameter | Description | Example |
| :--- | :--- | :--- |
| `minbytes` | Min stream size in bytes. Supports `k`, `m`, `g` suffixes. | `100k`, `2m` |
| `maxbytes` | Max stream size in bytes. Supports `k`, `m`, `g` suffixes. | `10000`, `1.6m` |
| `minpixels` | Min dimensions (`WxH`) OR min total area (single number). | `20x400`, `100k` |
| `maxpixels` | Max dimensions (`WxH`) OR max total area (single number). | `1000x1000`, `10m` |
| `format` | Substring match against the image filter. | `dct` (JPEG), `flate` (PNG) |

*Note: If multiple parameters are provided, an image must meet ALL criteria to be replaced.*

"""

_DELETE_IMAGES_KEYS = ["minbytes", "maxbytes", "minpixels", "maxpixels", "format"]

_DELETE_IMAGES_EXAMPLES = [
    {
        "cmd": "in.pdf delete_images minbytes=500k output out.pdf",
        "desc": "Globally scan and destroy any image larger than 500KB anywhere in the document.",
    },
    {
        "cmd": "in.pdf delete_images 1-10even minbytes=100k minpixels=20x400 output out.pdf",
        "desc": (
            "On even pages from 1 to 10, destroy images > 100KB "
            "with a minimum dimension of 20x400."
        ),
    },
    {
        "cmd": "in.pdf delete_images '1-3(minbytes=100k)' 4-end output out.pdf",
        "desc": ("On pages 1 to 3, delete images > 100KB, and all images on remaining pages"),
    },
    {
        "cmd": "in.pdf delete_images format=dct output out.pdf",
        "desc": "Globally destroy all JPEG (DCTDecode) images.",
    },
]


def _parse_size_str(size_str: str) -> int:
    """Converts '100k', '1M' to an integer. Raises ValueError on bad input."""
    size_str = size_str.lower().strip()
    multipliers = {"k": 1024, "m": 1024**2, "g": 1024**3}
    for unit, mult in multipliers.items():
        if size_str.endswith(unit):
            return int(float(size_str[:-1]) * mult)
    return int(size_str)  # Will raise ValueError if size_str isn't a number


def _overwrite_with_stub(obj):
    """Mutates an existing Image stream into a 1x1 transparent mask in place."""
    from pikepdf import Array, Name

    # 1. Overwrite the stream data with a 1-byte mask (0xFF = transparent)
    obj.write(b"\xff")

    # 2. Rewrite the dictionary attributes
    obj.Width = 1
    obj.Height = 1
    obj.BitsPerComponent = 1
    obj.ImageMask = True
    obj.Decode = Array([0, 1])

    # 3. Strip out conflicting keys from the old image
    keys_to_delete = [
        Name.ColorSpace,
        Name.Filter,
        Name.SMask,
        Name.Mask,
        Name.Intent,
        Name.Interpolate,
    ]
    for key in keys_to_delete:
        if key in obj:
            del obj[key]


def _image_matches(obj, params) -> bool:
    """Evaluates if an image stream matches the provided parameters."""
    from pikepdf import Name

    if not params:
        return True

    if not (_bytes_limits_satisfied(params, obj) and _pixels_limits_satisfied(params, obj)):
        return False

    if "format" in params:
        f = str(obj.get(Name.Filter, "")).lower()
        if params["format"] not in f:
            return False

    return True


def _bytes_limits_satisfied(params, obj):
    minbytes = _parse_size_str(params["minbytes"]) if "minbytes" in params else None
    maxbytes = _parse_size_str(params["maxbytes"]) if "maxbytes" in params else None
    if minbytes is not None or maxbytes is not None:
        obj_len = len(obj.read_raw_bytes())
        if (minbytes is not None and obj_len < minbytes) or (
            maxbytes is not None and obj_len > maxbytes
        ):
            return False
    return True


def _pixels_limits_satisfied(params, obj):
    # Safely attempt to grab dimensions once for pixel checks
    w, h = None, None
    if "minpixels" in params or "maxpixels" in params:
        try:
            w = int(obj.Width)
            h = int(obj.Height)
        except (AttributeError, KeyError, ValueError):
            # The PDF dictionary is missing Width/Height, or they aren't numbers
            pass
    return all(
        _pixels_limit_satisfied(params, pname, w, h) for pname in ["minpixels", "maxpixels"]
    )


def _pixels_limit_satisfied(params, pname, w, h):
    if pname not in params:
        return True
    if w is None or h is None:
        return False
    pixels_limit_array = _parse_pixels_limit(params[pname])
    s = 1 if pname.startswith("min") else -1
    if len(pixels_limit_array) == 2:
        return s * w >= s * pixels_limit_array[0] and s * h >= s * pixels_limit_array[1]
    return s * w * h >= s * pixels_limit_array[0]


def _parse_pixels_limit(raw):
    val = raw.lower()
    if "x" in val:
        return list(map(int, val.split("x", 1)))
    return [_parse_size_str(val.replace("pixels", ""))]


def _process_resources(resources, params, modified_objects) -> None:
    """Recursively hunts for and mutates images within a Resources dictionary."""
    from pikepdf import Name

    if Name.XObject not in resources:
        return

    for _, obj in list(resources.XObject.items()):
        subtype = obj.get(Name.Subtype)

        # Form XObjects inherit from the enclosing stream scope, not via /Parent keys
        # so get_inheritable is not required here
        if subtype == Name.Form and Name.Resources in obj:
            _process_resources(obj.Resources, params, modified_objects)
        elif subtype == Name.Image:
            if obj.objgen not in modified_objects and _image_matches(obj, params):
                _overwrite_with_stub(obj)
                modified_objects.add(obj.objgen)


@register_operation(
    "delete_images",
    tags=["in_place", "images", "optimization", "delete"],
    type="single input operation",
    desc="Delete images",
    long_desc=_DELETE_IMAGES_LONG_DESC,
    usage="<input> delete_images [[spec](params)...] output <output>",
    examples=_DELETE_IMAGES_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS_EXPANDED], {}),
)
def delete_images(pdf, specs) -> OpResult:
    """
    Overwrites images matching criteria with 1x1 transparent stubs.
    Defaults to global search if no page range is provided.
    """
    import pikepdf

    logger.debug("specs=%s", specs)
    if not specs:
        specs = [""]  # Empty string will trigger global mode with no params

    # Track Object IDs to prevent double-processing and get an accurate count
    modified_objects: set[tuple] = set()

    for spec in specs:
        _apply_spec(pdf, spec, modified_objects, pikepdf)

    logger.info("Permanently overwritten %d unique images with 1x1 stubs.", len(modified_objects))
    return OpResult(success=True, pdf=pdf)


def _apply_spec(pdf, spec, modified_objects, pikepdf):
    selector = spec
    params_str = ""
    if "(" in spec and spec.endswith(")"):
        selector, params_str = spec[:-1].split("(", 1)
    params = _get_params(params_str)

    # ROUTING: Treat empty selector or "-" as Global Mode
    if not selector or selector == "-":
        _delete_images_globally(pdf, params, modified_objects, pikepdf)
    else:
        _delete_images_from_pages(selector, pdf, params, modified_objects, pikepdf)


def _delete_images_globally(pdf, params, modified_objects, pikepdf):
    _delete_images_from_pages("-", pdf, params, modified_objects, pikepdf)
    for obj in pdf.objects:
        if (
            isinstance(obj, pikepdf.Stream)
            and obj.get(pikepdf.Name.Type) == pikepdf.Name.XObject
            and obj.get(pikepdf.Name.Subtype) == pikepdf.Name.Image
            and obj.objgen not in modified_objects
            and _image_matches(obj, params)
        ):
            _overwrite_with_stub(obj)
            modified_objects.add(obj.objgen)


def _delete_images_from_pages(selector, pdf, params, modified_objects, pikepdf):
    # ROUTING: Specific page selector -> Page-Based Mode
    target_pages = page_numbers_matching_page_spec(selector, len(pdf.pages))
    for p_num in target_pages:
        page = pdf.pages[p_num - 1]
        resources = get_inheritable(page, "/Resources")
        if resources is not None:
            _process_resources(resources, params, modified_objects)


def _get_params(params_str):
    params = parse_keyval_string(
        params_str,
        allowed_keys=_DELETE_IMAGES_KEYS,
        lowercase_values=True,
        context="delete_images",
    )
    for k, v in params.items():
        _validate_param(k, v)
    return params


def _validate_param(k, v):
    # --- VALIDATION ---
    try:
        if k in ("minbytes", "maxbytes"):
            _parse_size_str(v)
        elif k in ("minpixels", "maxpixels"):
            if "x" in v:
                w, h = v.split("x", 1)
                int(w)
                int(h)
            else:
                _parse_size_str(v.replace("pixels", ""))
    except ValueError as exc:
        raise InvalidArgumentError(
            f"Invalid value '{v}' for parameter '{k}'. "
            "Expected a size (e.g., '100k') or dimensions (e.g., '20x400')."
        ) from exc
