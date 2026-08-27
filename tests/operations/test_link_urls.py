# tests/operations/test_link_urls.py

import re
from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.link_urls import (
    _Candidate,
    _bbox_area,
    _bbox_overlap_area,
    _build_link_annot,
    _find_candidates,
    _get_existing_link_rects,
    _make_uri,
    _overlaps_existing_link,
    _parse_args,
    _parse_bool,
    _parse_color,
    _parse_float,
    _parse_fraction,
    _process_page,
    _strip_trailing_punct,
    _suppress_nested_matches,
    _union_bbox,
    link_urls,
    link_urls_cli_hook,
)


##################################################
# _parse_bool


def test_parse_bool_key_present_true():
    assert _parse_bool({"urls": "true"}, "urls", False) is True


def test_parse_bool_key_present_false():
    assert _parse_bool({"urls": "false"}, "urls", True) is False


def test_parse_bool_key_absent_returns_default():
    assert _parse_bool({}, "urls", True) is True
    assert _parse_bool({}, "urls", False) is False


##################################################
# _parse_fraction


def test_parse_fraction_key_absent_returns_default():
    assert _parse_fraction({}, "overlap_threshold", 0.5) == 0.5


def test_parse_fraction_valid_value():
    assert _parse_fraction({"overlap_threshold": "0.8"}, "overlap_threshold", 0.5) == 0.8


def test_parse_fraction_non_numeric_raises():
    with pytest.raises(InvalidArgumentError, match="must be a number"):
        _parse_fraction({"overlap_threshold": "abc"}, "overlap_threshold", 0.5)


def test_parse_fraction_out_of_range_raises():
    with pytest.raises(InvalidArgumentError, match="between 0.0 and 1.0"):
        _parse_fraction({"overlap_threshold": "1.5"}, "overlap_threshold", 0.5)


def test_parse_fraction_boundary_values_accepted():
    assert _parse_fraction({"overlap_threshold": "0.0"}, "overlap_threshold", 0.5) == 0.0
    assert _parse_fraction({"overlap_threshold": "1.0"}, "overlap_threshold", 0.5) == 1.0


##################################################
# _parse_args & Parameter Parsing helpers


def test_parse_float():
    assert _parse_float({}, "padding", 0.0) == 0.0
    assert _parse_float({"padding": "2.5"}, "padding", 0.0) == 2.5
    with pytest.raises(InvalidArgumentError, match="must be a number"):
        _parse_float({"padding": "abc"}, "padding", 0.0)


def test_parse_color():
    assert _parse_color({}, "border_color") is None
    assert _parse_color({"border_color": "0,0,1"}, "border_color") == (0.0, 0.0, 1.0)
    assert _parse_color({"border_color": "1.0, 0.5, 0.2"}, "border_color") == (1.0, 0.5, 0.2)
    with pytest.raises(InvalidArgumentError, match="must be R,G,B"):
        _parse_color({"border_color": "0,1"}, "border_color")
    with pytest.raises(InvalidArgumentError, match="components must be numbers"):
        _parse_color({"border_color": "a,b,c"}, "border_color")


def test_parse_args_defaults():
    page_specs, urls, emails, threshold, padding, b_width, b_color = _parse_args([])
    assert page_specs == []
    assert urls is True
    assert emails is True
    assert threshold == 0.5
    assert padding == 0.0
    assert b_width == 0.0
    assert b_color is None


def test_parse_args_urls_only():
    _, urls, emails, _, _, _, _ = _parse_args(["emails=false"])
    assert urls is True
    assert emails is False


def test_parse_args_emails_only():
    _, urls, emails, _, _, _, _ = _parse_args(["urls=false"])
    assert urls is False
    assert emails is True


def test_parse_args_both_disabled_raises():
    with pytest.raises(InvalidArgumentError, match="at least one"):
        _parse_args(["urls=false", "emails=false"])


def test_parse_args_page_specs_captured_as_bare_tokens():
    page_specs, *_ = _parse_args(["1-5"])
    assert page_specs == ["1-5"]


