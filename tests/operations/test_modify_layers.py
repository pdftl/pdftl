from unittest.mock import MagicMock, patch

import pikepdf

from pdftl.operations.modify_layers import (
    _ensure_auto_state,
    _process_content_stream,
    _resolve_targets,
    modify_layers,
)


_KEEP_ALIVE = []


def _real_ocgs(n):
    pdf = pikepdf.new()
    _KEEP_ALIVE.append(pdf)
    return [pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.OCG)) for _ in range(n)]


def _id(ocg):
    return ocg.objgen[0]


# --- Mocks for Testing ---
def create_mock_ocg(obj_id, name):
    ocg = MagicMock()
    ocg.objgen = [obj_id, 0]
    ocg.get.return_value = f"/{name}"
    return ocg


@patch("pdftl.operations.modify_layers.get_page_layer_map")
@patch("pikepdf.parse_content_stream")
@patch("pikepdf.unparse_content_stream")
def test_process_content_stream(mock_unparse, mock_parse, mock_get_map):
    pdf = MagicMock()
    stream_dict = MagicMock()
    stream_dict.get.return_value = "/Page"

    # Setup maps
    ocg10, ocg20, ocg30 = _real_ocgs(3)
    prop_map = {"/MC0": ocg10, "/MC1": ocg20}
    xobj_map = {"/Fm0": ocg30}
    mock_get_map.return_value = (prop_map, xobj_map)

    # Resolved targets: layer 10 is strip, 20 is merge, 30 is merge
    resolved = {_id(ocg10): "strip", _id(ocg20): "merge", _id(ocg30): "merge"}

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

    # Setup the XObject mock
    xobj_mock = MagicMock()
    xobj_mock.__contains__.return_value = True  # Tells 'if "/OC" in xobj:' to return True

    # Setup the Resources mock (Your prod code uses resources.XObject)
    mock_resources = MagicMock()
    mock_resources.XObject = {"/Fm0": xobj_mock}

    # Route the .get() calls so the stream finds the resources
    def mock_stream_get(key, default=None):
        if key == "/Type":
            return "/Page"
        if key == "/Resources":
            return mock_resources
        return default

    stream_dict.get.side_effect = mock_stream_get

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
    # Verify the /OC key was deleted from the merged XObject
    xobj_mock.__delitem__.assert_called_with("/OC")


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
    resources_mock.__contains__.return_value = True

    resources_mock.XObject.values.return_value = [form_xobj]

    # (Optional) Keep .items() mocked if other parts of the code still use it
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

    # XObject /FmToStrip belongs to a layer resolved to "strip"
    (ocg,) = _real_ocgs(1)
    mock_get_map = ({}, {"/FmToStrip": ocg})
    resolved_targets = {_id(ocg): "strip"}

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


def _named_layers_pdf(*names):
    import pikepdf

    pdf = pikepdf.new()
    ocgs = [
        pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.OCG, Name=pikepdf.String(n)))
        for n in names
    ]
    pdf.Root.OCProperties = pikepdf.Dictionary(OCGs=pikepdf.Array(ocgs))
    return pdf, [o.objgen[0] for o in ocgs]


def test_resolve_targets():
    pdf, (id1, _, _) = _named_layers_pdf("Layer1", "Layer2", "Layer3")

    rules_by_id = {id1: {"merge"}}
    rules_by_name = {"Layer1": {"strip"}, "Layer2": {"strip"}}

    targets = _resolve_targets(pdf, rules_by_id, rules_by_name, {"keep"})

    # The function correctly drops 'keep' and only tracks 'merge'
    assert targets[id1] == {"merge"}


def test_resolve_targets_specific_overrides_all():
    """Old code returned {'hide', 'show'} here, applied in hash-seed order."""
    pdf, (id_a, id_b) = _named_layers_pdf("A", "B")

    targets = _resolve_targets(pdf, {}, {"A": {"show"}}, {"hide"})

    assert targets == {id_a: {"show"}, id_b: {"hide"}}


def test_resolve_targets_keep_overrides_strip_all():
    pdf, (_, id_b) = _named_layers_pdf("A", "B")

    targets = _resolve_targets(pdf, {}, {"A": {"keep"}}, {"strip"})

    assert targets == {id_b: {"strip"}}


