# tests/operations/test_style_text.py

import pytest
import pikepdf
from unittest.mock import MagicMock, patch, call
from pdftl.exceptions import InvalidArgumentError
from pdftl.core.core_types import OpResult

# Import the module components under test
from pdftl.operations.style_text import (
    style_text_in_content_streams,
    _get_color_or_raise,
    _parse_style_text_args,
    _build_replacer,
    _apply_to_pages,
    TextStrokeReplaceContentStream,
)


# ==============================================================================
# 1. Tests for _parse_style_text_args
# ==============================================================================


def test_parse_style_text_args_empty():
    """No args → apply defaults to all pages."""
    result = _parse_style_text_args([])
    assert result == [("-", {})]


def test_parse_style_text_args_options_only():
    """Bare-paren form (no selector) → all-pages spec with parsed kwargs."""
    result = _parse_style_text_args(["(stroke=0.5,color=0 0 0)"])
    assert len(result) == 1
    page_spec, kwargs = result[0]
    assert page_spec == "-"
    assert kwargs["stroke"] == "0.5"
    assert kwargs["color"] == "0 0 0"


def test_parse_style_text_args_with_selector():
    """Selector(opts) form → correct page spec and kwargs."""
    result = _parse_style_text_args(["1-3(stroke=1.5,stroke_color=1 0 0)"])
    assert len(result) == 1
    page_spec, kwargs = result[0]
    assert page_spec == "1-3"
    assert kwargs["stroke"] == "1.5"
    assert kwargs["stroke_color"] == "1 0 0"


def test_parse_style_text_args_multiple_specs():
    """Multiple spec blocks produce multiple (page_spec, kwargs) pairs."""
    result = _parse_style_text_args(["1-3(stroke=0.5)", "4-end(stroke=2%)"])
    assert len(result) == 2
    assert result[0] == ("1-3", {"stroke": "0.5"})
    assert result[1] == ("4-end", {"stroke": "2%"})


def test_parse_style_text_args_no_parens_fallback():
    """A plain token with no parens (shouldn't occur after framework expansion, but
    handled gracefully) → treated as page spec with empty kwargs."""
    result = _parse_style_text_args(["odd"])
    assert result == [("odd", {})]


def test_parse_style_text_args_empty_parens():
    """Empty parens () → all-pages spec with empty kwargs."""
    result = _parse_style_text_args(["()"])
    page_spec, kwargs = result[0]
    assert page_spec == "-"
    assert kwargs == {}


# ==============================================================================
# 2. Tests for _build_replacer & Intent Resolution
# ==============================================================================


def test_build_replacer_returns_none_when_nothing_set():
    """No meaningful options → returns None (nothing to apply)."""
    mock_pdf = MagicMock()
    assert _build_replacer(mock_pdf, {}) is None


def test_build_replacer_absolute_stroke():
    r = _build_replacer(MagicMock(), {"stroke": "1.5"})
    assert r is not None
    assert r.stroke_width == 1.5
    assert r.stroke_width_type == "absolute"
    assert r.has_stroke_intent is True
    assert r.has_fill_intent is False


def test_build_replacer_percentage_stroke():
    r = _build_replacer(MagicMock(), {"stroke": "2%"})
    assert r is not None
    assert r.stroke_width == 2.0
    assert r.stroke_width_type == "percentage"
    assert r.has_stroke_intent is True


def test_build_replacer_stroke_color_defaults_stroke_width():
    """stroke_color without explicit stroke → stroke defaults to 0.5."""
    r = _build_replacer(MagicMock(), {"stroke_color": "1 0 0"})
    assert r is not None
    assert r.stroke_width == 0.5
    assert r.stroke_color == [1.0, 0.0, 0.0]
    assert r.has_stroke_intent is True


def test_build_replacer_color_sets_fill_and_stroke():
    r = _build_replacer(MagicMock(), {"stroke": "0.5", "color": "0 0 1"})
    assert r.has_fill_intent is True
    assert r.has_stroke_intent is True
    assert r.fill_color == [0.0, 0.0, 1.0]
    assert r.stroke_color == [0.0, 0.0, 1.0]


def test_build_replacer_fill_and_stroke_separate():
    r = _build_replacer(
        MagicMock(), {"stroke": "0.5", "fill_color": "1 0 0", "stroke_color": "0 1 0"}
    )
    assert r.has_fill_intent is True
    assert r.has_stroke_intent is True
    assert r.fill_color == [1.0, 0.0, 0.0]
    assert r.stroke_color == [0.0, 1.0, 0.0]


def test_build_replacer_invalid_stroke_raises():
    with pytest.raises(InvalidArgumentError, match="Invalid stroke width"):
        _build_replacer(MagicMock(), {"stroke": "not_a_number"})


def test_build_replacer_negative_stroke_raises():
    with pytest.raises(InvalidArgumentError):
        _build_replacer(MagicMock(), {"stroke": "-1.5"})


def test_build_replacer_fill_color_only_sets_intent_correctly():
    """Providing only fill_color identifies fill intent without stroke intent."""
    r = _build_replacer(MagicMock(), {"fill_color": "1 0 0"})
    assert r is not None
    assert r.fill_color == [1.0, 0.0, 0.0]
    assert r.has_fill_intent is True
    assert r.has_stroke_intent is False
    assert r._get_target_tr_mode(3) == 0  # Tr 3 (invisible) becomes Tr 0 (filled)
    assert r._get_target_tr_mode(2) == 2  # Tr 2 (fill+stroke) remains Tr 2


def test_target_tr_mode_mutation_matrix():
    """Explicitly verify the mutation matrix behaviors for Tr states."""
    # Test fill intent isolated
    r_fill = TextStrokeReplaceContentStream(pdf=MagicMock(), has_fill_intent=True)
    assert r_fill._get_target_tr_mode(0) == 0
    assert r_fill._get_target_tr_mode(1) == 2
    assert r_fill._get_target_tr_mode(2) == 2
    assert r_fill._get_target_tr_mode(3) == 0

    # Test stroke intent isolated
    r_stroke = TextStrokeReplaceContentStream(pdf=MagicMock(), has_stroke_intent=True)
    assert r_stroke._get_target_tr_mode(0) == 2
    assert r_stroke._get_target_tr_mode(1) == 1
    assert r_stroke._get_target_tr_mode(2) == 2
    assert r_stroke._get_target_tr_mode(3) == 1

    # Test compound intents (both)
    r_both = TextStrokeReplaceContentStream(
        pdf=MagicMock(), has_fill_intent=True, has_stroke_intent=True
    )
    assert r_both._get_target_tr_mode(0) == 2
    assert r_both._get_target_tr_mode(3) == 2