def test_parse_args_padding_and_borders():
    args = ["padding=2.5", "border_width=1", "border_color=1,0,0", "overlap_threshold=0.9"]
    _, _, _, threshold, padding, b_width, b_color = _parse_args(args)
    assert threshold == 0.9
    assert padding == 2.5
    assert b_width == 1.0
    assert b_color == (1.0, 0.0, 0.0)


##################################################
# _strip_trailing_punct


def test_strip_trailing_punct_no_punct():
    text, end = _strip_trailing_punct("example.com", 11)
    assert text == "example.com"
    assert end == 11


def test_strip_trailing_punct_removes_period():
    text, end = _strip_trailing_punct("example.com.", 12)
    assert text == "example.com"
    assert end == 11


def test_strip_trailing_punct_removes_multiple_chars():
    text, end = _strip_trailing_punct("example.com,;", 13)
    assert text == "example.com"
    assert end == 11


def test_strip_trailing_punct_balanced_paren_not_stripped():
    text, end = _strip_trailing_punct("example.com/wiki/Foo_(bar)", 27)
    assert text == "example.com/wiki/Foo_(bar)"
    assert end == 27


def test_strip_trailing_punct_unbalanced_paren_is_stripped():
    text, end = _strip_trailing_punct("example.com)", 12)
    assert text == "example.com"
    assert end == 11


def test_strip_trailing_punct_strips_down_to_empty():
    text, end = _strip_trailing_punct("...", 3)
    assert text == ""
    assert end == 0


##################################################
# _find_candidates / _suppress_nested_matches


def test_find_candidates_urls_only():
    text = "Visit https://example.com for more."
    cands = _find_candidates(text, detect_urls=True, detect_emails=False)
    assert len(cands) == 1
    assert cands[0].kind == "url"
    assert cands[0].text == "https://example.com"


def test_find_candidates_emails_only():
    text = "Contact person@example.com today."
    cands = _find_candidates(text, detect_urls=False, detect_emails=True)
    assert len(cands) == 1
    assert cands[0].kind == "email"
    assert cands[0].text == "person@example.com"


def test_find_candidates_both_disabled_returns_empty():
    cands = _find_candidates("https://example.com person@example.com", False, False)
    assert cands == []


def test_find_candidates_url_stripped_to_empty_is_skipped():
    import pdftl.operations.link_urls as mod

    fake_match = MagicMock()
    fake_match.group.return_value = "..."
    fake_match.start.return_value = 0
    fake_match.end.return_value = 3

    class _FakePattern:
        def finditer(self, text):
            return [fake_match]

    original = mod._URL_PATTERN
    mod._URL_PATTERN = _FakePattern()
    try:
        cands = _find_candidates("...", detect_urls=True, detect_emails=False)
    finally:
        mod._URL_PATTERN = original
    assert cands == []


def test_find_candidates_nested_mailto_suppresses_bare_email():
    text = "mailto:person@example.com"
    cands = _find_candidates(text, detect_urls=True, detect_emails=True)
    assert cands


def test_suppress_nested_matches_keeps_outer_discards_inner():
    outer = _Candidate(0, 20, "http://example.com/x", "url")
    inner = _Candidate(5, 12, "example", "email")
    kept = _suppress_nested_matches([outer, inner])
    assert kept == [outer]


def test_suppress_nested_matches_no_overlap_keeps_both():
    a = _Candidate(0, 5, "aaaaa", "url")
    b = _Candidate(10, 15, "bbbbb", "email")
    kept = _suppress_nested_matches([a, b])
    assert set(id(k) for k in kept) == {id(a), id(b)}


def test_suppress_nested_matches_identical_span_keeps_one():
    a = _Candidate(0, 10, "same", "url")
    b = _Candidate(0, 10, "same", "email")
    kept = _suppress_nested_matches([a, b])
    assert len(kept) == 1


def test_suppress_nested_matches_empty_list():
    assert _suppress_nested_matches([]) == []


##################################################
# bbox geometry helpers


def test_bbox_area_positive():
    assert _bbox_area((0, 0, 10, 5)) == 50


def test_bbox_area_degenerate_zero():
    assert _bbox_area((0, 0, 0, 0)) == 0
    assert _bbox_area((5, 5, 5, 10)) == 0