def test_resolve_targets_skips_direct_layers():
    pdf, (id_a,) = _named_layers_pdf("A")
    direct = pikepdf.Dictionary(Type=pikepdf.Name.OCG, Name=pikepdf.String("B"))
    pdf.Root.OCProperties.OCGs.append(direct)

    assert _resolve_targets(pdf, {}, {}, {"strip"}) == {id_a: {"strip"}}


def _two_layer_pdf():
    from pdftl.utils.ocg import create_layer

    pdf = pikepdf.new()
    pdf.add_blank_page()
    return pdf, create_layer(pdf, "A"), create_layer(pdf, "B")


def _ids(arr):
    return {o.objgen[0] for o in arr}


def test_hide_all_show_one_end_to_end():
    pdf, a, b = _two_layer_pdf()
    modify_layers(pdf, ["hide", "all", "show", "A"])
    d = pdf.Root.OCProperties.D
    assert _ids(d.ON) == {a.objgen[0]}
    assert _ids(d.OFF) == {b.objgen[0]}


def test_show_all_hide_one_end_to_end():
    pdf, a, b = _two_layer_pdf()
    modify_layers(pdf, ["show", "all", "hide", "A"])
    d = pdf.Root.OCProperties.D
    assert _ids(d.ON) == {b.objgen[0]}
    assert _ids(d.OFF) == {a.objgen[0]}


def test_strip_all_keep_one_end_to_end():
    pdf, a, _b = _two_layer_pdf()
    modify_layers(pdf, ["strip", "all", "keep", "A"])
    assert [o.objgen for o in pdf.Root.OCProperties.OCGs] == [a.objgen]


def test_resolve_targets_no_match():
    """Line 120ish: Hits the branch where a layer doesn't match any specific rule."""
    pdf = MagicMock()
    ocg1 = create_mock_ocg(10, "UnknownLayer")
    pdf.Root.get.return_value = [ocg1]

    targets = _resolve_targets(pdf, {}, {"SpecificName": {"strip"}}, {"keep"})

    # Because 'keep' is a no-op, the queue correctly remains empty
    assert targets == {}


@patch("pdftl.operations.modify_layers.parse_modify_layers_rules")
@patch("pdftl.operations.modify_layers._resolve_targets")
@patch("pdftl.operations.modify_layers._process_content_stream")
@patch("pdftl.operations.modify_layers._process_annotations")
@patch("pdftl.operations.modify_layers.clean_ocproperties")
def test_modify_layers_merge(mock_clean, _mock_annots, mock_process, mock_resolve, mock_parse):
    """Hits lines 269-270: The 'merge' structural action branch."""
    pdf = MagicMock()
    pdf.pages = ["dummy_page"]

    mock_parse.return_value = ({}, {}, {"merge"})
    mock_resolve.return_value = {123: {"merge"}}

    result = modify_layers(pdf, ["in.pdf", "modify_layers", "merge", "all", "output", "out.pdf"])

    assert result.success is True
    mock_process.assert_called_with(pdf, "dummy_page", {123: "merge"}, set())
    mock_clean.assert_called_with(pdf, {123})


@patch("pdftl.operations.modify_layers.parse_modify_layers_rules")
@patch("pdftl.operations.modify_layers._resolve_targets")
@patch("pdftl.operations.modify_layers.set_layer_state")
@patch("pdftl.operations.modify_layers.set_layer_usage")
def test_modify_layers_state_and_usage(mock_usage, mock_state, mock_resolve, mock_parse):
    """Hits lines 289 & 291: The state and usage action loops."""
    pdf = MagicMock()
    mock_parse.return_value = ({}, {}, set())

    # Resolve multiple different actions
    mock_resolve.return_value = {10: {"hide", "lock"}, 11: {"noprint", "screen"}}

    result = modify_layers(pdf, [])

    assert result.success is True
    mock_state.assert_any_call(pdf, {10}, "hide")
    mock_state.assert_any_call(pdf, {10}, "lock")
    mock_usage.assert_any_call(pdf, {11}, "noprint")
    mock_usage.assert_any_call(pdf, {11}, "screen")


