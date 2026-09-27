# tests/cli/test_pipeline_save_hints.py
#
# What a stage leaves for saving (shrink's recompress, prune_resources...)
# must reach the final save however many stages follow, and `usage` must
# measure the file an `output` would write.

import json
import os
import subprocess
import sys
import zlib
from pathlib import Path

import pikepdf
import pytest

import pdftl.core.constants as c
from pdftl.cli.pipeline import _carry_save_hints

SRC = str(Path(__file__).resolve().parents[2] / "src")
# Level 9 compresses this clearly better than level 1.
CONTENT = b"".join(
    b"%d %d m %d %d l S\n" % (i % 37, i * i % 101, i * 3 % 53, i % 11) for i in range(1500)
)


def _source(tmp_path) -> Path:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.pages[0].Contents = pdf.make_stream(
        zlib.compress(CONTENT, 1), Filter=pikepdf.Name.FlateDecode
    )
    path = tmp_path / "in.pdf"
    pdf.save(path, compress_streams=False)
    return path


def _pdftl(*args):
    env = dict(os.environ, PYTHONPATH=SRC)
    return subprocess.run(
        [sys.executable, "-m", "pdftl", *map(str, args)],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )


# --- carrying hints to a new document ---


class _Doc:
    pass


def test_hints_move_to_a_new_document():
    old, new = _Doc(), _Doc()
    setattr(old, c.PDFTL_SAVE_HINTS_ATTR, {"recompress": True, "drop_meta": True})
    setattr(new, c.PDFTL_SAVE_HINTS_ATTR, {"drop_meta": False})
    _carry_save_hints(old, new)
    assert getattr(new, c.PDFTL_SAVE_HINTS_ATTR) == {"recompress": True, "drop_meta": False}


@pytest.mark.parametrize("current", ["same", None, "generator"])
def test_hints_left_alone_when_there_is_nothing_new_to_carry_to(current):
    old = _Doc()
    setattr(old, c.PDFTL_SAVE_HINTS_ATTR, {"recompress": True})
    target = {"same": old, None: None, "generator": (x for x in [])}[current]
    _carry_save_hints(old, target)  # no error, nothing set on a generator
    assert getattr(old, c.PDFTL_SAVE_HINTS_ATTR) == {"recompress": True}


def test_no_hints_means_nothing_to_carry():
    new = _Doc()
    _carry_save_hints(_Doc(), new)
    assert not hasattr(new, c.PDFTL_SAVE_HINTS_ATTR)


def test_only_dict_hints_count():
    old, new = _Doc(), _Doc()
    setattr(old, c.PDFTL_SAVE_HINTS_ATTR, "not a dict")
    _carry_save_hints(old, new)
    assert not hasattr(new, c.PDFTL_SAVE_HINTS_ATTR)
    setattr(old, c.PDFTL_SAVE_HINTS_ATTR, {"recompress": True})
    setattr(new, c.PDFTL_SAVE_HINTS_ATTR, "junk")  # replaced, not merged
    _carry_save_hints(old, new)
    assert getattr(new, c.PDFTL_SAVE_HINTS_ATTR) == {"recompress": True}


# --- end to end ---


def test_recompress_survives_a_stage_that_builds_a_new_document(tmp_path):
    src = _source(tmp_path)
    out = tmp_path / "out.pdf"
    _pdftl(src, "shrink", "---", "cat", "output", out)
    with pikepdf.open(out) as pdf:
        # cat builds a new document; shrink's recompress must still apply
        raw = pdf.pages[0].Contents.read_raw_bytes()
        assert len(raw) <= len(zlib.compress(CONTENT, 9)) < len(zlib.compress(CONTENT, 1))


def test_usage_after_shrink_measures_the_saved_file(tmp_path):
    src = _source(tmp_path)
    out = tmp_path / "out.pdf"
    _pdftl(src, "shrink", "output", out)
    report = json.loads(_pdftl(src, "shrink", "---", "usage", "bytes", "json").stdout)
    total = report["usage"]["file_size"]
    assert abs(total - out.stat().st_size) <= 64  # only the file identifier may differ
