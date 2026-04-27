from unittest.mock import MagicMock, patch
from pdftl.operations.modify_layers import _resolve_targets, _process_content_stream, modify_layers


# --- Mocks for Testing ---
def create_mock_ocg(obj_id, name):
    ocg = MagicMock()
    ocg.objgen = [obj_id, 0]
    ocg.get.return_value = f"/{name}"
    return ocg


def test_resolve_targets():
    pdf = MagicMock()
    ocg1 = create_mock_ocg(10, "Layer1")
    ocg2 = create_mock_ocg(20, "Layer2")
    ocg3 = create_mock_ocg(30, "Layer3")

    pdf.Root.get.return_value = [ocg1, ocg2, ocg3]

    # Strict ID overrides sloppy name
    rules_by_id = {10: "merge"}
    rules_by_name = {"Layer1": "strip", "Layer2": "strip"}

    targets = _resolve_targets(pdf, rules_by_id, rules_by_name, "keep")

    assert targets[10] == "merge"  # ID takes precedence
    assert targets[20] == "strip"
    assert 30 not in targets  # Kept targets aren't tracked


@patch("pdftl.operations.modify_layers.get_page_layer_map")
@patch("pikepdf.parse_content_stream")
@patch("pikepdf.unparse_content_stream")
def test_process_content_stream(mock_unparse, mock_parse, mock_get_map):
    pdf = MagicMock()
    stream_dict = MagicMock()
    stream_dict.get.return_value = "/Page"

    # Setup maps
    prop_map = {"/MC0": 10, "/MC1": 20}
    xobj_map = {"/Fm0": {30}}
    mock_get_map.return_value = (prop_map, xobj_map)

    # Resolved targets: ID 10 is strip, ID 20 is merge, ID 30 is merge
    resolved = {10: "strip", 20: "merge", 30: "merge"}

    # Create an artificial stream
    mock_stream = [
        # Layer 10 (Strip) - should be completely dropped
        (["/OC", "/MC0"], "BDC"),
        (["0", "0", "m"], "l"),
        ([], "EMC"),
        # Layer 20 (Merge) - tags dropped, content kept
        (["/OC", "/MC1"], "BDC"),
        (["1", "1", "m"], "l"),
        ([], "EMC"),
        # Nested BMC inside a Merge
        (["/OC", "/MC1"], "BDC"),
        (["/Span"], "BMC"),
        (["2", "2", "m"], "l"),
        ([], "EMC"),
        ([], "EMC"),
        # XObject (Merge) - The 'Do' should be KEPT, but its /OC tag deleted
        (["/Fm0"], "Do"),
    ]
    mock_parse.return_value = mock_stream
    mock_unparse.return_value = b"new_stream"

    # We need to mock the resources for the XObject mergeing logic
    xobj_mock = MagicMock()
    xobj_mock.__contains__.return_value = True  # Has /OC
    stream_dict.get.return_value = MagicMock(XObject={"/Fm0": xobj_mock})

    _process_content_stream(pdf, stream_dict, resolved)

    # Verify unparse was called with the correct filtered stream
    filtered_stream = mock_unparse.call_args[0][0]
    operators = [(ops, str(op)) for ops, op in filtered_stream]

    expected = [
        (["1", "1", "m"], "l"),  # Content from Layer 20
        (["/Span"], "BMC"),  # Inner BMC tag kept
        (["2", "2", "m"], "l"),  # Inner content kept
        ([], "EMC"),  # Inner EMC kept
        (["/Fm0"], "Do"),  # Do command kept
    ]

    assert operators == expected
    # Verify the /OC key was deleted from the mergeed XObject
    assert xobj_mock.__delitem__.called_with("/OC")


@patch("pdftl.operations.modify_layers.parse_modify_layers_rules")
@patch("pdftl.operations.modify_layers._resolve_targets")
@patch("pdftl.operations.modify_layers._process_content_stream")
@patch("pdftl.operations.modify_layers.clean_ocproperties")
def test_modify_layers_orchestrator(mock_clean, mock_process, mock_resolve, mock_parse):
    pdf = MagicMock()
    pdf.pages = [MagicMock()]

    # Case 1: No targets matched
    mock_parse.return_value = ({}, {}, "keep")
    mock_resolve.return_value = {}

    result = modify_layers(pdf, ["strip", "all"])
    assert result.success
    assert "No matching layers" in result.data
    mock_process.assert_not_called()

    # Case 2: Targets matched
    mock_resolve.return_value = {1: "strip"}
    result = modify_layers(pdf, ["strip", "all"])
    assert result.success
    assert "Targets matched: 1" in result.data
    mock_process.assert_called_once()
    mock_clean.assert_called_once_with(pdf, {1})


def test_process_content_stream_invalid_stream():
    """Hits the `except pikepdf.PdfError: return` block if the stream can't be parsed."""
    pdf = MagicMock()
    stream_dict = MagicMock()
    import pikepdf

    with patch("pikepdf.parse_content_stream", side_effect=pikepdf.PdfError("Bad stream")):
        # Should gracefully return without blowing up
        _process_content_stream(pdf, stream_dict, {})


