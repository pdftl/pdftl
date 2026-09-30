import json
import os

import pikepdf
import pytest

from pdftl.gui import op_policy
from pdftl.gui.interfaces import OpKind


def test_all_ops_nonempty_and_sorted():
    ops = op_policy.all_ops()
    assert len(ops) > 50
    assert [o.name for o in ops] == sorted(o.name for o in ops)


def test_all_ops_every_op_gets_a_kind():
    for op in op_policy.all_ops():
        assert isinstance(op.kind, OpKind)


def test_all_ops_includes_blocked():
    names = {o.name for o in op_policy.all_ops()}
    assert "server" in names


class _Entry:
    def __init__(self, type="", tags=(), args=([], {}), skip=False):
        self.type, self.tags, self.args, self.skip_pipeline_save = type, tags, args, skip


@pytest.mark.parametrize(
    ("entry", "kind"),
    [
        (_Entry("source operation", ["utility"]), OpKind.BLOCKED),
        (_Entry("source operation", ["source"]), OpKind.PDF),
        (_Entry(args=([], {"is_last_stage": "x", "output_pattern": "o"}), skip=True), OpKind.PDF),
        (_Entry(args=([], {"output_dir": "output"}), skip=True), OpKind.SIDE_EFFECT),
        (_Entry(args=([], {"output_pattern": "p"}), skip=True), OpKind.SIDE_EFFECT),
        (_Entry(tags=["export"], args=([], {"output_file": "o"}), skip=True), OpKind.SIDE_EFFECT),
        (_Entry(args=([], {"output_file": "o"}), skip=True), OpKind.DATA),
        (_Entry(args=([], {"output_file": "o"})), OpKind.PDF),
        (_Entry(args=None), OpKind.PDF),
    ],
)
def test_kind_comes_from_registry_fields_alone(entry, kind):
    assert op_policy._op_info("any_name", entry).kind is kind


def test_the_classifier_names_no_operation():
    """Kinds are derived, so the classifier names no registered operation."""
    import inspect
    import re

    names = {o.name for o in op_policy.all_ops()}
    quoted = set(re.findall(r"\"([a-z_]+)\"", inspect.getsource(op_policy._kind_and_reason)))
    assert not quoted & names


def test_classify_unknown_raises_keyerror():
    with pytest.raises(KeyError):
        op_policy.classify("not_a_real_pdftl_operation")


def test_server_is_blocked():
    info = op_policy.classify("server")
    assert info.kind == OpKind.BLOCKED
    assert info.reason


def test_burst_is_side_effect():
    info = op_policy.classify("burst")
    assert info.kind == OpKind.SIDE_EFFECT
    assert info.reason


def test_render_is_a_pdf_stage_with_a_note():
    info = op_policy.classify("render")
    assert info.kind == OpKind.PDF
    assert "command line" in info.reason


def test_gui_is_blocked():
    assert op_policy.classify("gui").kind == OpKind.BLOCKED


def test_create_is_a_pdf_source():
    assert op_policy.classify("create").kind == OpKind.PDF


def test_dump_data_is_data():
    info = op_policy.classify("dump_data")
    assert info.kind == OpKind.DATA


def test_rotate_and_cat_are_pdf():
    for name in ("rotate", "cat"):
        info = op_policy.classify(name)
        assert info.kind == OpKind.PDF
        assert info.reason == ""


def test_export_fonts_and_images_are_side_effect_despite_skip_pipeline_save():
    for name in ("export_fonts", "export_images", "unpack_files"):
        info = op_policy.classify(name)
        assert info.kind == OpKind.SIDE_EFFECT, name


def test_dump_signatures_is_a_pass_through_pdf_stage(run_pdftl, two_page_pdf, tmp_path):
    """It lacks `skip_pipeline_save`, and the CLI does save its input as output."""
    assert op_policy.classify("dump_signatures").kind == OpKind.PDF
    out = tmp_path / "out.pdf"
    run_pdftl([str(two_page_pdf), "dump_signatures", "output", str(out)])
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 2


def test_opinfo_fields_pass_through_from_registry():
    info = op_policy.classify("rotate")
    assert info.name == "rotate"
    assert info.desc
    assert info.usage
    assert all(isinstance(pair, tuple) and len(pair) == 2 for pair in info.examples)


def test_example_pair_handles_dict_and_object_forms():
    class _Fake:
        cmd = "in.pdf rotate 1east output out.pdf"
        desc = "Rotate page 1"

    assert op_policy._example_pair({"cmd": "a", "desc": "b"}) == ("a", "b")
    assert op_policy._example_pair({"cmd": "a"}) == ("a", "")
    assert op_policy._example_pair(_Fake()) == (_Fake.cmd, _Fake.desc)


def test_op_info_defaults_when_entry_has_no_optional_fields():
    class _BareEntry:
        pass

    info = op_policy._op_info("fake_op", _BareEntry())
    assert info.name == "fake_op"
    assert info.kind == OpKind.PDF
    assert info.desc == ""
    assert info.usage == ""
    assert info.long_desc == ""
    assert info.examples == ()
    assert info.reason == ""