def test_ensure_auto_state_creates_missing_as_array():
    pdf = pikepdf.Pdf.new()
    ocg1 = pdf.make_indirect(pikepdf.Dictionary({"/Type": pikepdf.Name("/OCG")}))

    # Mock /OCProperties with /D but NO /AS array
    pdf.Root.OCProperties = pikepdf.Dictionary(
        {"/D": pikepdf.Dictionary(), "/OCGs": pikepdf.Array([ocg1])}
    )

    # Run the function (will hit lines 84-85, and 106-126)
    _ensure_auto_state(pdf)

    # Assert the /AS array was created and populated
    assert "/AS" in pdf.Root.OCProperties.D
    as_array = pdf.Root.OCProperties.D.AS
    events = [str(as_dict.get("/Event")) for as_dict in as_array]
    assert "/Print" in events
    assert "/View" in events


def test_ensure_auto_state_updates_existing_events():
    pdf = pikepdf.Pdf.new()
    ocg1 = pdf.make_indirect(pikepdf.Dictionary({"/Type": pikepdf.Name("/OCG")}))
    ocg2 = pdf.make_indirect(pikepdf.Dictionary({"/Type": pikepdf.Name("/OCG")}))

    # Mock an existing /AS array that only has /Print, and only maps to ocg1
    existing_print_dict = pikepdf.Dictionary(
        {"/Event": pikepdf.Name("/Print"), "/OCGs": pikepdf.Array([ocg1])}
    )

    pdf.Root.OCProperties = pikepdf.Dictionary(
        {
            "/D": pikepdf.Dictionary({"/AS": pikepdf.Array([existing_print_dict])}),
            # Note: We now have TWO OCGs in the document
            "/OCGs": pikepdf.Array([ocg1, ocg2]),
        }
    )

    # Run the function (will hit lines 95-103 to update /Print, and 118-126 to append /View)
    _ensure_auto_state(pdf)

    as_array = pdf.Root.OCProperties.D.AS
    events = {str(as_dict.get("/Event")): as_dict for as_dict in as_array}

    # Assert both exist
    assert "/Print" in events
    assert "/View" in events

    # Assert both were updated to contain all OCGs
    assert len(events["/Print"].OCGs) == 2
    assert len(events["/View"].OCGs) == 2


def test_ensure_auto_state_updates_existing_view_event():
    import pikepdf

    from pdftl.operations.modify_layers import _ensure_auto_state

    pdf = pikepdf.Pdf.new()
    ocg1 = pdf.make_indirect(pikepdf.Dictionary({"/Type": pikepdf.Name("/OCG")}))
    ocg2 = pdf.make_indirect(pikepdf.Dictionary({"/Type": pikepdf.Name("/OCG")}))

    # Mock an existing /AS array that only has /View, mapped to a single OCG
    existing_view_dict = pikepdf.Dictionary(
        {"/Event": pikepdf.Name("/View"), "/OCGs": pikepdf.Array([ocg1])}
    )

    pdf.Root.OCProperties = pikepdf.Dictionary(
        {
            "/D": pikepdf.Dictionary({"/AS": pikepdf.Array([existing_view_dict])}),
            # Note: Two OCGs are in the document total
            "/OCGs": pikepdf.Array([ocg1, ocg2]),
        }
    )

    # Run the function (will hit lines 101-103 to update /View)
    _ensure_auto_state(pdf)

    as_array = pdf.Root.OCProperties.D.AS
    events = {str(as_dict.get("/Event")): as_dict for as_dict in as_array}

    # Assert it was successfully updated to contain all OCGs
    assert "/View" in events
    assert len(events["/View"].OCGs) == 2


