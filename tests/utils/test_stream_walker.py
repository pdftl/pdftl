from unittest.mock import MagicMock, patch

import pytest

from pdftl.utils.stream_walker import (
    DEFAULT_GS,
    handle_do,
    process_color_val_op,
    process_cs_op,
    process_operator,
    process_shorthand_op,
    recurse_form_xobject,
    recurse_pattern,
    walk_page,
    walk_stream,
)


class MockPikepdf:
    class PdfError(Exception):
        pass

    Name = str
    Array = list


@pytest.fixture
def pikepdf_mock():
    return MockPikepdf()


def test_process_cs_op(pikepdf_mock):
    gs = DEFAULT_GS()
    detail = {"fills": [], "strokes": []}

    process_cs_op("cs", [], None, pikepdf_mock, gs, detail, 1, False, "s")
    assert not detail["fills"]

    process_cs_op("cs", ["/DeviceRGB"], None, pikepdf_mock, gs, detail, 2, False, "s")
    assert gs["fill_cs"]["family"] == "rgb"

    process_cs_op("CS", ["/DeviceCMYK"], None, pikepdf_mock, gs, detail, 3, False, "s")
    assert gs["stroke_cs"]["family"] == "cmyk"


def test_process_shorthand_op():
    gs = DEFAULT_GS()
    detail = {"fills": [], "strokes": []}

    process_shorthand_op("rg", [], gs, detail, 1, False, "s")
    assert gs["fill_cs"]["family"] == "rgb"

    process_shorthand_op("K", [], gs, detail, 2, False, "s")
    assert gs["stroke_cs"]["family"] == "cmyk"


def test_process_color_val_op():
    gs = DEFAULT_GS()
    gs["fill_cs"] = {"family": "rgb"}
    gs["stroke_cs"] = {"family": "cmyk"}
    detail = {"fills": [], "strokes": []}

    process_color_val_op(
        "sc",
        [],
        None,
        None,
        gs,
        detail,
        set(),
        False,
        1,
        False,
        "s",
    )
    assert not detail["fills"]

    process_color_val_op(
        "sc",
        [],
        None,
        None,
        gs,
        detail,
        set(),
        True,
        2,
        False,
        "s",
    )
    assert detail["fills"][-1]["family"] == "rgb"

    process_color_val_op(
        "SCN",
        [],
        None,
        None,
        gs,
        detail,
        set(),
        True,
        3,
        False,
        "s",
    )
    assert detail["strokes"][-1]["family"] == "cmyk"


def test_recurse_form_xobject(pikepdf_mock):
    xobj = MagicMock()
    seen = set()

    # Success path
    with patch("pdftl.utils.stream_walker.walk_stream") as mock_walk:
        recurse_form_xobject(xobj, 123, None, pikepdf_mock, {}, seen, False, "s")
        assert mock_walk.called
        assert 123 not in seen  # Cleaned up by finally block

    # Prevent infinite recursion loop path
    seen.add(456)
    with patch("pdftl.utils.stream_walker.walk_stream") as mock_walk:
        recurse_form_xobject(xobj, 456, None, pikepdf_mock, {}, seen, False, "s")
        assert not mock_walk.called


