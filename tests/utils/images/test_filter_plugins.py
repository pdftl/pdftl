import pytest
from PIL import Image

from pdftl.exceptions import InvalidArgumentError
from pdftl.core.registry import registry
from pdftl.utils.images.filter_plugins import (
    register_image_modifier,
    _to_bool,
    _to_float,
    _to_percent,
    _to_levels,
    _to_bits,
    convert_to_continuous,
    preserve_alpha,
    filter_invert,
    filter_despeckle,
    filter_autocontrast,
    filter_brightness,
    filter_contrast,
    filter_saturation,
    filter_hue,
    filter_lightness,
    filter_gamma,
    filter_levels,
    filter_posterize,
    filter_solarize,
    filter_threshold,
    filter_sharpen,
    filter_blur,
    filter_unsharp_mask,
)

# --- 1. VALIDATOR TESTS ---


def test_to_bool():
    assert _to_bool("true") is True
    assert _to_bool("1") is True
    assert _to_bool("YES") is True
    assert _to_bool("false") is False
    assert _to_bool("0") is False
    assert _to_bool("No") is False

    with pytest.raises(InvalidArgumentError, match="must be a boolean"):
        _to_bool("invalid")


def test_to_float():
    assert _to_float("1.5") == 1.5
    assert _to_float("-0.25") == -0.25

    with pytest.raises(InvalidArgumentError, match="must be a valid decimal"):
        _to_float("abc")


def test_to_percent():
    assert _to_percent("50%") == 50
    assert _to_percent("  100  ") == 100
    assert _to_percent("0") == 0

    with pytest.raises(InvalidArgumentError, match="between 0 and 100"):
        _to_percent("101")
    with pytest.raises(InvalidArgumentError, match="between 0 and 100"):
        _to_percent("-1")
    with pytest.raises(InvalidArgumentError, match="between 0 and 100"):
        _to_percent("abc")


def test_to_levels():
    assert _to_levels("0, 50, 100") == [0.0, 0.5, 1.0]

    # Missing comma (less than 2 parts)
    with pytest.raises(InvalidArgumentError, match="comma-separated percentages"):
        _to_levels("50")

    # Out of bounds
    with pytest.raises(InvalidArgumentError, match="between 0 and 100"):
        _to_levels("-10, 50")
    with pytest.raises(InvalidArgumentError, match="between 0 and 100"):
        _to_levels("0, 150")

    # Not numbers
    with pytest.raises(InvalidArgumentError, match="comma-separated percentages"):
        _to_levels("a, b")


def test_to_bits():
    assert _to_bits("1") == 1
    assert _to_bits("8") == 8

    with pytest.raises(InvalidArgumentError, match="between 1 and 8"):
        _to_bits("0")
    with pytest.raises(InvalidArgumentError, match="between 1 and 8"):
        _to_bits("9")
    with pytest.raises(InvalidArgumentError, match="between 1 and 8"):
        _to_bits("abc")


# --- 2. REGISTRY & DECORATOR TESTS ---


def test_registry_population():
    # Verify a standard filter was registered correctly
    assert "invert" in registry.image_modifiers
    plugin = registry.image_modifiers["invert"]
    assert plugin.arg_name == "enabled"
    assert plugin.arg_type == "bool"


def test_registry_fallback_signature():
    # Create a dummy filter with only 1 argument to hit the `len(params) < 2` branch
    @register_image_modifier("dummy_no_arg", "Dummy", "Desc", _to_bool)
    def dummy_filter(img):
        return img

    plugin = registry.image_modifiers["dummy_no_arg"]
    assert plugin.arg_name == "enabled"
    assert plugin.arg_type == "bool"


# --- 3. FILTER EXECUTION TESTS ---


@pytest.fixture
def rgb_img():
    # A simple 10x10 red image
    return Image.new("RGB", (10, 10), color="red")


@pytest.fixture
def rgba_img():
    # A simple 10x10 transparent image (used to trigger mode conversions)
    return Image.new("RGBA", (10, 10), color=(255, 0, 0, 128))