# ==============================================================================
# 3. Tests for Main Entry Point
# ==============================================================================


def test_style_text_in_content_streams_no_args():
    """No args → succeeds, calls _apply_to_pages with all-pages spec."""
    mock_pdf = MagicMock()
    mock_pdf.pages = [MagicMock(), MagicMock()]

    with patch("pdftl.operations.style_text._apply_to_pages") as mock_apply:
        result = style_text_in_content_streams(mock_pdf, [])
        assert isinstance(result, OpResult)
        assert result.success is True
        # No-op: _build_replacer({}) returns None, so _apply_to_pages not called
        mock_apply.assert_not_called()


def test_style_text_in_content_streams_with_expanded_args():
    """OPERATION_ARGS_EXPANDED form: selector(opts) → correct page spec applied."""
    mock_pdf = MagicMock()
    mock_pdf.pages = [MagicMock()]

    with patch("pdftl.operations.style_text._apply_to_pages") as mock_apply:
        style_text_in_content_streams(mock_pdf, ["1-2(stroke=1.5,color=0 0 1)"])
        assert mock_apply.call_count == 1
        page_spec = mock_apply.call_args[0][1]
        replacer = mock_apply.call_args[0][2]
        assert page_spec == "1-2"
        assert replacer.stroke_width == 1.5
        assert replacer.fill_color == [0.0, 0.0, 1.0]
        assert replacer.has_fill_intent is True
        assert replacer.has_stroke_intent is True


def test_style_text_in_content_streams_multiple_specs():
    """Multiple spec blocks → _apply_to_pages called once per spec."""
    mock_pdf = MagicMock()
    mock_pdf.pages = [MagicMock()]

    with patch("pdftl.operations.style_text._apply_to_pages") as mock_apply:
        style_text_in_content_streams(mock_pdf, ["1-3(stroke=0.5)", "4-end(stroke=2%)"])
        assert mock_apply.call_count == 2
        assert mock_apply.call_args_list[0][0][1] == "1-3"
        assert mock_apply.call_args_list[1][0][1] == "4-end"


def test_style_text_in_content_streams_returns_pdf():
    mock_pdf = MagicMock()
    mock_pdf.pages = []
    result = style_text_in_content_streams(mock_pdf, [])
    assert result.pdf is mock_pdf


# ==============================================================================
# 4. Tests for _apply_to_pages
# ==============================================================================


def test_apply_to_pages_calls_replacer_for_matching_pages():
    mock_pdf = MagicMock()
    mock_pdf.pages = [MagicMock(), MagicMock(), MagicMock()]
    mock_replacer = MagicMock()

    with patch(
        "pdftl.operations.style_text.page_numbers_matching_page_spec",
        return_value=[1, 3],
    ) as mock_matching:
        _apply_to_pages(mock_pdf, "1,3", mock_replacer)
        mock_matching.assert_called_once_with("1,3", 3)
        assert mock_replacer.apply.call_count == 2
        mock_replacer.apply.assert_has_calls([call(1), call(3)])


# ==============================================================================
# 5. Tests for Color Helper Function
# ==============================================================================


@pytest.mark.parametrize(
    "data, key, expected",
    [
        ({"color": "0 0 0"}, "color", [0.0, 0.0, 0.0]),
        ({"fill_color": "0.5 0.2 0.1 0.0"}, "fill_color", [0.5, 0.2, 0.1, 0.0]),
        ({"stroke_color": "1"}, "stroke_color", [1.0]),
        ({}, "missing_key", None),
    ],
)
def test_get_color_or_raise_success(data, key, expected):
    """Ensure valid colors map to flat float lists and missing keys return None."""
    assert _get_color_or_raise(data, key) == expected


@pytest.mark.parametrize(
    "data, key",
    [
        ({"color": "red"}, "color"),
        ({"color": "1,abc,3"}, "color"),
        ({"color": 123}, "color"),
    ],
)
def test_get_color_or_raise_failure(data, key):
    """Ensure malformed color inputs raise an InvalidArgumentError."""
    with pytest.raises(InvalidArgumentError) as exc_info:
        _get_color_or_raise(data, key)
    assert "Invalid color provided for" in str(exc_info.value)


# ==============================================================================
# 6. Tests for TextStrokeReplaceContentStream Mechanics
# ==============================================================================


@pytest.fixture
def base_replacer():
    """Provides a baseline TextStrokeReplaceContentStream instance."""
    mock_pdf = MagicMock()
    replacer = TextStrokeReplaceContentStream(
        pdf=mock_pdf,
        has_fill_intent=True,
        has_stroke_intent=True,
        fill_color=[0.1, 0.2, 0.3],
        stroke_color=[0.4, 0.5, 0.6],
        stroke_width=2.0,
        stroke_width_type="absolute",
    )
    return replacer


def test_pikepdf_property_lazy_load():
    """Verify property imports pikepdf dynamically if cached copy is None."""
    mock_pdf = MagicMock()
    replacer = TextStrokeReplaceContentStream(pdf=mock_pdf)
    assert replacer._pikepdf is None
    assert replacer.pikepdf is pikepdf
    assert replacer._pikepdf is pikepdf


def test_color_instruction_generation(base_replacer):
    """Test generating operators for Gray, RGB, and CMYK structural elements."""
    assert base_replacer._color_instruction([0.5], "fill") == ([0.5], "g")
    assert base_replacer._color_instruction([0.5], "stroke") == ([0.5], "G")
    assert base_replacer._color_instruction([1, 0, 0], "fill") == ([1, 0, 0], "rg")
    assert base_replacer._color_instruction([1, 0, 0], "stroke") == ([1, 0, 0], "RG")
    assert base_replacer._color_instruction([0, 0, 0, 1], "fill") == ([0, 0, 0, 1], "k")
    assert base_replacer._color_instruction([0, 0, 0, 1], "stroke") == ([0, 0, 0, 1], "K")

    with pytest.raises(ValueError):
        base_replacer._color_instruction([1, 2, 3, 4, 5])


