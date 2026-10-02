import shlex
from pathlib import Path

import pikepdf
import pytest
from hypothesis import given
from hypothesis import strategies as st

from types import SimpleNamespace

import pdftl.core.constants as c
from pdftl.gui import shell_style
from pdftl.gui import stage_model as stage_model_module
from pdftl.gui.interfaces import InputFile, Pipeline, Stage
from pdftl.gui.stage_model import (
    add_input,
    extract_output_clause,
    insert_stage,
    is_save_only_option,
    fill_output_passwords,
    with_masked_passwords,
    output_passwords,
    option_tokens,
    output_option_names,
    output_options_help_markdown,
    output_stem,
    move_stage,
    parse_output_options,
    remove_input,
    remove_stage,
    replace_stage,
    stage_problems,
    to_cli,
    word_at,
)

S = tuple(Stage(op) for op in ("cat", "rotate", "shuffle", "compress"))


def _ops(p):
    return [s.op for s in p.stages]


def _widths(path):
    with pikepdf.open(path) as pdf:
        return [int(p.mediabox[2]) for p in pdf.pages]


@pytest.fixture
def marked(tmp_path):
    """Directory with spaces holding A (widths 101..104) and an encrypted B (201..203)."""
    d = tmp_path / "dir with 'quote' and space"
    d.mkdir()
    for name, base, n, enc in (("a.pdf", 100, 4, None), ("b é.pdf", 200, 3, "s3 cret")):
        pdf = pikepdf.new()
        for i in range(1, n + 1):
            pdf.add_blank_page(page_size=(base + i, 100))
        encryption = pikepdf.Encryption(owner=enc, user=enc) if enc else False
        pdf.save(d / name, encryption=encryption)
    return d / "a.pdf", d / "b é.pdf"


def test_add_input_uses_lowest_free_handle():
    p = add_input(add_input(add_input(Pipeline(), "x.pdf"), "y.pdf"), "z.pdf", "pw")
    assert [(f.handle, f.path, f.password) for f in p.inputs] == [
        ("A", Path("x.pdf"), None),
        ("B", Path("y.pdf"), None),
        ("C", Path("z.pdf"), "pw"),
    ]
    p = add_input(remove_input(p, "B"), "w.pdf")
    assert [f.handle for f in p.inputs] == ["A", "C", "B"]


def test_add_input_full():
    p = Pipeline()
    for _ in range(26):
        p = add_input(p, "x.pdf")
    assert "".join(f.handle for f in p.inputs) == "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    with pytest.raises(ValueError, match="26"):
        add_input(p, "x.pdf")


def test_remove_input_missing():
    with pytest.raises(KeyError):
        remove_input(Pipeline(), "A")


def test_edits_do_not_mutate():
    p = Pipeline(stages=S[:2])
    insert_stage(p, 0, S[2])
    remove_stage(p, 0)
    replace_stage(p, 1, S[3])
    move_stage(p, 0, 1)
    add_input(p, "x.pdf")
    assert p == Pipeline(stages=S[:2])


def test_insert_remove_replace():
    p = Pipeline(stages=S[:2])
    assert _ops(insert_stage(p, 0, S[2])) == ["shuffle", "cat", "rotate"]
    assert _ops(insert_stage(p, 1, S[2])) == ["cat", "shuffle", "rotate"]
    assert _ops(insert_stage(p, 2, S[2])) == ["cat", "rotate", "shuffle"]
    assert _ops(remove_stage(p, 1)) == ["cat"]
    assert _ops(replace_stage(p, 0, S[3])) == ["compress", "rotate"]


@pytest.mark.parametrize(
    ("call", "args"),
    [
        (insert_stage, (3, S[0])),
        (insert_stage, (-1, S[0])),
        (remove_stage, (2,)),
        (remove_stage, (-1,)),
        (replace_stage, (2, S[0])),
        (move_stage, (2, 1)),
    ],
)
def test_bad_index(call, args):
    with pytest.raises(IndexError):
        call(Pipeline(stages=S[:2]), *args)


@pytest.mark.parametrize(
    ("index", "delta", "ops", "new"),
    [
        (0, 1, ["rotate", "cat", "shuffle", "compress"], 1),
        (3, -2, ["cat", "compress", "rotate", "shuffle"], 1),
        (1, -9, ["rotate", "cat", "shuffle", "compress"], 0),
        (1, 9, ["cat", "shuffle", "compress", "rotate"], 3),
        (2, 0, ["cat", "rotate", "shuffle", "compress"], 2),
    ],
)
def test_move_stage(index, delta, ops, new):
    p, i = move_stage(Pipeline(stages=S), index, delta)
    assert (_ops(p), i) == (ops, new)