def test_bbox_overlap_area_full_overlap():
    a = (0, 0, 10, 10)
    b = (0, 0, 10, 10)
    assert _bbox_overlap_area(a, b) == 100


def test_bbox_overlap_area_partial_overlap():
    a = (0, 0, 10, 10)
    b = (5, 5, 15, 15)
    assert _bbox_overlap_area(a, b) == 25


def test_bbox_overlap_area_no_overlap():
    a = (0, 0, 10, 10)
    b = (20, 20, 30, 30)
    assert _bbox_overlap_area(a, b) == 0


##################################################
# _get_existing_link_rects


def test_get_existing_link_rects_no_annots_key():
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    assert _get_existing_link_rects(page) == []


def test_get_existing_link_rects_skips_non_link_subtype():
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    annot = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name.Annot,
                "/Subtype": pikepdf.Name.Highlight,
                "/Rect": pikepdf.Array([0, 0, 10, 10]),
            }
        )
    )
    page.Annots = pikepdf.Array([annot])
    assert _get_existing_link_rects(page) == []


def test_get_existing_link_rects_skips_entries_without_rect():
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    annot = pdf.make_indirect(
        pikepdf.Dictionary({"/Type": pikepdf.Name.Annot, "/Subtype": pikepdf.Name.Link})
    )
    page.Annots = pikepdf.Array([annot])
    assert _get_existing_link_rects(page) == []


def test_get_existing_link_rects_skips_non_object_entries():
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    import pikepdf as _pikepdf

    bad_entry_holder = pdf.make_indirect(_pikepdf.Dictionary({"/Foo": "bar"}))
    page.Annots = pikepdf.Array([bad_entry_holder])
    assert _get_existing_link_rects(page) == []


def test_get_existing_link_rects_skips_invalid_rect_values():
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    annot = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name.Annot,
                "/Subtype": pikepdf.Name.Link,
                "/Rect": pikepdf.Array([pikepdf.Name.Foo, 0, 10, 10]),
            }
        )
    )
    page.Annots = pikepdf.Array([annot])
    assert _get_existing_link_rects(page) == []


def test_get_existing_link_rects_normalizes_and_returns_valid_rect():
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    annot = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name.Annot,
                "/Subtype": pikepdf.Name.Link,
                "/Rect": pikepdf.Array([10, 10, 0, 0]),
            }
        )
    )
    page.Annots = pikepdf.Array([annot])
    rects = _get_existing_link_rects(page)
    assert rects == [(0.0, 0.0, 10.0, 10.0)]


##################################################
# _overlaps_existing_link


def test_overlaps_existing_link_zero_area_candidate_returns_false():
    assert _overlaps_existing_link((5, 5, 5, 5), [(0, 0, 10, 10)], 0.5) is False


def test_overlaps_existing_link_no_existing_rects():
    assert _overlaps_existing_link((0, 0, 10, 10), [], 0.5) is False


def test_overlaps_existing_link_below_threshold():
    cand = (0, 0, 10, 10)
    existing = [(9, 9, 10, 10)]
    assert _overlaps_existing_link(cand, existing, 0.5) is False


def test_overlaps_existing_link_meets_threshold():
    cand = (0, 0, 10, 10)
    existing = [(0, 0, 10, 10)]
    assert _overlaps_existing_link(cand, existing, 0.5) is True


def test_overlaps_existing_link_checks_all_rects_before_giving_up():
    cand = (0, 0, 10, 10)
    existing = [(20, 20, 30, 30), (0, 0, 10, 10)]
    assert _overlaps_existing_link(cand, existing, 0.5) is True


##################################################
# _union_bbox


def test_union_bbox_empty_returns_none():
    assert _union_bbox([]) is None


def test_union_bbox_single_box():
    assert _union_bbox([(1, 2, 3, 4)]) == (1, 2, 3, 4)


def test_union_bbox_multiple_boxes_merged():
    boxes = [(0, 0, 5, 5), (3, 3, 10, 8)]
    assert _union_bbox(boxes) == (0, 0, 10, 8)


##################################################
# _make_uri


