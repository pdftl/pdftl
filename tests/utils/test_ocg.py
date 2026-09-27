import pytest
import pikepdf

from pdftl.utils.ocg import (
    _remove_targets_from_array,
    clean_ocproperties,
    create_layer,
    fold_visibility,
    get_page_layer_map,
    ocg_id_list,
    set_layer_state,
    set_layer_usage,
)


def test_remove_targets_from_array():
    pdf = pikepdf.Pdf.new()
    obj1 = pdf.make_indirect(pikepdf.Dictionary(Type="/OCG"))
    obj2 = pdf.make_indirect(pikepdf.Dictionary(Type="/OCG"))
    obj3 = pdf.make_indirect(pikepdf.Dictionary(Type="/OCG"))
    obj4 = pdf.make_indirect(pikepdf.Dictionary(Type="/OCG"))
    obj5 = pdf.make_indirect(pikepdf.Dictionary(Type="/OCG"))

    sub_arr = pikepdf.Array([obj4, obj5])
    arr = pikepdf.Array([obj1, obj2, obj3, sub_arr])

    # Target obj2 and obj4 for removal
    targets = {obj2.objgen[0], obj4.objgen[0]}
    _remove_targets_from_array(arr, targets)

    assert len(arr) == 3  # obj1, obj3, sub_arr
    assert arr[0].objgen[0] == obj1.objgen[0]
    assert arr[1].objgen[0] == obj3.objgen[0]
    assert len(arr[2]) == 1
    assert arr[2][0].objgen[0] == obj5.objgen[0]


def test_remove_targets_from_array_cleans_empty():
    pdf = pikepdf.Pdf.new()
    obj1 = pdf.make_indirect(pikepdf.Dictionary())
    obj2 = pdf.make_indirect(pikepdf.Dictionary())

    sub_arr = pikepdf.Array([obj2])
    arr = pikepdf.Array([obj1, sub_arr])

    _remove_targets_from_array(arr, {obj2.objgen[0]})

    # sub_arr becomes empty and should be deleted automatically
    assert len(arr) == 1
    assert arr[0].objgen[0] == obj1.objgen[0]


def test_clean_ocproperties():
    pdf = pikepdf.Pdf.new()
    obj1 = pdf.make_indirect(pikepdf.Dictionary())
    obj2 = pdf.make_indirect(pikepdf.Dictionary())

    # Build OCProperties skeleton
    ocgs_array = pikepdf.Array([obj1, obj2])
    pdf.Root.OCProperties = pikepdf.Dictionary(OCGs=ocgs_array)

    # Action: remove 1
    clean_ocproperties(pdf, {obj1.objgen[0]})
    assert len(pdf.Root.OCProperties.OCGs) == 1
    assert "/OCProperties" in pdf.Root

    # Action: remove 2 (array is now empty, dict should be deleted)
    clean_ocproperties(pdf, {obj2.objgen[0]})
    assert "/OCProperties" not in pdf.Root


def test_clean_ocproperties_no_ocproperties():
    """Line 92ish: Early exit if /OCProperties doesn't exist."""
    pdf = pikepdf.Pdf.new()
    clean_ocproperties(pdf, {1})  # Should return silently without error


def test_clean_ocproperties_alternate_configs():
    """Lines 97-101ish: Sweeping the /Configs array and /D dicts."""
    pdf = pikepdf.Pdf.new()
    obj1 = pdf.make_indirect(pikepdf.Dictionary())

    # Create the Default config (/D) and Alternate Configs (/Configs)
    default_dict = pikepdf.Dictionary(ON=pikepdf.Array([obj1]))
    config_dict = pikepdf.Dictionary(
        ON=pikepdf.Array([obj1]), OFF=pikepdf.Array([obj1]), Order=pikepdf.Array([obj1])
    )

    pdf.Root.OCProperties = pikepdf.Dictionary(
        OCGs=pikepdf.Array([obj1]), D=default_dict, Configs=pikepdf.Array([config_dict])
    )

    clean_ocproperties(pdf, {obj1.objgen[0]})
    assert "/OCProperties" not in pdf.Root