def test_colors_to_list_empty_operands(base_replacer):
    """Verify that empty operands safely yield empty lists."""
    assert base_replacer._colors_to_list(None, 4) == []
    assert base_replacer._colors_to_list([], 4) == []


def test_get_absolute_stroke_width(base_replacer):
    """Verify stroke calculation under both absolute and percentage modes."""
    state = {"font_size": 10.0}

    base_replacer.stroke_width_type = "absolute"
    base_replacer.stroke_width = 1.5
    assert base_replacer._get_absolute_stroke_width(state) == 1.5

    base_replacer.stroke_width_type = "percentage"
    base_replacer.stroke_width = 10.0
    assert base_replacer._get_absolute_stroke_width(state) == 1.0

    state_no_font = {"font_size": None}
    assert base_replacer._get_absolute_stroke_width(state_no_font) == 12.0 * 10.0 / 100


def test_state_matches_desired_granularity():
    """Verify specific branch returns when target states do or do not match."""
    replacer = TextStrokeReplaceContentStream(
        pdf=MagicMock(),
        has_fill_intent=True,
        has_stroke_intent=True,
        stroke_width=1.0,
        stroke_color=[1, 1, 1],
        fill_color=[0, 0, 0],
    )

    assert (
        replacer._state_matches_desired(
            {
                "render_mode": 0,  # Will target Tr 2
                "stroke_width": 1.0,
                "stroke_color": [1, 1, 1],
                "fill_color": [0, 0, 0],
            }
        )
        is False
    ), "render_mode mismatch should return False"

    assert (
        replacer._state_matches_desired(
            {
                "render_mode": 2,
                "stroke_width": 9.9,
                "stroke_color": [1, 1, 1],
                "fill_color": [0, 0, 0],
            }
        )
        is False
    ), "stroke_width mismatch should return False"

    assert (
        replacer._state_matches_desired(
            {
                "render_mode": 2,
                "stroke_width": 1.0,
                "stroke_color": [0, 0, 0],
                "fill_color": [0, 0, 0],
            }
        )
        is False
    ), "stroke_color mismatch should return False"

    assert (
        replacer._state_matches_desired(
            {
                "render_mode": 2,
                "stroke_width": 1.0,
                "stroke_color": [1, 1, 1],
                "fill_color": [1, 1, 1],
            }
        )
        is False
    ), "fill_color mismatch should return False"

    assert (
        replacer._state_matches_desired(
            {
                "render_mode": 2,
                "stroke_width": 1.0,
                "stroke_color": [1, 1, 1],
                "fill_color": [0, 0, 0],
            }
        )
        is True
    ), "all matching should return True"


def test_force_style_state_injection(base_replacer):
    """Verify correct operator combinations are appended when state is out of sync."""
    current_state = {
        "render_mode": 0,
        "stroke_width": 0.0,
        "stroke_color": [0.0, 0.0, 0.0],
        "fill_color": [0.0, 0.0, 0.0],
    }
    new_instructions = []
    base_replacer._force_style_state(new_instructions, current_state)

    assert ([0.1, 0.2, 0.3], "rg") in new_instructions
    assert ([0.4, 0.5, 0.6], "RG") in new_instructions
    assert ([2.0], "w") in new_instructions
    assert ([2], "Tr") in new_instructions  # Based on Tr 0 -> Tr 2 mutation


# ==============================================================================
# 7. Tests for State Management Engine & Instruction Processing
# ==============================================================================


def test_process_instructions_stack_and_operators(base_replacer):
    """Test graphics state push/pop tracking and operator intercept updates."""
    instructions = [
        ([], "q"),
        (["/F1", 12], "Tf"),
        ([1], "Tr"),
        ([1.0, 0.0, 0.0], "rg"),
        ([0.5, 0.5, 0.5, 0.0], "K"),
        ([3.0], "w"),
        ([], "Q"),
    ]

    new_insts_bytes = base_replacer._process_instructions(instructions)
    assert isinstance(new_insts_bytes, bytes)

    with pikepdf.new() as pdf:
        dummy_stream = pikepdf.Stream(pdf, new_insts_bytes)
        parsed_insts = list(pikepdf.parse_content_stream(dummy_stream))

    assert len(parsed_insts) == len(instructions)
    assert parsed_insts[0][1] == pikepdf.Operator("q")


def test_process_op_color_spaces(base_replacer):
    """Exercise alternate color space operator processing blocks."""
    state = {}

    base_replacer._process_op("k", [0.1, 0.2, 0.3, 0.4], state, [], [])
    assert state["fill_color"] == [0.1, 0.2, 0.3, 0.4]

    base_replacer._process_op("G", [0.7], state, [], [])
    assert state["stroke_color"] == [0.7]

    base_replacer._process_op("g", [0.2], state, [], [])
    assert state["fill_color"] == [0.2]

    base_replacer._process_op("RG", [1.0, 0.5, 0.0], state, [], [])
    assert state["stroke_color"] == [1.0, 0.5, 0.0]


def test_update_state_full_coverage():
    """Verify _update_state updates all fields inside the state map."""
    replacer = TextStrokeReplaceContentStream(
        pdf=MagicMock(),
        has_fill_intent=True,
        has_stroke_intent=True,
        fill_color=[0.1],
        stroke_color=[0.2],
        stroke_width=1.5,
    )
    state = {"font_size": 12.0, "render_mode": 0}
    replacer._update_state(state)

    assert state["fill_color"] == [0.1]
    assert state["stroke_color"] == [0.2]
    assert state["stroke_width"] == 1.5
    assert state["render_mode"] == 2  # State should mutate 0 -> 2


def test_process_op_text_triggering(base_replacer):
    """Verify styling elements are injected right before text draw operators."""
    state = {
        "font": "/F1",
        "font_size": 12.0,
        "render_mode": 0,
        "stroke_color": [0, 0, 0],
        "stroke_width": 0.0,
        "fill_color": [0, 0, 0],
    }
    stack = []
    new_instructions = []

    with (
        patch.object(base_replacer, "_state_matches_desired", return_value=False) as mock_match,
        patch.object(base_replacer, "_force_style_state") as mock_force,
    ):
        base_replacer._process_op("Tj", ["Hello"], state, stack, new_instructions)

        mock_match.assert_called_once_with(state)
        mock_force.assert_called_once_with(new_instructions, state)