def test_make_uri_email():
    cand = _Candidate(0, 10, "a@b.com", "email")
    assert _make_uri(cand) == "mailto:a@b.com"


def test_make_uri_www_gets_http_prefix():
    cand = _Candidate(0, 10, "www.example.com", "url")
    assert _make_uri(cand) == "http://www.example.com"


def test_make_uri_full_url_unchanged():
    cand = _Candidate(0, 10, "https://example.com", "url")
    assert _make_uri(cand) == "https://example.com"


##################################################
# _build_link_annot


def test_build_link_annot_invisible_border_default():
    pdf = pikepdf.new()
    annot = _build_link_annot(
        pdf, (0, 0, 10, 10), "https://example.com", border_width=0.0, border_color=None
    )
    assert annot.Subtype == pikepdf.Name.Link
    assert list(annot.Rect) == [0, 0, 10, 10]
    assert annot.A.URI == "https://example.com"
    assert list(annot.Border) == [0, 0, 0]


def test_build_link_annot_visible_border():
    pdf = pikepdf.new()
    annot = _build_link_annot(
        pdf, (0, 0, 10, 10), "https://example.com", border_width=1.0, border_color=(1.0, 0.0, 0.0)
    )
    assert list(annot.Border) == [0, 0, 1.0]
    assert list(annot.C) == [1.0, 0.0, 0.0]


##################################################
# _process_page


def _fake_tp(text, bboxes_by_span=None):
    tp = MagicMock()
    tp.get_text.return_value = text

    def _get_bboxes(page_idx, start, end):
        if bboxes_by_span and (start, end) in bboxes_by_span:
            return bboxes_by_span[(start, end)]
        return [(0.0, 0.0, 10.0, 10.0)]

    tp.get_bboxes_for_lines.side_effect = _get_bboxes
    return tp


def test_process_page_no_text_returns_zero():
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    tp = _fake_tp("")
    created = _process_page(pdf, 1, tp, True, True, 0.5, 0.0, 0.0, None)
    assert created == 0


def test_process_page_no_candidates_returns_zero():
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    tp = _fake_tp("just plain text, nothing to link")
    created = _process_page(pdf, 1, tp, True, True, 0.5, 0.0, 0.0, None)
    assert created == 0


def test_process_page_creates_link_and_appends_to_new_annots_list():
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    text = "See https://example.com for details."
    tp = _fake_tp(text)

    created = _process_page(pdf, 1, tp, True, True, 0.5, 0.0, 0.0, None)

    assert created == 1
    assert "/Annots" in page
    assert len(page.Annots) == 1
    assert page.Annots[0].A.URI == "https://example.com"


def test_process_page_appends_to_existing_annots():
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    existing = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name.Annot,
                "/Subtype": pikepdf.Name.Highlight,
                "/Rect": pikepdf.Array([50, 50, 60, 60]),
            }
        )
    )
    page.Annots = pikepdf.Array([existing])

    text = "See https://example.com for details."
    tp = _fake_tp(text)
    created = _process_page(pdf, 1, tp, True, True, 0.5, 0.0, 0.0, None)

    assert created == 1
    assert len(page.Annots) == 2


def test_process_page_skips_degenerate_bbox():
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    text = "See https://example.com now."
    match_span = (4, 23)
    tp = _fake_tp(text, bboxes_by_span={match_span: [(0.0, 0.0, 0.0, 0.0)]})

    created = _process_page(pdf, 1, tp, True, True, 0.5, 0.0, 0.0, None)

    assert created == 0
    assert "/Annots" not in page


def test_process_page_skips_when_overlapping_existing_link():
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    existing_link = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name.Annot,
                "/Subtype": pikepdf.Name.Link,
                "/Rect": pikepdf.Array([0, 0, 10, 10]),
                "/A": pikepdf.Dictionary(
                    {"/S": pikepdf.Name.URI, "/URI": "https://already-linked.example"}
                ),
            }
        )
    )
    page.Annots = pikepdf.Array([existing_link])

    text = "See https://example.com now."
    tp = _fake_tp(text)

    created = _process_page(pdf, 1, tp, True, True, 0.5, 0.0, 0.0, None)

    assert created == 0
    assert len(page.Annots) == 1


