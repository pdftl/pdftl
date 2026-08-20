# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/images/filter_plugins.py

from __future__ import annotations
import functools
import inspect
from typing import Any
from collections.abc import Callable

from pdftl.core.core_types import ImageModifier
from pdftl.core.registry import registry
from pdftl.exceptions import InvalidArgumentError


# Dynamically anchor our custom container to the existing global registry
if not hasattr(registry, "image_modifiers"):
    registry.image_modifiers = {}


def _is_grayscale_palette(img: Any) -> bool:
    """Returns True if the palette image contains only shades of gray."""
    palette_data = img.getpalette()
    if not palette_data:
        return False

    # Grab the active RGB triplets up to the total number of colors used
    # (Pillow defaults to a 768-byte palette, padding with 0s)
    r = palette_data[0::3]
    g = palette_data[1::3]
    b = palette_data[2::3]

    return r == g == b


def register_image_modifier(
    name: str,
    title: str,
    desc: str,
    validator: Callable[[str], Any],
):
    """Decorator to register a parallelizable image modifier pipeline plugin."""

    def decorator(func: Callable[[Any, Any], Any]):
        long_desc = inspect.getdoc(func) or ""

        # --- DYNAMIC SIGNATURE REFLECTION ---
        sig = inspect.signature(func)
        params = list(sig.parameters.values())

        # Index 0 is 'img'. Index 1 is the filter argument variable.
        if len(params) >= 2:
            arg_name = params[1].name
            annotation = params[1].annotation

            # Safely stringify standard primitives or structural type hints like list[float]
            if hasattr(annotation, "__name__"):
                arg_type = annotation.__name__
            else:
                arg_type = str(annotation).replace("typing.", "")
        else:
            arg_name = "enabled"
            arg_type = "bool"

        # Add to our internal engine lookup map
        plugin = ImageModifier(
            name=name,
            title=title,
            desc=desc,
            long_desc=long_desc,
            validator=validator,
            function=func,
            arg_name=arg_name,
            arg_type=arg_type,
        )
        registry.image_modifiers[name] = plugin
        return func

    return decorator


def preserve_alpha(func: Callable) -> Callable:
    """
    Decorator to safely split and restore alpha channels for mathematical filters
    that would otherwise crash or strip transparency.
    """

    @functools.wraps(func)
    def wrapper(img: Any, *args: Any, **kwargs: Any) -> Any:
        if img.mode in ("RGBA", "LA", "RGBa", "La"):
            alpha = img.getchannel("A")
            base_mode = "RGB" if "RGB" in img.mode else "L"
            safe_img = img.convert(base_mode)

            processed = func(safe_img, *args, **kwargs)

            if processed.mode != base_mode:
                processed = processed.convert(base_mode)
            processed.putalpha(alpha)

            return processed

        return func(img, *args, **kwargs)

    return wrapper


def preserve_cmyk(func: Callable) -> Callable:
    """
    Decorator to safely translate CMYK images to RGB for calculation,
    then restore the image matrix back to CMYK space after filtering.
    """

    @functools.wraps(func)
    def wrapper(img: Any, *args: Any, **kwargs: Any) -> Any:
        if img.mode == "CMYK":
            rgb_conv = img.convert("RGB")
            processed_rgb = func(rgb_conv, *args, **kwargs)
            return processed_rgb.convert("CMYK")

        return func(img, *args, **kwargs)

    return wrapper


def preserve_indexed_palette(func: Callable) -> Callable:
    @functools.wraps(func)
    def wrapper(img: Any, *args: Any, **kwargs: Any) -> Any:
        if img.mode != "P":
            return func(img, *args, **kwargs)

        from PIL import Image

        raw_palette = img.getpalette() or []

        # 1. Ensure the palette is exactly 768 bytes (256 RGB tuples)
        if len(raw_palette) < 768:
            raw_palette.extend([0] * (768 - len(raw_palette)))
        else:
            raw_palette = raw_palette[:768]

        # 2. Convert the 1D palette list directly into a 256x1 RGB Image
        palette_bytes = bytes(raw_palette)
        palette_img = Image.frombytes("RGB", (256, 1), palette_bytes)

        # 3. Apply the heavy filter strictly to our tiny 256-pixel image
        processed_palette = func(palette_img, *args, **kwargs)

        if processed_palette.mode != "RGB":
            processed_palette = processed_palette.convert("RGB")

        # 4. Extract the processed bytes and inject them back into a copy of the original
        result = img.copy()
        result.putpalette(list(processed_palette.tobytes()))

        return result

    return wrapper