@given(st.integers(0, 3), st.integers(-5, 5))
def test_move_stage_is_a_permutation_landing_at_new_index(index, delta):
    p, i = move_stage(Pipeline(stages=S), index, delta)
    assert sorted(_ops(p)) == sorted(s.op for s in S)
    assert p.stages[i] == S[index]


def test_stage_problems():
    p = Pipeline(
        inputs=(InputFile("A", Path("a.pdf")),),
        stages=(
            Stage("cat", "1-2", inputs=("A",)),
            Stage("cat", "'unclosed"),
            Stage("cat", "A B", inputs=("A", "B", "C")),
            Stage("cat", "1 output x.pdf"),
            Stage("rotate", "east"),
        ),
    )
    assert stage_problems(p) == [
        (0, "The first stage always reads every input file"),
        (1, "Cannot parse arguments: No closing quotation"),
        (2, "Undefined input handle(s): B, C"),
        (3, "Remove 'output': Save chooses where output goes"),
    ]


def test_stage_problems_flags_an_output_option_in_stage_args():
    """Fires on any stage, not just the last: a real pdftl run only honors the
    final stage's options, so an earlier one is silently dropped by to_argv."""
    p = Pipeline(
        stages=(
            Stage("cat", "1-2 UNCOMPRESS"),
            Stage("rotate", "east"),
            Stage("cat", "allow"),
        ),
    )
    assert stage_problems(p) == [
        (0, "'UNCOMPRESS' is an output option; put it in the Output box"),
        (2, "'allow' is an output option; put it in the Output box"),
    ]


def test_to_cli_text():
    p = Pipeline(
        inputs=(InputFile("A", Path("a b.pdf"), "pw"),),
        stages=(Stage("cat", "1-2"), Stage("rotate", "east", inputs=("A",))),
    )
    assert to_cli(Pipeline()) == "pdftl"
    assert to_cli(p, Path("o.pdf")) == (
        "pdftl 'A=a b.pdf' input_pw A=PROMPT cat 1-2 --- 'A=a b.pdf' input_pw A=PROMPT"
        " rotate east output o.pdf"
    )
    assert to_cli(p, style="windows", mask_passwords=False) == (
        'pdftl "A=a b.pdf" input_pw A=pw cat 1-2 --- "A=a b.pdf" input_pw A=pw rotate east'
    )
    with pytest.raises(ValueError, match="style"):
        to_cli(p, style="fish")


def test_mask_input_passwords():
    p = Pipeline(inputs=(InputFile("A", Path("a.pdf"), "pw"), InputFile("B", Path("b.pdf"))))
    masked = with_masked_passwords(p)
    assert [f.password for f in masked.inputs] == ["PROMPT", None]
    assert [f.path for f in masked.inputs] == [Path("a.pdf"), Path("b.pdf")]


def test_to_cli_default_style_is_the_platform_style(monkeypatch):
    p = Pipeline(stages=(Stage("stamp", "C:\\logo.pdf"),))
    monkeypatch.setattr(shell_style, "_WINDOWS", False)
    assert to_cli(p) == to_cli(p, style="posix")
    monkeypatch.setattr(shell_style, "_WINDOWS", True)
    assert to_cli(p) == to_cli(p, style="windows")


def test_to_cli_runs_on_the_real_cli(run_pdftl, marked, tmp_path):
    a, b = marked
    p = Pipeline(
        inputs=(InputFile("A", a), InputFile("B", b, "s3 cret")),
        stages=(Stage("cat", "A1-2 B3"), Stage("cat", "A4 _1-end B1", inputs=("A", "B"))),
    )
    out = tmp_path / "out é.pdf"
    run_pdftl(shlex.split(to_cli(p, out, mask_passwords=False))[1:])
    assert _widths(out) == [104, 101, 102, 203, 201]
    masked = to_cli(p, out)
    assert "s3 cret" not in masked
    assert "B=PROMPT" in shlex.split(masked)


def test_stage_problems_source_op_after_the_first_stage():
    p = Pipeline(stages=(Stage("create", "1"), Stage("create", "1")))
    assert stage_problems(p) == [(1, "'create' makes a new PDF, so it must be the first stage")]


