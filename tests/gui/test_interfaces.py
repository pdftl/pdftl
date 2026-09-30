from pathlib import Path

import pikepdf
import pytest

from pdftl.gui import shell_style
from pdftl.gui.interfaces import InputFile, Pipeline, Stage, plural


@pytest.mark.parametrize(("n", "expected"), [(0, "0 pages"), (1, "1 page"), (2, "2 pages")])
def test_plural(n, expected):
    assert plural(n, "page") == expected


def test_stage_tokens_split_like_a_shell():
    assert Stage("rotate", "'1-3 east' 5west").tokens() == ["1-3 east", "5west"]


def test_stage_tokens_unbalanced_quote():
    with pytest.raises(ValueError):
        Stage("rotate", "'1east").tokens()


def test_stage_tokens_reject_a_stage_separator():
    with pytest.raises(ValueError, match="separates stages"):
        Stage("cat", "A1-2 --- rotate 1east").tokens()
    assert Stage("cat", "'A1 --- x'").tokens() == ["A1 --- x"]


def test_stage_tokens_split_with_posix_rules_by_default(monkeypatch):
    monkeypatch.setattr(shell_style, "_WINDOWS", False)
    assert Stage("stamp", r"C:\logo.pdf").tokens() == ["C:logo.pdf"]


def test_stage_tokens_split_with_windows_rules_on_windows(monkeypatch):
    monkeypatch.setattr(shell_style, "_WINDOWS", True)
    assert Stage("stamp", r"C:\logo.pdf").tokens() == ["C:\\logo.pdf"]


def test_to_argv_layout():
    p = Pipeline(
        inputs=(InputFile("A", Path("a.pdf")), InputFile("B", Path("b.pdf"), "pw")),
        stages=(Stage("cat", "A B", inputs=("X",)), Stage("rotate", "1east", inputs=("B",))),
    )
    assert p.to_argv(Path("out.pdf")) == [
        "A=a.pdf",
        "B=b.pdf",
        "input_pw",
        "B=pw",
        "cat",
        "A",
        "B",
        "---",
        "B=b.pdf",
        "input_pw",
        "B=pw",
        "rotate",
        "1east",
        "output",
        "out.pdf",
    ]


def test_to_argv_undefined_handle():
    p = Pipeline(stages=(Stage("cat"), Stage("cat", inputs=("Q",))))
    with pytest.raises(ValueError, match="undefined"):
        p.to_argv()


def test_to_argv_without_output_or_stages():
    assert Pipeline(inputs=(InputFile("A", Path("a.pdf")),)).to_argv() == ["A=a.pdf"]


def test_to_argv_runs_on_the_real_cli(run_pdftl, two_page_pdf, six_page_pdf, tmp_path):
    out = tmp_path / "out.pdf"
    p = Pipeline(
        inputs=(InputFile("A", two_page_pdf), InputFile("B", six_page_pdf)),
        stages=(Stage("cat", "A B1-3"), Stage("rotate", "2east")),
    )
    run_pdftl(p.to_argv(out))
    with pikepdf.open(out) as pdf:
        assert [int(pg.obj.get("/Rotate", 0)) for pg in pdf.pages] == [0, 90, 0, 0, 0]


def test_to_argv_source_first_stage_declares_no_inputs():
    p = Pipeline(
        inputs=(InputFile("A", Path("a.pdf")),),
        stages=(Stage("create", "2"), Stage("cat", "A _", inputs=("A",))),
    )
    assert p.to_argv() == ["create", "2", "---", "A=a.pdf", "cat", "A", "_"]


def test_to_argv_source_first_stage_runs_on_the_real_cli(run_pdftl, two_page_pdf, tmp_path):
    out = tmp_path / "out.pdf"
    p = Pipeline(
        inputs=(InputFile("A", two_page_pdf),),
        stages=(Stage("create", "3"), Stage("cat", "_ A", inputs=("A",))),
    )
    run_pdftl(p.to_argv(out))
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 5


def test_output_tokens_split_like_a_shell():
    assert Pipeline(output_options="'a b' c").output_tokens() == ["a b", "c"]


def test_output_tokens_reject_a_stage_separator():
    with pytest.raises(ValueError, match="separates stages"):
        Pipeline(output_options="uncompress --- flatten").output_tokens()


def test_output_tokens_empty_is_empty():
    assert Pipeline().output_tokens() == []


def test_to_argv_appends_output_options_after_the_output_clause():
    p = Pipeline(
        inputs=(InputFile("A", Path("a.pdf")),),
        stages=(Stage("cat", "1-2"),),
        output_options="uncompress owner_pw s3cret",
    )
    assert p.to_argv(Path("out.pdf")) == [
        "A=a.pdf",
        "cat",
        "1-2",
        "output",
        "out.pdf",
        "uncompress",
        "owner_pw",
        "s3cret",
    ]


def test_to_argv_appends_output_options_without_an_output_clause():
    p = Pipeline(stages=(Stage("cat"),), output_options="linearize")
    assert p.to_argv() == ["cat", "linearize"]


def test_to_argv_output_options_run_on_the_real_cli(run_pdftl, two_page_pdf, tmp_path):
    """Ground truth: pikepdf on the saved file, not a re-derivation of the tokens."""
    out = tmp_path / "out.pdf"
    p = Pipeline(
        inputs=(InputFile("A", two_page_pdf),),
        stages=(Stage("cat", "1"),),
        output_options="uncompress",
    )
    run_pdftl(p.to_argv(out))
    with pikepdf.open(out) as pdf:
        assert "/Filter" not in pdf.pages[0].Contents