def _layered_pdf():
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page()

    def ocg(name=None):
        d = pikepdf.Dictionary({"/Type": pikepdf.Name("/OCG")})
        if name:
            d["/Name"] = pikepdf.String(name)
        return pdf.make_indirect(d)

    def form(oc=None):
        f = pdf.make_stream(b"0 0 1 1 re f")
        f["/Type"] = pikepdf.Name("/XObject")
        f["/Subtype"] = pikepdf.Name("/Form")
        f["/BBox"] = pikepdf.Array([0, 0, 1, 1])
        if oc is not None:
            f["/OC"] = oc
        return f

    ocg_a, ocg_b, ocg_unnamed = ocg("A"), ocg("B"), ocg()
    image = pdf.make_stream(b"\x00")
    image.update(
        {
            "/Type": pikepdf.Name("/XObject"),
            "/Subtype": pikepdf.Name("/Image"),
            "/Width": 1,
            "/Height": 1,
            "/ColorSpace": pikepdf.Name("/DeviceGray"),
            "/BitsPerComponent": 8,
        }
    )
    page.Resources = pikepdf.Dictionary(
        {
            "/Properties": pikepdf.Dictionary({"/oc1": ocg_a}),
            "/XObject": pikepdf.Dictionary(
                {"/Fm1": form(ocg_a), "/Fm2": form(ocg_b), "/Im1": image}
            ),
        }
    )
    page.Contents = pdf.make_stream(
        b"EMC\n/OC /oc1 BDC 1 0 0 RG EMC\n/Fm1 Do\n/Fm1 Do\n/Fm2 Do\n/Im1 Do\n"
    )
    pdf.Root.OCProperties = pikepdf.Dictionary(
        {
            "/OCGs": pikepdf.Array([ocg_a, ocg_b, ocg_unnamed]),
            "/D": pikepdf.Dictionary({"/Order": pikepdf.Array([ocg_a, ocg_b, ocg_unnamed])}),
        }
    )
    return pdf, ocg_a, ocg_b, ocg_unnamed


def test_modify_layers_merge_real_pdf():
    from pdftl.operations.modify_layers import modify_layers

    pdf, ocg_a, ocg_b, ocg_unnamed = _layered_pdf()
    modify_layers(pdf, ["merge", "name=A"])

    page = pdf.pages[0]
    ops = [
        (str(op), [str(o) for o in operands])
        for operands, op in pikepdf.parse_content_stream(page)
    ]
    assert ops == [
        ("EMC", []),
        ("RG", ["1", "0", "0"]),
        ("Do", ["/Fm1"]),
        ("Do", ["/Fm1"]),
        ("Do", ["/Fm2"]),
        ("Do", ["/Im1"]),
    ]
    xobjs = page.Resources.XObject
    assert "/OC" not in xobjs.Fm1
    assert xobjs.Fm2.OC.objgen == ocg_b.objgen
    remaining = [o.objgen for o in pdf.Root.OCProperties.OCGs]
    assert remaining == [ocg_b.objgen, ocg_unnamed.objgen]


def test_ensure_auto_state_leaves_other_events_alone():
    pdf = pikepdf.Pdf.new()
    ocg1 = pdf.make_indirect(pikepdf.Dictionary({"/Type": pikepdf.Name("/OCG")}))
    export = pikepdf.Dictionary({"/Event": pikepdf.Name("/Export"), "/OCGs": pikepdf.Array()})
    pdf.Root.OCProperties = pikepdf.Dictionary(
        {
            "/D": pikepdf.Dictionary({"/AS": pikepdf.Array([export])}),
            "/OCGs": pikepdf.Array([ocg1]),
        }
    )
    _ensure_auto_state(pdf)
    as_array = pdf.Root.OCProperties.D.AS
    assert [str(d.Event) for d in as_array] == ["/Export", "/Print", "/View"]
    assert len(as_array[0].OCGs) == 0


def _ocmd_pdf():
    pdf = pikepdf.new()
    pdf.add_blank_page()
    page = pdf.pages[0]
    a, b = (
        pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.OCG, Name=pikepdf.String(n)))
        for n in ("A", "B")
    )

    def ocmd(**entries):
        return pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.OCMD, **entries))

    def form():
        return pdf.make_stream(
            b"0 0 1 1 re f",
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Form,
            BBox=[0, 0, 1, 1],
        )

    any_on = ocmd(OCGs=pikepdf.Array([a, b]))
    all_on = ocmd(OCGs=pikepdf.Array([a, b]), P=pikepdf.Name.AllOn)
    fm_any, fm_not = form(), form()
    fm_any.OC = ocmd(OCGs=pikepdf.Array([a, b]))
    fm_not.OC = ocmd(VE=pikepdf.Array([pikepdf.Name.Not, a]))
    page.Resources = pikepdf.Dictionary(
        Properties=pikepdf.Dictionary(MC0=any_on, MC1=all_on),
        XObject=pikepdf.Dictionary(FmAny=fm_any, FmNot=fm_not),
    )
    page.Contents = pdf.make_stream(
        b"/OC /MC0 BDC 1 w EMC /OC /MC1 BDC 2 w EMC /FmAny Do /FmNot Do"
    )
    pdf.Root.OCProperties = pikepdf.Dictionary(
        OCGs=pikepdf.Array([a, b]), D=pikepdf.Dictionary(Order=pikepdf.Array([a, b]))
    )
    return pdf, a, b