# ==============================================================================
# 8. Real pikepdf Stream Processing & Form XObject Recursion Tests
# ==============================================================================


def test_apply_page_with_no_contents(base_replacer):
    """Ensure safe completion when processing a page with no contents."""
    mock_page = MagicMock()
    mock_page.get.return_value = None
    base_replacer.pdf.pages = [mock_page]

    base_replacer.apply(1)


def test_apply_single_stream_and_processed_filtering_real_objects():
    """Test processing unique streams, skipping visited ones, using real pikepdf structures."""
    with pikepdf.new() as pdf:
        page = pdf.add_blank_page()

        contents = page.get("/Contents")
        assert contents is not None
        assert not isinstance(contents, pikepdf.Array)
        assert isinstance(contents, pikepdf.Stream)

        mock_pdf_wrapper = MagicMock()
        mock_pdf_wrapper.pages = [page]

        replacer = TextStrokeReplaceContentStream(
            pdf=mock_pdf_wrapper,
            has_stroke_intent=True,
            fill_color=[0, 0, 0],
            stroke_color=[1, 1, 1],
            stroke_width=1.0,
        )

        stream_objgen = contents.objgen

        with patch.object(replacer, "_process_resources") as mock_res:
            replacer.apply(1)
            assert stream_objgen in replacer._processed
            mock_res.assert_called_once()

        with (
            patch.object(replacer, "_process_resources") as mock_res,
            patch("pikepdf.parse_content_stream") as mock_parse,
        ):
            replacer.apply(1)
            mock_parse.assert_not_called()
            # Second call is skipped entirely once stream is in _processed —
            # _process_resources is not called either.
            mock_res.assert_not_called()


def test_apply_page_with_multiple_streams_real_objects():
    """Verify multiple content streams are coalesced and the result is processed."""
    with pikepdf.new() as pdf:
        page = pdf.add_blank_page()

        stream1 = pikepdf.Stream(pdf, b"1 0 0 rg /F1 12 Tf (Stream 1) Tj")
        stream2 = pikepdf.Stream(pdf, b"0 1 0 rg (Stream 2) Tj")

        array_contents = pikepdf.Array([stream1, stream2])
        page["/Contents"] = array_contents

        mock_pdf_wrapper = MagicMock()
        mock_pdf_wrapper.pages = [page]

        replacer = TextStrokeReplaceContentStream(
            pdf=mock_pdf_wrapper,
            has_fill_intent=True,
            has_stroke_intent=True,
            fill_color=[0, 0, 1],
            stroke_color=[1, 0, 0],
            stroke_width=2.0,
        )

        replacer.apply(1)

        # After coalescing, /Contents is a single new stream — not an array.
        new_contents = page.get("/Contents")
        assert new_contents is not None
        assert not isinstance(new_contents, pikepdf.Array), (
            "contents_coalesce() should have replaced the array with a single stream"
        )

        # The coalesced stream's objgen is tracked so a second apply() call
        # on the same page is skipped correctly.
        assert new_contents.objgen in replacer._processed

        # Verify skipping works on second call.
        with patch("pikepdf.parse_content_stream") as mock_parse:
            replacer.apply(1)
            mock_parse.assert_not_called()


def test_process_resources_already_processed_xobject_real_objects():
    """Verify resource loops skip XObjects if their objgen is already tracked."""
    with pikepdf.new() as pdf:
        xobj_stream = pikepdf.Stream(pdf, b"(Form Content) Tj")
        xobj_stream["/Subtype"] = pikepdf.Name("/Form")

        resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/X1": xobj_stream})})

        replacer = TextStrokeReplaceContentStream(pdf=MagicMock())
        replacer._processed.add(xobj_stream.objgen)

        with patch("pikepdf.parse_content_stream") as mock_parse:
            replacer._process_resources(resources)
            mock_parse.assert_not_called()


def test_process_resources_recurs_into_form_xobjects_real_objects(base_replacer):
    """Verify recursive lookups step into nested Form XObjects."""
    with pikepdf.new() as pdf:
        nested_form = pikepdf.Stream(pdf, b"(Nested content) Tj")
        nested_form["/Subtype"] = pikepdf.Name("/Form")
        nested_form["/Resources"] = pikepdf.Dictionary()

        resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/Form1": nested_form})})

        with (
            patch.object(base_replacer, "_process_instructions") as mock_proc,
            patch.object(
                base_replacer, "_process_resources", wraps=base_replacer._process_resources
            ) as spy_res,
        ):
            mock_proc.return_value = b"Mocked Bytes Injection"

            base_replacer._process_resources(resources)

            mock_proc.assert_called_once()
            assert nested_form.objgen in base_replacer._processed
            assert spy_res.call_count == 2


def test_process_op_named_colorspace_marks_fill_unknown(base_replacer):
    """cs/scn/sc operators mark fill_color as None (unknown) so the next
    text operator forces re-injection even if fill was previously matching."""
    state = {"fill_color": [0.0, 0.0, 0.0], "stroke_color": [0.0, 0.0, 0.0]}

    base_replacer._process_op("cs", [], state, [], [])
    assert state["fill_color"] is None, "cs should mark fill_color unknown"

    state["fill_color"] = [1.0, 0.0, 0.0]
    base_replacer._process_op("scn", [], state, [], [])
    assert state["fill_color"] is None, "scn should mark fill_color unknown"

    state["fill_color"] = [0.5, 0.5, 0.5]
    base_replacer._process_op("sc", [], state, [], [])
    assert state["fill_color"] is None, "sc should mark fill_color unknown"


def test_process_op_named_colorspace_marks_stroke_unknown(base_replacer):
    """CS/SCN/SC operators mark stroke_color as None (unknown)."""
    state = {"fill_color": [0.0, 0.0, 0.0], "stroke_color": [0.0, 0.0, 0.0]}

    base_replacer._process_op("CS", [], state, [], [])
    assert state["stroke_color"] is None, "CS should mark stroke_color unknown"

    state["stroke_color"] = [1.0, 0.0, 0.0]
    base_replacer._process_op("SCN", [], state, [], [])
    assert state["stroke_color"] is None, "SCN should mark stroke_color unknown"

    state["stroke_color"] = [0.5, 0.5, 0.5]
    base_replacer._process_op("SC", [], state, [], [])
    assert state["stroke_color"] is None, "SC should mark stroke_color unknown"


