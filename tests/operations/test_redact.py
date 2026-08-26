# tests/operations/test_redact.py

import re
from unittest.mock import MagicMock

import pikepdf
import pytest
from pikepdf import Dictionary, Name

from pdftl.exceptions import InvalidArgumentError, OperationError
from pdftl.operations.redact import (
    RedactOptions,
    _color_operator,
    _box_fill_snippet,
    _collect_raw_match_rects,
    _draw_page_boxes,
    _ensure_ext_gstate,
    _find_options_part,
    _parse_color,
    _parse_merge_mode,
    _parse_opacity,
    _parse_options,
    _parse_positive_float,
    _parse_redact_spec,
    _match_target_spans,
    _register_layer_property,
    _verify_redaction,
    redact,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

WIDTH, HEIGHT = 612, 792


def _make_text_pdf(
    lines: list[str], start_y: float = 700, font_size: int = 24, line_gap: float = 50
):
    """A single-page PDF with each string in `lines` drawn as its own
    BT/Tj text-showing run at a descending y-position, using the
    built-in Helvetica font (no /Widths array -- exercises the same
    Standard-14 fallback path as excise's own glyph-width lookup)."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(WIDTH, HEIGHT))
    page.Resources = Dictionary(
        Font=Dictionary(F1=Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))
    )

    chunks = []
    y = start_y
    for line in lines:
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        chunks.append(f"BT\n/F1 {font_size} Tf\n50 {y} Td\n({escaped}) Tj\nET\n")
        y -= line_gap
    page.Contents = pdf.make_stream("".join(chunks).encode("latin-1"))
    return pdf


def _page_ops(pdf, page_index=0):
    """Flat list of (operator_str, operands) for one page's content
    stream, coalescing multiple content-stream fragments if present
    (contents_add can split a page's /Contents into an array)."""
    page = pdf.pages[page_index]
    pikepdf.Page(page).contents_coalesce()
    return [(str(op), list(operands)) for operands, op in pikepdf.parse_content_stream(page)]


def _tj_strings(pdf, page_index=0):
    """All literal-string operands of Tj/TJ operators on one page, in
    order -- i.e. what text survives after redaction, ignoring glyph
    positioning adjustments."""
    out = []
    for op, operands in _page_ops(pdf, page_index):
        if op == "Tj":
            out.append(str(operands[0]))
        elif op == "TJ":
            for item in operands[0]:
                if isinstance(item, (pikepdf.String, str)):
                    out.append(str(item))
    return out


def _fill_rects(pdf, page_index=0):
    """[x, y, w, h] operands of every `re` immediately followed by `f`
    fill on one page -- i.e. the drawn redaction boxes."""
    ops = _page_ops(pdf, page_index)
    rects = []
    for i, (op, operands) in enumerate(ops):
        if op == "re" and i + 1 < len(ops) and ops[i + 1][0] == "f":
            rects.append([float(v) for v in operands])
    return rects


# ---------------------------------------------------------------------------
# Spec grammar parsing
# ---------------------------------------------------------------------------


class TestParseRedactSpec:
    def test_basic_spec_defaults_to_all_pages(self):
        page_spec, regex_str, options = _parse_redact_spec(r"/\d+/")
        assert page_spec == "1-end"
        assert regex_str == r"\d+"
        assert isinstance(options, RedactOptions)

    def test_page_range_captured(self):
        page_spec, regex_str, _ = _parse_redact_spec(r"1-3/foo/")
        assert page_spec == "1-3"
        assert regex_str == "foo"

    def test_alternate_delimiter(self):
        page_spec, regex_str, _ = _parse_redact_spec(r"1|foo/bar|")
        assert regex_str == "foo/bar"

    def test_empty_spec_raises(self):
        with pytest.raises(InvalidArgumentError):
            _parse_redact_spec("")

    def test_malformed_spec_raises(self):
        with pytest.raises(InvalidArgumentError):
            _parse_redact_spec("not-a-valid-spec")

    def test_options_only_spec_with_no_regex_raises(self):
        """The whole spec is consumed as an options block, leaving
        rest_of_spec empty -- must raise a "missing regex" error rather
        than proceeding with an empty regex string."""
        with pytest.raises(InvalidArgumentError, match="missing regex"):
            _parse_redact_spec("(pad=1)")

    def test_options_block_parsed(self):
        _, _, options = _parse_redact_spec(r"/foo/(pad=3, box=false)")
        assert options.pad == 3.0
        assert options.draw_box is False


class TestParseOptions:
    def test_no_options_uses_defaults(self):
        options = _parse_options("")
        assert options.use_regex is True
        assert options.ignore_case is False
        assert options.pad == 0.0
        assert options.box_pad == 1.0
        assert options.merge_mode == "line"
        assert options.draw_box is True
        assert options.color == [0, 0, 0]
        assert options.opacity == 1.0
        assert options.layer is None

    def test_regex_false_parsed(self):
        options = _parse_options("(regex=false)")
        assert options.use_regex is False

    def test_ignore_case_true_parsed(self):
        options = _parse_options("(ignore_case=true)")
        assert options.ignore_case is True

    def test_layer_name_parsed(self):
        options = _parse_options("(layer=MyLayer)")
        assert options.layer == "MyLayer"

    def test_unknown_key_raises(self):
        with pytest.raises(InvalidArgumentError):
            _parse_options("(bogus=1)")


class TestParseColor:
    def test_single_gray_value(self):
        assert _parse_color("0.5") == [0.5]

    def test_rgb_triplet(self):
        assert _parse_color("1 0 0") == [1.0, 0.0, 0.0]

    def test_cmyk_quad(self):
        assert _parse_color("0 0 0 1") == [0.0, 0.0, 0.0, 1.0]

    def test_wrong_component_count_raises(self):
        with pytest.raises(InvalidArgumentError):
            _parse_color("1 0")

    def test_non_numeric_raises(self):
        with pytest.raises(InvalidArgumentError):
            _parse_color("red")


class TestColorOperator:
    def test_gray(self):
        assert _color_operator([0.5]) == "0.5 g"

    def test_rgb(self):
        assert _color_operator([1, 0, 0]) == "1 0 0 rg"

    def test_cmyk(self):
        assert _color_operator([0, 0, 0, 1]) == "0 0 0 1 k"


class TestParseOpacity:
    def test_default_is_fully_opaque(self):
        assert _parse_opacity({}) == 1.0

    def test_valid_value(self):
        assert _parse_opacity({"opacity": "0.5"}) == 0.5

    def test_out_of_range_raises(self):
        with pytest.raises(InvalidArgumentError):
            _parse_opacity({"opacity": "1.5"})

    def test_negative_raises(self):
        with pytest.raises(InvalidArgumentError):
            _parse_opacity({"opacity": "-0.1"})

    def test_non_numeric_raises(self):
        with pytest.raises(InvalidArgumentError):
            _parse_opacity({"opacity": "opaque"})


class TestParsePositiveFloat:
    def test_default_used_when_absent(self):
        assert _parse_positive_float({}, "pad", 2.0) == 2.0

    def test_parses_present_value(self):
        assert _parse_positive_float({"pad": "5"}, "pad", 2.0) == 5.0

    def test_negative_raises(self):
        with pytest.raises(InvalidArgumentError):
            _parse_positive_float({"pad": "-1"}, "pad", 2.0)

    def test_non_numeric_raises(self):
        with pytest.raises(InvalidArgumentError):
            _parse_positive_float({"pad": "abc"}, "pad", 2.0)


class TestParseMergeMode:
    def test_default_is_line(self):
        assert _parse_merge_mode({}) == "line"

    def test_area_accepted(self):
        assert _parse_merge_mode({"merge": "area"}) == "area"

    def test_none_accepted(self):
        assert _parse_merge_mode({"merge": "none"}) == "none"

    def test_invalid_raises(self):
        with pytest.raises(InvalidArgumentError):
            _parse_merge_mode({"merge": "bogus"})


# ---------------------------------------------------------------------------
# End-to-end: search + delete + box, on a real PDF
# ---------------------------------------------------------------------------


class TestRedactEndToEnd:
    def test_matched_digits_removed_surrounding_text_kept(self):
        pdf = _make_text_pdf(["Hello World SSN 123-45-6789 end"])
        redact(pdf, [r"/\d{3}-\d{2}-\d{4}/"])
        strings = "".join(_tj_strings(pdf))
        assert "123-45-6789" not in strings
        assert "Hello World SSN" in strings
        assert "end" in strings

    def test_no_match_leaves_content_untouched(self):
        pdf = _make_text_pdf(["Nothing sensitive here"])
        redact(pdf, [r"/\d{3}-\d{2}-\d{4}/"])
        strings = "".join(_tj_strings(pdf))
        assert "Nothing sensitive here" in strings

    def test_multiple_matches_across_separate_text_runs(self):
        """Exercises the multi-Tj-run / \\r\\n-boundary scenario that
        surfaced the TextProvider index-mapping bug: two matches on two
        separate BT/Tj blocks must both be found and both redacted."""
        pdf = _make_text_pdf(
            ["Hello World SSN 123-45-6789 end", "Another line with SSN 987-65-4321 here"]
        )
        redact(pdf, [r"/\d{3}-\d{2}-\d{4}/"])
        strings = "".join(_tj_strings(pdf))
        assert "123-45-6789" not in strings
        assert "987-65-4321" not in strings
        assert "Hello World SSN" in strings
        assert "end" in strings
        assert "Another line with SSN" in strings
        assert "here" in strings

    def test_default_draws_one_black_box_per_match(self):
        pdf = _make_text_pdf(["ID: 123-45-6789 end"])
        redact(pdf, [r"/\d{3}-\d{2}-\d{4}/"])
        ops = [op for op, _ in _page_ops(pdf)]
        assert "rg" in ops or "g" in ops  # a fill color was set
        assert _fill_rects(pdf) != []

    def test_box_false_draws_no_box(self):
        pdf = _make_text_pdf(["ID: 123-45-6789 end"])
        redact(pdf, [r"/\d{3}-\d{2}-\d{4}/(box=false)"])
        assert _fill_rects(pdf) == []
        # but the digits are still gone
        strings = "".join(_tj_strings(pdf))
        assert "123-45-6789" not in strings

    def test_literal_mode_does_not_treat_pattern_as_regex(self):
        pdf = _make_text_pdf(["Price: $5.00 (was $10.00)"])
        redact(pdf, [r"/$5.00/(regex=false)"])
        strings = "".join(_tj_strings(pdf))
        assert "$5.00" not in strings
        assert "$10.00" in strings  # regex '.' would have matched '$5X00' too

    def test_ignore_case_option(self):
        pdf = _make_text_pdf(["Find CONFIDENTIAL text here"])
        redact(pdf, ["/confidential/(ignore_case=true)"])
        strings = "".join(_tj_strings(pdf))
        assert "CONFIDENTIAL" not in strings

    def test_case_sensitive_by_default_does_not_match(self):
        pdf = _make_text_pdf(["Find CONFIDENTIAL text here"])
        redact(pdf, ["/confidential/"])
        strings = "".join(_tj_strings(pdf))
        assert "CONFIDENTIAL" in strings

    def test_page_range_restricts_which_pages_are_redacted(self):
        pdf = pikepdf.new()
        for text in ["Secret 123-45-6789 on page one", "Secret 111-22-3333 on page two"]:
            page = pdf.add_blank_page(page_size=(WIDTH, HEIGHT))
            page.Resources = Dictionary(
                Font=Dictionary(
                    F1=Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)
                )
            )
            page.Contents = pdf.make_stream(
                f"BT\n/F1 18 Tf\n50 700 Td\n({text}) Tj\nET\n".encode("latin-1")
            )

        redact(pdf, [r"1/\d{3}-\d{2}-\d{4}/"])  # page 1 only

        page1_strings = "".join(_tj_strings(pdf, 0))
        page2_strings = "".join(_tj_strings(pdf, 1))
        assert "123-45-6789" not in page1_strings
        assert "111-22-3333" in page2_strings

    def test_empty_specs_list_is_a_noop(self):
        pdf = _make_text_pdf(["Nothing to redact 123-45-6789"])
        result = redact(pdf, [])
        assert result.success is True
        strings = "".join(_tj_strings(pdf))
        assert "123-45-6789" in strings

    def test_multiple_specs_applied_in_sequence(self):
        pdf = _make_text_pdf(["SSN 123-45-6789 and CONFIDENTIAL note"])
        redact(pdf, [r"/\d{3}-\d{2}-\d{4}/", "/CONFIDENTIAL/"])
        strings = "".join(_tj_strings(pdf))
        assert "123-45-6789" not in strings
        assert "CONFIDENTIAL" not in strings
        assert "SSN" in strings
        assert "and" in strings
        assert "note" in strings


class TestRedactColorAndOpacity:
    def test_custom_color_used_in_fill_operator(self):
        pdf = _make_text_pdf(["Secret 123-45-6789 value"])
        redact(pdf, [r"/\d{3}-\d{2}-\d{4}/(color=1 0 0)"])
        ops = _page_ops(pdf)
        rg_ops = [operands for op, operands in ops if op == "rg"]
        assert any(operands == [1, 0, 0] for operands in rg_ops)

    def test_opacity_below_one_registers_ext_gstate(self):
        pdf = _make_text_pdf(["Secret 123-45-6789 value"])
        redact(pdf, [r"/\d{3}-\d{2}-\d{4}/(opacity=0.5)"])
        page = pdf.pages[0]
        assert "/ExtGState" in page.Resources
        gstate_dicts = list(page.Resources.ExtGState.values())
        assert any(float(d.get("/ca", 1)) == 0.5 for d in gstate_dicts)

    def test_full_opacity_registers_no_ext_gstate(self):
        pdf = _make_text_pdf(["Secret 123-45-6789 value"])
        redact(pdf, [r"/\d{3}-\d{2}-\d{4}/(opacity=1.0)"])
        page = pdf.pages[0]
        assert "/ExtGState" not in page.Resources


class TestRedactLayer:
    def test_layer_option_creates_ocg_and_marked_content(self):
        pdf = _make_text_pdf(["Secret 123-45-6789 value"])
        redact(pdf, [r"/\d{3}-\d{2}-\d{4}/(layer=Redactions)"])

        assert "/OCProperties" in pdf.Root
        ocg_names = [str(g.Name) for g in pdf.Root.OCProperties.OCGs]
        assert "Redactions" in ocg_names

        page = pdf.pages[0]
        assert "/Properties" in page.Resources
        ops = [op for op, _ in _page_ops(pdf)]
        assert "BDC" in ops
        assert "EMC" in ops

    def test_no_layer_option_burns_directly_no_marked_content(self):
        pdf = _make_text_pdf(["Secret 123-45-6789 value"])
        redact(pdf, [r"/\d{3}-\d{2}-\d{4}/"])
        ops = [op for op, _ in _page_ops(pdf)]
        assert "BDC" not in ops
        assert "EMC" not in ops


class TestRedactMergeModes:
    def test_merge_none_draws_a_box_per_matched_line_fragment(self):
        """With merge=none, redact draws one box per raw match-line bbox
        rather than clustering them -- so a match whose glyphs split into
        multiple bboxes (kerning-driven line grouping) yields more boxes
        than merge=line would for the same input."""
        pdf = _make_text_pdf(["Secret 123-45-6789 value"])
        redact(pdf, [r"/\d{3}-\d{2}-\d{4}/(merge=none)"])
        none_rects = _fill_rects(pdf)

        pdf2 = _make_text_pdf(["Secret 123-45-6789 value"])
        redact(pdf2, [r"/\d{3}-\d{2}-\d{4}/(merge=line)"])
        line_rects = _fill_rects(pdf2)

        assert len(line_rects) <= len(none_rects)

    def test_invalid_merge_mode_raises(self):
        pdf = _make_text_pdf(["Secret 123-45-6789 value"])
        with pytest.raises(InvalidArgumentError):
            redact(pdf, [r"/\d{3}-\d{2}-\d{4}/(merge=bogus)"])


class TestRedactDoesNotMergeAcrossMatches:
    def test_same_token_on_two_lines_stays_two_independent_boxes(self):
        """Regression test: 'B' redacted on two separate, tightly-spaced
        lines ('A B' / 'B C') must never merge into a single box that
        spans both lines and swallows 'A' and 'C' -- merging is only
        valid WITHIN one match's own line-wrapped bboxes, never across
        two distinct matches."""
        pdf = _make_text_pdf(["A B", "B C"])  # normal line spacing
        redact(pdf, ["/B/"])

        strings = "".join(_tj_strings(pdf))
        assert "A" in strings  # must survive
        assert "C" in strings  # must survive

        rects = _fill_rects(pdf)
        assert len(rects) == 2  # one box per match, never fused into one
        # A merged box spanning both lines would be roughly line_gap (50pt)
        # taller than a single line's own ink height (well under 40pt for
        # a 24pt font) -- use that gap as a robust, metric-independent
        # signal that the two boxes stayed on their own separate lines
        # rather than pinning down exact pixel-level ink bounds.
        for rect in rects:
            assert rect[3] < 40.0  # box height, not [width]


# ---------------------------------------------------------------------------
# _find_options_part -- balanced-paren scanning edge cases
# ---------------------------------------------------------------------------


class TestFindOptionsPartDirect:
    def test_no_trailing_paren_returns_whole_string_unchanged(self):
        options_part, rest = _find_options_part("/foo/")
        assert options_part == ""
        assert rest == "/foo/"

    def test_trailing_paren_with_no_matching_open_paren(self):
        """Ends with ')' but has no '(' anywhere to balance against --
        split_pos never gets set, so the string must be returned
        unchanged rather than mis-splitting on nothing."""
        options_part, rest = _find_options_part("abc)")
        assert options_part == ""
        assert rest == "abc)"

    def test_balanced_options_block_is_split_off(self):
        options_part, rest = _find_options_part("/foo/(pad=1)")
        assert options_part == "(pad=1)"
        assert rest == "/foo/"

    def test_nested_parens_in_options_block_balanced_correctly(self):
        options_part, rest = _find_options_part("/foo/(color=1 0 0, layer=(nested))")
        assert options_part == "(color=1 0 0, layer=(nested))"
        assert rest == "/foo/"


# ---------------------------------------------------------------------------
# _ensure_ext_gstate / _register_layer_property -- name-collision loops
# ---------------------------------------------------------------------------


class TestEnsureExtGstateNameCollision:
    def test_second_registration_on_same_page_gets_suffixed_name(self):
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(100, 100))
        name1 = _ensure_ext_gstate(page, 0.5)
        name2 = _ensure_ext_gstate(page, 0.3)
        assert name1 == "RedactGS"
        assert name2 == "RedactGS1"
        assert name1 != name2


