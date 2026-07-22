import pikepdf

from pdftl.utils.ocg import (
    _remove_targets_from_array,
    clean_ocproperties,
    create_layer,
    get_page_layer_map,
    get_xobject_ocg_ids,
    set_layer_state,
    set_layer_usage,
)


def test_get_xobject_ocg_ids():
    pdf = pikepdf.Pdf.new()

    # 1. No /OC
    xobj1 = pdf.make_stream(b"")
    assert get_xobject_ocg_ids(xobj1) == set()

    # 2. Direct OCG
    ocg = pdf.make_indirect(pikepdf.Dictionary(Type="/OCG"))
    xobj2 = pdf.make_stream(b"")
    xobj2.OC = ocg
    assert get_xobject_ocg_ids(xobj2) == {ocg.objgen[0]}

    # 3. OCMD Single
    ocmd1 = pdf.make_indirect(pikepdf.Dictionary(Type="/OCMD", OCGs=ocg))
    xobj3 = pdf.make_stream(b"")
    xobj3.OC = ocmd1
    assert get_xobject_ocg_ids(xobj3) == {ocg.objgen[0]}

    # 4. OCMD Array
    ocg2 = pdf.make_indirect(pikepdf.Dictionary(Type="/OCG"))
    ocmd2 = pdf.make_indirect(pikepdf.Dictionary(Type="/OCMD", OCGs=pikepdf.Array([ocg, ocg2])))
    xobj4 = pdf.make_stream(b"")
    xobj4.OC = ocmd2
    assert get_xobject_ocg_ids(xobj4) == {ocg.objgen[0], ocg2.objgen[0]}


def test_get_page_layer_map():
    pdf = pikepdf.Pdf.new()
    ocg = pdf.make_indirect(pikepdf.Dictionary(Type="/OCG"))

    xobj = pdf.make_stream(b"")
    xobj.OC = pdf.make_indirect(pikepdf.Dictionary(Type="/OCG"))

    resources = pikepdf.Dictionary(
        Properties=pikepdf.Dictionary(MC0=ocg), XObject=pikepdf.Dictionary(Fm0=xobj)
    )

    prop_map, xobj_map = get_page_layer_map(resources)
    assert prop_map == {"/MC0": ocg.objgen[0]}
    assert xobj_map == {"/Fm0": {xobj.OC.objgen[0]}}


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


def test_get_xobject_ocg_ids_invalid_oc():
    """Hits line 16: oc is present but not a pikepdf.Dictionary."""
    pdf = pikepdf.Pdf.new()
    xobj = pdf.make_stream(b"")
    # Assign a Name instead of a Dictionary
    xobj.OC = pikepdf.Name("/NotADict")
    assert get_xobject_ocg_ids(xobj) == set()


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


def test_get_xobject_ocg_ids_direct_dict():
    """Hits line 27: /OC is a direct dictionary of Type /OCG, so it has no objgen."""
    import pikepdf

    from pdftl.utils.ocg import get_xobject_ocg_ids

    # Construct a dummy XObject dictionary with a direct /OC dictionary
    xobj = pikepdf.Dictionary(
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Form,
        OC=pikepdf.Dictionary(Type=pikepdf.Name.OCG),
    )

    # Because it is a direct dict (not attached to a PDF), it has no objgen.
    # It passes the first check but fails the objgen check, hitting line 27.
    assert get_xobject_ocg_ids(xobj) == set()


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


def test_get_xobject_ocg_ids_other_dict_type():
    """Ensures XObjects referencing non-OCG dictionaries without indirect object IDs return an empty set."""
    xobj = pikepdf.Dictionary(OC=pikepdf.Dictionary(Type="/OtherType"))
    assert get_xobject_ocg_ids(xobj) == set()


def test_ocg_ids_from_ocmd_branches():
    """Ensures OCMD parser safely handles items or collections lacking indirect object IDs."""
    from pdftl.utils.ocg import _ocg_ids_from_ocmd

    class ItemWithoutObjgen:
        pass

    # 1. Element in array without objgen (covers branch 34->33)
    assert _ocg_ids_from_ocmd([ItemWithoutObjgen()], list) == set()

    # 2. Non-array object without objgen (covers branch 36->38)
    assert _ocg_ids_from_ocmd(ItemWithoutObjgen(), list) == set()


def test_get_page_layer_map_non_ocg_properties_and_empty_xobj_ocg():
    """Ensures page layer mapping skips non-OCG property entries and XObjects without OCG IDs."""
    pdf = pikepdf.Pdf.new()
    xobj = pdf.make_stream(b"")  # No /OC, so get_xobject_ocg_ids(xobj) returns set()

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


def test_get_xobject_ocg_ids_ignores_non_ocg_dictionary():
    """Ensures XObjects referencing non-OCG dictionaries return an empty set."""
    xobj = pikepdf.Dictionary(OC=pikepdf.Dictionary(Type=pikepdf.Name("/OtherType")))
    assert get_xobject_ocg_ids(xobj) == set()


from pdftl.utils.ocg import _get_obj_id


def test_get_obj_id_returns_zero_on_malformed_objgen():
    """Ensures _get_obj_id handles objects with empty or malformed objgen tuples gracefully."""

    class MalformedObjgen:
        objgen = ()

    assert _get_obj_id(MalformedObjgen()) == 0


def test_get_page_layer_map_ignores_direct_ocg_properties():
    """Ensures direct (unattached) OCG dictionaries are excluded from the property map."""
    direct_ocg = pikepdf.Dictionary(Type=pikepdf.Name.OCG)
    resources = pikepdf.Dictionary(Properties=pikepdf.Dictionary(Layer1=direct_ocg))

    prop_map, xobj_map = get_page_layer_map(resources)

    assert prop_map == {}
    assert xobj_map == {}


def test_get_obj_id_returns_zero_on_invalid_objgen_types():
    """Ensures _get_obj_id gracefully handles objects containing non-integer objgen values."""

    class InvalidObjgen:
        objgen = (None,)

    assert _get_obj_id(InvalidObjgen()) == 0