def test_process_op_named_colorspace_forces_injection_before_text():
    """After cs/scn, _state_matches_desired returns False even if the desired
    fill_color matches the PDF default, because state fill_color is now None.
    This ensures text following a named colorspace gets the fill override injected."""
    replacer = TextStrokeReplaceContentStream(
        pdf=MagicMock(),
        has_fill_intent=True,
        fill_color=[0.0, 0.0, 0.0],  # matches PDF default
    )
    state = {
        "font": "/F1",
        "font_size": 12.0,
        "render_mode": 0,
        "stroke_color": [0.0, 0.0, 0.0],
        "stroke_width": 0.0,
        "fill_color": [0.0, 0.0, 0.0],  # currently matches desired
    }

    # Before cs: state matches desired, no injection
    assert replacer._state_matches_desired(state) is True

    # After cs: fill_color becomes None → no longer matches → injection fires
    replacer._process_op("cs", [], state, [], [])
    assert state["fill_color"] is None
    assert replacer._state_matches_desired(state) is False


def test_fill_only_intent_does_not_touch_existing_stroke():
    """Regression test: when only fill_color is requested (no stroke intent),
    and the current text is already Tr 2 (fill+stroke), the existing stroke_color
    and stroke_width must be left untouched. Only fill_color should be affected.

    This guards against the stroke branches in _state_matches_desired,
    _force_style_state, and _update_state firing whenever target_tr_mode
    happens to land in (1, 2), regardless of has_stroke_intent.
    """
    replacer = TextStrokeReplaceContentStream(
        pdf=MagicMock(),
        has_fill_intent=True,
        has_stroke_intent=False,
        fill_color=[0.9, 0.9, 0.9],
        # stroke_color/stroke_width may still be populated by _build_replacer's
        # fallback logic even though the user never asked to touch stroke.
        stroke_color=[0.0, 0.0, 0.0],
        stroke_width=0.5,
        stroke_width_type="absolute",
    )

    state = {
        "render_mode": 2,  # already fill+stroke
        "fill_color": [0.1, 0.2, 0.3],
        "stroke_color": [0.4, 0.5, 0.6],  # pre-existing, deliberately different
        "stroke_width": 3.0,  # pre-existing, deliberately different
        "font_size": 12.0,
    }

    # Sanity: fill differs so state should NOT already match desired.
    assert replacer._state_matches_desired(state) is False

    new_instructions = []
    replacer._force_style_state(new_instructions, state)

    # Fill color change should be injected.
    assert ([0.9, 0.9, 0.9], "rg") in new_instructions

    # Stroke color/width must NOT be touched — no stroke intent was expressed.
    assert ([0.4, 0.5, 0.6], "RG") not in new_instructions
    assert ([0.0, 0.0, 0.0], "RG") not in new_instructions
    assert ([3.0], "w") not in new_instructions
    assert ([0.5], "w") not in new_instructions

    replacer._update_state(state)

    # State's stroke fields should remain exactly as they were before.
    assert state["stroke_color"] == [0.4, 0.5, 0.6]
    assert state["stroke_width"] == 3.0
    # Fill should have updated, render_mode stays 2.
    assert state["fill_color"] == [0.9, 0.9, 0.9]
    assert state["render_mode"] == 2


# ==============================================================================
# Additional coverage: stroke-only intent, symmetric state matching,
# and Tr-mode transitions from modes 1 and 3
# ==============================================================================


def test_stroke_only_intent_does_not_touch_existing_fill():
    """Mirror of the fill-only regression test: when only stroke_color/width
    is requested (no fill intent), pre-existing fill_color in state must be
    left completely untouched, even though target_tr_mode may land on 1 or 2.
    """
    replacer = TextStrokeReplaceContentStream(
        pdf=MagicMock(),
        has_fill_intent=False,
        has_stroke_intent=True,
        fill_color=None,
        stroke_color=[0.9, 0.1, 0.1],
        stroke_width=1.25,
        stroke_width_type="absolute",
    )

    state = {
        "render_mode": 0,  # currently filled only; stroke intent -> Tr 2
        "fill_color": [0.2, 0.3, 0.4],  # pre-existing, must not change
        "stroke_color": [0.0, 0.0, 0.0],
        "stroke_width": 0.0,
        "font_size": 10.0,
    }

    assert replacer._state_matches_desired(state) is False

    new_instructions = []
    replacer._force_style_state(new_instructions, state)

    # Stroke changes should be injected.
    assert ([0.9, 0.1, 0.1], "RG") in new_instructions
    assert ([1.25], "w") in new_instructions
    assert ([2], "Tr") in new_instructions

    # Fill must NOT be touched — no fill intent expressed.
    assert ([0.2, 0.3, 0.4], "rg") not in new_instructions
    assert not any(instr[1] == "rg" for instr in new_instructions)

    replacer._update_state(state)

    assert state["fill_color"] == [0.2, 0.3, 0.4]  # untouched
    assert state["stroke_color"] == [0.9, 0.1, 0.1]
    assert state["stroke_width"] == 1.25
    assert state["render_mode"] == 2


def test_state_matches_desired_true_when_only_relevant_intent_matches():
    """If only fill intent is set and fill already matches, the state should
    be considered matching regardless of any differing stroke values —
    proving _state_matches_desired doesn't spuriously key off stroke state
    when has_stroke_intent is False."""
    replacer = TextStrokeReplaceContentStream(
        pdf=MagicMock(),
        has_fill_intent=True,
        has_stroke_intent=False,
        fill_color=[0.5, 0.5, 0.5],
        stroke_color=[0.0, 0.0, 0.0],  # fallback value, irrelevant here
        stroke_width=0.5,
    )

    state = {
        "render_mode": 2,  # fill intent alone keeps Tr 2 at Tr 2
        "fill_color": [0.5, 0.5, 0.5],  # matches
        "stroke_color": [0.9, 0.9, 0.9],  # deliberately different
        "stroke_width": 9.0,  # deliberately different
        "font_size": 12.0,
    }

    assert replacer._state_matches_desired(state) is True

    # Symmetric case: only stroke intent, stroke matches, fill differs.
    replacer2 = TextStrokeReplaceContentStream(
        pdf=MagicMock(),
        has_fill_intent=False,
        has_stroke_intent=True,
        fill_color=None,
        stroke_color=[0.2, 0.2, 0.2],
        stroke_width=0.75,
    )
    state2 = {
        "render_mode": 2,
        "fill_color": [0.1, 0.1, 0.1],  # irrelevant, differs
        "stroke_color": [0.2, 0.2, 0.2],  # matches
        "stroke_width": 0.75,  # matches
        "font_size": 12.0,
    }
    assert replacer2._state_matches_desired(state2) is True