def test_get_page_layer_map_missing_keys():
    """Line 16/45ish: Handling resources with no /Properties or /XObject."""
    resources = pikepdf.Dictionary()  # Completely empty
    prop_map, xobj_map = get_page_layer_map(resources)
    assert not prop_map
    assert not xobj_map


def test_remove_targets_ignores_non_indirect():
    """Line 81ish: Safely skips array items that aren't indirect objects (like Names/Strings)."""
    import pikepdf

    arr = pikepdf.Array(["/SomeName", 42])  # Not objects with objgen
    _remove_targets_from_array(arr, {1})
    assert len(arr) == 2  # Remains untouched


def test_get_page_layer_map_none():
    """Hits line 45: resources is explicitly None."""
    prop_map, xobj_map = get_page_layer_map(None)
    assert prop_map == {}
    assert xobj_map == {}


def test_create_layer_initializes_missing_catalog():
    pdf = pikepdf.new()
    pdf.add_blank_page()
    assert "/OCProperties" not in pdf.Root

    ocg = create_layer(pdf, "NewLayer")

    assert "/OCProperties" in pdf.Root
    assert ocg in pdf.Root.OCProperties.OCGs
    assert ocg in pdf.Root.OCProperties.D.Order
    assert ocg in pdf.Root.OCProperties.D.ON


def test_create_layer_appends_to_existing_catalog():
    pdf = pikepdf.new()
    pdf.add_blank_page()
    create_layer(pdf, "Layer1")

    # Add a second one
    create_layer(pdf, "Layer2")

    assert len(pdf.Root.OCProperties.OCGs) == 2
    assert pdf.Root.OCProperties.OCGs[1].Name == "Layer2"


def test_create_layer_partial_ocproperties():
    """Covers ocg.py:132,137,139,145 — create_layer defends against a
    pre-existing but incomplete /OCProperties (missing /OCGs, /D, /Order, /ON)."""
    from pdftl.utils.ocg import create_layer

    pdf = pikepdf.new()
    pdf.add_blank_page()

    # Intentionally skeletal — none of the expected sub-keys are present
    pdf.Root.OCProperties = pikepdf.Dictionary()

    ocg = create_layer(pdf, "DefensiveLayer")

    assert ocg in pdf.Root.OCProperties.OCGs
    assert ocg in pdf.Root.OCProperties.D.Order
    assert ocg in pdf.Root.OCProperties.D.ON


def test_create_layer_missing_order_and_on_in_existing_d():
    """Covers ocg.py:139,145 — /D exists but is missing /Order and /ON."""
    from pdftl.utils.ocg import create_layer

    pdf = pikepdf.new()
    pdf.add_blank_page()

    # /D exists but has neither /Order nor /ON
    pdf.Root.OCProperties = pikepdf.Dictionary(
        OCGs=pikepdf.Array(),
        D=pikepdf.Dictionary(),
    )

    ocg = create_layer(pdf, "SparseLayer")

    assert ocg in pdf.Root.OCProperties.D.Order
    assert ocg in pdf.Root.OCProperties.D.ON


def test_set_layer_state_all_actions():
    """Hits lines 154-190: Covers show, hide, lock, unlock logic."""
    pdf = pikepdf.Pdf.new()

    l1 = create_layer(pdf, "Layer1")
    l2 = create_layer(pdf, "Layer2")
    id1, id2 = int(l1.objgen[0]), int(l2.objgen[0])

    # Test hide & show
    set_layer_state(pdf, {id1}, "hide")
    assert l1 in pdf.Root.OCProperties.D.OFF

    set_layer_state(pdf, {id1}, "show")
    assert l1 in pdf.Root.OCProperties.D.ON
    assert l1 not in pdf.Root.OCProperties.D.OFF

    # Test lock & unlock
    set_layer_state(pdf, {id2}, "lock")
    assert l2 in pdf.Root.OCProperties.D.Locked

    set_layer_state(pdf, {id2}, "unlock")
    assert l2 not in pdf.Root.OCProperties.D.Locked


