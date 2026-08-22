# tests/operations/helpers/test_excise_geometry.py
from types import SimpleNamespace
import pytest

from pdftl.operations.helpers.excise_geometry import (
    IDENTITY_CTM,
    _matches,
    filter_fill_path,
    filter_path,
    filter_stroke_path,
    overlap_means_delete,
    path_bbox,
    points_bbox,
    resolve_box_rect,
    transform_point_local,
)


class DummyPage:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class DummyStats:
    def __init__(self, paths_total=0, paths_deleted=0, subpaths_deleted=0):
        self.paths_total = paths_total
        self.paths_deleted = paths_deleted
        self.subpaths_deleted = subpaths_deleted


class DummyExciseRect:
    def __init__(self, rects=None, partial="inside", delete="inside"):
        self.rects = rects or []
        self.partial = partial
        self.delete = delete


# ---------------------------------------------------------------------------
# resolve_box_rect Tests
# ---------------------------------------------------------------------------


def test_resolve_box_rect_direct_match():
    page = DummyPage(mediabox=[100.0, 200.0, 0.0, 50.0])
    rect = resolve_box_rect(page, "media")
    assert rect == [0.0, 50.0, 100.0, 200.0]


@pytest.mark.parametrize("box_name", ["art", "bleed", "trim", "crop"])
def test_resolve_box_rect_fallbacks(box_name):
    page = DummyPage(mediabox=[0.0, 0.0, 100.0, 100.0])
    rect = resolve_box_rect(page, box_name)
    assert rect == [0.0, 0.0, 100.0, 100.0]


def test_resolve_box_rect_type_error_fallback():
    page = DummyPage(artbox=[None, 0, 10, 10], cropbox=[0.0, 0.0, 50.0, 50.0])
    rect = resolve_box_rect(page, "art")
    assert rect == [0.0, 0.0, 50.0, 50.0]


def test_resolve_box_rect_value_error_fallback():
    page = DummyPage(artbox=["invalid", "0", "10", "10"], cropbox=[0.0, 0.0, 50.0, 50.0])
    rect = resolve_box_rect(page, "art")
    assert rect == [0.0, 0.0, 50.0, 50.0]


def test_resolve_box_rect_invalid_length_fallback():
    page = DummyPage(artbox=[0.0, 0.0, 100.0], cropbox=[0.0, 0.0, 50.0, 50.0])
    rect = resolve_box_rect(page, "art")
    assert rect == [0.0, 0.0, 50.0, 50.0]


def test_resolve_box_rect_unresolvable_raises():
    page = DummyPage()
    with pytest.raises(ValueError, match="excise: page has no resolvable /mediaBox"):
        resolve_box_rect(page, "media")


# ---------------------------------------------------------------------------
# Point & Bounding Box Helpers Tests
# ---------------------------------------------------------------------------


def test_transform_point_local():
    ctm = (2.0, 0.0, 0.0, 3.0, 10.0, 20.0)
    assert transform_point_local(5.0, 4.0, ctm) == (20.0, 32.0)


def test_points_bbox_empty():
    assert points_bbox([], IDENTITY_CTM) is None


def test_points_bbox_valid():
    pts = [(0.0, 0.0), (10.0, 20.0), (5.0, -5.0)]
    assert points_bbox(pts, IDENTITY_CTM) == [0.0, -5.0, 10.0, 20.0]


def test_path_bbox_empty():
    subpaths = [SimpleNamespace(points=[]), SimpleNamespace(points=[])]
    path = SimpleNamespace(subpaths=subpaths)
    assert path_bbox(path, IDENTITY_CTM) is None


def test_path_bbox_valid():
    subpaths = [
        SimpleNamespace(points=[(0.0, 0.0), (5.0, 5.0)]),
        SimpleNamespace(points=[(10.0, 20.0)]),
    ]
    path = SimpleNamespace(subpaths=subpaths)
    assert path_bbox(path, IDENTITY_CTM) == [0.0, 0.0, 10.0, 20.0]


# ---------------------------------------------------------------------------
# Overlap & Matching Logic Tests
# ---------------------------------------------------------------------------


def test_matches_outside():
    excise = DummyExciseRect(rects=[[0, 0, 10, 10]], partial="outside")
    assert _matches([2, 2, 8, 8], excise) is True
    assert _matches([-2, -2, 8, 8], excise) is False