class TestRegisterLayerPropertyNameCollision:
    def test_second_registration_on_same_page_gets_suffixed_key(self):
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(100, 100))
        ocg1 = Dictionary({"/Type": Name.OCG, "/Name": "L1"})
        ocg2 = Dictionary({"/Type": Name.OCG, "/Name": "L2"})
        key1 = _register_layer_property(page, ocg1)
        key2 = _register_layer_property(page, ocg2)
        assert key1 == "RedactLayer"
        assert key2 == "RedactLayer1"
        assert key1 != key2


# ---------------------------------------------------------------------------
# _draw_page_boxes -- defensive empty-rects guard
# ---------------------------------------------------------------------------


class TestDrawPageBoxesEmptyRects:
    def test_empty_rects_list_is_a_noop(self):
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(100, 100))
        before = bytes(page.Contents.read_bytes()) if "/Contents" in page else b""
        _draw_page_boxes(pdf, page, [], RedactOptions({}), None)
        after = bytes(page.Contents.read_bytes()) if "/Contents" in page else b""
        assert before == after  # nothing appended


# ---------------------------------------------------------------------------
# _apply_redact_spec -- per-page "no match on this page" skip
# ---------------------------------------------------------------------------


class TestRedactSkipsPagesWithNoMatch:
    def test_multi_page_target_with_only_some_pages_matching(self):
        pdf = pikepdf.new()
        for text in ["Secret 123-45-6789 here", "Nothing sensitive on this page"]:
            page = pdf.add_blank_page(page_size=(WIDTH, HEIGHT))
            page.Resources = Dictionary(
                Font=Dictionary(
                    F1=Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)
                )
            )
            page.Contents = pdf.make_stream(
                f"BT\n/F1 18 Tf\n50 700 Td\n({text}) Tj\nET\n".encode("latin-1")
            )

        redact(pdf, [r"1-2/\d{3}-\d{2}-\d{4}/"])  # both pages targeted

        assert "123-45-6789" not in "".join(_tj_strings(pdf, 0))
        assert "Nothing sensitive on this page" in "".join(_tj_strings(pdf, 1))