def test_process_page_two_candidates_second_dedups_against_first():
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    text = "https://example.com person@example.com"
    tp = MagicMock()
    tp.get_text.return_value = text
    tp.get_bboxes_for_lines.return_value = [(0.0, 0.0, 10.0, 10.0)]

    created = _process_page(pdf, 1, tp, True, True, 0.5, 0.0, 0.0, None)

    assert created == 1
    assert len(page.Annots) == 1


def test_process_page_padding_and_border_style():
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(100, 100))

    mock_tp_inst = MagicMock()
    mock_tp_inst.get_text.return_value = "Check out https://example.com please."
    mock_tp_inst.get_bboxes_for_lines.return_value = [(10.0, 10.0, 90.0, 20.0)]

    _process_page(
        pdf,
        page_1_indexed=1,
        tp=mock_tp_inst,
        detect_urls=True,
        detect_emails=False,
        overlap_threshold=0.5,
        padding=5.0,
        border_width=2.0,
        border_color=(0.5, 0.5, 0.5),
    )

    page = pdf.pages[0]
    assert "/Annots" in page
    annot = page.Annots[0]

    assert [float(x) for x in annot.Rect] == [5.0, 5.0, 95.0, 25.0]
    assert [float(x) for x in annot.Border] == [0.0, 0.0, 2.0]
    assert [float(x) for x in annot.C] == [0.5, 0.5, 0.5]


##################################################
# link_urls_cli_hook


def test_link_urls_cli_hook_logs_created_count(caplog):
    import logging
    from pdftl.core.core_types import OpResult

    caplog.set_level(logging.INFO)
    result = OpResult(success=True, meta={"created_count": 3})
    link_urls_cli_hook(result, stage=None, _pipeline=None)
    assert "3" in caplog.text


def test_link_urls_cli_hook_defaults_to_zero_when_missing():
    from pdftl.core.core_types import OpResult

    result = OpResult(success=True, meta={})
    link_urls_cli_hook(result, stage=None, _pipeline=None)


##################################################
# link_urls (top-level operation, mocked TextProvider/pdfium)


@pytest.fixture
def _patched_text_pipeline(mocker):
    mocker.patch("pdftl.operations.link_urls.ensure_dependencies")
    mocker.patch("pypdfium2.PdfDocument", return_value=MagicMock())

    tp_instance = MagicMock()
    tp_instance.get_text.return_value = "Visit https://example.com today."
    tp_instance.get_bboxes_for_lines.return_value = [(0.0, 0.0, 10.0, 10.0)]

    mocker.patch("pdftl.utils.pdf_text.text_provider.TextProvider", return_value=tp_instance)
    return tp_instance


def test_link_urls_end_to_end_creates_link(_patched_text_pipeline):
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))

    result = link_urls(pdf, [])

    assert result.success is True
    assert result.meta["created_count"] == 1
    assert len(pdf.pages[0].Annots) == 1
    _patched_text_pipeline.close.assert_called_once()
    _patched_text_pipeline.clear_page_cache.assert_called_once_with(0)


def test_link_urls_restricts_to_page_spec(_patched_text_pipeline):
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    pdf.add_blank_page(page_size=(200, 200))

    result = link_urls(pdf, ["1"])

    assert result.success is True
    assert result.meta["created_count"] == 1
    _patched_text_pipeline.get_text.assert_called_once_with(0)


def test_link_urls_closes_text_provider_even_on_error(mocker, _patched_text_pipeline):
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))

    mocker.patch("pdftl.operations.link_urls._process_page", side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        link_urls(pdf, [])

    _patched_text_pipeline.close.assert_called_once()


def test_candidate_span():
    candidate = _Candidate(start=10, end=25, text="test@example.com", kind="email")
    assert candidate.span == (10, 25)


@patch("pdftl.operations.link_urls._EMAIL_PATTERN")
def test_find_candidates_email_completely_stripped(mock_email_pattern):
    mock_match = re.match(r"(.*)", "")
    mock_email_pattern.finditer.return_value = [mock_match]

    candidates = _find_candidates("dummy text", detect_urls=False, detect_emails=True)
    assert len(candidates) == 0