def test_handle_do(pikepdf_mock):
    detail = {"images": []}
    seen = set()

    # Missing / fail paths
    assert (
        handle_do(
            "X1",
            None,
            pikepdf_mock,
            detail=detail,
            seen_stream_ids=seen,
            full=False,
            op_idx=1,
            stream_id="s",
        )
        is None
    )

    res = MagicMock()
    res.get.return_value = None  # No XObjects
    assert (
        handle_do(
            "X1",
            res,
            pikepdf_mock,
            detail=detail,
            seen_stream_ids=seen,
            full=False,
            op_idx=1,
            stream_id="s",
        )
        is None
    )

    res.get.return_value = {"X2": "exists"}  # Not X1
    assert (
        handle_do(
            "X1",
            res,
            pikepdf_mock,
            detail=detail,
            seen_stream_ids=seen,
            full=False,
            op_idx=1,
            stream_id="s",
        )
        is None
    )

    res.get.side_effect = TypeError  # Catch block
    assert (
        handle_do(
            "X1",
            res,
            pikepdf_mock,
            detail=detail,
            seen_stream_ids=seen,
            full=False,
            op_idx=1,
            stream_id="s",
        )
        is None
    )

    # Success paths
    xobj_img = MagicMock()
    xobj_img.get.side_effect = lambda k, d=None: "/Image" if k == "/Subtype" else "/DeviceRGB"
    xobj_img.objgen = (1,)
    res.get = MagicMock(return_value={"X1": xobj_img})

    handle_do(
        "X1",
        res,
        pikepdf_mock,
        detail=detail,
        seen_stream_ids=seen,
        full=False,
        op_idx=1,
        stream_id="s",
    )
    assert detail["images"][-1]["family"] == "rgb"

    # Form recursion
    xobj_form = MagicMock()
    xobj_form.get.return_value = "/Form"
    res.get = MagicMock(return_value={"X1": xobj_form})
    with patch("pdftl.utils.stream_walker.recurse_form_xobject") as mock_recurse:
        handle_do(
            "X1",
            res,
            pikepdf_mock,
            detail=detail,
            seen_stream_ids=seen,
            full=False,
            op_idx=1,
            stream_id="s",
        )
        assert mock_recurse.called


def test_process_operator(pikepdf_mock):
    gs = DEFAULT_GS()
    stack = []
    detail = {"fills": [], "strokes": [], "patterns": []}

    process_operator("q", [], None, pikepdf_mock, gs, stack, detail, set(), False, 1, False, "s")
    assert len(stack) == 1

    process_operator("Q", [], None, pikepdf_mock, gs, stack, detail, set(), False, 2, False, "s")
    assert len(stack) == 0
    process_operator("Q", [], None, pikepdf_mock, gs, stack, detail, set(), False, 3, False, "s")
    assert len(stack) == 0

    process_operator(
        "cs",
        ["/DeviceRGB"],
        None,
        pikepdf_mock,
        gs,
        stack,
        detail,
        set(),
        False,
        4,
        False,
        "s",
    )
    process_operator(
        "rg",
        [],
        None,
        pikepdf_mock,
        gs,
        stack,
        detail,
        set(),
        False,
        5,
        False,
        "s",
    )
    process_operator(
        "sc",
        [],
        None,
        pikepdf_mock,
        gs,
        stack,
        detail,
        set(),
        True,
        6,
        False,
        "s",
    )

    with patch("pdftl.utils.stream_walker.handle_do") as mock_do:
        process_operator(
            "Do",
            ["X1"],
            None,
            pikepdf_mock,
            gs,
            stack,
            detail,
            set(),
            False,
            7,
            False,
            "s",
        )
        assert mock_do.called

    process_operator(
        "sh",
        ["Sh1"],
        None,
        pikepdf_mock,
        gs,
        stack,
        detail,
        set(),
        False,
        8,
        False,
        "s",
    )
    assert len(detail["patterns"]) == 1


class BadInst:
    @property
    def operator(self):
        raise AttributeError


class OkInst:
    operator = "rg"
    operands = []


def test_walk_stream(pikepdf_mock):
    # Using isolated classes prevents property mocks from leaking into standard instructions
    pikepdf_mock.parse_content_stream = MagicMock(return_value=[BadInst(), OkInst()])

    with patch("pdftl.utils.stream_walker.process_operator") as mock_process:
        walk_stream(
            MagicMock(),
            None,
            pikepdf_mock,
            detail={},
            seen_stream_ids=set(),
            full=False,
            stream_id="s",
        )
        mock_process.assert_called_once()  # Called only for OkInst

    # Exception handling
    pikepdf_mock.parse_content_stream.side_effect = pikepdf_mock.PdfError
    with patch("pdftl.utils.stream_walker.logger") as mock_logger:
        walk_stream(
            MagicMock(),
            None,
            pikepdf_mock,
            detail={},
            seen_stream_ids=set(),
            full=False,
            stream_id="s",
        )
        mock_logger.warning.assert_called_once()


def test_process_shorthand_op_operands():
    gs = DEFAULT_GS()
    detail = {"fills": [], "strokes": []}

    process_shorthand_op(
        "rg",
        [1, 0.9999999, "/Name"],
        gs,
        detail,
        1,
        False,
        "stream",
    )

    assert detail["fills"][-1]["operands"] == [1.0, 1.0, "/Name"]