def test_output_stem_names_inputs_reaching_the_stage_then_its_op():
    p = Pipeline(
        inputs=(
            InputFile("A", Path("/x/in 1.pdf")),
            InputFile("B", Path("in2.pdf")),
            InputFile("C", Path("c.pdf")),
        ),
        stages=(
            Stage("cat", "A1 B1"),
            Stage("shrink"),
            Stage("cat", "_ C", inputs=("C", "Q")),
        ),
    )
    assert output_stem(p, 0) == "in1-in2-c-cat"
    assert output_stem(p, 1) == "in1-in2-c-shrink"


def test_output_stem_only_counts_inputs_already_declared():
    p = Pipeline(
        inputs=(InputFile("A", Path("a.pdf")), InputFile("B", Path("b.pdf"))),
        stages=(Stage("create", "1"), Stage("rotate"), Stage("cat", "_ B", inputs=("B",))),
    )
    assert output_stem(p, 0) == "create"
    assert output_stem(p, 1) == "rotate"
    assert output_stem(p, 2) == "b-cat"


def test_output_stem_caps_the_number_of_input_names():
    inputs = tuple(InputFile(h, Path(f"{h.lower()}.pdf")) for h in "ABCDE")
    p = Pipeline(inputs=inputs, stages=(Stage("?!"),))
    assert output_stem(p, 0) == "a-b-c-2more-stage"


# --- output options: naming, save-only classification, parsing -------------


def test_output_option_names_covers_known_options_and_excludes_output():
    names = output_option_names()
    for expected in ("flatten", "uncompress", "compress", "owner_pw", "allow", "sign_key"):
        assert expected in names
    assert c.OUTPUT not in names


def test_is_save_only_option_encryption_and_signing():
    for name in ("owner_pw", "user_pw", "encrypt_aes256", "allow", "sign_key", "sign_pass_prompt"):
        assert is_save_only_option(name), name


def test_is_save_only_option_false_for_ordinary_options_and_unknown_names():
    for name in ("flatten", "uncompress", "compress", "linearize", "keep_first_id", "not_a_thing"):
        assert not is_save_only_option(name), name


def test_option_tokens_renders_flags_values_and_allow():
    assert option_tokens({"flatten": True}) == ["flatten"]
    assert option_tokens({"deflate": "zopfli"}) == ["deflate", "zopfli"]
    assert option_tokens({"allow": {"Printing", "Assembly"}}) == ["allow", "Assembly", "Printing"]


def test_option_tokens_skips_output():
    assert option_tokens({c.OUTPUT: "x.pdf", "flatten": True}) == ["flatten"]


def test_parse_output_options_empty_text():
    assert parse_output_options("") == {}


def test_parse_output_options_flags_and_values():
    assert parse_output_options("uncompress owner_pw s3cret") == {
        "uncompress": True,
        "owner_pw": "s3cret",
    }


def test_parse_output_options_rejects_a_stage_separator():
    with pytest.raises(ValueError, match="separates stages"):
        parse_output_options("uncompress --- flatten")


def test_parse_output_options_rejects_unbalanced_quotes():
    with pytest.raises(ValueError):
        parse_output_options("'uncompress")


def test_parse_output_options_rejects_an_unrecognized_token():
    with pytest.raises(ValueError, match="not a recognized output option"):
        parse_output_options("banana")


def test_parse_output_options_wraps_the_native_parser_error():
    with pytest.raises(ValueError, match="Missing value for keyword"):
        parse_output_options("deflate")


def test_parse_output_options_rejects_output_itself():
    with pytest.raises(ValueError, match="Save picks the output path"):
        parse_output_options("output out.pdf")


def test_parse_output_options_round_trips_through_option_tokens():
    text = "uncompress owner_pw s3cret"
    assert option_tokens(parse_output_options(text)) == text.split()


# --- extract_output_clause: 'output <path>' sets the save target, not an error ---


def test_extract_output_clause_pulls_out_the_path():
    remaining, target = extract_output_clause("uncompress output out.pdf flatten")
    assert remaining == "uncompress flatten"
    assert target == Path("out.pdf")


def test_extract_output_clause_no_output_present():
    remaining, target = extract_output_clause("uncompress flatten")
    assert remaining == "uncompress flatten"
    assert target is None


def test_extract_output_clause_empty_text():
    assert extract_output_clause("") == ("", None)