def test_set_layer_usage_all_actions():
    """Hits lines 197-215: Covers print, noprint, screen, noscreen overrides."""
    pdf = pikepdf.Pdf.new()

    l1 = create_layer(pdf, "Layer1")
    id1 = int(l1.objgen[0])

    # Print / Noprint overrides
    set_layer_usage(pdf, {id1}, "print")
    assert str(l1.Usage.Print.PrintState) == "/ON"

    set_layer_usage(pdf, {id1}, "noprint")
    assert str(l1.Usage.Print.PrintState) == "/OFF"

    # Screen / Noscreen overrides
    set_layer_usage(pdf, {id1}, "screen")
    assert str(l1.Usage.View.ViewState) == "/ON"

    set_layer_usage(pdf, {id1}, "noscreen")
    assert str(l1.Usage.View.ViewState) == "/OFF"


def test_set_layer_missing_ocproperties():
    """Hits the early returns in set_layer_state and set_layer_usage."""
    pdf = pikepdf.Pdf.new()

    # Should safely return without throwing exceptions
    set_layer_state(pdf, {1}, "hide")
    set_layer_usage(pdf, {1}, "print")


def test_set_layer_state_no_matching_targets():
    """Hits line 172: Early return when target_ids don't match any OCGs."""
    import pikepdf

    from pdftl.utils.ocg import create_layer, set_layer_state

    pdf = pikepdf.Pdf.new()

    # Create a layer to initialize the /OCProperties dictionary.
    # Without this, it hits the earlier return at line 157 instead.
    create_layer(pdf, "ValidLayer")

    # Call with a dummy ID that doesn't exist in the PDF.
    # `target_ocgs` will evaluate to [] and safely trigger the return at line 172.
    set_layer_state(pdf, {99999}, "hide")

    # Asserting that it exits gracefully without raising an exception or doing anything
    assert True


def test_set_layer_usage_skip_unmatched_targets():
    """Hits line 226: Safely skips OCGs that are not in the target_ids list."""
    import pikepdf

    from pdftl.utils.ocg import create_layer, set_layer_usage

    pdf = pikepdf.Pdf.new()
    l1 = create_layer(pdf, "Layer1")

    # Target an ID that doesn't match Layer1's ID.
    # When _process_ocg_layer_usage checks Layer1, it will hit the early return.
    set_layer_usage(pdf, {99999}, "print")

    # Assert the layer was skipped and NOT modified
    assert "/Usage" not in l1


def test_get_page_layer_map_non_ocg_properties_and_empty_xobj_ocg():
    """Ensures page layer mapping skips non-OCG property entries and XObjects without OCG IDs."""
    pdf = pikepdf.Pdf.new()
    xobj = pdf.make_stream(b"")  # No /OC

    resources = pikepdf.Dictionary(
        Properties=pikepdf.Dictionary(
            NotAnOCG=pikepdf.Dictionary(Type="/NotOCG"),
            SimpleVal=123,
        ),
        XObject=pikepdf.Dictionary(Fm0=xobj),
    )

    prop_map, xobj_map = get_page_layer_map(resources)
    assert prop_map == {}
    assert xobj_map == {}


def test_clean_ocproperties_missing_ocgs_and_config_keys():
    """Ensures OCProperties cleaning handles missing OCG lists and incomplete config dictionaries gracefully."""
    pdf = pikepdf.Pdf.new()
    # OCProperties exists without OCGs key, and Configs array contains an empty dictionary
    pdf.Root.OCProperties = pikepdf.Dictionary(Configs=pikepdf.Array([pikepdf.Dictionary()]))

    clean_ocproperties(pdf, {1})


def test_set_layer_state_unknown_action():
    """Ensures set_layer_state safely ignores unrecognized layer actions."""
    pdf = pikepdf.Pdf.new()
    l1 = create_layer(pdf, "Layer1")
    id1 = int(l1.objgen[0])

    set_layer_state(pdf, {id1}, "invalid_action")


def test_set_layer_usage_unknown_action():
    """Ensures set_layer_usage safely ignores unrecognized usage actions."""
    pdf = pikepdf.Pdf.new()
    l1 = create_layer(pdf, "Layer1")
    id1 = int(l1.objgen[0])

    set_layer_usage(pdf, {id1}, "invalid_action")


from pdftl.utils.ocg import _get_obj_id