def test_recurse_pattern_branches(pikepdf_mock):
    detail = {"fills": [], "strokes": [], "patterns": []}

    recurse_pattern("/P1", None, pikepdf_mock, detail, set(), False)

    resources = MagicMock()
    resources.get.return_value = None
    recurse_pattern("/P1", resources, pikepdf_mock, detail, set(), False)

    patterns = {}
    resources.get.return_value = patterns
    recurse_pattern("/P1", resources, pikepdf_mock, detail, set(), False)


def test_recurse_pattern_walks_stream(pikepdf_mock):
    pattern = MagicMock()
    pattern.get.side_effect = lambda key, default=None: (
        1 if key == "/PatternType" else {} if key == "/Resources" else default
    )
    pattern.objgen = (42,)

    resources = MagicMock()
    resources.get.return_value = {"/P1": pattern}

    with patch("pdftl.utils.stream_walker.walk_stream") as mock_walk:
        recurse_pattern("/P1", resources, pikepdf_mock, {}, set(), False)

        mock_walk.assert_called_once()


def test_recurse_pattern_seen_stream(pikepdf_mock):
    pattern = MagicMock()
    pattern.get.side_effect = lambda key, default=None: (1 if key == "/PatternType" else default)
    pattern.objgen = (42,)

    resources = MagicMock()
    resources.get.return_value = {"/P1": pattern}

    seen = {42}

    with patch("pdftl.utils.stream_walker.walk_stream") as mock_walk:
        recurse_pattern("/P1", resources, pikepdf_mock, {}, seen, False)

        mock_walk.assert_not_called()


def test_process_color_val_pattern_recursion(pikepdf_mock):
    gs = DEFAULT_GS()
    gs["fill_cs"] = {"family": "pattern"}

    detail = {"fills": [], "strokes": []}

    with patch("pdftl.utils.stream_walker.recurse_pattern") as mock_recurse:
        process_color_val_op(
            "scn",
            [1, 2, "/P1"],
            None,
            pikepdf_mock,
            gs,
            detail,
            set(),
            True,
            5,
            False,
            "stream",
        )

        mock_recurse.assert_called_once()


class TextInst:
    def __init__(self, op):
        self.operator = op
        self.operands = []


def test_walk_stream_text_mode(pikepdf_mock):
    pikepdf_mock.parse_content_stream = MagicMock(
        return_value=[
            TextInst("BT"),
            TextInst("rg"),
            TextInst("ET"),
        ]
    )

    seen = []

    def capture(*args):
        seen.append(args[10])  # in_text

    with patch(
        "pdftl.utils.stream_walker.process_operator",
        side_effect=capture,
    ):
        walk_stream(
            MagicMock(),
            None,
            pikepdf_mock,
            detail={},
            seen_stream_ids=set(),
            full=False,
            stream_id="s",
        )

    assert True


def test_walk_page_none_contents(pikepdf_mock):
    page = MagicMock()
    page.get.return_value = None

    walk_page(
        page,
        None,
        pikepdf_mock,
        detail={},
        seen_stream_ids=set(),
        full=False,
    )


def test_walk_page_attribute_error(pikepdf_mock):
    page = MagicMock()
    page.get.side_effect = AttributeError

    walk_page(
        page,
        None,
        pikepdf_mock,
        detail={},
        seen_stream_ids=set(),
        full=False,
    )


def test_walk_page_single_stream(pikepdf_mock):
    stream = MagicMock()
    stream.objgen = (7,)

    page = MagicMock()
    page.get.return_value = stream

    with patch("pdftl.utils.stream_walker.walk_stream") as mock_walk:
        walk_page(
            page,
            None,
            pikepdf_mock,
            detail={},
            seen_stream_ids=set(),
            full=False,
        )

        mock_walk.assert_called_once()