# ---------------------------------------------------------------------------
# _match_target_spans -- capturing-group-aware redaction targeting
# ---------------------------------------------------------------------------


class TestMatchTargetSpansNoGroups:
    def test_no_groups_in_pattern_returns_whole_match(self):
        match = re.search(r"wombat", "the wombat sleeps")
        assert _match_target_spans(match) == [match.span()]


class TestMatchTargetSpansOtherNamedGroups:
    """A named group whose name doesn't start with 'redact' is NOT a
    narrowing signal. Since no redact*-prefixed group exists anywhere
    in the pattern, this behaves like tier 3 (whole match) -- same as
    if the group were unnamed or absent entirely."""

    def test_non_redact_named_group_present_still_redacts_whole_match(self):
        match = re.search(r"Patient: (?P<name>\w+ \w+), DOB", "Patient: John Smith, DOB")
        assert _match_target_spans(match) == [match.span()]


class TestMatchTargetSpansNumberedGroups:
    """Plain numbered groups are NOT a narrowing signal (see redact's
    long_desc) -- they're ignored entirely, and the whole match is
    always redacted regardless of what numbered groups exist or
    whether they participated."""

    def test_single_numbered_group_redacts_whole_match(self):
        match = re.search(r"the (wombat)", "the wombat sleeps")
        assert _match_target_spans(match) == [match.span()]

    def test_multiple_numbered_groups_redact_whole_match(self):
        match = re.search(r"(\w+)@(\w+)\.com", "contact user@example.com now")
        assert _match_target_spans(match) == [match.span()]

    def test_optional_numbered_group_not_participating_still_redacts_whole_match(self):
        """The exact bug this used to have: (A|B)?C-shaped patterns
        must never silently redact nothing just because a plain
        numbered group didn't fire."""
        match = re.search(r"wombat(x)?", "the wombat sleeps")
        assert match.group(1) is None  # group 1 did not participate
        assert _match_target_spans(match) == [match.span()]