def _page_ops(pdf):
    return [
        (str(op), [str(x) for x in operands])
        for operands, op in pikepdf.parse_content_stream(pdf.pages[0])
    ]


def _member_ids(ocmd):
    return [o.objgen[0] for o in ocmd.OCGs]


def test_strip_folds_membership_dictionaries():
    pdf, a, b = _ocmd_pdf()
    modify_layers(pdf, ["strip", "A"])

    # AnyOn [A B] now depends on B alone; AllOn [A B] is always hidden.
    assert _page_ops(pdf) == [
        ("BDC", ["/OC", "/MC0"]),
        ("w", ["1"]),
        ("EMC", []),
        ("Do", ["/FmAny"]),
        ("Do", ["/FmNot"]),
    ]
    resources = pdf.pages[0].Resources
    assert _member_ids(resources.Properties.MC0) == [b.objgen[0]]
    assert _member_ids(resources.XObject.FmAny.OC) == [b.objgen[0]]
    # [/Not A] is always visible once A is gone.
    assert "/OC" not in resources.XObject.FmNot


def test_merge_folds_membership_dictionaries():
    pdf, a, b = _ocmd_pdf()
    modify_layers(pdf, ["merge", "A"])

    # AnyOn [A B] is always visible; AllOn [A B] now depends on B alone.
    assert _page_ops(pdf) == [
        ("w", ["1"]),
        ("BDC", ["/OC", "/MC1"]),
        ("w", ["2"]),
        ("EMC", []),
        ("Do", ["/FmAny"]),
    ]
    resources = pdf.pages[0].Resources
    assert _member_ids(resources.Properties.MC1) == [b.objgen[0]]
    assert "/OC" not in resources.XObject.FmAny


def _annot_pdf():
    pdf, a, b = _ocmd_pdf()
    page = pdf.pages[0].obj

    def annot(subtype, **entries):
        return pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Annot,
                Subtype=pikepdf.Name(subtype),
                Rect=[0, 0, 1, 1],
                **entries,
            )
        )

    return pdf, page, a, b, annot


def _annot_ids(page):
    return [x.objgen for x in page.Annots]


def test_strip_removes_annotations_and_their_popups():
    pdf, page, a, b, annot = _annot_pdf()
    on_a = annot("/Square", OC=a)
    popup = annot("/Popup", Parent=on_a)
    on_a.Popup = popup
    orphan_popup = annot("/Popup", Parent=on_a)
    on_b = annot("/Square", OC=b)
    plain = annot("/Square")
    page.Annots = pikepdf.Array([on_a, popup, orphan_popup, on_b, plain])

    modify_layers(pdf, ["strip", "A"])
    assert _annot_ids(page) == [on_b.objgen, plain.objgen]


def test_merge_makes_annotations_unconditional():
    pdf, page, a, b, annot = _annot_pdf()
    on_a = annot("/Square", OC=a)
    on_b = annot("/Square", OC=b)
    page.Annots = pikepdf.Array([on_a, on_b])

    modify_layers(pdf, ["merge", "A"])
    assert _annot_ids(page) == [on_a.objgen, on_b.objgen]
    assert "/OC" not in on_a
    assert on_b.OC.objgen == b.objgen


def test_strip_rewrites_annotation_membership_dictionary():
    pdf, page, a, b, annot = _annot_pdf()
    ocmd = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.OCMD, OCGs=pikepdf.Array([a, b]))
    )
    kept = annot("/Square", OC=ocmd)
    page.Annots = pikepdf.Array([kept])

    modify_layers(pdf, ["strip", "A"])
    assert _annot_ids(page) == [kept.objgen]
    assert _member_ids(kept.OC) == [b.objgen[0]]