def test_boolean_filters_disabled(rgb_img):
    # Verify that enabled=False returns the exact same object
    assert filter_invert(rgb_img, False) is rgb_img
    assert filter_despeckle(rgb_img, False) is rgb_img
    assert filter_autocontrast(rgb_img, False) is rgb_img
    assert filter_sharpen(rgb_img, False) is rgb_img


def test_filter_invert(rgb_img, rgba_img):
    # Standard RGB
    res1 = filter_invert(rgb_img, True)
    assert res1.mode == "RGB"

    # RGBA is preserved
    res2 = filter_invert(rgba_img, True)
    assert res2.mode == "RGBA"


def test_filter_despeckle(rgb_img):
    res = filter_despeckle(rgb_img, True)
    assert isinstance(res, Image.Image)


def test_filter_autocontrast(rgb_img):
    res = filter_autocontrast(rgb_img, True)
    assert isinstance(res, Image.Image)


def test_filter_enhancers(rgb_img):
    # Testing Brightness, Contrast, Saturation
    assert isinstance(filter_brightness(rgb_img, 1.5), Image.Image)
    assert isinstance(filter_contrast(rgb_img, 1.5), Image.Image)
    assert isinstance(filter_saturation(rgb_img, 0.5), Image.Image)


def test_filter_hue(rgb_img, rgba_img):
    # Standard RGB
    res1 = filter_hue(rgb_img, 90.0)
    assert res1.mode == "RGB"

    res2 = filter_hue(rgba_img, 180.0)
    assert res2.mode == "RGBA"


def test_filter_lightness(rgb_img, rgba_img):
    # Factor > 1.0 (Lighten)
    res1 = filter_lightness(rgb_img, 1.5)
    assert res1.mode == "RGB"

    # Factor <= 1.0 (Darken)
    res2 = filter_lightness(rgb_img, 0.5)
    assert res2.mode == "RGB"

    # Non-RGB triggers conversion
    res3 = filter_lightness(rgba_img, 1.2)
    assert res3.mode == "RGBA"


def test_filter_gamma(rgb_img):
    # Standard mode
    res1 = filter_gamma(rgb_img, 1.2)
    assert isinstance(res1, Image.Image)

    # Mode "1" or "P" triggers conversion to "L"
    bw_img = Image.new("1", (10, 10), color=1)
    res2 = filter_gamma(bw_img, 0.8)
    assert res2.mode == "L"


def test_filter_levels(rgb_img):
    # Standard mode
    res1 = filter_levels(rgb_img, [0.0, 50, 100.0])
    assert isinstance(res1, Image.Image)

    # Mode "P" triggers conversion to "RGB"
    p_img = Image.new("P", (10, 10), color=1)
    res2 = filter_levels(p_img, [10, 90])
    assert res2.mode == "P"


def test_filter_posterize(rgb_img, rgba_img):
    res1 = filter_posterize(rgb_img, 4)
    assert isinstance(res1, Image.Image)

    res2 = filter_posterize(rgba_img, 2)
    assert res2.mode == "RGBA"


def test_filter_solarize(rgb_img):
    res = filter_solarize(rgb_img, 50)
    assert isinstance(res, Image.Image)


def test_filter_threshold(rgb_img):
    res = filter_threshold(rgb_img, 50)
    # Threshold strictly forces "1" (1-bit monochrome)
    assert res.mode == "1"


def test_filter_sharpen(rgb_img):
    res = filter_sharpen(rgb_img, True)
    assert isinstance(res, Image.Image)


def test_filter_blur(rgb_img):
    res = filter_blur(rgb_img, 2.0)
    assert isinstance(res, Image.Image)


def test_filter_unsharp_mask(rgb_img):
    res = filter_unsharp_mask(rgb_img, 2.0)
    assert isinstance(res, Image.Image)


