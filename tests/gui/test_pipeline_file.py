# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/gui/test_pipeline_file.py

"""Tests for pipeline_file: Pipeline <-> a hand-editable `--args` YAML file.

The ground-truth test never compares two computations that share the same
formula: it runs the saved file through the real, separate `pdftl` CLI
process (from a different working directory, to prove paths were made
absolute) and checks the resulting PDF against the GUI engine's own
`save()`, which is a second, independent execution path.
"""

import os
import shlex
import subprocess
import sys
import threading
from pathlib import Path

import pikepdf
import pytest
import yaml

from pdftl.gui import stage_model
from pdftl.gui.command_bar import parse_command
from pdftl.gui.engine import SubprocessEngine
from pdftl.gui.interfaces import InputFile, Pipeline, Stage
from pdftl.gui.pipeline_file import PipelineFileError, load, save

SRC = str(Path(__file__).resolve().parents[2] / "src")


def _make_pdf(path, widths, password=None):
    pdf = pikepdf.new()
    for w in widths:
        pdf.add_blank_page(page_size=(w, 100))
    enc = pikepdf.Encryption(owner=password, user=password) if password else False
    pdf.save(path, encryption=enc)
    return path


def _pages(path):
    with pikepdf.open(path) as pdf:
        return [(int(p.mediabox[2]), int(p.obj.get("/Rotate", 0))) for p in pdf.pages]


@pytest.fixture
def inputs_dir(tmp_path):
    d = tmp_path / "inputs"
    d.mkdir()
    _make_pdf(d / "a.pdf", [101, 102, 103])
    _make_pdf(d / "b.pdf", [201, 202])
    return d


# --- save() ----------------------------------------------------------------


def test_save_absolutizes_input_paths_against_cwd(tmp_path):
    p = Pipeline(inputs=(InputFile("A", Path("a.pdf")),), stages=(Stage("cat", "1"),))
    out = tmp_path / "pipe.yaml"
    save(p, out, None, cwd=tmp_path / "somewhere")
    text = out.read_text()
    assert f"A={tmp_path / 'somewhere' / 'a.pdf'}" in text


def test_save_leaves_an_already_absolute_input_path_alone(tmp_path):
    abs_path = tmp_path / "elsewhere" / "a.pdf"
    p = Pipeline(inputs=(InputFile("A", abs_path),), stages=(Stage("cat", "1"),))
    out = tmp_path / "pipe.yaml"
    save(p, out, None, cwd=tmp_path / "cwd")
    assert f"A={abs_path}" in out.read_text()


def test_save_never_writes_the_real_password(tmp_path):
    p = Pipeline(
        inputs=(InputFile("A", Path("a.pdf"), "correct horse battery staple"),),
        stages=(Stage("cat", "1"),),
    )
    out = tmp_path / "pipe.yaml"
    save(p, out, None, cwd=tmp_path)
    text = out.read_text()
    assert "correct horse battery staple" not in text
    assert "A=PROMPT" in text


def test_save_includes_output_only_when_given(tmp_path):
    p = Pipeline(inputs=(InputFile("A", Path("a.pdf")),), stages=(Stage("cat", "1"),))
    with_out = tmp_path / "pipe1.yaml"
    sans_out = tmp_path / "pipe2.yaml"
    save(p, with_out, tmp_path / "result.pdf", cwd=tmp_path)
    save(p, sans_out, None, cwd=tmp_path)
    assert list(yaml.safe_load_all(with_out.read_text()))[0][-2] == "output"
    assert "output" not in list(yaml.safe_load_all(sans_out.read_text()))[0]


def test_save_leaves_free_text_stage_args_untouched(tmp_path):
    p = Pipeline(
        inputs=(InputFile("A", Path("a.pdf")),),
        stages=(Stage("stamp", "watermark.pdf"),),
    )
    out = tmp_path / "pipe.yaml"
    save(p, out, None, cwd=tmp_path / "cwd")
    assert "- watermark.pdf\n" in out.read_text()
    assert str(tmp_path / "cwd" / "watermark.pdf") not in out.read_text()


def test_save_has_a_leading_run_hint_comment(tmp_path):
    p = Pipeline(inputs=(InputFile("A", Path("a.pdf")),), stages=(Stage("cat", "1"),))
    out = tmp_path / "my_pipe.yaml"
    save(p, out, None, cwd=tmp_path)
    first_line = out.read_text().splitlines()[0]
    assert first_line.startswith("#")
    assert "my_pipe.yaml" in first_line