def test_strip_removes_form_fields_on_the_layer():
    pdf, page, a, b, annot = _annot_pdf()
    lone = annot("/Widget", OC=a, FT=pikepdf.Name.Tx, T=pikepdf.String("lone"))
    parent = pdf.make_indirect(pikepdf.Dictionary(FT=pikepdf.Name.Btn, T=pikepdf.String("p")))
    kid_a = annot("/Widget", OC=a, Parent=parent)
    kid_b = annot("/Widget", OC=b, Parent=parent)
    parent.Kids = pikepdf.Array([kid_a, kid_b])
    only_parent = pdf.make_indirect(pikepdf.Dictionary(FT=pikepdf.Name.Tx, T=pikepdf.String("q")))
    only_kid = annot("/Widget", OC=a, Parent=only_parent)
    only_parent.Kids = pikepdf.Array([only_kid])
    page.Annots = pikepdf.Array([lone, kid_a, kid_b, only_kid])
    pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array([lone, parent, only_parent]))

    modify_layers(pdf, ["strip", "A"])
    assert _annot_ids(page) == [kid_b.objgen]
    assert [f.objgen for f in pdf.Root.AcroForm.Fields] == [parent.objgen]
    assert [k.objgen for k in parent.Kids] == [kid_b.objgen]


def test_strip_processes_annotation_appearance_streams():
    pdf, page, a, b, annot = _annot_pdf()
    appearance = pdf.make_stream(
        b"/OC /MC0 BDC 1 w EMC 2 w",
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Form,
        BBox=[0, 0, 1, 1],
        Resources=pikepdf.Dictionary(Properties=pikepdf.Dictionary(MC0=a)),
    )
    kept = annot("/Square", AP=pikepdf.Dictionary(N=appearance))
    page.Annots = pikepdf.Array([kept])

    modify_layers(pdf, ["strip", "A"])
    ops = [
        (str(op), [str(x) for x in args]) for args, op in pikepdf.parse_content_stream(kept.AP.N)
    ]
    assert ops == [("w", ["2"])]


def test_strip_skips_malformed_and_direct_annotations():
    pdf, page, a, b, annot = _annot_pdf()
    direct_on_a = pikepdf.Dictionary(Subtype=pikepdf.Name.Square, OC=a)
    direct_plain = pikepdf.Dictionary(Subtype=pikepdf.Name.Square)
    page.Annots = pikepdf.Array([5, direct_on_a, direct_plain])

    modify_layers(pdf, ["strip", "A"])
    assert len(page.Annots) == 2
    assert page.Annots[0] == 5
    assert "/OC" not in page.Annots[1]


def test_strip_removes_widgets_outside_a_field_tree():
    pdf, page, a, b, annot = _annot_pdf()
    direct_widget = pikepdf.Dictionary(Subtype=pikepdf.Name.Widget, OC=a)
    orphan_widget = annot("/Widget", OC=a)
    page.Annots = pikepdf.Array([direct_widget, orphan_widget])

    modify_layers(pdf, ["strip", "A"])
    assert len(page.Annots) == 0
    assert "/AcroForm" not in pdf.Root


def test_strip_processes_appearance_state_streams():
    pdf, page, a, b, annot = _annot_pdf()

    def appearance(content):
        return pdf.make_stream(
            content,
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Form,
            BBox=[0, 0, 1, 1],
            Resources=pikepdf.Dictionary(Properties=pikepdf.Dictionary(MC0=a)),
        )

    states = pikepdf.Dictionary(On=appearance(b"/OC /MC0 BDC 1 w EMC 2 w"), Off=5)
    kept = annot("/Widget", AP=pikepdf.Dictionary(N=states))
    page.Annots = pikepdf.Array([kept])

    modify_layers(pdf, ["strip", "A"])
    ops = [
        (str(op), [str(x) for x in args])
        for args, op in pikepdf.parse_content_stream(kept.AP.N.On)
    ]
    assert ops == [("w", ["2"])]
