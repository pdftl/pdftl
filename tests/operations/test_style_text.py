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
# 2. Tests for _build_replacer
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
    assert r.tr_mode == 2


def test_build_replacer_percentage_stroke():
    r = _build_replacer(MagicMock(), {"stroke": "2%"})
    assert r is not None
    assert r.stroke_width == 2.0
    assert r.stroke_width_type == "percentage"
    assert r.tr_mode == 2


def test_build_replacer_stroke_color_defaults_stroke_width():
    """stroke_color without explicit stroke → stroke defaults to 0.5."""
    r = _build_replacer(MagicMock(), {"stroke_color": "1 0 0"})
    assert r is not None
    assert r.stroke_width == 0.5
    assert r.stroke_color == [1.0, 0.0, 0.0]
    assert r.tr_mode == 2


def test_build_replacer_color_sets_fill_and_stroke():
    r = _build_replacer(MagicMock(), {"stroke": "0.5", "color": "0 0 1"})
    assert r.fill_color == [0.0, 0.0, 1.0]
    assert r.stroke_color == [0.0, 0.0, 1.0]


def test_build_replacer_fill_and_stroke_separate():
    r = _build_replacer(
        MagicMock(), {"stroke": "0.5", "fill_color": "1 0 0", "stroke_color": "0 1 0"}
    )
    assert r.fill_color == [1.0, 0.0, 0.0]
    assert r.stroke_color == [0.0, 1.0, 0.0]


def test_build_replacer_invalid_stroke_raises():
    with pytest.raises(InvalidArgumentError, match="Invalid stroke width"):
        _build_replacer(MagicMock(), {"stroke": "not_a_number"})


def test_build_replacer_negative_stroke_raises():
    with pytest.raises(InvalidArgumentError):
        _build_replacer(MagicMock(), {"stroke": "-1.5"})


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
        assert replacer.tr_mode == 2


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
        fill_color=[0.1, 0.2, 0.3],
        stroke_color=[0.4, 0.5, 0.6],
        stroke_width=2.0,
        stroke_width_type="absolute",
        tr_mode=2,
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
        pdf=MagicMock(), stroke_width=1.0, stroke_color=[1, 1, 1], fill_color=[0, 0, 0], tr_mode=2
    )

    assert (
        replacer._state_matches_desired(
            {
                "render_mode": 0,
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
    assert ([2], "Tr") in new_instructions


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
        pdf=MagicMock(), fill_color=[0.1], stroke_color=[0.2], stroke_width=1.5, tr_mode=3
    )
    state = {"font_size": 12.0}
    replacer._update_state(state)

    assert state["fill_color"] == [0.1]
    assert state["stroke_color"] == [0.2]
    assert state["stroke_width"] == 1.5
    assert state["render_mode"] == 3


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
            fill_color=[0, 0, 0],
            stroke_color=[1, 1, 1],
            stroke_width=1.0,
            tr_mode=2,
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
            fill_color=[0, 0, 1],
            stroke_color=[1, 0, 0],
            stroke_width=2.0,
            tr_mode=2,
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


def test_process_op_named_colorspace_forces_injection_before_text(base_replacer):
    """After cs/scn, _state_matches_desired returns False even if the desired
    fill_color matches the PDF default, because state fill_color is now None.
    This ensures text following a named colorspace gets the fill override injected."""
    # base_replacer has fill_color=[0.1, 0.2, 0.3] so None != that → injection fires.
    # Use a replacer whose fill matches the PDF default to confirm None still triggers.
    replacer = TextStrokeReplaceContentStream(
        pdf=MagicMock(),
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