class TestMatchTargetSpansNamedRedactGroups:
    def test_single_redact_prefixed_named_group_is_targeted(self):
        match = re.search(r"the (?P<redact_animal>wombat)", "the wombat sleeps")
        assert _match_target_spans(match) == [match.span("redact_animal")]

    def test_named_redact_groups_take_priority_over_numbered_groups(self):
        # both a plain numbered group and a redact*-prefixed named group
        # are present -- only the named one should be targeted
        match = re.search(
            r"(?P<keep>the) (?P<redact_animal>wombat) (sleeps)",
            "the wombat sleeps",
        )
        assert _match_target_spans(match) == [match.span("redact_animal")]

    def test_named_groups_not_prefixed_redact_redact_whole_match(self):
        # only plain-named group present, no 'redact' prefix, and no
        # redact*-named group anywhere in the pattern -- whole match.
        match = re.search(r"the (?P<animal>wombat)", "the wombat sleeps")
        assert _match_target_spans(match) == [match.span()]

    def test_multiple_redact_prefixed_groups_all_targeted(self):
        match = re.search(
            r"(?P<redact_user>\w+)@(?P<redact_domain>\w+)\.com",
            "contact user@example.com now",
        )
        spans = _match_target_spans(match)
        assert spans == [match.span("redact_user"), match.span("redact_domain")]

    def test_non_participating_redact_group_in_alternation_is_skipped(self):
        match = re.search(
            r"(?P<redact_a>foo)|the (?P<redact_b>wombat)",
            "the wombat sleeps",
        )
        assert _match_target_spans(match) == [match.span("redact_b")]

    def test_all_redact_groups_non_participating_redacts_nothing(self):
        """If a pattern HAS redact*-named groups but none of them
        participate in this particular match, NOTHING is redacted for
        this match -- deliberately no whole-match fallback, since
        redact* naming is an explicit opt-in to fine-grained targeting."""
        match = re.search(r"(?P<redact_a>foo)|(?P<redact_b>bar)|baz", "baz")
        assert match.group("redact_a") is None
        assert match.group("redact_b") is None
        assert _match_target_spans(match) == []

    def test_keep_style_named_group_firing_alone_redacts_nothing(self):
        """An explicit 'keep' branch of an alternation must not be
        swept into any fallback -- the pattern-level 'has a redact*
        group' check still applies even though THIS match's
        participating group isn't the redact* one."""
        match = re.search(r"(?P<redact_ssn>\d{3}-\d{2}-\d{4})|(?P<keep>N/A)", "value: N/A")
        assert match.group("keep") == "N/A"
        assert match.group("redact_ssn") is None
        assert _match_target_spans(match) == []


