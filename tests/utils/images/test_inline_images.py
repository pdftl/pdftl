# tests/utils/images/test_inline_images.py
import pikepdf
import pytest
from PIL import Image

from pdftl.utils.images.finders import extract_pdf_images
from pdftl.utils.images.inline_images import (
    apply_inline_replacements,
    encode_inline_replacement,
    mode_to_colorspace,
)


@pytest.fixture
def empty_pdf():
    with pikepdf.Pdf.new() as pdf:
        yield pdf


def _inline_bytes(width=2, height=2, cs="RGB", bpc=8):
    channels = {"G": 1, "RGB": 3, "CMYK": 4}[cs]
    data = bytes([128]) * (width * height * channels)
    header = f"BI /W {width} /H {height} /BPC {bpc} /CS /{cs} ID ".encode("ascii")
    return header + data + b" EI"


def test_apply_inline_replacements_stubs_the_image(empty_pdf):
    page = empty_pdf.add_blank_page()
    page.Resources = pikepdf.Dictionary()
    page.Contents = empty_pdf.make_stream(b"q " + _inline_bytes() + b" Q")

    refs = extract_pdf_images(empty_pdf, [1])
    assert len(refs) == 1

    changed = apply_inline_replacements(empty_pdf, refs)
    assert changed == 1

    # Re-extracting must now see a 1x1 stub, not the original 2x2 image.
    refs_after = extract_pdf_images(empty_pdf, [1])
    assert len(refs_after) == 1
    assert refs_after[0]["width_px"] == 1
    assert refs_after[0]["height_px"] == 1
    assert refs_after[0]["bits"] == 1


def test_apply_inline_replacements_multiple_in_same_stream(empty_pdf):
    page = empty_pdf.add_blank_page()
    page.Resources = pikepdf.Dictionary()
    page.Contents = empty_pdf.make_stream(_inline_bytes() + b" " + _inline_bytes(width=3))

    refs = extract_pdf_images(empty_pdf, [1])
    assert len(refs) == 2

    changed = apply_inline_replacements(empty_pdf, refs)
    assert changed == 2

    refs_after = extract_pdf_images(empty_pdf, [1])
    assert len(refs_after) == 2
    assert all(r["width_px"] == 1 for r in refs_after)


def test_apply_inline_replacements_skips_untagged_entries(empty_pdf, caplog):
    changed = apply_inline_replacements(empty_pdf, [{"inline": True, "name": None}])
    assert changed == 0
    assert "Skipping non-rewritable" in caplog.text


def test_apply_inline_replacements_stale_index_is_skipped(empty_pdf, caplog):
    page = empty_pdf.add_blank_page()
    page.Resources = pikepdf.Dictionary()
    content_stream = empty_pdf.make_stream(_inline_bytes())
    page.Contents = content_stream

    # Fabricate a ref pointing at an index that isn't an inline image.
    bad_ref = {
        "inline": True,
        "host_objgen": content_stream.objgen,
        "instruction_index": 999,
    }
    changed = apply_inline_replacements(empty_pdf, [bad_ref])
    assert changed == 0


def test_apply_inline_replacements_unresolvable_host_is_skipped(empty_pdf, caplog):
    bad_ref = {"inline": True, "host_objgen": (999, 0), "instruction_index": 0}
    changed = apply_inline_replacements(empty_pdf, [bad_ref])
    assert changed == 0
    assert "Could not resolve host stream" in caplog.text


def test_apply_inline_replacements_reparse_failure_is_skipped(empty_pdf, monkeypatch, caplog):
    page = empty_pdf.add_blank_page()
    page.Resources = pikepdf.Dictionary()
    content_stream = empty_pdf.make_stream(_inline_bytes())
    page.Contents = content_stream

    def raise_parse(*args, **kwargs):
        raise TypeError("simulated corruption")

    monkeypatch.setattr(pikepdf, "parse_content_stream", raise_parse)

    ref = {"inline": True, "host_objgen": content_stream.objgen, "instruction_index": 0}
    changed = apply_inline_replacements(empty_pdf, [ref])
    assert changed == 0
    assert "Could not re-parse host stream" in caplog.text


def test_parse_single_inline_instruction_raises_if_not_inline_image(empty_pdf, monkeypatch):
    """Covers the defensive RuntimeError branch in
    _parse_single_inline_instruction:
    if pikepdf's own parser ever came back with something other than a
    single INLINE IMAGE instruction for the given bytes, that must be
    surfaced loudly rather than silently returning the wrong instruction."""
    import pikepdf
    from pdftl.utils.images.inline_images import _parse_single_inline_instruction

    def fake_parse_content_stream(stream_obj):
        return []

    monkeypatch.setattr(pikepdf, "parse_content_stream", fake_parse_content_stream)

    with pytest.raises(RuntimeError, match="Failed to construct inline image instruction"):
        _parse_single_inline_instruction(empty_pdf, b"BI ... EI")