def test_registry_primitive_annotation():
    # To hit line 47, we need an annotation that is a real Python type object,
    # not a stringified annotation (which happens due to future imports).
    def dummy_primitive(img, val):
        return img

    # Manually inject the actual `int` class into the annotations dict
    dummy_primitive.__annotations__ = {"val": int}

    # Register the dummy function
    register_image_modifier("dummy_primitive", "Dummy Primitive", "Desc", lambda x: int(x))(
        dummy_primitive
    )

    # Assert it was parsed correctly via line 47
    plugin = registry.image_modifiers["dummy_primitive"]
    assert plugin.arg_name == "val"
    assert plugin.arg_type == "int"


# Add this import at the top of your test file if not present

# --- 4. COVERAGE GAP FIXES ---


def test_is_grayscale_palette_coverage():
    """Covers lines 25-29 by feeding gray and non-gray palette structures."""
    # 1. Grayscale palette case (R == G == B)
    gray_p = Image.new("P", (10, 10))
    # Create a simple grayscale palette: 256 triplets of [0,0,0], [1,1,1]...
    gray_palette = []
    for i in range(256):
        gray_palette.extend([i, i, i])
    gray_p.putpalette(gray_palette)

    # Trigger convert_to_continuous -> _is_grayscale_palette
    res_gray = convert_to_continuous(gray_p)
    assert res_gray.mode == "L"

    # 2. Color palette case (R != G != B)
    color_p = Image.new("P", (10, 10))
    color_palette = []
    for i in range(256):
        color_palette.extend([i, 0, 255 - i])
    color_p.putpalette(color_palette)

    res_color = convert_to_continuous(color_p)
    assert res_color.mode == "RGB"


def test_preserve_alpha_mode_mismatch_recovery():
    """Covers line 100 where a filter unexpectedly changes base_mode."""
    rgba_img = Image.new("RGBA", (10, 10), color=(255, 0, 0, 128))

    # We decorate a dummy function that forcefully drops RGB down to L mid-flight
    @preserve_alpha
    def rogue_filter(img):
        return img.convert("L")

    res = rogue_filter(rgba_img)
    # The decorator should have caught the mismatch, upscaled it back, and merged alpha
    assert res.mode == "RGBA"


def test_one_bit_and_grayscale_filter_passthroughs():
    """Covers lines 175, 200, 220, 251, 270, 286, 304, 405, 437, 484."""
    img_1bit = Image.new("1", (10, 10), color=1)
    img_gray = Image.new("L", (10, 10), color=128)

    # Line 175: convert_to_continuous with passthrough enabled
    assert convert_to_continuous(img_1bit, one_bit_passthrough=True) is img_1bit

    # Line 200: filter_invert custom 1-bit logic
    assert filter_invert(img_1bit, True).mode == "1"

    # Line 220: filter_despeckle 1-bit reconversion boundary
    assert filter_despeckle(img_1bit, True).mode == "1"

    # Line 251 & 270: brightness/contrast early-exit for 1-bit
    assert filter_brightness(img_1bit, 1.5) is img_1bit
    assert filter_contrast(img_1bit, 1.5) is img_1bit

    # Line 286 & 304: saturation/hue early-exit for grayscale/1-bit
    assert filter_saturation(img_gray, 0.5) is img_gray
    assert filter_saturation(img_1bit, 0.5) is img_1bit
    assert filter_hue(img_gray, 90.0) is img_gray
    assert filter_hue(img_1bit, 90.0) is img_1bit

    # Line 405, 437, 484: posterize, threshold, unsharp early exits
    assert filter_posterize(img_1bit, 4) is img_1bit
    assert filter_threshold(img_1bit, 50) is img_1bit
    assert filter_unsharp_mask(img_1bit, 2.0) is img_1bit