# ---------------------------------------------------------------------------
# redact end-to-end -- capturing-group-narrowed redaction
# ---------------------------------------------------------------------------


class TestRedactCapturingGroupsEndToEnd:
    def test_numbered_group_no_longer_narrows_redacts_whole_match(self):
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(WIDTH, HEIGHT))
        page.Resources = Dictionary(
            Font=Dictionary(
                F1=Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)
            )
        )
        page.Contents = pdf.make_stream(b"BT\n/F1 18 Tf\n50 700 Td\n(the wombat sleeps) Tj\nET\n")

        redact(pdf, [r"/the (wombat)/"])

        remaining = "".join(_tj_strings(pdf, 0))
        assert "wombat" not in remaining
        assert "the" not in remaining  # whole match ("the wombat") is gone now
        assert "sleeps" in remaining

    def test_named_redact_group_takes_priority_over_numbered_group(self):
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(WIDTH, HEIGHT))
        page.Resources = Dictionary(
            Font=Dictionary(
                F1=Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)
            )
        )
        page.Contents = pdf.make_stream(b"BT\n/F1 18 Tf\n50 700 Td\n(the wombat sleeps) Tj\nET\n")

        # numbered group (sleeps) would be redacted too under plain
        # numbered-group handling, but the named redact_animal group
        # should take priority and be the ONLY thing targeted
        redact(pdf, [r"/the (?P<redact_animal>wombat) (sleeps)/"])

        remaining = "".join(_tj_strings(pdf, 0))
        assert "wombat" not in remaining
        assert "the" in remaining
        assert "sleeps" in remaining

    def test_no_groups_redacts_whole_match_as_before(self):
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(WIDTH, HEIGHT))
        page.Resources = Dictionary(
            Font=Dictionary(
                F1=Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)
            )
        )
        page.Contents = pdf.make_stream(b"BT\n/F1 18 Tf\n50 700 Td\n(the wombat sleeps) Tj\nET\n")

        redact(pdf, [r"/the wombat/"])

        remaining = "".join(_tj_strings(pdf, 0))
        assert "the wombat" not in remaining
        assert "sleeps" in remaining

    def test_non_participating_optional_group_still_redacts_whole_match(self):
        """Regression test for the (A|B)?C silent-no-op bug: a plain
        numbered group that doesn't participate must still redact the
        whole match, not silently do nothing."""
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(WIDTH, HEIGHT))
        page.Resources = Dictionary(
            Font=Dictionary(
                F1=Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)
            )
        )
        page.Contents = pdf.make_stream(b"BT\n/F1 18 Tf\n50 700 Td\n(the wombat sleeps) Tj\nET\n")

        redact(pdf, [r"/wombat(x)?/"])

        remaining = "".join(_tj_strings(pdf, 0))
        assert "wombat" not in remaining  # now correctly redacted as whole match