def test_get_obj_id_returns_zero_on_malformed_objgen():
    """Ensures _get_obj_id handles objects with empty or malformed objgen tuples gracefully."""

    class MalformedObjgen:
        objgen = ()

    assert _get_obj_id(MalformedObjgen()) == 0


def test_get_obj_id_returns_zero_on_invalid_objgen_types():
    """Ensures _get_obj_id gracefully handles objects containing non-integer objgen values."""

    class InvalidObjgen:
        objgen = (None,)

    assert _get_obj_id(InvalidObjgen()) == 0


def test_get_page_layer_map_returns_ocgs_and_ocmds():
    pdf = pikepdf.Pdf.new()
    ocg = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.OCG))
    ocmd = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.OCMD, OCGs=ocg))
    fm0, fm1, fm2 = (pdf.make_stream(b"") for _ in range(3))
    fm0.OC, fm1.OC = ocg, ocmd
    pdf.Root.Res = pikepdf.Dictionary(
        Properties=pikepdf.Dictionary(MC0=ocg, MC1=ocmd, MC2=pikepdf.Dictionary(Type="/Other")),
        XObject=pikepdf.Dictionary(Fm0=fm0, Fm1=fm1, Fm2=fm2),
    )

    prop_map, xobj_map = get_page_layer_map(pdf.Root.Res)
    assert {k: v.objgen for k, v in prop_map.items()} == {"/MC0": ocg.objgen, "/MC1": ocmd.objgen}
    assert {k: v.objgen for k, v in xobj_map.items()} == {"/Fm0": ocg.objgen, "/Fm1": ocmd.objgen}


def _layers(n):
    pdf = pikepdf.Pdf.new()
    ocgs = [pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.OCG)) for _ in range(n)]
    return pdf, ocgs


def _visible(oc, states):
    """Visibility by definition (ISO 32000-2 8.11.2.2), `states` mapping id -> on."""
    if oc.Type == "/OCG":
        return states[oc.objgen[0]]
    if "/VE" in oc:
        return _evaluate(oc.VE, states)
    members = oc.OCGs if isinstance(oc.OCGs, pikepdf.Array) else [oc.OCGs]
    values = [states[o.objgen[0]] for o in members]
    return {
        "/AnyOn": any(values),
        "/AllOn": all(values),
        "/AnyOff": not all(values),
        "/AllOff": not any(values),
    }[str(oc.get("/P", "/AnyOn"))]


def _evaluate(expr, states):
    if isinstance(expr, pikepdf.Dictionary):
        return states[expr.objgen[0]]
    op, *args = list(expr)
    values = [_evaluate(a, states) for a in args]
    return {"/And": all, "/Or": any, "/Not": lambda v: not v[0]}[str(op)](values)


def _assignments(ids):
    for bits in range(2 ** len(ids)):
        yield {i: bool(bits >> k & 1) for k, i in enumerate(ids)}


def _mentioned_ids(oc):
    found = set()

    def walk(obj, is_expression):
        if isinstance(obj, pikepdf.Dictionary):
            found.add(obj.objgen[0])
        elif isinstance(obj, pikepdf.Array):
            for item in list(obj)[1:] if is_expression else obj:
                walk(item, is_expression)

    if "/VE" in oc:
        walk(oc.VE, True)
    else:
        walk(oc.OCGs, False)
    return found


def _check_fold(make_oc, ocgs, fixed_ocg):
    ids = [o.objgen[0] for o in ocgs]
    fixed_id = fixed_ocg.objgen[0]
    for fixed_on in (False, True):
        oc = make_oc()
        cases = [s for s in _assignments(ids) if s[fixed_id] == fixed_on]
        expected = [_visible(oc, s) for s in cases]
        folded = fold_visibility(oc, {fixed_id: fixed_on})
        got = [folded if folded is not None else _visible(oc, s) for s in cases]
        assert got == expected, fixed_on
        if folded is None:
            assert fixed_id not in _mentioned_ids(oc)


@pytest.mark.parametrize("policy", ["/AnyOn", "/AllOn", "/AnyOff", "/AllOff", None])
@pytest.mark.parametrize("n", [1, 2, 3])
def test_fold_visibility_policies(policy, n):
    pdf, ocgs = _layers(n)

    def make_oc():
        oc = pikepdf.Dictionary(Type=pikepdf.Name.OCMD, OCGs=pikepdf.Array(ocgs))
        if policy:
            oc.P = pikepdf.Name(policy)
        return pdf.make_indirect(oc)

    _check_fold(make_oc, ocgs, ocgs[0])