def preserve_indexed_palette_whole_image(func: Callable) -> Callable:
    """
    Use this for filters that rely on whole-image statistics (e.g., Contrast,
    Autocontrast, Equalize). It converts the image, applies the filter, and
    uses NumPy to rapidly map the original indices to their new colors.
    """

    @functools.wraps(func)
    def wrapper(img: Any, *args: Any, **kwargs: Any) -> Any:
        if img.mode != "P":
            return func(img, *args, **kwargs)

        import numpy as np

        # 1. Process the whole image so global statistics are calculated accurately
        full_rgb = img.convert("RGB")
        processed_rgb = func(full_rgb, *args, **kwargs)

        if processed_rgb.mode != "RGB":
            processed_rgb = processed_rgb.convert("RGB")

        # 2. Flatten image data into fast NumPy arrays
        p_arr = np.array(img).ravel()
        rgb_arr = np.array(processed_rgb).reshape(-1, 3)

        # 3. Find the flat index of the first occurrence of each unique palette index
        unique_indices, first_occurrences = np.unique(p_arr, return_index=True)

        # 4. Extract the exact new RGB colors mapped to those occurrences
        new_colors = rgb_arr[first_occurrences]

        # 5. Reconstruct the 768-byte (256 color) palette
        raw_palette = img.getpalette() or []
        if len(raw_palette) < 768:
            raw_palette.extend([0] * (768 - len(raw_palette)))
        else:
            raw_palette = raw_palette[:768]

        # 6. Inject the updated colors into the palette
        for idx, color in zip(unique_indices, new_colors):
            raw_palette[idx * 3 : idx * 3 + 3] = color.tolist()

        # 7. Apply the new palette to the unmodified indices
        result = img.copy()
        result.putpalette(raw_palette)
        return result

    return wrapper


# --- STANDARD INPUT VALIDATORS (STAY IN MAIN THREAD, NO HEAVY IMPORTS) ---


def _to_bool(val: str) -> bool:
    if val.lower() in ("true", "1", "yes"):
        return True
    if val.lower() in ("false", "0", "no"):
        return False
    raise InvalidArgumentError(f"Value '{val}' must be a boolean (expected true/false)")


def _to_float(val: str) -> float:
    try:
        return float(val)
    except ValueError:
        raise InvalidArgumentError(f"Value '{val}' must be a valid decimal number")


def _to_percent(val: str) -> int:
    try:
        p = int(val.strip().replace("%", ""))
        if 0 <= p <= 100:
            return p
        raise ValueError
    except ValueError:
        raise InvalidArgumentError(
            f"Value '{val}' must be an integer percentage between 0 and 100"
        )


def _to_levels(val: str) -> list[float]:
    try:
        parts = [float(p.strip()) for p in val.split(",")]
        if len(parts) < 2:
            raise ValueError
        if any(p < 0 or p > 100 for p in parts):
            raise InvalidArgumentError(
                f"Invalid levels '{val}'. All values must be percentages between 0 and 100"
            )
        return [p / 100.0 for p in parts]
    except ValueError:
        raise InvalidArgumentError(
            f"Levels '{val}' must be comma-separated percentages (e.g., 0,75,100)"
        )


def _to_bits(val: str) -> int:
    try:
        b = int(val)
        if 1 <= b <= 8:
            return b
        raise ValueError
    except ValueError:
        raise InvalidArgumentError(f"Posterize bits '{val}' must be an integer between 1 and 8")


def _to_adaptive_params(val: str) -> tuple[int, int]:
    try:
        parts = [p.strip() for p in val.split(",")]
        if len(parts) == 1:
            window_size = int(parts[0])
            C = 10  # Default noise floor constant
        elif len(parts) == 2:
            window_size = int(parts[0])
            C = int(parts[1])
        else:
            raise ValueError

        if window_size < 3 or window_size % 2 == 0:
            raise InvalidArgumentError(f"Window size '{window_size}' must be an odd integer >= 3")
        return (window_size, C)
    except ValueError:
        raise InvalidArgumentError(
            f"Adaptive threshold parameters '{val}' must be an odd integer or a pair like "
            "'window_size,C' (e.g., '15' or '15,12')"
        )


# --- PLUGIN IMPLEMENTATIONS ---


