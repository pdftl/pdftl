# tests/utils/test_pikepdf_helpers.py

import pikepdf

from pdftl.utils.pikepdf_helpers import get_inheritable


def test_get_inheritable_direct_key_on_page():
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(100, 200))

    # MediaBox is set directly on the local page dictionary by pikepdf
    result = get_inheritable(page, "/MediaBox")
    assert result is not None
    assert [float(x) for x in result] == [0.0, 0.0, 100.0, 200.0]


def test_get_inheritable_from_parent_node():
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    parent = page.obj["/Parent"]

    # Set an attribute on the parent node and ensure it's absent on the page
    parent["/CropBox"] = pikepdf.Array([0, 0, 300, 400])
    if "/CropBox" in page.obj:
        del page.obj["/CropBox"]

    val = get_inheritable(page, "/CropBox")
    assert val is not None
    assert [float(x) for x in val] == [0.0, 0.0, 300.0, 400.0]


def test_get_inheritable_multi_level_parent():
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    root_parent = page.obj["/Parent"]

    # Insert an intermediate node: page -> mid_parent -> root_parent
    mid_parent = pikepdf.Dictionary({"/Type": pikepdf.Name("/Pages"), "/Parent": root_parent})
    pdf.make_indirect(mid_parent)
    page.obj["/Parent"] = mid_parent

    # Place attribute at the root parent level
    root_parent["/Rotate"] = 90

    assert int(get_inheritable(page, "/Rotate")) == 90


def test_get_inheritable_page_overrides_parent():
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    parent = page.obj["/Parent"]

    parent["/Rotate"] = 90
    page.obj["/Rotate"] = 180

    assert int(get_inheritable(page, "/Rotate")) == 180


def test_get_inheritable_missing_key():
    pdf = pikepdf.new()
    page = pdf.add_blank_page()

    assert get_inheritable(page, "/NonExistentKey") is None


def test_get_inheritable_raw_dictionary_input():
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    parent = page.obj["/Parent"]

    # Remove the default empty local /Resources so it inherits from parent
    if "/Resources" in page.obj:
        del page.obj["/Resources"]

    parent["/Resources"] = pikepdf.Dictionary({"/Font": pikepdf.Dictionary()})

    # Pass page.obj (Dictionary) instead of page (Page) wrapper
    res = get_inheritable(page.obj, "/Resources")
    assert res is not None
    assert "/Font" in res


def test_get_inheritable_cyclic_tree_protection():
    pdf = pikepdf.new()
    node_a = pikepdf.Dictionary({"/Type": pikepdf.Name("/Pages")})
    node_b = pikepdf.Dictionary({"/Type": pikepdf.Name("/Pages")})

    # Indirect registration ensures objgen ID is non-zero for loop tracking
    pdf.make_indirect(node_a)
    pdf.make_indirect(node_b)

    node_a["/Parent"] = node_b
    node_b["/Parent"] = node_a  # Form a cyclic loop

    assert get_inheritable(node_a, "/Resources") is None


def test_get_inheritable_direct_unattached_dictionary():
    # Direct dictionary (not added to a PDF document or made indirect)
    direct_dict = pikepdf.Dictionary({"/Rotate": 270})

    # Line 22 evaluates to False and jumps directly to line 27 (hits 22->27 branch)
    assert int(get_inheritable(direct_dict, "/Rotate")) == 270


def test_get_inheritable_max_depth_bound():
    """Verifies that search terminates and returns None when max_depth is reached."""
    # Create a parent chain 10 levels deep with the key at the root (bottom)
    root = {"/MediaBox": [0, 0, 600, 800]}
    curr = root
    for _ in range(10):
        curr = {"/Parent": curr}

    # Should find the key when max_depth allows full traversal
    assert get_inheritable(curr, "/MediaBox", max_depth=15) == [0, 0, 600, 800]

    # Should hit depth limit and return None when max_depth is smaller than tree height
    assert get_inheritable(curr, "/MediaBox", max_depth=5) is None


def test_get_inheritable_circular_parent_reference():
    """Verifies that circular /Parent graphs terminate safely without infinite loops."""
    node_a = {}
    node_b = {"/Parent": node_a}
    node_a["/Parent"] = node_b  # Circular link: A -> B -> A

    # Looking for a non-existent key should break out via visited node check and return None
    assert get_inheritable(node_a, "/TrimBox") is None