_EXPRESSIONS = [
    lambda x, y, z: ["/Or", x],
    lambda x, y, z: ["/Not", x],
    lambda x, y, z: ["/And", x, ["/Or", y, z]],
    lambda x, y, z: ["/Or", ["/Not", x], y],
    lambda x, y, z: ["/And", ["/Not", ["/And", x, y]], z],
    lambda x, y, z: ["/Or", ["/And", x, y], ["/And", ["/Not", x], z]],
    lambda x, y, z: ["/And", y, z],
]


def _to_pdf(expr):
    if isinstance(expr, list):
        return pikepdf.Array([pikepdf.Name(expr[0]), *map(_to_pdf, expr[1:])])
    return expr


@pytest.mark.parametrize("build", _EXPRESSIONS)
def test_fold_visibility_expressions(build):
    pdf, (x, y, z) = _layers(3)
    tree = build(x, y, z)

    def make_oc():
        return pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.OCMD, VE=_to_pdf(tree)))

    _check_fold(make_oc, [x, y, z], x)


def test_fold_visibility_plain_ocg_and_unrelated():
    _, (x, y) = _layers(2)
    assert fold_visibility(x, {x.objgen[0]: False}) is False
    assert fold_visibility(x, {x.objgen[0]: True}) is True
    assert fold_visibility(y, {x.objgen[0]: True}) is None
    ocmd = pikepdf.Dictionary(Type=pikepdf.Name.OCMD, OCGs=pikepdf.Array([y]))
    assert fold_visibility(ocmd, {x.objgen[0]: True}) is None
    assert [o.objgen for o in ocmd.OCGs] == [y.objgen]
    assert fold_visibility(pikepdf.Dictionary(Type=pikepdf.Name.Other), {}) is None


def test_fold_visibility_keeps_non_expression_operands():
    pdf, (x,) = _layers(1)
    ocmd = pikepdf.Dictionary(Type=pikepdf.Name.OCMD, VE=pikepdf.Array([pikepdf.Name.Or, 5, x]))
    assert fold_visibility(ocmd, {x.objgen[0]: True}) is True
    assert fold_visibility(ocmd, {x.objgen[0]: False}) is None
    assert ocmd.VE == pikepdf.Array([pikepdf.Name.Or, 5])


def test_fold_visibility_leaves_unknown_operator():
    pdf, (x,) = _layers(1)
    ocmd = pikepdf.Dictionary(Type=pikepdf.Name.OCMD, VE=pikepdf.Array([pikepdf.Name.Xor, x]))
    assert fold_visibility(ocmd, {x.objgen[0]: True}) is None
    assert [str(ocmd.VE[0]), ocmd.VE[1].objgen] == ["/Xor", x.objgen]


def test_clean_ocproperties_auto_state():
    pdf = pikepdf.Pdf.new()
    obj1 = pdf.make_indirect(pikepdf.Dictionary())
    obj2 = pdf.make_indirect(pikepdf.Dictionary())
    usage_app = pikepdf.Dictionary(Event=pikepdf.Name.View, OCGs=pikepdf.Array([obj1, obj2]))
    pdf.Root.OCProperties = pikepdf.Dictionary(
        OCGs=pikepdf.Array([obj1, obj2]),
        D=pikepdf.Dictionary(AS=pikepdf.Array([usage_app])),
    )

    clean_ocproperties(pdf, {obj1.objgen[0]})
    assert [o.objgen for o in pdf.Root.OCProperties.D.AS[0].OCGs] == [obj2.objgen]


def test_ocg_id_list_keeps_indirect_ids_in_order():
    pdf, (x, y) = _layers(2)
    assert ocg_id_list(pikepdf.Array([y, pikepdf.Dictionary(), 5, x])) == [
        y.objgen[0],
        x.objgen[0],
    ]
    assert ocg_id_list(x) == [x.objgen[0]]
    assert ocg_id_list(None) == []