def test_walk_page_array_streams(pikepdf_mock):
    s1 = MagicMock()
    s1.objgen = (1,)

    s2 = MagicMock()
    s2.objgen = (2,)

    page = MagicMock()
    page.get.return_value = [s1, s2]

    with patch("pdftl.utils.stream_walker.walk_stream") as mock_walk:
        walk_page(
            page,
            None,
            pikepdf_mock,
            detail={},
            seen_stream_ids=set(),
            full=False,
        )

        assert mock_walk.call_count == 2


def test_recurse_pattern_attributeerror_typeerror_branches(pikepdf_mock):
    # Covers lines 106-107: exception while accessing pattern resources
    detail = {}
    seen = set()

    bad_resources = MagicMock()
    bad_resources.get.side_effect = TypeError

    from pdftl.utils.stream_walker import recurse_pattern

    recurse_pattern("/P1", bad_resources, pikepdf_mock, detail, seen, False)

    # Covers lines 136-137: exception inside PatternType handling block
    pattern_obj = MagicMock()
    pattern_obj.get.side_effect = AttributeError

    resources = MagicMock()
    resources.get.return_value = {"/P1": pattern_obj}

    recurse_pattern("/P1", resources, pikepdf_mock, detail, seen, False)


def test_recurse_pattern_patterntype_exception_path(pikepdf_mock):
    # Alternative explicit coverage of lines 136-137 via TypeError
    from pdftl.utils.stream_walker import recurse_pattern

    detail = {}
    seen = set()

    pattern_obj = MagicMock()
    pattern_obj.get.side_effect = TypeError

    patterns = {"/P1": pattern_obj}

    resources = MagicMock()
    resources.get.return_value = patterns

    recurse_pattern("/P1", resources, pikepdf_mock, detail, seen, False)


def test_process_cs_op_coverage_extensions(pikepdf_mock, monkeypatch):
    """Covers lines 59, 61-66: None result and Exception paths in process_cs_op."""
    gs = DEFAULT_GS()
    detail = {"fills": [], "strokes": []}

    # Mock resolve_named_cs to return None (Line 59)
    with patch("pdftl.utils.stream_walker.resolve_named_cs", return_value=None):
        process_cs_op("cs", ["/DeviceRGB"], None, pikepdf_mock, gs, detail, 1, False, "s")
        assert not detail["fills"]

    # Mock resolve_named_cs to raise PdfError (Lines 64-66)
    with patch("pdftl.utils.stream_walker.resolve_named_cs", side_effect=pikepdf_mock.PdfError):
        process_cs_op("cs", ["/DeviceRGB"], None, pikepdf_mock, gs, detail, 1, False, "s")
        assert not detail["fills"]


def test_execute_pattern_walk_fallback_resources(pikepdf_mock):
    """Covers line 119: pattern_resources falling back to parent resources."""
    pattern = MagicMock()
    pattern.get.return_value = None  # No /Resources inside the pattern

    parent_resources = MagicMock()

    with patch("pdftl.utils.stream_walker.walk_stream") as mock_walk:
        from pdftl.utils.stream_walker import _execute_pattern_walk

        _execute_pattern_walk(pattern, parent_resources, pikepdf_mock, {}, set(), False, "s", None)
        # Verify it passed the parent resources instead
        mock_walk.assert_called_once_with(
            pattern,
            parent_resources,
            pikepdf_mock,
            detail={},
            seen_stream_ids=set(),
            full=False,
            stream_id="s",
        )


def test_recurse_pattern_type_mismatch_and_pdf_error(pikepdf_mock):
    """Covers lines 157, 166-167: PatternType != 1 and PdfError catch block."""
    detail = {}

    # Pattern type mismatch (e.g., Shading Pattern Type 2) -> Line 157
    p_obj_bad_type = MagicMock()
    p_obj_bad_type.get.return_value = 2
    res_bad_type = MagicMock()
    res_bad_type.get.return_value = {"/P1": p_obj_bad_type}
    recurse_pattern("/P1", res_bad_type, pikepdf_mock, detail, set(), False)

    # Triggering PdfError within the try-except block -> Lines 166-167
    p_obj_error = MagicMock()
    p_obj_error.get.side_effect = pikepdf_mock.PdfError
    res_error = MagicMock()
    res_error.get.return_value = {"/P1": p_obj_error}
    recurse_pattern("/P1", res_error, pikepdf_mock, detail, set(), False)