def convert_to_continuous(img, one_bit_passthrough=False):
    if img.mode == "1":
        if one_bit_passthrough:
            return img
        return img.convert("L")
    if img.mode == "P":
        return img.convert("L" if _is_grayscale_palette(img) else "RGB")
    if img.mode in ("YCbCr", "HSV"):
        return img.convert("RGB")
    return img


@register_image_modifier(
    "invert",
    "Invert Channels",
    "Creates a negative effect by flipping color channels.\n\n"
    "Argument `enabled` (boolean): "
    "Pass 'true' to apply the inversion or 'false' to leave the image unchanged.",
    _to_bool,
)
@preserve_alpha
@preserve_cmyk
@preserve_indexed_palette
def filter_invert(img: Any, enabled: bool) -> Any:
    if not enabled:
        return img
    from PIL import ImageChops

    if img.mode == "P":
        img = convert_to_continuous(img)
    return ImageChops.invert(img)


@register_image_modifier(
    "despeckle",
    "Despeckle Noise",
    "Applies a 3x3 median filter to eliminate salt-and-pepper noise.\n\n"
    "Argument `enabled` (boolean): "
    "Pass 'true' to apply the filter or 'false' to bypass.",
    _to_bool,
)
def filter_despeckle(img: Any, enabled: bool) -> Any:
    if not enabled:
        return img
    from PIL import ImageFilter

    was_one_bit = img.mode == "1"
    img = convert_to_continuous(img).filter(ImageFilter.MedianFilter(size=3))
    if was_one_bit:
        return img.convert("1")
    return img


@register_image_modifier(
    "autocontrast",
    "Auto Contrast",
    "Maximizes image contrast automatically by scaling image histogram bounds.\n\n"
    "Argument `enabled` (boolean): "
    "Pass 'true' to apply auto-contrast or 'false' to bypass.",
    _to_bool,
)
@preserve_alpha
@preserve_cmyk
@preserve_indexed_palette_whole_image
def filter_autocontrast(img: Any, enabled: bool) -> Any:
    if not enabled or img.mode == "1":
        return img
    from PIL import ImageOps

    return ImageOps.autocontrast(convert_to_continuous(img), cutoff=2)


@register_image_modifier(
    "brightness",
    "Brightness Adjustment",
    "Linearly scales overall image exposure levels.\n\n"
    "Argument `factor` (float): "
    "Multiplier where 1.0 is the original image, < 1.0 darkens the image, and > 1.0 brightens it.",
    _to_float,
)
@preserve_cmyk
@preserve_indexed_palette
def filter_brightness(img: Any, factor: float) -> Any:
    if img.mode == "1":
        return img
    from PIL import ImageEnhance

    return ImageEnhance.Brightness(convert_to_continuous(img)).enhance(factor)


@register_image_modifier(
    "contrast",
    "Contrast Adjustment",
    "Scales image contrast boundaries around a gray midpoint.\n\n"
    "Argument `factor` (float): "
    "Multiplier where 1.0 is original, < 1.0 reduces contrast (flatter), and > 1.0 increases "
    "contrast (punchier).",
    _to_float,
)
@preserve_cmyk
@preserve_indexed_palette_whole_image
def filter_contrast(img: Any, factor: float) -> Any:
    from PIL import ImageEnhance

    if img.mode == "1":
        return img

    return ImageEnhance.Contrast(convert_to_continuous(img)).enhance(factor)


@register_image_modifier(
    "saturation",
    "Saturation Adjustment",
    "Controls color intensity levels.\n\n"
    "Argument `factor` (float): "
    "Multiplier where 1.0 is "
    "original, > 1.0 increases vibrancy, and 0.0 results in a completely grayscale image.",
    _to_float,
)
@preserve_alpha
@preserve_cmyk
@preserve_indexed_palette
def filter_saturation(img: Any, factor: float) -> Any:
    if img.mode in ("1", "L"):
        return img
    from PIL import ImageEnhance

    return ImageEnhance.Color(convert_to_continuous(img)).enhance(factor)


@register_image_modifier(
    "hue",
    "Hue Rotation",
    "Rotates the color hue spectrum wheel.\n\n"
    "Argument `degrees` (float): "
    "The angle of "
    "rotation in degrees, ranging from -180.0 to 180.0.",
    _to_float,
)
@preserve_alpha
@preserve_indexed_palette
def filter_hue(img: Any, degrees: float) -> Any:
    if img.mode in ("1", "L"):
        return img
    import numpy as np
    from PIL import Image

    if img.mode != "RGB":
        img = img.convert("RGB")
    arr = np.array(img, dtype=np.float32)
    hsv = Image.fromarray(arr.astype(np.uint8)).convert("HSV")
    h, s, v = hsv.split()
    h_arr = np.array(h, dtype=np.int32)
    h_arr = (h_arr + int((degrees / 360.0) * 255)) % 256
    hsv = Image.merge("HSV", [Image.fromarray(h_arr.astype(np.uint8)), s, v])
    return hsv.convert("RGB")


