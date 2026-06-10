import pytest
import pikepdf
from unittest.mock import MagicMock, patch, call
from pdftl.exceptions import InvalidArgumentError
from pdftl.core.core_types import OpResult

# Import the module components under test
from pdftl.operations.style_text import (
    style_text_in_content_streams,
    _get_color_or_raise,
    _apply_style_text_spec_in_content_streams,
    TextStrokeReplaceContentStream,
)


# ==============================================================================
# 1. Tests for Argument Parsing & Main Entry Point
# ==============================================================================


def test_style_text_in_content_streams_defaults():
    """Verify default behavior when no explicit options or pages are provided."""
    mock_pdf = MagicMock()
    mock_pdf.pages = [MagicMock(), MagicMock()]

    with (
        patch("pdftl.operations.style_text.parse_keyval_list") as mock_parse,
        patch(
            "pdftl.operations.style_text._apply_style_text_spec_in_content_streams"
        ) as mock_apply,
    ):
        mock_parse.return_value = {}

        result = style_text_in_content_streams(mock_pdf, None)

        assert isinstance(result, OpResult)
        assert result.success is True
        assert result.pdf == mock_pdf
        mock_apply.assert_called_once_with(mock_pdf, "-", None, "absolute", None, None, None)


def test_style_text_in_content_streams_with_valid_args():
    """Verify absolute and percentage stroke parsing along with colors."""
    mock_pdf = MagicMock()
    mock_pdf.pages = [MagicMock()]

    with (
        patch("pdftl.operations.style_text.parse_keyval_list") as mock_parse,
        patch(
            "pdftl.operations.style_text._apply_style_text_spec_in_content_streams"
        ) as mock_apply,
    ):
        mock_parse.side_effect = lambda args, **kwargs: (
            kwargs["bare_tokens"].append("1-2"),
            {"stroke": "1.5", "color": "0,0,1"},
        )[1]

        style_text_in_content_streams(mock_pdf, ["1-2", "stroke=1.5", "color=0,0,1"])
        mock_apply.assert_called_once_with(
            mock_pdf, "1-2", 1.5, "absolute", [0.0, 0.0, 1.0], [0.0, 0.0, 1.0], 2
        )


def test_style_text_in_content_streams_with_valid_percentage_args():
    """Verify absolute and percentage stroke parsing along with colors."""
    mock_pdf = MagicMock()
    mock_pdf.pages = [MagicMock()]

    with (
        patch("pdftl.operations.style_text.parse_keyval_list") as mock_parse,
        patch(
            "pdftl.operations.style_text._apply_style_text_spec_in_content_streams"
        ) as mock_apply,
    ):
        mock_parse.side_effect = lambda args, **kwargs: (
            kwargs["bare_tokens"].append("1-2"),
            {"stroke": "1.5%", "stroke_color": "1,0.5,0.01"},
        )[1]

        style_text_in_content_streams(mock_pdf, ["1-2", "stroke=1.5%", "stroke_color=1,0.5,0.01"])
        mock_apply.assert_called_once_with(
            mock_pdf, "1-2", 1.5, "percentage", [1.0, 0.5, 0.01], None, 2
        )


def test_style_text_stroke_color_defaults_stroke_width():
    """Verify stroke defaults to 0.5 if stroke_color is set without width."""
    mock_pdf = MagicMock()
    mock_pdf.pages = [MagicMock()]

    with (
        patch("pdftl.operations.style_text.parse_keyval_list") as mock_parse,
        patch(
            "pdftl.operations.style_text._apply_style_text_spec_in_content_streams"
        ) as mock_apply,
    ):
        mock_parse.return_value = {"stroke_color": "1,0,0"}
        style_text_in_content_streams(mock_pdf, ["stroke_color=1,0,0"])

        mock_apply.assert_called_once_with(
            mock_pdf, "-", 0.5, "absolute", [1.0, 0.0, 0.0], None, 2
        )


def test_style_text_invalid_stroke_raises_error():
    """Verify that a poorly formatted stroke value yields an InvalidArgumentError."""
    mock_pdf = MagicMock()
    with patch("pdftl.operations.style_text.parse_keyval_list") as mock_parse:
        mock_parse.return_value = {"stroke": "invalid_stroke_val"}

        with pytest.raises(InvalidArgumentError) as exc_info:
            style_text_in_content_streams(mock_pdf, ["stroke=invalid_stroke_val"])
        assert "Invalid stroke width provided" in str(exc_info.value)


def test_style_text_negative_stroke_raises_error():
    """Verify that negative numerical stroke values raise an error."""
    mock_pdf = MagicMock()
    with patch("pdftl.operations.style_text.parse_keyval_list") as mock_parse:
        mock_parse.return_value = {"stroke": "-1.5"}

        with pytest.raises(InvalidArgumentError):
            style_text_in_content_streams(mock_pdf, ["stroke=-1.5"])


# ==============================================================================
# 2. Tests for Color Helper Function
# ==============================================================================