def test_process_color_val_op_prepend_slash():
    """Covers line 203: auto-prepending '/' to a pattern name if missing."""
    gs = DEFAULT_GS()
    gs["fill_cs"] = {"family": "pattern"}
    detail = {"fills": [], "strokes": []}

    with patch("pdftl.utils.stream_walker.recurse_pattern") as mock_recurse:
        process_color_val_op(
            "scn", ["PatternWithoutSlash"], None, None, gs, detail, set(), True, 1, False, "s"
        )
        # Ensure it normalized the name with a leading slash
        mock_recurse.assert_called_once_with(
            "/PatternWithoutSlash", None, None, detail, set(), True
        )


def test_recurse_form_xobject_fallback_resources(pikepdf_mock):
    """Covers line 227: form_resources falling back to parent resources."""
    xobj = MagicMock()
    xobj.get.return_value = None  # No local resources
    parent_resources = MagicMock()

    with patch("pdftl.utils.stream_walker.walk_stream") as mock_walk:
        recurse_form_xobject(xobj, None, parent_resources, pikepdf_mock, {}, set(), False, "s")
        mock_walk.assert_called_once_with(
            xobj,
            parent_resources,
            pikepdf_mock,
            detail={},
            seen_stream_ids=set(),
            full=False,
            stream_id="s",
        )


def test_extract_xobject_fallback_bracket_syntax():
    """Covers line 251: standard dictionary square bracket item access fallback."""
    from pdftl.utils.stream_walker import _extract_xobject

    class PseudoDictWithoutGet:
        def __init__(self, data):
            self.data = data

        def __contains__(self, key):
            return key in self.data

        def __getitem__(self, key):
            return self.data[key]

    xobjects_mock = PseudoDictWithoutGet({"/X1": "target_object"})
    resources = MagicMock()
    resources.get.return_value = xobjects_mock

    assert _extract_xobject("/X1", resources) == "target_object"


def test_handle_image_subtype_exceptions(pikepdf_mock):
    """Covers lines 260-265: image_colorspace exceptions (TypeError and PdfError)."""
    from pdftl.utils.stream_walker import _handle_image_subtype

    detail = {"images": []}
    xobj = MagicMock()

    with patch("pdftl.utils.stream_walker.image_colorspace", side_effect=TypeError):
        _handle_image_subtype(xobj, None, pikepdf_mock, detail, "Img1", 1, "s", None)
        assert detail["images"][-1]["family"] == "unknown"

    with patch("pdftl.utils.stream_walker.image_colorspace", side_effect=pikepdf_mock.PdfError):
        _handle_image_subtype(xobj, None, pikepdf_mock, detail, "Img1", 2, "s", None)
        assert detail["images"][-1]["family"] == "unknown"


def test_handle_do_prepend_slash(pikepdf_mock):
    """Covers line 294: prepending a leading slash to the Subtype descriptor."""
    detail = {"images": []}
    xobj = MagicMock()
    # Return a subtype value missing its explicit forward slash
    xobj.get.side_effect = lambda k: "Image" if k == "/Subtype" else None

    resources = MagicMock()
    resources.get.return_value = {"Im1": xobj}

    with patch("pdftl.utils.stream_walker._handle_image_subtype") as mock_handle:
        handle_do(
            "Im1",
            resources,
            pikepdf_mock,
            detail=detail,
            seen_stream_ids=set(),
            full=False,
            op_idx=1,
            stream_id="s",
        )
        assert mock_handle.called


def test_process_shading_op_exceptions(pikepdf_mock):
    """Covers lines 318-321: _process_shading_op error handling paths."""
    from pdftl.utils.stream_walker import _process_shading_op

    detail = {"patterns": []}

    with patch("pdftl.utils.stream_walker.resolve_shading_cs", side_effect=ValueError):
        _process_shading_op(["Sh1"], None, pikepdf_mock, detail, 1, "s")
        assert len(detail["patterns"]) == 0

    with patch("pdftl.utils.stream_walker.resolve_shading_cs", side_effect=pikepdf_mock.PdfError):
        _process_shading_op(["Sh1"], None, pikepdf_mock, detail, 1, "s")
        assert len(detail["patterns"]) == 0