def test_save_multi_stage_writes_one_document_per_segment(tmp_path):
    p = Pipeline(
        inputs=(InputFile("A", Path("a.pdf")),),
        stages=(Stage("cat", "1-2"), Stage("rotate", "right")),
    )
    out = tmp_path / "pipe.yaml"
    save(p, out, None, cwd=tmp_path)
    docs = [d for d in yaml.safe_load_all(out.read_text()) if d is not None]
    assert docs == [["A=" + str(tmp_path / "a.pdf"), "cat", "1-2"], ["rotate", "right"]]


def test_save_raises_oserror_on_an_unwritable_path(tmp_path):
    p = Pipeline(inputs=(InputFile("A", Path("a.pdf")),), stages=(Stage("cat", "1"),))
    with pytest.raises(OSError):
        save(p, tmp_path / "no_such_dir" / "pipe.yaml", None, cwd=tmp_path)


# --- load() ------------------------------------------------------------


def test_load_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(PipelineFileError, match="not found"):
        load(tmp_path / "nope.yaml")


def test_load_a_yaml_dict_is_a_clear_error(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("key: value\n")
    with pytest.raises(PipelineFileError):
        load(path)


def test_load_malformed_yaml_syntax_is_a_clear_error(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("- [unclosed\n")
    with pytest.raises(PipelineFileError):
        load(path)


def test_load_content_the_pipeline_model_cannot_represent_is_a_clear_error(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("- '-'\n- cat\n")
    with pytest.raises(PipelineFileError, match="stdin"):
        load(path)


def test_load_hand_written_yaml_with_comments_and_multiple_documents(tmp_path):
    a = tmp_path / "a.pdf"
    _make_pdf(a, [100])
    path = tmp_path / "hand.yaml"
    path.write_text(
        f"""\
# a hand-edited pipeline
- # inputs
  - A={a}
- cat
- 1
---
- rotate
- right
"""
    )
    parsed = load(path)
    assert [f.handle for f in parsed.pipeline.inputs] == ["A"]
    assert [s.op for s in parsed.pipeline.stages] == ["cat", "rotate"]
    assert parsed.pipeline.stages[1].args_text == "right"


# --- round trip --------------------------------------------------------


def test_round_trip_load_of_save_is_the_same_pipeline_modulo_password_and_path(tmp_path):
    p = Pipeline(
        inputs=(InputFile("A", Path("a.pdf"), "s3cret"),),
        stages=(Stage("cat", "1-3"), Stage("rotate", "right")),
    )
    out = tmp_path / "pipe.yaml"
    save(p, out, tmp_path / "result.pdf", cwd=tmp_path)
    parsed = load(out)
    assert parsed.output == tmp_path / "result.pdf"
    assert parsed.pipeline.stages == p.stages
    assert [f.handle for f in parsed.pipeline.inputs] == ["A"]
    assert parsed.pipeline.inputs[0].path == tmp_path / "a.pdf"
    assert parsed.pipeline.inputs[0].password == "PROMPT"


@pytest.mark.parametrize(
    "name",
    [
        "a b.pdf",
        "a#b.pdf",
        "a'b.pdf",
        'a"b.pdf',
        "café.pdf",
        "-leading-dash.pdf",
    ],
)
def test_round_trip_awkward_input_filenames(tmp_path, name):
    p = Pipeline(inputs=(InputFile("A", Path(name)),), stages=(Stage("cat", "1"),))
    out = tmp_path / "pipe.yaml"
    save(p, out, None, cwd=tmp_path)
    parsed = load(out)
    assert parsed.pipeline.inputs[0].path == tmp_path / name


@pytest.mark.parametrize("arg", ["yes", "null", "1-3", "A1-end", "-x", "off"])
def test_round_trip_awkward_stage_args(tmp_path, arg):
    p = Pipeline(inputs=(InputFile("A", Path("a.pdf")),), stages=(Stage("cat", arg),))
    out = tmp_path / "pipe.yaml"
    save(p, out, None, cwd=tmp_path)
    parsed = load(out)
    assert parsed.pipeline.stages[0].tokens() == [arg]


def test_round_trip_password_is_never_readable_and_prompts_on_reload(tmp_path):
    a = tmp_path / "a.pdf"
    _make_pdf(a, [100], password="s3 cret")
    p = Pipeline(inputs=(InputFile("A", a, "s3 cret"),), stages=(Stage("cat", "1"),))
    out = tmp_path / "pipe.yaml"
    save(p, out, None, cwd=tmp_path)
    raw = out.read_bytes()
    assert b"s3 cret" not in raw
    assert b"PROMPT" in raw
    parsed = load(out)
    assert parsed.pipeline.inputs[0].password == stage_model.MASK


# --- ground truth: an independently-run real CLI process ------------------


def test_ground_truth_saved_pipeline_matches_gui_engine_save(tmp_path, inputs_dir):
    a, b = inputs_dir / "a.pdf", inputs_dir / "b.pdf"
    pipeline = Pipeline(
        inputs=(InputFile("A", a), InputFile("B", b)),
        stages=(
            Stage("cat", "A1-2 B1", inputs=("A", "B")),
            Stage("rotate", "right"),
        ),
    )
    gui_out = tmp_path / "gui_out.pdf"
    engine = SubprocessEngine(cwd=inputs_dir, cache_dir=tmp_path / "cache")
    try:
        result = engine.save(pipeline, gui_out, threading.Event())
    finally:
        engine.close()
    assert result.error == ""
    assert gui_out.exists()

    saved_yaml = tmp_path / "saved.yaml"
    cli_out = tmp_path / "cli_out.pdf"
    save(pipeline, saved_yaml, cli_out, cwd=inputs_dir)

    other_cwd = tmp_path / "elsewhere_entirely"
    other_cwd.mkdir()
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(filter(None, [SRC, os.environ.get("PYTHONPATH")])),
    }
    proc = subprocess.run(
        [sys.executable, "-m", "pdftl", "--args", str(saved_yaml)],
        cwd=other_cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert cli_out.exists()
    assert not (other_cwd / "a.pdf").exists()  # nothing relative resolved here

    assert _pages(cli_out) == _pages(gui_out)
    assert _pages(cli_out) == [(101, 90), (102, 90), (201, 90)]


def test_round_trip_output_options(tmp_path):
    p = Pipeline(
        inputs=(InputFile("A", Path("a.pdf")),),
        stages=(Stage("cat", "1"),),
        output_options="uncompress keep_first_id",
    )
    out = tmp_path / "pipe.yaml"
    save(p, out, None, cwd=tmp_path)
    parsed = load(out)
    assert parsed.pipeline.output_options == "uncompress keep_first_id"


def test_ground_truth_output_options_apply_through_to_argv_and_parse_tokens(tmp_path, inputs_dir):
    """Independent check: pikepdf's own /ID trailer on the real CLI's output."""
    a, b = inputs_dir / "a.pdf", inputs_dir / "b.pdf"
    pipeline = Pipeline(
        inputs=(InputFile("A", a), InputFile("B", b)),
        stages=(Stage("cat", "A1 B1", inputs=("A", "B")),),
        output_options="keep_first_id",
    )
    saved_yaml = tmp_path / "pipe.yaml"
    out = tmp_path / "out.pdf"
    save(pipeline, saved_yaml, out, cwd=inputs_dir)

    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(filter(None, [SRC, os.environ.get("PYTHONPATH")])),
    }
    proc = subprocess.run(
        [sys.executable, "-m", "pdftl", "--args", str(saved_yaml)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    with pikepdf.open(a) as pdf:
        a_id = bytes(pdf.trailer.ID[0])
    with pikepdf.open(out) as pdf:
        out_id = bytes(pdf.trailer.ID[0])
    assert out_id == a_id


def test_ground_truth_saved_pipeline_matches_a_typed_command(tmp_path, inputs_dir):
    """Independent cross-check: the file also matches command_bar's own parse."""
    a = inputs_dir / "a.pdf"
    text = f"A={shlex.quote(str(a))} cat 1-2"
    parsed = parse_command(text)
    out = tmp_path / "pipe.yaml"
    save(parsed.pipeline, out, tmp_path / "out.pdf", cwd=inputs_dir)

    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(filter(None, [SRC, os.environ.get("PYTHONPATH")])),
    }
    proc = subprocess.run(
        [sys.executable, "-m", "pdftl", "--args", str(out)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert _pages(tmp_path / "out.pdf") == [(101, 0), (102, 0)]


def test_save_never_writes_a_real_output_password(tmp_path):
    p = Pipeline(stages=(Stage("create", "1"),), output_options="owner_pw hunter22 uncompress")
    out = tmp_path / "pipe.yaml"
    save(p, out, None, cwd=tmp_path)
    text = out.read_text()
    assert "hunter22" not in text
    assert load(out).pipeline.output_options.split() == ["owner_pw", "PROMPT", "uncompress"]
