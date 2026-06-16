from unittest.mock import MagicMock, patch
from pdftl.utils.pdf_text.text_diff_calc import (
    _tokenize_to_chars,
    compute_diff_chunks,
    _get_page_hit_data,
    _extract_hit_data,
    _build_change_entry,
    process_diff_stream,
)


def test_tokenize_to_chars():
    text_a = "hello world"
    text_b = "hello earth"
    pattern = r"(\W+)"

    enc_a, enc_b, tokens = _tokenize_to_chars(text_a, text_b, pattern)

    assert tokens[0] == "hello"
    assert tokens[1] == " "
    assert tokens[2] == "world"
    assert tokens[3] == "earth"

    # Check that repeated tokens resolve to the same encoded character
    assert enc_a == "\x00\x01\x02"
    assert enc_b == "\x00\x01\x03"


@patch("diff_match_patch.diff_match_patch")
def test_compute_diff_chunks(MockDMP):
    mock_dmp = MockDMP.return_value

    # 1. Granularity = char, soft hyphens = True
    mock_dmp.diff_main.return_value = [(0, "test")]
    chunks = compute_diff_chunks("te\ufffest", "test", "char", ignore_soft_hyphens=True)
    assert chunks == [(0, "test")]
    mock_dmp.diff_main.assert_called_with("test", "test")

    # 2. Granularity = word, de-anchor logic
    # Create a scenario where an empty space is sandwiched between two changes
    raw_diffs = [(-1, "delete"), (0, " "), (1, "insert")]
    mock_dmp.diff_main.return_value = raw_diffs

    chunks2 = compute_diff_chunks("delete", "insert", "word", ignore_soft_hyphens=False)

    # The (0, " ") should have been transformed into a nested list of delete/insert
    assert len(chunks2) == 4
    assert chunks2[0] == (-1, "delete")
    assert chunks2[1] == (-1, " ")
    assert chunks2[2] == (1, " ")
    assert chunks2[3] == (1, "insert")


def test_get_page_hit_data():
    mapper = MagicMock()
    mapper.global_text = {0: "a\n c"}
    mapper.margins = {"top": 10}
    mapper.page_sizes = {0: (100, 100)}

    # Mock text provider bbox returns
    def mock_get_bboxes(p, start, end):
        if start == 0:  # 'a' (Valid box)
            return [[10, 10, 20, 20]]
        elif start == 2:  # ' ' (Filtered by margin)
            return [[5, 95, 10, 100]]
        elif start == 3:  # 'c' (No area)
            return [[50, 50, 50, 50]]
        return []

    mapper.tp.get_bboxes_for_lines.side_effect = mock_get_bboxes
    mapper._is_inside_margin.side_effect = lambda box, w, h: box[1] > 90

    # Execute
    res = _get_page_hit_data(mapper, 0, 0, 4, merge=False)

    # 'a' is kept, '\n' is kept but no box, ' ' is in margin (skipped), 'c' has no area box (box ignored)
    assert res is not None
    assert res["page"] == 1
    assert res["text"] == "a\nc"
    assert res["bboxes"] == [[10, 10, 20, 20]]

    # Empty text case
    mapper.global_text = {0: "   "}
    res_empty = _get_page_hit_data(mapper, 0, 0, 3, merge=False)
    assert res_empty is None


@patch("pdftl.utils.pdf_text.text_diff_calc._get_page_hit_data")
def test_extract_hit_data(mock_get_hit):
    mapper = MagicMock()
    mapper.resolve_span.return_value = [(0, 0, 5)]
    mock_get_hit.return_value = {"page": 1, "text": "hello", "bboxes": []}

    res = _extract_hit_data(mapper, 0, 5, True)
    assert len(res) == 1
    assert res[0]["text"] == "hello"


def test_build_change_entry():
    hits = [{"page": 1, "text": "test", "bboxes": [[0, 0, 1, 1]]}]

    # Insert with bboxes
    res1 = _build_change_entry(1, hits, include_bboxes=True)
    assert res1["type"] == "insert"
    assert res1["file_a"] is None
    assert res1["file_b"] == hits
    assert "bboxes" in res1["file_b"][0]

    # Delete without bboxes
    hits_copy = [{"page": 1, "text": "test", "bboxes": [[0, 0, 1, 1]]}]
    res2 = _build_change_entry(-1, hits_copy, include_bboxes=False)
    assert res2["type"] == "delete"
    assert res2["file_a"] == hits_copy
    assert res2["file_b"] is None
    assert "bboxes" not in res2["file_a"][0]


@patch("pdftl.utils.pdf_text.text_diff_calc._extract_hit_data")
def test_process_diff_stream(mock_extract):
    mapper_a = MagicMock()
    mapper_b = MagicMock()

    # Diffs: EQUAL, DELETE (whitespace), INSERT (content)
    diffs = [(0, "skip"), (-1, "   "), (1, "added")]

    mock_extract.return_value = [{"page": 1, "text": "added", "bboxes": []}]

    # Run with ignore_whitespace = True
    output = process_diff_stream(
        diffs, mapper_a, mapper_b, ignore_whitespace=True, include_bboxes=True, merge_bboxes=False
    )

    # op=0 is skipped. op=-1 is skipped (ignore whitespace). op=1 is processed.
    assert len(output) == 1
    assert output[0]["type"] == "insert"
    assert output[0]["file_b"][0]["text"] == "added"

    # Ensure chunk lengths updated correctly
    # file_b offset should be len("skip") = 4 at the time of calling extract
    mock_extract.assert_called_with(mapper_b, 4, 9, False)


def test_compute_diff_chunks_soft_hyphens_integration():
    """
    Unmocked test to strictly verify that soft hyphens (\\ufffe) and
    soft hyphen linebreaks (\\ufffe\\n) are physically stripped before DMP comparison.
    """
    # \ufffe\n simulates a word broken across a line by a hyphen
    text_a = "under\ufffe\nstand\ufffeing"
    text_b = "understanding"

    # Path 1: ignore_soft_hyphens=True
    # The strings should equalize completely, resulting in a single EQUAL (0) chunk
    chunks_ignored = compute_diff_chunks(
        text_a, text_b, granularity="char", ignore_soft_hyphens=True
    )
    assert len(chunks_ignored) == 1
    assert chunks_ignored[0] == (0, "understanding")

    # Path 2: ignore_soft_hyphens=False
    # The engine should flag the \ufffe and \n as deletions (-1)
    chunks_kept = compute_diff_chunks(
        text_a, text_b, granularity="char", ignore_soft_hyphens=False
    )
    assert len(chunks_kept) > 1

    # Verify the diff actually caught the soft hyphens as removed text
    deleted_text = "".join([text for op, text in chunks_kept if op == -1])
    assert "\ufffe\n" in deleted_text or "\ufffe" in deleted_text