@pytest.mark.parametrize(
    "data, key, expected",
    [
        ({"color": "0,0,0"}, "color", [0.0, 0.0, 0.0]),
        ({"fill_color": "0.5,0.2,0.1,0.0"}, "fill_color", [0.5, 0.2, 0.1, 0.0]),
        ({"stroke_color": "1"}, "stroke_color", [1.0]),
        ({}, "missing_key", None),
    ],
)
def test_get_color_or_raise_success(data, key, expected):
    """Ensure valid colors maps to flat float lists and missing keys return None."""
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
# 3. Tests for Spec Pipeline Processing
# ==============================================================================


def test_apply_style_text_spec_empty():
    """Verify early return when spec string evaluates to empty/falsy."""
    mock_pdf = MagicMock()
    _apply_style_text_spec_in_content_streams(mock_pdf, "", 0.5, "absolute", None, None, 2)
    mock_pdf.pages.assert_not_called()


def test_apply_style_text_spec_in_content_streams():
    """Verify page loop logic and TextStrokeReplaceContentStream integration."""
    mock_pdf = MagicMock()
    mock_pdf.pages = [MagicMock(), MagicMock(), MagicMock()]

    with (
        patch("pdftl.operations.style_text.page_numbers_matching_page_spec") as mock_matching,
        patch("pdftl.operations.style_text.TextStrokeReplaceContentStream") as mock_replacer_cls,
    ):
        mock_matching.return_value = [1, 3]
        mock_replacer = MagicMock()
        mock_replacer_cls.return_value = mock_replacer

        _apply_style_text_spec_in_content_streams(
            mock_pdf, "1,3", 0.5, "absolute", [0, 0, 0], [1, 1, 1], 2
        )

        assert mock_replacer.apply.call_count == 2
        mock_replacer.apply.assert_has_calls([call(1), call(3)])


# ==============================================================================
# 4. Tests for TextStrokeReplaceContentStream Mechanics
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
    """Verify stroke calculation under both absolute and percentage bounds."""
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

    # Line 288 mismatch: render mode mismatch
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
    )

    # Line 292 mismatch: stroke width mismatch
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
    )

    # Line 294 mismatch: stroke color mismatch
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
    )

    # Line 296 mismatch: fill color mismatch
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
    )

    # Line 297 match: All match perfectly
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
    )


def test_force_style_state_injection(base_replacer):
    """Verify correct operator combinations are appended when the state is out of sync."""
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
# 5. Tests for State Management Engine & Instruction Processing
# ==============================================================================


def test_process_instructions_stack_and_operators(base_replacer):
    """Test graphics state push/pop tracking loops and operator intercept updates."""
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

    # Parse back the returned bytes to ensure standard operator count maps perfectly.
    # pikepdf.parse_content_stream requires a Stream object, so we wrap the raw bytes.
    with pikepdf.new() as pdf:
        dummy_stream = pikepdf.Stream(pdf, new_insts_bytes)
        parsed_insts = list(pikepdf.parse_content_stream(dummy_stream))

    assert len(parsed_insts) == len(instructions)
    assert parsed_insts[0][1] == pikepdf.Operator("q")


def test_process_op_color_spaces(base_replacer):
    """Exercise alternate color space operator processing blocks."""
    state = {}

    # Line 351: CMYK fill ('k')
    base_replacer._process_op("k", [0.1, 0.2, 0.3, 0.4], state, [], [])
    assert state["fill_color"] == [0.1, 0.2, 0.3, 0.4]

    # Line 353: Gray stroke ('G')
    base_replacer._process_op("G", [0.7], state, [], [])
    assert state["stroke_color"] == [0.7]

    # Line 355: Gray fill ('g')
    base_replacer._process_op("g", [0.2], state, [], [])
    assert state["fill_color"] == [0.2]

    # Line 357: RGB stroke ('RG')
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
    """Verify styling elements are injected right before text draw triggers."""
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
# 6. Real pikepdf Stream Processing & Form XObject Recursion Tests
# ==============================================================================


def test_apply_page_with_no_contents(base_replacer):
    """Ensure safe completion when processing a page containing no contents."""
    mock_page = MagicMock()
    mock_page.get.return_value = None
    base_replacer.pdf.pages = [mock_page]

    base_replacer.apply(1)


def test_apply_single_stream_and_processed_filtering_real_objects():
    """Test processing unique streams, skipping visited streams, and resource mapping using real pikepdf structures."""
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
            mock_res.assert_called_once()


def test_apply_page_with_multiple_streams_real_objects():
    """Verify that multiple content streams inside a pikepdf.Array are individually processed."""
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

        assert stream1.objgen in replacer._processed
        assert stream2.objgen in replacer._processed


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
    """Verify recursive lookups step into nested Form XObjects using real Dictionary/Stream wrappers."""
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
            # Fix: The method requires a real `bytes` object to write back to the stream
            mock_proc.return_value = b"Mocked Bytes Injection"

            base_replacer._process_resources(resources)

            mock_proc.assert_called_once()
            assert nested_form.objgen in base_replacer._processed
            assert spy_res.call_count == 2