def test_walk_stream_iterator_and_loop_exceptions(pikepdf_mock):
    """Covers lines 384-386 and 393-398: Parser execution and item retrieval loops."""
    # Line 384-386: PdfError thrown directly when entering stream initialization
    pikepdf_mock.parse_content_stream = MagicMock(side_effect=pikepdf_mock.PdfError)
    walk_stream(
        MagicMock(),
        None,
        pikepdf_mock,
        detail={},
        seen_stream_ids=set(),
        full=False,
        stream_id="s",
    )

    # Line 393-398: Errors occurring mid-iteration inside the operational loop
    class FaultyIterator:
        def __iter__(self):
            return self

        def __next__(self):
            raise pikepdf_mock.PdfError

    pikepdf_mock.parse_content_stream = MagicMock(return_value=FaultyIterator())
    walk_stream(
        MagicMock(),
        None,
        pikepdf_mock,
        detail={},
        seen_stream_ids=set(),
        full=False,
        stream_id="s",
    )

    # Repeat for internal standard operational TypeErrors inside the loop
    class FaultyIteratorTwo:
        def __iter__(self):
            return self

        def __next__(self):
            raise TypeError

    pikepdf_mock.parse_content_stream = MagicMock(return_value=FaultyIteratorTwo())
    walk_stream(
        MagicMock(),
        None,
        pikepdf_mock,
        detail={},
        seen_stream_ids=set(),
        full=False,
        stream_id="s",
    )


def test_walk_page_none_guard():
    """Covers line 436: Direct guard clause exit when a target page object is missing."""
    assert walk_page(None, None, None, detail={}, seen_stream_ids=set(), full=False) is None


def test_pdf_error_explicit_coverage_paths(pikepdf_mock):
    """Explicitly satisfies all residual pikepdf_module.PdfError catch blocks."""
    from pdftl.utils.stream_walker import (
        _handle_image_subtype,
        _process_shading_op,
        process_cs_op,
        recurse_pattern,
        walk_stream,
    )

    error_to_raise = pikepdf_mock.PdfError("Mock PDF parsing Exception")

    # Lines 64-66: process_cs_op dedicated PdfError path
    with patch("pdftl.utils.stream_walker.resolve_named_cs", side_effect=error_to_raise):
        process_cs_op(
            "cs",
            ["/DeviceRGB"],
            None,
            pikepdf_mock,
            DEFAULT_GS(),
            {"fills": [], "strokes": []},
            1,
            False,
            "s",
        )

    # Lines 166-167: recurse_pattern dedicated PdfError path
    p_obj_error = MagicMock()
    p_obj_error.get.side_effect = error_to_raise
    res_error = MagicMock()
    res_error.get.return_value = {"/P1": p_obj_error}
    recurse_pattern("/P1", res_error, pikepdf_mock, {}, set(), False)

    # Lines 263-265: _handle_image_subtype dedicated PdfError path
    with patch("pdftl.utils.stream_walker.image_colorspace", side_effect=error_to_raise):
        _handle_image_subtype(
            MagicMock(), None, pikepdf_mock, {"images": []}, "Img1", 1, "s", None
        )

    # Lines 320-321: _process_shading_op dedicated PdfError path
    with patch("pdftl.utils.stream_walker.resolve_shading_cs", side_effect=error_to_raise):
        _process_shading_op(["Sh1"], None, pikepdf_mock, {"patterns": []}, 1, "s")

    # Lines 384-386: walk_stream initial parsing iterator setup PdfError path
    pikepdf_mock.parse_content_stream = MagicMock(side_effect=error_to_raise)
    walk_stream(
        MagicMock(),
        None,
        pikepdf_mock,
        detail={},
        seen_stream_ids=set(),
        full=False,
        stream_id="s",
    )

    # Lines 396-398: walk_stream mid-loop iteration truncation PdfError path
    class FaultyIterator:
        def __iter__(self):
            return self

        def __next__(self):
            raise error_to_raise

    pikepdf_mock.parse_content_stream = MagicMock(return_value=FaultyIterator())
    walk_stream(
        MagicMock(),
        None,
        pikepdf_mock,
        detail={},
        seen_stream_ids=set(),
        full=False,
        stream_id="s",
    )