# ---------------------------------------------------------------------------
# Ground truth: SIDE_EFFECT operations really do write extra files.
# ---------------------------------------------------------------------------


def test_burst_ground_truth_writes_one_file_per_page(
    run_pdftl, two_page_pdf, tmp_path, monkeypatch
):
    assert op_policy.classify("burst").kind == OpKind.SIDE_EFFECT
    monkeypatch.chdir(tmp_path)
    run_pdftl([str(two_page_pdf), "burst"])
    written = sorted(p.name for p in tmp_path.iterdir())
    # burst also writes a pdftk-compatible doc_data.txt dump alongside the pages.
    assert written == ["doc_data.txt", "pg_0001.pdf", "pg_0002.pdf"]
    for name in ("pg_0001.pdf", "pg_0002.pdf"):
        with pikepdf.open(tmp_path / name) as pdf:
            assert len(pdf.pages) == 1


def test_render_ground_truth_with_a_pdf_output_is_one_image_per_page(
    run_pdftl, two_page_pdf, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    run_pdftl([str(two_page_pdf), "render", "dpi=30", "output", "out.pdf"])
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.pdf"]
    with pikepdf.open(tmp_path / "out.pdf") as pdf:
        assert len(pdf.pages) == 2
        assert [len(page.get_images()) for page in pdf.pages] == [1, 1]


def test_unpack_files_ground_truth_recreates_attachment_bytes(
    run_pdftl, two_page_pdf, tmp_path, monkeypatch
):
    assert op_policy.classify("unpack_files").kind == OpKind.SIDE_EFFECT
    monkeypatch.chdir(tmp_path)
    payload = b"pdftl-gui-op-policy-attachment-payload"
    note_path = tmp_path / "note.txt"
    note_path.write_bytes(payload)

    run_pdftl([str(two_page_pdf), "attach_files", "note.txt", "output", "attached.pdf"])
    note_path.unlink()
    assert not note_path.exists()

    run_pdftl([str(tmp_path / "attached.pdf"), "unpack_files"])
    assert note_path.exists()
    assert note_path.read_bytes() == payload


# ---------------------------------------------------------------------------
# Ground truth: DATA operations produce text, never a PDF.
# ---------------------------------------------------------------------------


def test_dump_data_ground_truth_prints_page_count(
    run_pdftl, two_page_pdf, tmp_path, monkeypatch, capsys
):
    assert op_policy.classify("dump_data").kind == OpKind.DATA
    monkeypatch.chdir(tmp_path)
    run_pdftl([str(two_page_pdf), "dump_data"])
    captured = capsys.readouterr()
    assert "NumberOfPages: 2" in captured.out
    assert list(tmp_path.iterdir()) == []


def test_usage_ground_truth_byte_total_matches_real_file_size(
    run_pdftl, two_page_pdf, tmp_path, monkeypatch, capsys
):
    assert op_policy.classify("usage").kind == OpKind.DATA
    monkeypatch.chdir(tmp_path)
    run_pdftl([str(two_page_pdf), "usage", "json"])
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    category_total = sum(cat["bytes"] for cat in parsed["usage"]["categories"])
    assert category_total == os.path.getsize(two_page_pdf)
    assert list(tmp_path.iterdir()) == []


def test_dump_images_ground_truth_no_pdf_written(
    run_pdftl, two_page_pdf, tmp_path, monkeypatch, capsys
):
    assert op_policy.classify("dump_images").kind == OpKind.DATA
    monkeypatch.chdir(tmp_path)
    run_pdftl([str(two_page_pdf), "dump_images"])
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert parsed["images"] == []
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# Ground truth: sample PDF operations really do write a valid output PDF.
# ---------------------------------------------------------------------------


def test_rotate_ground_truth_valid_output_pdf(run_pdftl, two_page_pdf, tmp_path):
    assert op_policy.classify("rotate").kind == OpKind.PDF
    out = tmp_path / "out.pdf"
    run_pdftl([str(two_page_pdf), "rotate", "1east", "output", str(out)])
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 2
        assert int(pdf.pages[0].obj.get("/Rotate", 0)) == 90


def test_cat_ground_truth_valid_output_pdf(run_pdftl, two_page_pdf, tmp_path):
    assert op_policy.classify("cat").kind == OpKind.PDF
    out = tmp_path / "out.pdf"
    run_pdftl([str(two_page_pdf), "cat", "output", str(out)])
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 2


def test_delete_blank_ground_truth_valid_output_pdf(run_pdftl, two_page_pdf, tmp_path):
    assert op_policy.classify("delete_blank").kind == OpKind.PDF
    out = tmp_path / "out.pdf"
    run_pdftl([str(two_page_pdf), "delete_blank", "output", str(out)])
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) <= 2


def test_is_source_only_for_ops_that_take_no_input():
    assert op_policy.is_source("create")
    assert not op_policy.is_source("cat")
    assert not op_policy.is_source("no_such_op")


def test_every_help_field_is_text():
    for info in op_policy.all_ops():
        assert all(isinstance(v, str) for v in (info.desc, info.usage, info.long_desc)), info.name
    assert "contrast=" in op_policy.classify("modify_images").long_desc