def test_apply_inline_replacements_get_object_raises_is_skipped(empty_pdf, monkeypatch, caplog):
    """Covers the except branch around pdf.get_object itself (as opposed
    to the 'resolved but not a Stream' branch already covered by the
    unresolvable-host test) -- some pikepdf versions/backends may raise
    directly rather than returning a non-Stream placeholder."""
    from pdftl.utils.images.inline_images import apply_inline_replacements

    page = empty_pdf.add_blank_page()
    page.Resources = pikepdf.Dictionary()
    content_stream = empty_pdf.make_stream(_inline_bytes())
    page.Contents = content_stream

    def fake_get_object(objgen):
        raise KeyError("simulated missing object")

    monkeypatch.setattr(empty_pdf, "get_object", fake_get_object)

    ref = {"inline": True, "host_objgen": content_stream.objgen, "instruction_index": 0}
    changed = apply_inline_replacements(empty_pdf, [ref])
    assert changed == 0
    assert "Could not resolve host stream" in caplog.text


def test_apply_inline_replacements_stale_index_logs_full_warning(empty_pdf, caplog):
    """Covers the full multi-line logger.warning call in the 'no longer
    an inline image' branch (lines 146-152) end-to-end, distinct from the
    generic stale-index test -- pins operand/operator values reach the
    log formatting without error."""
    import logging
    from pdftl.utils.images.inline_images import apply_inline_replacements

    page = empty_pdf.add_blank_page()
    page.Resources = pikepdf.Dictionary()
    content_stream = empty_pdf.make_stream(b"q " + _inline_bytes() + b" Q")
    page.Contents = content_stream

    # Instruction index 0 is "q", not an inline image.
    ref = {"inline": True, "host_objgen": content_stream.objgen, "instruction_index": 0}
    with caplog.at_level(logging.WARNING):
        changed = apply_inline_replacements(empty_pdf, [ref])
    assert changed == 0
    assert "is no longer an inline image" in caplog.text


class TestEncodeInlineReplacement:
    def test_defaults_colorspace_and_bits_from_mode(self):
        img = Image.new("RGB", (4, 4), color="blue")
        raw = encode_inline_replacement(img, "flatedecode", quality=75)
        assert b"/ColorSpace /DeviceRGB" in raw
        assert b"/BitsPerComponent 8" in raw
        assert b"/Filter /FlateDecode" in raw

    def test_bitonal_never_uses_jpeg(self):
        img = Image.new("1", (4, 4))
        raw = encode_inline_replacement(img, "dctdecode", quality=75)
        assert b"/Filter /FlateDecode" in raw
        assert b"/BitsPerComponent 1" in raw

    def test_dctdecode_reencodes_as_jpeg_rgb(self):
        img = Image.new("RGB", (4, 4), color="red")
        raw = encode_inline_replacement(img, "dctdecode", quality=80)
        assert b"/Filter /DCTDecode" in raw
        assert b"/ColorSpace /DeviceRGB" in raw

    def test_explicit_overrides_win_over_mode_defaults(self):
        img = Image.new("L", (4, 4))
        raw = encode_inline_replacement(
            img, "flatedecode", quality=75, cs_name="DeviceGray", bits=8
        )
        assert b"/ColorSpace /DeviceGray" in raw

    def test_mode_to_colorspace_fallback(self):
        assert mode_to_colorspace("CMYK") == "DeviceCMYK"
        assert mode_to_colorspace("P") == "DeviceGray"  # unrecognized mode fallback


def test_build_inline_image_bytes_omits_filter_when_falsy():
    """Covers the `if filter_name:` false branch (104->107): passing an
    empty/falsy filter_name must omit the /Filter entry entirely rather
    than emitting a malformed '/Filter /' token."""
    from pdftl.utils.images.inline_images import build_inline_image_bytes

    raw = build_inline_image_bytes(
        width=2,
        height=2,
        bits=8,
        colorspace_name="DeviceGray",
        filter_name="",
        data=b"\x00\x00\x00\x00",
    )
    assert b"/Filter" not in raw
    assert b"/ColorSpace /DeviceGray" in raw


