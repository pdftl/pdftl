import pikepdf
from pdftl.utils.ocg import (
    get_xobject_ocg_ids,
    get_page_layer_map,
    _remove_targets_from_array,
    clean_ocproperties,
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
    pdf = pikepdf.Pdf.new()
    resources = pikepdf.Dictionary()  # Completely empty
    prop_map, xobj_map = get_page_layer_map(resources)
    assert not prop_map
    assert not xobj_map


def test_remove_targets_ignores_non_indirect():
    """Line 81ish: Safely skips array items that aren't indirect objects (like Names/Strings)."""
    import pikepdf

    pdf = pikepdf.Pdf.new()
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
