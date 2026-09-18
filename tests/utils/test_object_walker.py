# tests/utils/test_object_walker.py

import pikepdf

from pdftl.utils.object_walker import _push_children
from pdftl.utils.object_walker import pikepdf_types, stream_or_dict, walk_objects


def test_pikepdf_types_returns_expected_classes():
    Stream, Dictionary, Array, PdfError = pikepdf_types()
    assert Stream is pikepdf.Stream
    assert Dictionary is pikepdf.Dictionary
    assert Array is pikepdf.Array
    assert PdfError is pikepdf.PdfError


def test_pikepdf_types_is_cached():
    first = pikepdf_types()
    second = pikepdf_types()
    assert first is second


def test_stream_or_dict_with_dictionary():
    pdf = pikepdf.new()
    d = pdf.make_indirect(pikepdf.Dictionary({"/Foo": "/Bar"}))
    assert stream_or_dict(d) is d


def test_stream_or_dict_with_stream():
    pdf = pikepdf.new()
    stream = pikepdf.Stream(pdf, b"content", {"/Foo": "/Bar"})
    result = stream_or_dict(stream)
    # .stream_dict appears to hand back a fresh wrapper each access
    # (equal by content, not identity), so compare by value.
    assert result == stream.stream_dict
    assert result["/Foo"] == "/Bar"


def test_stream_or_dict_with_array_returns_none():
    pdf = pikepdf.new()
    arr = pdf.make_indirect(pikepdf.Array([1, 2, 3]))
    assert stream_or_dict(arr) is None


def test_stream_or_dict_with_plain_value_returns_none():
    assert stream_or_dict(pikepdf.String("hello")) is None


def test_walk_objects_visits_each_indirect_object_once():
    pdf = pikepdf.new()
    pdf.add_blank_page()
    visited = []

    def visit(obj, objgen):
        visited.append(objgen)

    walk_objects(pdf, visit)

    assert len(visited) == len(set(visited)), "objects must be visited exactly once"
    assert len(visited) > 0


def test_walk_objects_reaches_page_dict():
    pdf = pikepdf.new()
    pdf.add_blank_page()
    seen_types = []

    def visit(obj, objgen):
        d = stream_or_dict(obj)
        if d is not None and "/Type" in d:
            seen_types.append(str(d["/Type"]))

    walk_objects(pdf, visit)

    assert "/Page" in seen_types
    assert "/Pages" in seen_types
    assert "/Catalog" in seen_types


def test_walk_objects_descends_into_arrays():
    pdf = pikepdf.new()
    marker_dict = pdf.make_indirect(pikepdf.Dictionary({"/Marker": "/Yes"}))
    pdf.Root.CustomArray = pdf.make_indirect(pikepdf.Array([marker_dict]))
    found_marker = []

    def visit(obj, objgen):
        d = stream_or_dict(obj)
        if d is not None and d.get("/Marker") == "/Yes":
            found_marker.append(True)

    walk_objects(pdf, visit)

    # The array is reached as a value of /CustomArray, and
    # _push_children's array branch must descend into it to reach the
    # indirect dict member, which is then visited in its own right.
    assert found_marker == [True]


def test_walk_objects_handles_cycles_without_infinite_loop():
    pdf = pikepdf.new()
    a = pdf.make_indirect(pikepdf.Dictionary({}))
    b = pdf.make_indirect(pikepdf.Dictionary({}))
    a.Next = b
    b.Next = a
    pdf.Root.CycleStart = a

    visited = []

    def visit(obj, objgen):
        visited.append(objgen)

    walk_objects(pdf, visit)

    assert a.objgen in visited
    assert b.objgen in visited
    assert len(visited) == len(set(visited))


def test_walk_objects_visit_can_mutate_document():
    pdf = pikepdf.new()
    pdf.Root.DropMe = pdf.make_indirect(pikepdf.Dictionary({"/Metadata": "placeholder"}))

    def visit(obj, objgen):
        d = stream_or_dict(obj)
        if d is not None and "/Metadata" in d:
            del d["/Metadata"]

    walk_objects(pdf, visit)

    assert "/Metadata" not in pdf.Root.DropMe


def test_push_children_getitem_exception_is_swallowed():
    """A dict-like whose keys() succeeds but whose __getitem__ raises
    KeyError/AttributeError/PdfError for a given key must not propagate
    -- that key is simply skipped."""
    from unittest.mock import patch

    _, _, Array, PdfError = pikepdf_types()

    class FlakyDict:
        def keys(self):
            return ["/Ok", "/Boom"]

        def __getitem__(self, key):
            if key == "/Boom":
                raise KeyError(key)
            return "fine"

    stack = []
    flaky = FlakyDict()
    with patch("pdftl.utils.object_walker.stream_or_dict", return_value=flaky):
        _push_children(flaky, stack, Array, PdfError)
    assert stack == ["fine"]