def test_fill_intent_from_invisible_mode_3():
    """Tr 3 (invisible) + fill intent only -> target Tr 0. Stroke branch must
    not fire even though state's current render_mode is not 0/2."""
    replacer = TextStrokeReplaceContentStream(
        pdf=MagicMock(),
        has_fill_intent=True,
        has_stroke_intent=False,
        fill_color=[0.7, 0.1, 0.1],
        stroke_color=[0.0, 0.0, 0.0],
        stroke_width=0.5,
    )

    state = {
        "render_mode": 3,
        "fill_color": [1.0, 1.0, 1.0],
        "stroke_color": [1.0, 1.0, 1.0],
        "stroke_width": 4.0,
        "font_size": 12.0,
    }

    assert replacer._get_target_tr_mode(state["render_mode"]) == 0
    assert replacer._state_matches_desired(state) is False

    new_instructions = []
    replacer._force_style_state(new_instructions, state)

    assert ([0.7, 0.1, 0.1], "rg") in new_instructions
    assert ([0], "Tr") in new_instructions
    # No stroke instructions injected.
    assert not any(instr[1] in ("RG", "w") for instr in new_instructions)

    replacer._update_state(state)
    assert state["render_mode"] == 0
    assert state["fill_color"] == [0.7, 0.1, 0.1]
    # Stroke state left exactly as it was.
    assert state["stroke_color"] == [1.0, 1.0, 1.0]
    assert state["stroke_width"] == 4.0


def test_stroke_intent_from_invisible_mode_3():
    """Tr 3 (invisible) + stroke intent only -> target Tr 1 (stroke only,
    not Tr 2), per the mutation matrix."""
    replacer = TextStrokeReplaceContentStream(
        pdf=MagicMock(),
        has_fill_intent=False,
        has_stroke_intent=True,
        fill_color=None,
        stroke_color=[0.0, 1.0, 0.0],
        stroke_width=1.0,
    )

    state = {
        "render_mode": 3,
        "fill_color": [1.0, 1.0, 1.0],
        "stroke_color": [0.0, 0.0, 0.0],
        "stroke_width": 0.0,
        "font_size": 12.0,
    }

    assert replacer._get_target_tr_mode(state["render_mode"]) == 1
    assert replacer._state_matches_desired(state) is False

    new_instructions = []
    replacer._force_style_state(new_instructions, state)

    assert ([0.0, 1.0, 0.0], "RG") in new_instructions
    assert ([1.0], "w") in new_instructions
    assert ([1], "Tr") in new_instructions
    assert not any(instr[1] == "rg" for instr in new_instructions)

    replacer._update_state(state)
    assert state["render_mode"] == 1
    assert state["stroke_color"] == [0.0, 1.0, 0.0]
    assert state["stroke_width"] == 1.0
    assert state["fill_color"] == [1.0, 1.0, 1.0]  # untouched


def test_fill_intent_from_stroke_only_mode_1():
    """Tr 1 (stroke only) + fill intent only -> target Tr 2 (per matrix,
    fill map sends 1 -> 2), adding a fill without disturbing existing stroke."""
    replacer = TextStrokeReplaceContentStream(
        pdf=MagicMock(),
        has_fill_intent=True,
        has_stroke_intent=False,
        fill_color=[0.3, 0.3, 0.9],
        stroke_color=[0.0, 0.0, 0.0],
        stroke_width=0.5,
    )

    state = {
        "render_mode": 1,
        "fill_color": [1.0, 1.0, 1.0],
        "stroke_color": [0.4, 0.4, 0.4],
        "stroke_width": 2.5,
        "font_size": 12.0,
    }

    assert replacer._get_target_tr_mode(state["render_mode"]) == 2

    new_instructions = []
    replacer._force_style_state(new_instructions, state)

    assert ([0.3, 0.3, 0.9], "rg") in new_instructions
    assert ([2], "Tr") in new_instructions
    # Existing stroke must be left alone since has_stroke_intent is False.
    assert not any(instr[1] in ("RG", "w") for instr in new_instructions)

    replacer._update_state(state)
    assert state["render_mode"] == 2
    assert state["fill_color"] == [0.3, 0.3, 0.9]
    assert state["stroke_color"] == [0.4, 0.4, 0.4]
    assert state["stroke_width"] == 2.5


##################################################


def create_minimal_pdf(content_stream: bytes, resources: dict | None = None) -> pikepdf.Pdf:
    """Helper construct for an in-memory single-page PDF document."""
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(100, 100))
    page.Contents = pdf.make_stream(content_stream)
    if resources is not None:
        page.Resources = pikepdf.Dictionary(resources)
    return pdf


def test_style_text_inherits_existing_fill_color_when_adding_stroke():
    """Text without a stroke inherits its active fill color when stroke width is added."""
    stream_red_text = b"1 0 0 rg /F1 12 Tf (Red Text) Tj"
    pdf = create_minimal_pdf(stream_red_text)

    style_text_in_content_streams(pdf, ["1(stroke=1.0)"])

    parsed = list(pikepdf.parse_content_stream(pdf.pages[0].Contents))
    operators = [str(op) for _, op in parsed]
    operands = [ops for ops, _ in parsed]

    assert "RG" in operators
    rg_index = operators.index("RG")
    assert [float(x) for x in operands[rg_index]] == [1.0, 0.0, 0.0]