def test_extract_output_clause_only_output():
    assert extract_output_clause("output out.pdf") == ("", Path("out.pdf"))


def test_extract_output_clause_propagates_the_same_parse_errors():
    with pytest.raises(ValueError, match="separates stages"):
        extract_output_clause("uncompress --- flatten")
    with pytest.raises(ValueError, match="not a recognized output option"):
        extract_output_clause("banana")


# --- word_at: cursor -> the option word it's touching -----------------------


@pytest.mark.parametrize(
    ("text", "pos", "word"),
    [
        ("uncompress flatten", 3, "uncompress"),
        ("uncompress flatten", 0, "uncompress"),
        ("uncompress flatten", 10, "uncompress"),  # touching its end boundary
        ("uncompress flatten", 11, "flatten"),  # touching its start boundary
        ("uncompress flatten", 15, "flatten"),
        ("uncompress  flatten", 11, ""),  # the second of two spaces: touches neither
        ("", 0, ""),
        ("solo", 4, "solo"),
    ],
)
def test_word_at(text, pos, word):
    assert word_at(text, pos) == word


# --- output_options_help_markdown -------------------------------------------


def test_output_options_help_markdown_has_general_and_save_only_sections():
    md = output_options_help_markdown()
    assert "## General" in md
    assert "## Encryption & signing (Save only)" in md
    assert "### `flatten`" in md
    assert "### `owner_pw`" in md
    assert "### `output`" not in md


def test_output_options_help_markdown_highlight_moves_option_first():
    md = output_options_help_markdown("owner_pw")
    general_idx = md.index("## General")
    save_only_idx = md.index("## Encryption")
    owner_pw_idx = md.index("### `owner_pw`")
    flatten_idx = md.index("### `flatten`")
    assert save_only_idx < owner_pw_idx
    assert general_idx < flatten_idx
    # owner_pw is the first entry after its section heading
    assert md[save_only_idx:owner_pw_idx].count("###") == 0


def test_output_options_help_markdown_unknown_highlight_is_ignored():
    md = output_options_help_markdown("not_a_real_option")
    assert "### `flatten`" in md


def test_output_options_help_markdown_no_general_options(monkeypatch):
    monkeypatch.setattr(stage_model_module, "is_save_only_option", lambda name: True)
    md = output_options_help_markdown()
    assert "## General" not in md
    assert "## Encryption & signing (Save only)" in md


def test_output_options_help_markdown_no_save_only_options(monkeypatch):
    monkeypatch.setattr(stage_model_module, "is_save_only_option", lambda name: False)
    md = output_options_help_markdown()
    assert "## General" in md
    assert "## Encryption & signing (Save only)" not in md


def test_option_section_omits_empty_desc_and_long_desc():
    opt = SimpleNamespace(desc="", long_desc="")
    assert stage_model_module._option_section("x", opt) == "### `x`"


def test_mask_passwords_hides_output_passwords_too():
    p = Pipeline(stages=(Stage("cat"),), output_options="owner_pw s3cret uncompress user_pw 'a b'")
    assert with_masked_passwords(p).output_options == "owner_pw PROMPT uncompress user_pw PROMPT"


def test_mask_passwords_leaves_text_without_passwords_or_unsplittable_as_is():
    for text in ("uncompress  linearize", "owner_pw 'open", "owner_pw"):
        p = Pipeline(stages=(Stage("cat"),), output_options=text)
        assert with_masked_passwords(p).output_options == text


def test_output_passwords_and_filling_prompts():
    assert output_passwords("owner_pw s3cret flatten user_pw PROMPT") == {
        "owner_pw": "s3cret",
        "user_pw": "PROMPT",
    }
    assert output_passwords("owner_pw 'open") == {}
    filled = fill_output_passwords("owner_pw PROMPT user_pw PROMPT", {"owner_pw": "x y"})
    assert shlex.split(filled) == ["owner_pw", "x y", "user_pw", "PROMPT"]
    assert fill_output_passwords("owner_pw typed", {"owner_pw": "old"}) == "owner_pw typed"


def test_password_options_are_the_registry_encryption_options_with_a_value():
    from pdftl.gui.stage_model import output_password_options

    assert output_password_options() == {"owner_pw", "user_pw"}


def test_option_tokens_renders_any_list_valued_option():
    assert option_tokens({"x_list": ["b", "a"]}) == ["x_list", "b", "a"]