def test_matches_inside_default():
    excise = DummyExciseRect(rects=[[0, 0, 10, 10]], partial="inside")
    assert _matches([5, 5, 15, 15], excise) is True
    assert _matches([20, 20, 30, 30], excise) is False


def test_overlap_means_delete():
    del_inside = DummyExciseRect(rects=[[0, 0, 10, 10]], delete="inside")
    del_outside = DummyExciseRect(rects=[[0, 0, 10, 10]], delete="outside")

    assert overlap_means_delete([2, 2, 8, 8], del_inside) is True
    assert overlap_means_delete([2, 2, 8, 8], del_outside) is False

    assert overlap_means_delete([20, 20, 30, 30], del_inside) is False
    assert overlap_means_delete([20, 20, 30, 30], del_outside) is True


# ---------------------------------------------------------------------------
# filter_fill_path Tests
# ---------------------------------------------------------------------------


def test_filter_fill_path_no_bbox():
    path = SimpleNamespace(subpaths=[], original_instructions=["f"])
    excise = DummyExciseRect(rects=[[0, 0, 10, 10]])
    stats = DummyStats()

    res = filter_fill_path(path, IDENTITY_CTM, excise, stats)
    assert res == ["f"]
    assert stats.paths_deleted == 0


def test_filter_fill_path_delete():
    subpath = SimpleNamespace(points=[(2, 2), (8, 8)])
    path = SimpleNamespace(subpaths=[subpath], original_instructions=["f"])
    excise = DummyExciseRect(rects=[[0, 0, 10, 10]])
    stats = DummyStats()

    res = filter_fill_path(path, IDENTITY_CTM, excise, stats)
    assert res == []
    assert stats.paths_deleted == 1


def test_filter_fill_path_keep():
    subpath = SimpleNamespace(points=[(20, 20), (30, 30)])
    path = SimpleNamespace(subpaths=[subpath], original_instructions=["f"])
    excise = DummyExciseRect(rects=[[0, 0, 10, 10]])
    stats = DummyStats()

    res = filter_fill_path(path, IDENTITY_CTM, excise, stats)
    assert res == ["f"]
    assert stats.paths_deleted == 0


# ---------------------------------------------------------------------------
# filter_stroke_path Tests
# ---------------------------------------------------------------------------


def test_filter_stroke_path_all_deleted():
    sp1 = SimpleNamespace(points=[(2, 2)], instructions=["l1"])
    sp2 = SimpleNamespace(points=[(3, 3)], instructions=["l2"])
    path = SimpleNamespace(subpaths=[sp1, sp2], original_instructions=["l1", "l2", "S"])
    excise = DummyExciseRect(rects=[[0, 0, 10, 10]])
    stats = DummyStats()

    res = filter_stroke_path(path, IDENTITY_CTM, excise, stats)
    assert res == []
    assert stats.paths_deleted == 1
    assert stats.subpaths_deleted == 2


def test_filter_stroke_path_all_kept():
    sp1 = SimpleNamespace(points=[(20, 20)], instructions=["l1"])
    sp2 = SimpleNamespace(points=[(30, 30)], instructions=["l2"])
    path = SimpleNamespace(subpaths=[sp1, sp2], original_instructions=["l1", "l2", "S"])
    excise = DummyExciseRect(rects=[[0, 0, 10, 10]])
    stats = DummyStats()

    res = filter_stroke_path(path, IDENTITY_CTM, excise, stats)
    assert res == ["l1", "l2", "S"]
    assert stats.paths_deleted == 0
    assert stats.subpaths_deleted == 0


def test_filter_stroke_path_partial_drop_with_orig_instructions():
    sp1 = SimpleNamespace(points=[(20, 20)], instructions=["l1"])
    sp2 = SimpleNamespace(points=[(2, 2)], instructions=["l2"])
    path = SimpleNamespace(subpaths=[sp1, sp2], original_instructions=["l1", "l2", "S"])
    excise = DummyExciseRect(rects=[[0, 0, 10, 10]])
    stats = DummyStats()

    res = filter_stroke_path(path, IDENTITY_CTM, excise, stats)
    assert res == ["l1", "S"]
    assert stats.paths_deleted == 0
    assert stats.subpaths_deleted == 1


def test_filter_stroke_path_partial_drop_without_orig_instructions():
    sp1 = SimpleNamespace(points=[(20, 20)], instructions=["l1"])
    sp2 = SimpleNamespace(points=[(2, 2)], instructions=["l2"])
    path = SimpleNamespace(subpaths=[sp1, sp2], original_instructions=[])
    excise = DummyExciseRect(rects=[[0, 0, 10, 10]])
    stats = DummyStats()

    res = filter_stroke_path(path, IDENTITY_CTM, excise, stats)
    assert res == ["l1"]
    assert stats.paths_deleted == 0
    assert stats.subpaths_deleted == 1