def test_exotic_continuous_modes_and_lightness():
    """Covers lines 181, 309, and 333."""
    # Line 181: convert_to_continuous handling exotic print layout modes
    cmyk_img = Image.new("CMYK", (10, 10))
    assert convert_to_continuous(cmyk_img).mode == "CMYK"

    # Line 309: filter_hue non-RGB color conversion route (using Palette image input)
    color_p = Image.new("P", (10, 10))
    color_p.putpalette([i for i in range(256)] * 3)
    assert filter_hue(color_p, 45.0).mode == "P"

    # Line 333: filter_lightness targeting a clean grayscale line math array
    img_gray = Image.new("L", (10, 10), color=128)
    assert filter_lightness(img_gray, 1.2).mode == "L"


def test_is_grayscale_palette_empty():
    """Covers line 27: validates behavior when palette data is missing."""
    from pdftl.utils.images.filter_plugins import _is_grayscale_palette

    img = Image.new("P", (10, 10))
    img.palette = None  # Explicitly strip the palette object

    assert _is_grayscale_palette(img) is False


def test_preserve_indexed_palette_non_rgb_return():
    """
    Covers lines 139 and 171: ensuring the decorator can recover if the
    underlying mathematical filter outputs a non-RGB mode (like "L").
    """
    from pdftl.utils.images.filter_plugins import preserve_indexed_palette

    @preserve_indexed_palette
    def rogue_filter(img):
        # Force conversion down to L mode to trigger the recovery branches
        return img.convert("L")

    # Inject a small palette so the swatch loop executes for unseen colors
    p_img = Image.new("P", (10, 10))
    p_img.putpalette([255, 0, 0, 0, 255, 0] + [0] * 762)

    res = rogue_filter(p_img)
    assert res.mode == "P"


def test_convert_to_continuous_exotic_modes():
    """Covers line 249: properly routing YCbCr and HSV conversions."""
    from pdftl.utils.images.filter_plugins import convert_to_continuous

    hsv_img = Image.new("HSV", (10, 10))
    ycbcr_img = Image.new("YCbCr", (10, 10))

    assert convert_to_continuous(hsv_img).mode == "RGB"
    assert convert_to_continuous(ycbcr_img).mode == "RGB"


def test_filter_invert_p_mode_fallback():
    """
    Covers line 270: Since filter_invert is decorated by @preserve_indexed_palette,
    P-mode images are intercepted before reaching the core function. We must unwrap
    the decorators to hit the inner safety fallback logic.
    """
    p_img = Image.new("P", (10, 10))
    p_img.putpalette([255, 255, 255] * 256)

    # Safely peel back all decorators (@preserve_alpha, @preserve_cmyk, etc.)
    raw_invert = filter_invert
    while hasattr(raw_invert, "__wrapped__"):
        raw_invert = raw_invert.__wrapped__

    res = raw_invert(p_img, enabled=True)

    # It should have dynamically converted to continuous
    assert res.mode in ("L", "RGB")


def test_preserve_cmyk_decorator_coverage():
    """
    Covers lines 119-121: Ensures the @preserve_cmyk decorator correctly
    translates CMYK to RGB and back.
    """
    from pdftl.utils.images.filter_plugins import filter_brightness

    cmyk_img = Image.new("CMYK", (10, 10), color=(100, 50, 0, 0))
    res = filter_brightness(cmyk_img, 1.2)

    # Assert it made the round trip back to CMYK safely
    assert res.mode == "CMYK"


def test_filter_hue_inner_conversion_fallback():
    """
    Covers line 389: filter_hue has a fallback `if img.mode != "RGB"` inside the function.
    Because decorators intercept "RGBA" and "P", we must use a mode like "CMYK"
    that bypasses the decorators to hit the inner fallback.
    """
    from pdftl.utils.images.filter_plugins import filter_hue

    cmyk_img = Image.new("CMYK", (10, 10), color=(100, 50, 0, 0))
    res = filter_hue(cmyk_img, 180.0)

    # filter_hue converts and returns as RGB
    assert res.mode == "RGB"


def test_filter_solarize_1_bit_passthrough():
    """Covers line 511: Explicit 1-bit bypass logic in solarize."""
    from pdftl.utils.images.filter_plugins import filter_solarize

    img_1bit = Image.new("1", (10, 10), color=1)
    res = filter_solarize(img_1bit, 50)

    # Should return the exact original object
    assert res is img_1bit


