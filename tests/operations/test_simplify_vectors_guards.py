# tests/operations/test_simplify_vectors_guards.py
#
# simplify_vectors never makes a content stream bigger, and skips streams
# over max_stream_size rather than exhausting memory on them.

import random

import pikepdf
import pytest

import pdftl.operations.simplify_vectors as sv
from pdftl.exceptions import InvalidArgumentError


def _polyline(n):
    # Dense, nearly straight polyline: simplification collapses it to a few points.
    pts = b" ".join(b"%.3f %.3f l" % (i * 0.5, (i % 3) * 0.001) for i in range(1, n))
    return b"0 0 m " + pts + b" S\n"


def _pdf(content):
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.pages[0].Contents = pdf.make_stream(content)
    return pdf


def _contents(pdf):
    return pdf.pages[0].Contents.read_bytes()


def test_simplification_that_helps_is_written():
    content = _polyline(2000)
    pdf = _pdf(content)
    sv.simplify_vectors_in_content_streams(pdf, [])
    assert len(_contents(pdf)) < len(content) // 10


def test_result_that_is_not_smaller_is_discarded(monkeypatch):
    content = _polyline(2000)
    real = sv.serialize

    noise = random.Random(0)

    def bloated(items):
        # Incompressible padding, so the result really is larger once Flate-compressed.
        return real(items) + [([noise.random() for _ in range(6)], "cm") for _ in range(2000)]

    monkeypatch.setattr(sv, "serialize", bloated)
    pdf = _pdf(content)
    sv.simplify_vectors_in_content_streams(pdf, [])
    assert _contents(pdf) == content


def test_stream_over_max_stream_size_is_left_untouched(caplog):
    content = _polyline(2000)
    pdf = _pdf(content)
    sv.simplify_vectors_in_content_streams(pdf, [f"(max_stream_size={len(content) - 1})"])
    assert _contents(pdf) == content
    assert "max_stream_size" in caplog.text


def test_max_stream_size_accepts_units():
    (_, config), *_ = sv._parse_args(["(max_stream_size=2MB)"])
    assert config.max_stream_size == 2 * 1024 * 1024


def test_max_stream_size_rejects_garbage():
    with pytest.raises(InvalidArgumentError):
        sv._parse_args(["(max_stream_size=lots)"])