def test_filter_stroke_path_empty_subpath_and_none_instructions():
    sp_empty_pts_with_instr = SimpleNamespace(points=[], instructions=["empty_pt_op"])
    sp_empty_pts_no_instr = SimpleNamespace(points=[], instructions=None)
    sp_kept_no_instr = SimpleNamespace(points=[(20, 20)], instructions=None)
    sp_dropped = SimpleNamespace(points=[(2, 2)], instructions=["drop_op"])

    path = SimpleNamespace(
        subpaths=[sp_empty_pts_with_instr, sp_empty_pts_no_instr, sp_kept_no_instr, sp_dropped],
        original_instructions=["S"],
    )
    excise = DummyExciseRect(rects=[[0, 0, 10, 10]])
    stats = DummyStats()

    res = filter_stroke_path(path, IDENTITY_CTM, excise, stats)
    assert res == ["empty_pt_op", "S"]
    assert stats.subpaths_deleted == 1


# ---------------------------------------------------------------------------
# filter_path Tests
# ---------------------------------------------------------------------------


def test_filter_path_clipping():
    path = SimpleNamespace(is_clipping=True, original_instructions=["W", "n"])
    excise = DummyExciseRect()
    stats = DummyStats()

    res = filter_path(path, excise, stats, IDENTITY_CTM)
    assert res == ["W", "n"]
    assert stats.paths_total == 0


def test_filter_path_stroke_with_provenance_S():
    subpath = SimpleNamespace(points=[(2, 2)], instructions=["l"])
    snapshot = SimpleNamespace(ctm=(1, 0, 0, 1, 0, 0))
    path = SimpleNamespace(
        is_clipping=False,
        state_snapshot=snapshot,
        paint_op="S",
        subpaths=[subpath],
        original_instructions=["l", "S"],
    )
    excise = DummyExciseRect(rects=[[0, 0, 10, 10]])
    stats = DummyStats()

    res = filter_path(path, excise, stats, IDENTITY_CTM)
    assert res == []
    assert stats.paths_total == 1
    assert stats.paths_deleted == 1


def test_filter_path_stroke_with_provenance_s():
    subpath = SimpleNamespace(points=[(2, 2)], instructions=["l"])
    snapshot = SimpleNamespace(ctm=(1, 0, 0, 1, 0, 0))
    path = SimpleNamespace(
        is_clipping=False,
        state_snapshot=snapshot,
        paint_op="s",
        subpaths=[subpath],
        original_instructions=["l", "s"],
    )
    excise = DummyExciseRect(rects=[[0, 0, 10, 10]])
    stats = DummyStats()

    res = filter_path(path, excise, stats, IDENTITY_CTM)
    assert res == []
    assert stats.paths_total == 1
    assert stats.paths_deleted == 1


def test_filter_path_stroke_without_provenance():
    subpath1 = SimpleNamespace(points=[(2, 2)], instructions=["l"])
    subpath2 = SimpleNamespace(points=[(3, 3)], instructions=None)
    path = SimpleNamespace(
        is_clipping=False,
        state_snapshot=None,
        paint_op="S",
        subpaths=[subpath1, subpath2],
        original_instructions=["l", "S"],
    )
    excise = DummyExciseRect(rects=[[0, 0, 10, 10]])
    stats = DummyStats()

    res = filter_path(path, excise, stats, IDENTITY_CTM)
    assert res == []
    assert stats.paths_total == 1
    assert stats.paths_deleted == 1


def test_filter_path_fill():
    subpath = SimpleNamespace(points=[(20, 20)], instructions=["l"])
    snapshot = SimpleNamespace(ctm=(1, 0, 0, 1, 0, 0))
    path = SimpleNamespace(
        is_clipping=False,
        state_snapshot=snapshot,
        paint_op="f",
        subpaths=[subpath],
        original_instructions=["l", "f"],
    )
    excise = DummyExciseRect(rects=[[0, 0, 10, 10]])
    stats = DummyStats()

    res = filter_path(path, excise, stats, IDENTITY_CTM)
    assert res == ["l", "f"]
    assert stats.paths_total == 1
    assert stats.paths_deleted == 0