def test_style_text_preserves_existing_stroke_color_when_visible():
    """Text that already has a visible stroke preserves its stroke color during stroke width updates."""
    stream_existing_stroke = b"2 Tr 0 1 0 rg 0 0 1 RG /F1 12 Tf (Outlined Text) Tj"
    pdf = create_minimal_pdf(stream_existing_stroke)

    style_text_in_content_streams(pdf, ["(stroke=2.0)"])

    parsed = list(pikepdf.parse_content_stream(pdf.pages[0].Contents))
    rg_ops = [ops for ops, op in parsed if str(op) == "RG"]

    assert len(rg_ops) >= 1
    assert [float(x) for x in rg_ops[-1]] == [0.0, 0.0, 1.0]


def test_style_text_falls_back_to_black_when_fill_color_is_unknown():
    """Text with an unhandled color space falls back to black when generating a stroke."""
    stream_unknown_fill = b"/DeviceRGB cs /F1 12 Tf (Text) Tj"
    pdf = create_minimal_pdf(stream_unknown_fill)

    style_text_in_content_streams(pdf, ["(stroke=1.0)"])

    parsed = list(pikepdf.parse_content_stream(pdf.pages[0].Contents))
    g_ops = [ops for ops, op in parsed if str(op) == "G"]

    assert len(g_ops) >= 1
    assert [float(x) for x in g_ops[0]] == [0.0]


def test_style_text_explicit_stroke_color_overrides_fill_inheritance():
    """Providing an explicit stroke color overrides fill color inheritance."""
    stream = b"1 0 0 rg /F1 12 Tf (Red Fill Green Stroke) Tj"
    pdf = create_minimal_pdf(stream)

    style_text_in_content_streams(pdf, ["(stroke=1.0,stroke_color=0 1 0)"])

    parsed = list(pikepdf.parse_content_stream(pdf.pages[0].Contents))
    rg_ops = [ops for ops, op in parsed if str(op) == "RG"]

    assert len(rg_ops) >= 1
    assert [float(x) for x in rg_ops[-1]] == [0.0, 1.0, 0.0]


def test_style_text_percentage_stroke_width_default_font_size_fallback():
    """Percentage stroke width uses default font size when text operator precedes font selection."""
    stream = b"(Text Before Tf) Tj /F1 14 Tf (Text After Tf) Tj"
    pdf = create_minimal_pdf(stream)

    style_text_in_content_streams(pdf, ["(stroke=10%)"])

    parsed = list(pikepdf.parse_content_stream(pdf.pages[0].Contents))
    w_ops = [ops for ops, op in parsed if str(op) == "w"]

    assert len(w_ops) >= 2
    assert float(w_ops[0][0]) == 1.2
    assert float(w_ops[1][0]) == 1.4


def test_style_text_handles_form_xobjects_and_resource_variations():
    """Processes Form XObjects and skips non-form XObjects in resource dictionaries."""
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(100, 100))
    page.Contents = pdf.make_stream(b"/F1 12 Tf (Main Page) Tj")

    image_xobj = pdf.make_stream(b"")
    image_xobj.Subtype = pikepdf.Name("/Image")

    form_xobj = pdf.make_stream(b"/F1 12 Tf (Form Text) Tj")
    form_xobj.Subtype = pikepdf.Name("/Form")

    nested_form = pdf.make_stream(b"/F1 10 Tf (Nested Text) Tj")
    nested_form.Subtype = pikepdf.Name("/Form")

    form_xobj.Resources = pikepdf.Dictionary(
        {
            "/XObject": pikepdf.Dictionary(
                {
                    "/NestedForm": nested_form,
                }
            )
        }
    )

    page.Resources = pikepdf.Dictionary(
        {
            "/XObject": pikepdf.Dictionary(
                {
                    "/Img1": image_xobj,
                    "/Form1": form_xobj,
                }
            )
        }
    )

    style_text_in_content_streams(pdf, ["(stroke=1.0)"])

    nested_parsed = list(pikepdf.parse_content_stream(nested_form))
    nested_operators = [str(op) for _, op in nested_parsed]
    assert "Tr" in nested_operators


def test_style_text_empty_args_or_no_intent():
    """Returns early or performs no-op when empty arguments or no styling intents are supplied."""
    pdf = create_minimal_pdf(b"/F1 12 Tf (Hello) Tj")
    result = style_text_in_content_streams(pdf, [])
    assert result.success

    result_no_intent = style_text_in_content_streams(pdf, ["1"])
    assert result_no_intent.success


def test_style_text_consecutive_drawing_ops_matching_state():
    """Avoids inserting redundant graphic state instructions when state is already aligned."""
    stream = b"/F1 12 Tf (A) Tj (B) TJ ' \" "
    pdf = create_minimal_pdf(stream)

    style_text_in_content_streams(pdf, ["(stroke=1.0)"])

    parsed = list(pikepdf.parse_content_stream(pdf.pages[0].Contents))
    tr_ops = [ops for ops, op in parsed if str(op) == "Tr"]

    assert len(tr_ops) == 1


def test_style_text_graphics_state_stack_push_pop():
    """Correctly restores graphics state across push and pop operators."""
    stream = b"1 0 0 rg q 0 1 0 rg /F1 12 Tf (Inner) Tj Q /F1 12 Tf (Outer) Tj"
    pdf = create_minimal_pdf(stream)

    style_text_in_content_streams(pdf, ["(stroke=0.5)"])

    parsed = list(pikepdf.parse_content_stream(pdf.pages[0].Contents))
    rg_ops = [ops for ops, op in parsed if str(op) == "RG"]

    assert len(rg_ops) == 2
    assert [float(x) for x in rg_ops[0]] == [0.0, 1.0, 0.0]
    assert [float(x) for x in rg_ops[1]] == [1.0, 0.0, 0.0]


def test_style_text_invalid_argument_errors():
    """Raises InvalidArgumentError on invalid input parameter values."""
    pdf = create_minimal_pdf(b"/F1 12 Tf (Hello) Tj")

    with pytest.raises(InvalidArgumentError):
        style_text_in_content_streams(pdf, ["(stroke=-1)"])

    with pytest.raises(InvalidArgumentError):
        style_text_in_content_streams(pdf, ["(stroke=invalid)"])

    with pytest.raises(InvalidArgumentError):
        style_text_in_content_streams(pdf, ["(color=invalid_color)"])