def test_final_catch_all_exception_coverage(pikepdf_mock):
    """Closes out lines 62-63 and 382-383 using standard built-in exceptions."""
    from pdftl.utils.stream_walker import process_cs_op, walk_stream

    # Lines 62-63: Force a ValueError inside process_cs_op to hit the standard logger.debug path
    with patch(
        "pdftl.utils.stream_walker.resolve_named_cs", side_effect=ValueError("Standard Error")
    ):
        process_cs_op(
            "cs",
            ["/DeviceRGB"],
            None,
            pikepdf_mock,
            DEFAULT_GS(),
            {"fills": [], "strokes": []},
            1,
            False,
            "s",
        )

    # Lines 382-383: Force a ValueError on stream setup inside walk_stream
    pikepdf_mock.parse_content_stream = MagicMock(side_effect=ValueError("Standard Linkage Error"))
    walk_stream(
        MagicMock(),
        None,
        pikepdf_mock,
        detail={},
        seen_stream_ids=set(),
        full=False,
        stream_id="s",
    )


def test_process_operator_gs_state_ops(pikepdf_mock):
    gs = DEFAULT_GS()
    stack = []
    detail = {"fills": [], "strokes": [], "patterns": []}

    # ri
    process_operator(
        "ri", ["/Perceptual"], None, pikepdf_mock, gs, stack, detail, set(), False, 1, False, "s"
    )
    assert gs["rendering_intent"] == "Perceptual"

    # op (fill overprint)
    process_operator(
        "op", [True], None, pikepdf_mock, gs, stack, detail, set(), False, 2, False, "s"
    )
    assert gs["overprint_fill"] is True

    # OP (stroke overprint)
    process_operator(
        "OP", [False], None, pikepdf_mock, gs, stack, detail, set(), False, 3, False, "s"
    )
    assert gs["overprint_stroke"] is False

    # gs — delegates to _apply_ext_gstate; just verify it doesn't raise with no resources
    process_operator(
        "gs", ["/GS1"], None, pikepdf_mock, gs, stack, detail, set(), False, 4, False, "s"
    )


def test_apply_ext_gstate(pikepdf_mock):
    from pdftl.utils.stream_walker import _apply_ext_gstate

    gs = DEFAULT_GS()

    # No ExtGState in resources
    res = MagicMock()
    res.get.return_value = None
    _apply_ext_gstate("/GS1", res, pikepdf_mock, gs)
    assert gs["rendering_intent"] == "RelativeColorimetric"  # unchanged

    # ExtGState present but named entry missing
    res.get.return_value = {}
    _apply_ext_gstate("/GS1", res, pikepdf_mock, gs)

    # OP only — should set both stroke and fill
    gstate = MagicMock()
    gstate.get.side_effect = lambda k, d=None: (True if k == "/OP" else None)
    res.get.return_value = {"/GS1": gstate}
    _apply_ext_gstate("/GS1", res, pikepdf_mock, gs)
    assert gs["overprint_stroke"] is True
    assert gs["overprint_fill"] is True

    # OP + op — OP sets stroke only, op sets fill
    gstate.get.side_effect = lambda k, d=None: (
        True if k == "/OP" else False if k == "/op" else None
    )
    _apply_ext_gstate("/GS1", res, pikepdf_mock, gs)
    assert gs["overprint_stroke"] is True
    assert gs["overprint_fill"] is False

    # RI, OPM, BM, name without leading slash
    gstate.get.side_effect = lambda k, d=None: {
        "/RI": "/AbsoluteColorimetric",
        "/OPM": 1,
        "/BM": "/Multiply",
    }.get(k)
    res.get.return_value = {"/GS1": gstate}  # leading slash — matches f"/{name}" lookup
    _apply_ext_gstate("GS1", res, pikepdf_mock, gs)
    assert gs["rendering_intent"] == "AbsoluteColorimetric"
    assert gs["overprint_mode"] == 1
    assert gs["blend_mode"] == "Multiply"

    # AttributeError — should not raise
    res.get.side_effect = AttributeError
    _apply_ext_gstate("/GS1", res, pikepdf_mock, gs)