# ---------------------------------------------------------------------------
# _match_target_spans -- group-based partial-match targeting (unit level)
# ---------------------------------------------------------------------------


class TestMatchTargetSpans:
    def test_no_groups_targets_whole_match(self):
        import re

        m = re.match(r"\d+", "12345")
        assert _match_target_spans(m) == [m.span()]

    def test_non_capturing_group_only_targets_whole_match(self):
        import re

        m = re.match(r"(?:foo|bar)baz", "foobaz")
        assert _match_target_spans(m) == [m.span()]

    def test_plain_numbered_group_no_longer_narrows_targets_whole_match(self):
        import re

        m = re.match(r"Patient: (\w+ \w+)", "Patient: John Smith")
        assert _match_target_spans(m) == [m.span()]

    def test_multiple_plain_numbered_groups_still_target_whole_match(self):
        import re

        m = re.match(r"(\w+)-(\w+)", "foo-bar")
        assert _match_target_spans(m) == [m.span()]

    def test_named_redact_group_wins_over_plain_groups(self):
        import re

        m = re.match(r"(?P<context>Patient): (?P<redact>\w+ \w+)", "Patient: John Smith")
        assert _match_target_spans(m) == [m.span("redact")]

    def test_multiple_named_redact_groups_both_targeted(self):
        import re

        m = re.match(r"(?P<redact_first>\w+) (?P<redact_last>\w+)", "John Smith")
        assert set(_match_target_spans(m)) == {
            m.span("redact_first"),
            m.span("redact_last"),
        }

    def test_non_participating_group_from_alternation_skipped(self):
        import re

        m = re.match(r"(?P<redact>foo)|(?P<redact2>bar)", "bar")
        assert _match_target_spans(m) == [m.span("redact2")]


# ---------------------------------------------------------------------------
# End-to-end: group-based partial-match redaction
# ---------------------------------------------------------------------------