def test_preserve_indexed_palette_early_break():
    """
    Covers line 158: The optimization break statement triggers when the palette
    mapper detects it has updated all `num_colors` present in the palette,
    aborting the loop before scanning the rest of the image pixels.
    """
    from pdftl.utils.images.filter_plugins import filter_invert

    # Create a 20x20 image (400 pixels total, which is > 256 max colors)
    p_img = Image.new("P", (20, 20))

    # Explicitly attach a 768-byte palette so `num_colors` evaluates to 256
    p_img.putpalette([128, 128, 128] * 256)

    # Inject all 256 distinct indices right at the start of the image,
    # followed by 144 padded zeros.
    pixel_data = list(range(256)) + [0] * 144
    p_img.putdata(pixel_data)

    # By pixel 256, all colors are seen, `remaining` hits 0, and the loop
    # explicitly BREAKS before it scans the final 144 pixels.
    res = filter_invert(p_img, enabled=True)

    assert res.mode == "P"


from PIL import ImageOps
from pdftl.utils.images.filter_plugins import preserve_indexed_palette_whole_image

# --- Dummy Filters for Decorator Testing ---


@preserve_indexed_palette_whole_image
def _dummy_invert_filter(img):
    """A simple RGB filter to test the happy path."""
    return ImageOps.invert(img)


@preserve_indexed_palette_whole_image
def _dummy_grayscale_filter(img):
    """Returns 'L' mode to trigger the non-RGB fallback at lines 185-186."""
    return img.convert("L")


# --- Coverage Tests ---


def test_whole_image_decorator_bypass_non_p():
    """
    Covers lines 176-177: Ensures non-'P' modes bypass the NumPy processing
    and are filtered normally.
    """
    img = Image.new("RGB", (10, 10), color=(255, 0, 0))  # Red
    res = _dummy_invert_filter(img)

    assert res.mode == "RGB"
    assert res.getpixel((0, 0)) == (0, 255, 255)  # Cyan (Inverted Red)


def test_whole_image_decorator_standard_palette():
    """
    Covers lines 178-195, 198-207: Tests the standard execution path with
    a fully populated 768-byte palette (hits the `else` block at line 198).
    """
    img = Image.new("P", (10, 10))

    # Create a palette where index 0 is Black, index 1 is White, padded to 768 bytes
    palette = [0, 0, 0, 255, 255, 255] + [0] * 762
    img.putpalette(palette)

    # Fill image: first half index 0, second half index 1
    img.putdata([0] * 50 + [1] * 50)

    res = _dummy_invert_filter(img)

    assert res.mode == "P"
    new_pal = res.getpalette()

    # Index 0 should now be White (255, 255, 255)
    assert new_pal[0:3] == [255, 255, 255]
    # Index 1 should now be Black (0, 0, 0)
    assert new_pal[3:6] == [0, 0, 0]


def test_whole_image_decorator_missing_palette():
    """
    Covers lines 196-197: If a 'P' image is created without an explicit palette,
    `getpalette()` returns None. This tests the empty array fallback and padding loop.
    """
    img = Image.new("P", (10, 10))
    img.putdata([0] * 100)  # Give it pixel data but no palette

    res = _dummy_invert_filter(img)

    assert res.mode == "P"
    assert len(res.getpalette()) == 768


def test_whole_image_decorator_non_rgb_fallback():
    """
    Covers lines 185-186: If the wrapped function returns something other
    than 'RGB' (like 'L'), the decorator must catch it and convert it to 'RGB'
    so the NumPy array reshaping (..., 3) doesn't crash.
    """
    img = Image.new("P", (10, 10))
    img.putpalette([128, 128, 128] * 256)
    img.putdata([0] * 100)

    res = _dummy_grayscale_filter(img)

    # The decorator handles the inner "L" mode return and safely maps it
    # back into the new palette, keeping the final output as "P"
    assert res.mode == "P"