def test_style_text_cmyk_and_grayscale_color_formatting():
    """Formats Grayscale and CMYK color operand instructions correctly."""
    pdf = create_minimal_pdf(b"0.5 g /F1 12 Tf (Gray) Tj 0.1 0.2 0.3 0.4 k /F1 12 Tf (CMYK) Tj")

    style_text_in_content_streams(pdf, ["(stroke=1.0)"])

    parsed = list(pikepdf.parse_content_stream(pdf.pages[0].Contents))
    operators = [str(op) for _, op in parsed]

    assert "G" in operators
    assert "K" in operators


def test_style_text_stroke_color_without_stroke_width():
    """Applies stroke color intent without injecting width instruction if width is omitted."""
    pdf = create_minimal_pdf(b"/F1 12 Tf (Text) Tj")

    style_text_in_content_streams(pdf, ["(stroke_color=1 0 0)"])

    parsed = list(pikepdf.parse_content_stream(pdf.pages[0].Contents))
    operators = [str(op) for _, op in parsed]

    assert "RG" in operators
    assert "w" not in operators


def test_style_text_color_instruction_operand_validation():
    """Raises ValueError when invalid color operand lengths are supplied to internal helper."""
    pdf = pikepdf.Pdf.new()
    replacer = TextStrokeReplaceContentStream(pdf=pdf)

    with pytest.raises(ValueError):
        replacer._color_instruction([1.0, 2.0], "fill")


def test_style_text_empty_page_contents_and_duplicate_processing():
    """Handles blank pages with no contents stream and guards against re-processing."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(100, 100))

    result = style_text_in_content_streams(pdf, ["(stroke=1.0)"])
    assert result.success

    pdf_with_contents = create_minimal_pdf(b"/F1 12 Tf (Text) Tj")
    style_text_in_content_streams(pdf_with_contents, ["(stroke=1.0)"])
    style_text_in_content_streams(pdf_with_contents, ["(stroke=1.0)"])


def test_apply_page_with_no_resources_key():
    """Covers the branch where a page has contents but no /Resources entry at all."""
    with pikepdf.new() as pdf:
        page = pdf.add_blank_page(page_size=(100, 100))
        page.Contents = pdf.make_stream(b"/F1 12 Tf (Text) Tj")
        if "/Resources" in page:
            del page["/Resources"]

        replacer = TextStrokeReplaceContentStream(
            pdf=pdf,
            has_stroke_intent=True,
            stroke_color=[1, 0, 0],
            stroke_width=1.0,
        )

        with patch.object(replacer, "_process_resources") as mock_res:
            replacer.apply(1)
            mock_res.assert_not_called()


def test_build_replacer_stroke_none_sets_removal_flag():
    r = _build_replacer(MagicMock(), {"stroke": "none"})
    assert r is not None
    assert r.has_stroke_removal is True
    assert r.has_stroke_intent is False


def test_build_replacer_stroke_none_with_stroke_color_raises():
    with pytest.raises(InvalidArgumentError, match="stroke=none"):
        _build_replacer(MagicMock(), {"stroke": "none", "stroke_color": "1 0 0"})


def test_get_target_tr_mode_stroke_removal_matrix():
    r = TextStrokeReplaceContentStream(pdf=MagicMock(), has_stroke_removal=True)
    assert r._get_target_tr_mode(0) == 0
    assert r._get_target_tr_mode(1) == 3
    assert r._get_target_tr_mode(2) == 0
    assert r._get_target_tr_mode(3) == 3


def test_get_target_tr_mode_fill_plus_stroke_removal_combo():
    """Stroke-only text (Tr1) gaining a fill while removing its stroke -> Tr0."""
    r = TextStrokeReplaceContentStream(
        pdf=MagicMock(), has_fill_intent=True, has_stroke_removal=True
    )
    assert r._get_target_tr_mode(1) == 0
    assert r._get_target_tr_mode(2) == 0


def test_style_text_stroke_none_removes_stroke_from_fill_and_stroke_text():
    """Tr 2 (fill+stroke) + stroke=none -> Tr 0, existing stroke ops left as-is
    (nothing new injected), only the Tr transition changes."""
    stream = b"2 Tr 1 0 0 rg 0 1 0 RG /F1 12 Tf (Text) Tj"
    pdf = create_minimal_pdf(stream)

    style_text_in_content_streams(pdf, ["(stroke=none)"])

    parsed = list(pikepdf.parse_content_stream(pdf.pages[0].Contents))
    tr_ops = [ops for ops, op in parsed if str(op) == "Tr"]

    assert [int(x) for x in tr_ops[-1]] == [0]


def test_style_text_stroke_none_on_stroke_only_text_goes_invisible():
    """Tr 1 (stroke-only, no fill) + stroke=none -> Tr 3 (invisible),
    since there's no fill to fall back to."""
    stream = b"1 Tr 0 1 0 RG /F1 12 Tf (Text) Tj"
    pdf = create_minimal_pdf(stream)

    style_text_in_content_streams(pdf, ["(stroke=none)"])

    parsed = list(pikepdf.parse_content_stream(pdf.pages[0].Contents))
    tr_ops = [ops for ops, op in parsed if str(op) == "Tr"]

    assert [int(x) for x in tr_ops[-1]] == [3]


def test_style_text_stroke_none_combined_with_fill_color():
    """Stroke-only text (Tr1) + fill_color + stroke=none -> Tr0: fill is set,
    stroke is gone, not left dangling at Tr2."""
    stream = b"1 Tr 0 1 0 RG /F1 12 Tf (Text) Tj"
    pdf = create_minimal_pdf(stream)

    style_text_in_content_streams(pdf, ["(fill_color=0.2 0.2 0.9,stroke=none)"])

    parsed = list(pikepdf.parse_content_stream(pdf.pages[0].Contents))
    tr_ops = [ops for ops, op in parsed if str(op) == "Tr"]
    rg_ops = [ops for ops, op in parsed if str(op) == "rg"]

    assert [int(x) for x in tr_ops[-1]] == [0]
    assert [float(x) for x in rg_ops[-1]] == [0.2, 0.2, 0.9]


def test_style_text_stroke_none_and_stroke_color_conflict_raises():
    pdf = create_minimal_pdf(b"/F1 12 Tf (Text) Tj")
    with pytest.raises(InvalidArgumentError):
        style_text_in_content_streams(pdf, ["(stroke=none,stroke_color=1 0 0)"])