def test_process_content_stream_recursion_guard():
    """Hits the check that prevents infinite loops on cyclic XObjects."""
    pdf = MagicMock()
    stream_dict = MagicMock()
    stream_dict.objgen = (99, 0)

    processed_xobjs = {(99, 0)}  # Already processed!

    # Should exit immediately, never calling resources.get
    _process_content_stream(pdf, stream_dict, {}, processed_xobjs)
    stream_dict.get.assert_not_called()


def test_process_content_stream_form_xobject_write():
    """Hits the `else` branch for writing back to a Form XObject stream."""
    pdf = MagicMock()
    stream_dict = MagicMock()
    stream_dict.get.return_value = "/Form"  # Not a /Page

    with (
        patch("pikepdf.parse_content_stream", return_value=[]),
        patch("pikepdf.unparse_content_stream", return_value=b"new_data"),
        patch("pdftl.operations.modify_layers.get_page_layer_map", return_value=({}, {})),
    ):
        _process_content_stream(pdf, stream_dict, {})

        # Ensure write() was called instead of assigning to Contents
        stream_dict.write.assert_called_once_with(b"new_data")


def test_resolve_targets_no_match():
    """Line 120ish: Hits the branch where a layer doesn't match any specific rule."""
    pdf = MagicMock()
    ocg1 = create_mock_ocg(10, "UnknownLayer")
    pdf.Root.get.return_value = [ocg1]

    targets = _resolve_targets(pdf, {}, {"SpecificName": "strip"}, "keep")
    assert targets == {}  # No matches found


def test_process_content_stream_missing_xobj():
    """Line 137ish: 'Do' operator is called, but the XObject name isn't in our xobj_map."""
    pdf = MagicMock()
    stream_dict = MagicMock()
    stream_dict.get.return_value = "/Page"

    with (
        patch("pikepdf.parse_content_stream", return_value=[(["/MissingFm"], "Do")]),
        patch("pikepdf.unparse_content_stream"),
        patch("pdftl.operations.modify_layers.get_page_layer_map", return_value=({}, {})),
    ):
        # Should process silently and ignore the missing XObject
        _process_content_stream(pdf, stream_dict, {})


def test_process_content_stream_exception_catch():
    """Line 67-69ish: Hitting the generic exception catch for unparsable streams."""
    pdf = MagicMock()
    stream_dict = MagicMock()

    # Simulate a corrupted stream that throws an error
    with patch("pikepdf.parse_content_stream", side_effect=ValueError("Corrupt stream data")):
        # Should return silently without bubbling up the crash
        _process_content_stream(pdf, stream_dict, {})


def test_modify_layers_no_ocproperties():
    """Line 187ish: Early exit in orchestrator if PDF has absolutely no layers."""
    pdf = MagicMock()
    pdf.Root.get.return_value = None  # No /OCProperties

    result = modify_layers(pdf, ["strip", "all"])
    assert result.success
    assert "No layers found" in result.data or "No matching layers" in result.data


def test_process_content_stream_recursive_form():
    """Hits lines 67-69: Recursively diving into a Form XObject."""
    pdf = MagicMock()

    # Outer stream
    stream_dict = MagicMock()

    # Inner form XObject mock
    form_xobj = MagicMock()
    # It needs to return "/Form" for Subtype, but nothing for Resources so we don't infinitely recurse
    form_xobj.get.side_effect = lambda k: "/Form" if k == "/Subtype" else None

    # Setup resources for outer stream containing the form
    resources_mock = MagicMock()
    resources_mock.__contains__.return_value = True  # Tells it "/XObject" is in resources
    resources_mock.XObject.items.return_value = [("/MyFormAlias", form_xobj)]

    # Make the outer stream return our mocked resources
    stream_dict.get.side_effect = lambda k: resources_mock if k == "/Resources" else None

    with (
        patch("pikepdf.parse_content_stream", return_value=[]),
        patch("pikepdf.unparse_content_stream"),
        patch("pdftl.operations.modify_layers.get_page_layer_map", return_value=({}, {})),
    ):
        _process_content_stream(pdf, stream_dict, {})

        # If it reached lines 67-69, it will have recursively called itself
        # and attempted to get the Subtype/Resources of the inner form_xobj
        form_xobj.get.assert_called()


def test_process_content_stream_strip_do_operator():
    """Hits line 120: continue loop when dropping a 'Do' command for a stripped layer."""
    pdf = MagicMock()
    stream_dict = MagicMock()
    stream_dict.get.return_value = None

    # XObject /FmToStrip is mapped to layer 99, which is resolved to "strip"
    mock_get_map = ({}, {"/FmToStrip": {99}})
    resolved_targets = {99: "strip"}

    mock_stream = [
        (["/FmToStrip"], "Do"),  # This should trigger the `continue` on line 120
        (["/KeepMe"], "Do"),  # This should bypass line 120
    ]

    with (
        patch("pikepdf.parse_content_stream", return_value=mock_stream),
        patch("pikepdf.unparse_content_stream") as mock_unparse,
        patch("pdftl.operations.modify_layers.get_page_layer_map", return_value=mock_get_map),
    ):
        _process_content_stream(pdf, stream_dict, resolved_targets)

        # Verify only the /KeepMe operator made it to unparse
        filtered_stream = mock_unparse.call_args[0][0]
        operators = [(ops, str(op)) for ops, op in filtered_stream]
        assert operators == [(["/KeepMe"], "Do")]