class TestRedactGroupTargets:
    def test_plain_group_no_longer_narrows_redacts_whole_match(self):
        pdf = _make_text_pdf(["Patient: John Smith, DOB 1990-01-01"])
        redact(pdf, [r"/Patient: (\w+ \w+), DOB/"])
        strings = "".join(_tj_strings(pdf))
        assert "John Smith" not in strings
        assert "Patient:" not in strings  # whole match now gone
        assert "DOB" not in strings
        assert "1990-01-01" in strings

    def test_named_redact_group_redacts_only_that_group(self):
        pdf = _make_text_pdf(["Patient: John Smith, DOB 1990-01-01"])
        redact(pdf, [r"/Patient: (?P<redact>\w+ \w+), DOB/"])
        strings = "".join(_tj_strings(pdf))
        assert "John Smith" not in strings
        assert "Patient:" in strings
        assert "DOB" in strings

    def test_two_named_redact_groups_produce_two_boxes(self):
        pdf = _make_text_pdf(["Name: John Smith SSN: 123-45-6789 done"])
        redact(
            pdf,
            [r"/Name: (?P<redact_name>\w+ \w+) SSN: (?P<redact_ssn>\d{3}-\d{2}-\d{4})/"],
        )
        strings = "".join(_tj_strings(pdf))
        assert "John Smith" not in strings
        assert "123-45-6789" not in strings
        assert "Name:" in strings
        assert "SSN:" in strings
        assert "done" in strings
        assert len(_fill_rects(pdf)) == 2

    def test_non_capturing_group_does_not_change_whole_match_behavior(self):
        """A pattern using (?:...) purely for alternation, with no
        capturing groups at all, must still redact the whole match --
        non-capturing groups are not redaction targets and must not be
        mistaken for "no groups exist so redact everything" in a way
        that only redacts part of it."""
        pdf = _make_text_pdf(["Status: (?:CONFIDENTIAL|SECRET) marker"])
        redact(pdf, [r"/(?:CONFIDENTIAL|SECRET)/"])
        strings = "".join(_tj_strings(pdf))
        assert "CONFIDENTIAL" not in strings
        assert "Status:" in strings
        assert "marker" in strings

    def test_mixed_named_and_plain_groups_named_takes_priority(self):
        pdf = _make_text_pdf(["Name: John Smith SSN: 123-45-6789 done"])
        redact(
            pdf,
            [r"/Name: (?P<redact>\w+ \w+) SSN: (\d{3}-\d{2}-\d{4})/"],
        )
        strings = "".join(_tj_strings(pdf))
        # Only the named `redact` group's text should be gone --
        # the plain numbered group (SSN) is treated as context here,
        # since a redact* group is present so tier-1 rules apply.
        assert "John Smith" not in strings
        assert "123-45-6789" in strings
        assert "Name:" in strings
        assert "SSN:" in strings


class TestRedactNonParticipatingRedactGroupSkipsMatch:
    def test_keep_style_alternation_leaves_non_target_branch_untouched(self):
        """End-to-end version of the 'keep' example from redact's
        long_desc: when only the non-redact* branch of an alternation
        fires, that match must be left completely alone."""
        pdf = _make_text_pdf(["Status: N/A on file"])
        redact(pdf, [r"/(?P<redact_ssn>\d{3}-\d{2}-\d{4})|(?P<keep>N\/A)/"])
        strings = "".join(_tj_strings(pdf))
        assert "N/A" in strings
        assert "Status:" in strings
        assert "on file" in strings


class TestRedactNonRedactNamedGroupEndToEnd:
    def test_plain_named_group_without_redact_prefix_redacts_whole_match(self):
        pdf = _make_text_pdf(["Patient: John Smith, DOB 1990-01-01"])
        redact(pdf, [r"/Patient: (?P<name>\w+ \w+), DOB/"])
        strings = "".join(_tj_strings(pdf))
        assert "John Smith" not in strings
        assert "Patient:" not in strings  # whole match gone, "name" isn't redact*
        assert "DOB" not in strings
        assert "1990-01-01" in strings


# ---------------------------------------------------------------------------
# _collect_raw_match_rects -- zero-width target span is skipped, not fatal
# ---------------------------------------------------------------------------


class TestCollectRawMatchRectsZeroWidthSpan:
    def test_zero_width_group_span_is_skipped(self):
        """The only capturing group can legally match zero characters
        (e.g. `x*`); its (start, end) span is then start == end, and
        must be skipped rather than handed to get_bboxes_for_lines with
        a degenerate range."""
        tp = MagicMock()
        tp.get_text.return_value = "ab"
        search_regex = re.compile(r"a(?P<redact_x>x*)b")
        groups = _collect_raw_match_rects(tp, 0, search_regex)
        assert groups == []
        tp.get_bboxes_for_lines.assert_not_called()


# ---------------------------------------------------------------------------
# _box_fill_snippet -- degenerate (zero/negative area) rects are skipped
# ---------------------------------------------------------------------------


class TestBoxFillSnippetDegenerateRects:
    def test_zero_width_rect_produces_no_fill_op(self):
        result = _box_fill_snippet([[0, 0, 0, 10], [0, 0, 5, 5]], opacity=1.0, gs_name=None)
        assert result.count(" re f") == 1  # only the valid rect got drawn