@register_image_modifier(
    "lightness",
    "Lightness Blend",
    "Adjusts color luminance parameters via an HSL shift.\n\n"
    "Argument `factor` (float): "
    "Multiplier where 1.0 is original, < 1.0 darkens towards black, and > 1.0 lightens "
    "towards white.",
    _to_float,
)
@preserve_alpha
@preserve_cmyk
@preserve_indexed_palette
def filter_lightness(img: Any, factor: float) -> Any:
    img = convert_to_continuous(img)
    target_mode = "RGB"
    if img.mode == "L":
        target_mode = "L"

    import numpy as np
    from PIL import Image

    arr = np.array(img, dtype=np.float32)
    if factor > 1.0:
        arr = arr + (255.0 - arr) * (factor - 1.0)
    else:
        arr = arr * factor
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode=target_mode)


@register_image_modifier(
    "gamma",
    "Gamma Correction",
    "Applies power-law tone adjustment to modify midtones.\n\n"
    "Argument `exponent` (float): "
    "Gamma value where 1.0 is original, < 1.0 lightens midtones, and > 1.0 darkens midtones.",
    _to_float,
)
@preserve_alpha
@preserve_cmyk
@preserve_indexed_palette
def filter_gamma(img: Any, exponent: float) -> Any:
    import numpy as np

    img = convert_to_continuous(img)
    inv_gamma = 1.0 / exponent
    lut = np.array([((i / 255.0) ** inv_gamma) * 255 for i in range(256)]).astype(np.uint8)

    lut_list = lut.tolist() * len(img.getbands())
    return img.point(lut_list)


@register_image_modifier(
    "levels",
    "N-Point Levels Curve",
    "Maps image tones using an n-point piecewise linear percentage curve.\n\n"
    "Argument `points` "
    "(list of percentages): "
    "A comma-separated list of nodes to define the curve "
    "(e.g., '0,50,100').",
    _to_levels,
)
@preserve_alpha
@preserve_cmyk
@preserve_indexed_palette
def filter_levels(img: Any, points: list[float]) -> Any:
    import numpy as np

    img = convert_to_continuous(img)
    n = len(points)
    xp = np.linspace(0, 255, n)
    fp = np.array(points) * 255.0
    lut = np.interp(np.arange(256), xp, fp).astype(np.uint8)

    lut_list = lut.tolist() * len(img.getbands())
    return img.point(lut_list)


@register_image_modifier(
    "posterize",
    "Posterization",
    "Reduces discrete color variations to specific bit depth counts.\n\n"
    "Argument `bits` (integer): "
    "The number of bits to keep per color channel, ranging from 1 to 8.",
    _to_bits,
)
@preserve_cmyk
@preserve_alpha
@preserve_indexed_palette
def filter_posterize(img: Any, bits: int) -> Any:
    if img.mode == "1":
        return img

    img = convert_to_continuous(img)

    if bits == 8:
        return img

    # Map truncated bit values back to full 0..255 scale
    max_level = (1 << bits) - 1
    shift = 8 - bits
    lut = [round((i >> shift) * (255.0 / max_level)) for i in range(256)]

    # Repeat lookup table for each active channel (e.g. RGB or L)
    lut_list = lut * len(img.getbands())
    return img.point(lut_list)


@register_image_modifier(
    "solarize",
    "Solarize Highlights",
    "Inverts pixel intensities lying above a given cutoff percentage.\n\n"
    "Argument `threshold_pct` (integer): "
    "The threshold percentage from 0 to 100 where inversion begins.",
    _to_percent,
)
@preserve_alpha
@preserve_cmyk
@preserve_indexed_palette
def filter_solarize(img: Any, threshold_pct: int) -> Any:
    from PIL import ImageOps

    if img.mode == "1":
        # we alnost certainly don't want to solarize 1-bit images: they end up solid black
        return img
    cutoff = int((threshold_pct / 100.0) * 255)
    return ImageOps.solarize(convert_to_continuous(img), threshold=cutoff)