def test_decode_inline_pil_returns_none_for_non_inline_instruction(empty_pdf):
    """Covers decode_inline_pil's `if str(inst.operator) != "INLINE IMAGE": return None`
    branch -- a ref whose instruction_index points at a non-inline-image
    instruction must degrade to None rather than raising or misreading."""
    from pdftl.utils.images.inline_images import decode_inline_pil

    page = empty_pdf.add_blank_page()
    page.Resources = pikepdf.Dictionary()
    content_stream = empty_pdf.make_stream(b"q " + _inline_bytes() + b" Q")
    page.Contents = content_stream

    # Instruction index 0 is "q", not an inline image.
    ref = {"host_objgen": content_stream.objgen, "instruction_index": 0}
    assert decode_inline_pil(empty_pdf, ref) is None


def test_rewrite_host_stream_raw_bytes_fn_raises_is_skipped(empty_pdf, caplog):
    """Covers the except branch around raw_bytes_fn(ref) in
    _rewrite_host_stream (lines 260-266): a callback that raises must be
    caught and logged, leaving that instruction untouched rather than
    propagating and aborting the whole rewrite pass."""
    from pdftl.utils.images.inline_images import apply_inline_rewrites

    page = empty_pdf.add_blank_page()
    page.Resources = pikepdf.Dictionary()
    content_stream = empty_pdf.make_stream(_inline_bytes())
    page.Contents = content_stream

    ref = {"inline": True, "host_objgen": content_stream.objgen, "instruction_index": 0}

    def raising_fn(_ref):
        raise ValueError("simulated encode failure")

    changed = apply_inline_rewrites(empty_pdf, [ref], raising_fn)
    assert changed == 0
    assert "Failed to build replacement for inline image" in caplog.text


def test_rewrite_host_stream_raw_bytes_fn_returns_none_leaves_untouched(empty_pdf):
    """Covers the `if raw is None: continue` branch (line 268): a
    callback that legitimately declines to replace a given ref (as
    opposed to raising) must also leave that instruction untouched."""
    from pdftl.utils.images.inline_images import apply_inline_rewrites

    page = empty_pdf.add_blank_page()
    page.Resources = pikepdf.Dictionary()
    content_stream = empty_pdf.make_stream(_inline_bytes())
    page.Contents = content_stream

    ref = {"inline": True, "host_objgen": content_stream.objgen, "instruction_index": 0}

    changed = apply_inline_rewrites(empty_pdf, [ref], lambda _ref: None)
    assert changed == 0


def test_decode_inline_pil_success(empty_pdf, monkeypatch):
    """Covers the successful decode path: a real INLINE IMAGE instruction
    whose operand's as_pil_image() succeeds must be returned as-is."""
    from pdftl.utils.images.inline_images import decode_inline_pil

    page = empty_pdf.add_blank_page()
    page.Resources = pikepdf.Dictionary()
    content_stream = empty_pdf.make_stream(_inline_bytes())
    page.Contents = content_stream

    sentinel = object()

    class FakeInlineImage:
        def as_pil_image(self):
            return sentinel

    class FakeInstruction:
        operator = "INLINE IMAGE"
        operands = [FakeInlineImage()]

    monkeypatch.setattr(pikepdf, "parse_content_stream", lambda stream_obj: [FakeInstruction()])

    ref = {"host_objgen": content_stream.objgen, "instruction_index": 0}
    assert decode_inline_pil(empty_pdf, ref) is sentinel


def test_decode_inline_pil_missing_ref_keys_returns_none(empty_pdf):
    """Covers the except branch itself: a ref missing host_objgen/
    instruction_index raises KeyError inside the try block, which must be
    caught and degrade to None rather than propagating."""
    from pdftl.utils.images.inline_images import decode_inline_pil

    assert decode_inline_pil(empty_pdf, {}) is None


def test_decode_inline_pil_as_pil_image_raises_is_caught(empty_pdf, monkeypatch):
    """Covers the except branch when the operand resolves fine but its
    own as_pil_image() call raises -- must degrade to None, not propagate."""
    from pdftl.utils.images.inline_images import decode_inline_pil

    page = empty_pdf.add_blank_page()
    page.Resources = pikepdf.Dictionary()
    content_stream = empty_pdf.make_stream(_inline_bytes())
    page.Contents = content_stream

    class FakeInlineImage:
        def as_pil_image(self):
            raise ValueError("simulated decode failure")

    class FakeInstruction:
        operator = "INLINE IMAGE"
        operands = [FakeInlineImage()]

    monkeypatch.setattr(pikepdf, "parse_content_stream", lambda stream_obj: [FakeInstruction()])

    ref = {"host_objgen": content_stream.objgen, "instruction_index": 0}
    assert decode_inline_pil(empty_pdf, ref) is None