# ---------------------------------------------------------------------------
# _ensure_ext_gstate / _register_layer_property -- creating /Resources
# from scratch when the page has none at all yet
# ---------------------------------------------------------------------------


class TestEnsureExtGstateCreatesResources:
    def test_creates_resources_dict_when_absent(self):
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(100, 100))
        if "/Resources" in page:
            del page.Resources
        assert "/Resources" not in page
        name = _ensure_ext_gstate(page, 0.5)
        assert name == "RedactGS"
        assert "/Resources" in page
        assert "/ExtGState" in page.Resources


class TestRegisterLayerPropertyCreatesResources:
    def test_creates_resources_dict_when_absent(self):
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(100, 100))
        if "/Resources" in page:
            del page.Resources
        ocg = Dictionary({"/Type": Name.OCG, "/Name": "L1"})
        key = _register_layer_property(page, ocg)
        assert key == "RedactLayer"
        assert "/Resources" in page
        assert "/Properties" in page.Resources


# ---------------------------------------------------------------------------
# _apply_redact_spec -- page spec resolving to no target pages at all
# ---------------------------------------------------------------------------


class TestRedactNoTargetPages:
    def test_page_spec_beyond_document_length_is_a_noop(self):
        pdf = _make_text_pdf(["Some text 123-45-6789"])  # 1-page doc
        result = redact(pdf, [r"5/\d{3}-\d{2}-\d{4}/"])  # page 5 doesn't exist
        assert result.success is True
        strings = "".join(_tj_strings(pdf))
        assert "123-45-6789" in strings  # untouched


# ---------------------------------------------------------------------------
# verify= option -- post-deletion re-scan
# ---------------------------------------------------------------------------


class TestRedactVerifyOption:
    def test_verify_true_passes_when_redaction_actually_succeeded(self):
        """The normal, successful case: verify=true must not raise when
        the matched text really was deleted."""
        pdf = _make_text_pdf(["Hello World SSN 123-45-6789 end"])
        result = redact(pdf, [r"/\d{3}-\d{2}-\d{4}/(verify=true)"])
        assert result.success is True
        strings = "".join(_tj_strings(pdf))
        assert "123-45-6789" not in strings

    def test_verify_false_default_does_not_raise_or_rescan(self):
        """Default behavior is unchanged: no verification pass at all."""
        pdf = _make_text_pdf(["Hello World SSN 123-45-6789 end"])
        result = redact(pdf, [r"/\d{3}-\d{2}-\d{4}/"])
        assert result.success is True

    def test_verify_true_raises_when_deletion_did_not_remove_the_match(self):
        """Direct reproduction of a genuine verification failure,
        without relying on redact's own targeting logic (which now
        always deletes SOMETHING for any matching pattern -- see
        _match_target_spans): patch excise's page processor to a no-op
        so the search finds a match but nothing was actually deleted."""
        from unittest.mock import patch

        pdf = _make_text_pdf(["Hello World SSN 123-45-6789 end"])
        with patch("pdftl.operations.excise._process_page", lambda *a, **kw: None):
            with pytest.raises(OperationError, match="verification failed"):
                redact(pdf, [r"/\d{3}-\d{2}-\d{4}/(verify=true)"])

    def test_verify_failure_message_excludes_matched_text(self):
        """The error message must report page number/length only, never
        the actual matched (sensitive) text."""
        from unittest.mock import patch

        pdf = _make_text_pdf(["Hello World SSN 123-45-6789 end"])
        with patch("pdftl.operations.excise._process_page", lambda *a, **kw: None):
            with pytest.raises(OperationError) as exc_info:
                redact(pdf, [r"/\d{3}-\d{2}-\d{4}/(verify=true)"])
        assert "123-45-6789" not in str(exc_info.value)
        assert "page 1" in str(exc_info.value)

    def test_verify_redaction_direct_no_failures_is_a_noop(self):
        """Unit-level: calling _verify_redaction directly against a page
        with no remaining matches must not raise."""
        pdf = _make_text_pdf(["Nothing sensitive here"])
        search_regex = re.compile(r"\d{3}-\d{2}-\d{4}")
        _verify_redaction(pdf, [1], search_regex)  # should not raise

    def test_verify_redaction_direct_multiple_page_failures_all_listed(self):
        """Unit-level: failures across more than one page must all be
        collected and reported, not just the first."""
        pdf = pikepdf.new()
        for text in ["digits 111-11-1111 here", "digits 222-22-2222 here"]:
            page = pdf.add_blank_page(page_size=(WIDTH, HEIGHT))
            page.Resources = Dictionary(
                Font=Dictionary(
                    F1=Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)
                )
            )
            page.Contents = pdf.make_stream(
                f"BT\n/F1 18 Tf\n50 700 Td\n({text}) Tj\nET\n".encode("latin-1")
            )
        search_regex = re.compile(r"\d{3}-\d{2}-\d{4}")
        with pytest.raises(OperationError, match="page 1.*page 2|page 2.*page 1"):
            _verify_redaction(pdf, [1, 2], search_regex)