@register_image_modifier(
    "threshold",
    "Binarize Threshold",
    "Converts an image into strict 1-bit monochrome artwork.\n\n"
    "Argument `threshold_pct` (integer): "
    "The threshold percentage (0-100) where pixels above become white and "
    "below become black.",
    _to_percent,
)
@preserve_alpha
def filter_threshold(img: Any, threshold_pct: int) -> Any:
    if img.mode == "1":
        return img
    cutoff = int((threshold_pct / 100.0) * 255)
    gray = img.convert("L")
    binary = gray.point(lambda x: 255 if x > cutoff else 0).convert("1")
    return binary


@register_image_modifier(
    "sharpen",
    "Edge Sharpening",
    "Applies a standard 3x3 convolution kernel enhancement pass.\n\n"
    "Argument `enabled` (boolean): Pass 'true' to apply sharpening or 'false' to bypass.",
    _to_bool,
)
@preserve_cmyk
def filter_sharpen(img: Any, enabled: bool) -> Any:
    if not enabled or img.mode == "1":
        return img
    from PIL import ImageFilter

    return convert_to_continuous(img).filter(ImageFilter.SHARPEN)


@register_image_modifier(
    "blur",
    "Gaussian Blur",
    "Blurs details smoothly using a floating-point radius calculation.\n\n"
    "Argument `radius` (float): The blur radius in pixels (e.g., 2.0).",
    _to_float,
)
@preserve_cmyk
def filter_blur(img: Any, radius: float) -> Any:
    from PIL import ImageFilter

    img = convert_to_continuous(img)
    return img.filter(ImageFilter.GaussianBlur(radius=radius))


@register_image_modifier(
    "unsharp_mask",
    "Unsharp Masking",
    "Sharpens fine edge details via a local unsharp radius subtractor.\n\n"
    "Argument `radius` (float): The sharpening radius size in pixels (e.g., 2.0).",
    _to_float,
)
@preserve_cmyk
def filter_unsharp_mask(img: Any, radius: float) -> Any:
    from PIL import ImageFilter

    if img.mode == "1":
        return img
    return convert_to_continuous(img).filter(ImageFilter.UnsharpMask(radius=radius))


@register_image_modifier(
    "upscale",
    "Upscale / Resample",
    "Multiplies the image resolution using high-quality interpolation.\n\n"
    "Argument `factor` (float): "
    "Multiplier where 1.0 is original, > 1.0 increases resolution.",
    _to_float,
)
def filter_upscale(img: Any, factor: float) -> Any:
    if factor == 1.0:
        return img
    from PIL import Image

    new_width = int(img.width * factor)
    new_height = int(img.height * factor)

    # Use Lanczos for continuous images for smooth text edges.
    # If the image is already 1-bit, fallback to Nearest Neighbor to prevent crash/blur.
    resample_filter = Image.Resampling.NEAREST if img.mode == "1" else Image.Resampling.LANCZOS

    return img.resize((new_width, new_height), resample=resample_filter)


@register_image_modifier(
    "adaptive_threshold",
    "Adaptive Binarization",
    "Converts an image to 1-bit monochrome using a local neighborhood calculation.\n\n"
    "Argument `params` (tuple[int, int]): "
    "An odd window size or a comma-separated window size and noise-floor adjustment C "
    "(e.g., '15' or '15,12'). The default value of C is 10.",
    _to_adaptive_params,
)
@preserve_alpha
def filter_adaptive_threshold(img: Any, params: tuple[int, int]) -> Any:
    if img.mode == "1":
        return img

    import numpy as np
    from PIL import ImageFilter, Image

    window_size, C = params

    # Convert to grayscale for calculations
    gray = convert_to_continuous(img).convert("L")

    # 1. Calculate local mean using a fast box blur
    # A box blur radius is roughly half the window size
    radius = window_size // 2
    local_mean = gray.filter(ImageFilter.BoxBlur(radius))

    # 2. Convert to fast NumPy arrays for comparison
    # We use int16 to prevent overflow when subtracting the constant
    gray_arr = np.array(gray, dtype=np.int16)
    mean_arr = np.array(local_mean, dtype=np.int16)

    # 3. Apply the threshold:
    # If a pixel is darker than the local average minus a constant (C), it is text.
    # C prevents random paper grain in blank areas from turning into black speckles.
    binary_arr = np.where(gray_arr > (mean_arr - C), 255, 0).astype(np.uint8)

    # 4. Pack back into a strict 1-bit PIL Image
    return Image.fromarray(binary_arr).convert("1")
