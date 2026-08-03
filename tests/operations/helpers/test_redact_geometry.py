# tests/operations/helpers/test_redact_geometry.py


from pdftl.operations.helpers.redact_geometry import (
    DEFAULT_MERGE_DISTANCE,
    DEFAULT_MERGE_RATIO,
    _UnionFind,
    _rect_area,
    _should_merge_area,
    _should_merge_line,
    _union_bbox,
    _x_gap,
    _y_gap,
    merge_rects,
)


# ---------------------------------------------------------------------------
# _y_gap / _x_gap
# ---------------------------------------------------------------------------


class TestGaps:
    def test_y_gap_overlapping_is_non_positive(self):
        a = [0, 0, 10, 10]
        b = [0, 5, 10, 15]
        assert _y_gap(a, b) <= 0

    def test_y_gap_disjoint_is_positive(self):
        a = [0, 0, 10, 10]
        b = [0, 20, 10, 30]
        assert _y_gap(a, b) == 10

    def test_x_gap_overlapping_is_non_positive(self):
        a = [0, 0, 10, 10]
        b = [5, 0, 15, 10]
        assert _x_gap(a, b) <= 0

    def test_x_gap_disjoint_is_positive(self):
        a = [0, 0, 10, 10]
        b = [20, 0, 30, 10]
        assert _x_gap(a, b) == 10

    def test_gap_symmetric(self):
        a = [0, 0, 10, 10]
        b = [15, 15, 25, 25]
        assert _x_gap(a, b) == _x_gap(b, a)
        assert _y_gap(a, b) == _y_gap(b, a)


# ---------------------------------------------------------------------------
# _rect_area / _union_bbox
# ---------------------------------------------------------------------------


class TestRectArea:
    def test_normal_rect(self):
        assert _rect_area([0, 0, 10, 4]) == 40

    def test_degenerate_rect_is_zero(self):
        assert _rect_area([5, 5, 5, 5]) == 0

    def test_inverted_rect_is_zero_not_negative(self):
        assert _rect_area([10, 10, 0, 0]) == 0


class TestUnionBbox:
    def test_two_disjoint_rects(self):
        result = _union_bbox([[0, 0, 5, 5], [10, 10, 15, 15]])
        assert result == [0, 0, 15, 15]

    def test_single_rect_is_itself(self):
        assert _union_bbox([[1, 2, 3, 4]]) == [1, 2, 3, 4]

    def test_overlapping_rects(self):
        result = _union_bbox([[0, 0, 10, 10], [5, 5, 15, 15]])
        assert result == [0, 0, 15, 15]


# ---------------------------------------------------------------------------
# "line" mode merge decision
# ---------------------------------------------------------------------------


class TestShouldMergeLine:
    def test_same_line_close_horizontally_merges(self):
        a = [0, 100, 20, 112]
        b = [22, 100, 40, 112]  # 2pt gap
        assert _should_merge_line(a, b, merge_distance=6.0) is True

    def test_same_line_far_horizontally_does_not_merge(self):
        a = [0, 100, 20, 112]
        b = [200, 100, 220, 112]  # 180pt gap
        assert _should_merge_line(a, b, merge_distance=6.0) is False

    def test_different_lines_does_not_merge_even_if_horizontally_close(self):
        """The case a naive Euclidean-distance threshold would get wrong:
        two boxes on different text lines that happen to sit at similar
        x-positions must NOT merge just because they're geometrically
        close overall."""
        a = [0, 100, 20, 112]  # line at y=100-112
        b = [0, 300, 20, 312]  # unrelated line far below
        assert _should_merge_line(a, b, merge_distance=6.0) is False

    def test_vertically_overlapping_rows_merge_regardless_of_x_gap_rule(self):
        a = [0, 100, 20, 112]
        b = [22, 102, 40, 110]  # y-range fully within a's y-range
        assert _should_merge_line(a, b, merge_distance=6.0) is True

    def test_small_baseline_jitter_still_merges(self):
        """Height-relative vertical tolerance absorbs small baseline
        differences between adjacent match fragments on the same line."""
        a = [0, 100, 20, 112]  # height 12
        b = [21, 101, 40, 113]  # shifted up by 1pt, gap 1pt horizontally
        assert _should_merge_line(a, b, merge_distance=6.0) is True

    def test_large_vertical_offset_does_not_merge(self):
        a = [0, 100, 20, 112]  # height 12
        b = [21, 130, 40, 142]  # far below, definitely a different line
        assert _should_merge_line(a, b, merge_distance=6.0) is False

    def test_merge_distance_is_respected(self):
        a = [0, 100, 20, 112]
        b = [30, 100, 40, 112]  # 10pt gap
        assert _should_merge_line(a, b, merge_distance=5.0) is False
        assert _should_merge_line(a, b, merge_distance=15.0) is True

    def test_symmetric(self):
        a = [0, 100, 20, 112]
        b = [22, 100, 40, 112]
        assert _should_merge_line(a, b, 6.0) == _should_merge_line(b, a, 6.0)


# ---------------------------------------------------------------------------
# "area" mode merge decision
# ---------------------------------------------------------------------------


class TestShouldMergeArea:
    def test_overlapping_rects_always_merge(self):
        a = [0, 0, 10, 10]
        b = [5, 5, 15, 15]
        assert _should_merge_area(a, b, merge_ratio=1.0) is True

    def test_close_boxes_merge_within_ratio(self):
        a = [0, 0, 10, 10]  # area 100
        b = [11, 0, 21, 10]  # area 100, adjacent, union area 210
        # union/sum = 210/200 = 1.05
        assert _should_merge_area(a, b, merge_ratio=1.1) is True
        assert _should_merge_area(a, b, merge_ratio=1.0) is False

    def test_far_apart_boxes_do_not_merge_even_with_generous_ratio(self):
        a = [0, 0, 10, 10]  # area 100
        b = [1000, 1000, 1010, 1010]  # area 100, far away
        assert _should_merge_area(a, b, merge_ratio=DEFAULT_MERGE_RATIO) is False

    def test_zero_area_rect_outside_other_does_not_merge(self):
        """A degenerate (zero-area) rect that does NOT overlap the other
        box falls through to the area-ratio math, which must not merge
        it (area_a == 0 makes the ratio meaningless / undefined)."""
        a = [500, 500, 500, 500]  # degenerate, zero area, far away
        b = [0, 0, 10, 10]
        assert _should_merge_area(a, b, merge_ratio=DEFAULT_MERGE_RATIO) is False

    def test_zero_area_rect_contained_in_other_merges_via_overlap(self):
        """A degenerate point-rect that geometrically sits inside another
        box still counts as "overlapping" (rects_overlap's own contract),
        so it merges via the overlap short-circuit before the area-ratio
        math (which would otherwise divide by its zero area) ever runs."""
        a = [5, 5, 5, 5]  # degenerate, zero area, inside b
        b = [0, 0, 10, 10]
        assert _should_merge_area(a, b, merge_ratio=DEFAULT_MERGE_RATIO) is True

    def test_row_agnostic_merges_across_rows(self):
        """Unlike line mode, area mode should merge vertically-stacked
        close boxes if their union isn't much bigger than their sum."""
        a = [0, 0, 10, 10]  # area 100
        b = [0, 10.5, 10, 20.5]  # area 100, directly below with tiny gap
        assert _should_merge_area(a, b, merge_ratio=1.2) is True


# ---------------------------------------------------------------------------
# merge_rects — end-to-end clustering
# ---------------------------------------------------------------------------


class TestMergeRectsBasic:
    def test_empty_list(self):
        assert merge_rects([]) == []

    def test_single_rect_returned_unchanged(self):
        result = merge_rects([[1, 2, 3, 4]])
        assert result == [[1, 2, 3, 4]]

    def test_single_rect_is_a_copy_not_same_object(self):
        original = [1, 2, 3, 4]
        result = merge_rects([original])
        assert result[0] == original
        assert result[0] is not original

    def test_default_mode_is_line(self):
        a = [0, 100, 20, 112]
        b = [22, 100, 40, 112]
        result = merge_rects([a, b])
        assert result == [[0, 100, 40, 112]]

    def test_unknown_mode_falls_back_to_area(self):
        """merge_rects dispatches on `mode == "line"` else area; any
        non-"line" string (besides the documented "none", handled by the
        caller before merge_rects is even invoked) takes the area path."""
        a = [0, 0, 10, 10]
        b = [5, 5, 15, 15]
        result = merge_rects([a, b], mode="something_else")
        assert result == [[0, 0, 15, 15]]


class TestMergeRectsLineMode:
    def test_two_separate_ssns_on_different_lines_stay_separate(self):
        """Regression-shaped test mirroring the real redact scenario:
        matches on two different lines of a document must never merge
        into one box spanning both lines."""
        line1 = [
            [238.1, 698.5, 276.9, 717.8],
            [276.7, 698.5, 311.3, 717.8],
            [311.4, 698.5, 372.7, 717.8],
        ]
        line2 = [
            [282.4, 648.5, 330.3, 667.8],
            [330.1, 648.5, 364.7, 667.8],
            [364.8, 648.5, 412.6, 667.8],
        ]
        result = merge_rects(line1 + line2, mode="line")
        assert len(result) == 2
        # Each merged rect's y-range should match exactly one source line.
        ys = sorted(r[1] for r in result)
        assert ys == sorted([698.5, 648.5])

    def test_chain_of_three_on_one_line_merges_into_one(self):
        a = [0, 100, 20, 112]
        b = [22, 100, 40, 112]
        c = [44, 100, 60, 112]
        result = merge_rects([a, b, c], mode="line", merge_distance=6.0)
        assert result == [[0, 100, 60, 112]]

    def test_far_apart_matches_on_same_line_stay_separate(self):
        a = [0, 100, 20, 112]
        b = [500, 100, 520, 112]
        result = merge_rects([a, b], mode="line", merge_distance=6.0)
        assert len(result) == 2

    def test_transitive_merge_via_middle_rect(self):
        """A and C aren't directly mergeable, but both merge with B, so
        union-find should still cluster all three together."""
        a = [0, 100, 20, 112]
        b = [22, 100, 40, 112]
        c = [42, 100, 60, 112]
        assert _should_merge_line(a, c, 6.0) is False  # not directly close
        result = merge_rects([a, b, c], mode="line", merge_distance=6.0)
        assert result == [[0, 100, 60, 112]]

    def test_custom_merge_distance_widens_clustering(self):
        a = [0, 100, 20, 112]
        b = [50, 100, 70, 112]  # 30pt gap
        assert merge_rects([a, b], mode="line", merge_distance=6.0) != [[0, 100, 70, 112]]
        assert merge_rects([a, b], mode="line", merge_distance=40.0) == [[0, 100, 70, 112]]


class TestMergeRectsAreaMode:
    def test_overlapping_boxes_merge(self):
        a = [0, 0, 10, 10]
        b = [5, 5, 15, 15]
        result = merge_rects([a, b], mode="area")
        assert result == [[0, 0, 15, 15]]

    def test_far_apart_boxes_stay_separate(self):
        a = [0, 0, 10, 10]
        b = [1000, 1000, 1010, 1010]
        result = merge_rects([a, b], mode="area")
        assert len(result) == 2

    def test_vertically_stacked_close_boxes_merge_unlike_line_mode(self):
        a = [0, 0, 10, 10]
        b = [0, 30, 10, 40]  # 20pt vertical gap -- well beyond line mode's
        #                       height-relative tolerance (0.6 * 10 = 6pt),
        #                       but still close enough for area mode's
        #                       union/sum ratio to allow a merge.
        area_result = merge_rects([a, b], mode="area", merge_ratio=3.0)
        assert len(area_result) == 1
        line_result = merge_rects([a, b], mode="line", merge_distance=6.0)
        assert len(line_result) == 2  # different "rows", no x-overlap logic applies here


class TestMergeRectsNoneEquivalent:
    def test_merge_distance_zero_still_merges_actual_overlaps(self):
        """merge_distance=0 in line mode should still merge boxes whose
        x-ranges genuinely overlap (gap <= 0 <= 0), only preventing any
        extra slack beyond touching/overlapping."""
        a = [0, 100, 20, 112]
        b = [15, 100, 40, 112]  # overlapping x-ranges
        result = merge_rects([a, b], mode="line", merge_distance=0.0)
        assert result == [[0, 100, 40, 112]]

    def test_default_merge_distance_constant_is_reasonable(self):
        assert 0 < DEFAULT_MERGE_DISTANCE < 50


# ---------------------------------------------------------------------------
# _UnionFind -- internals not reachable via merge_rects's own public behavior
# ---------------------------------------------------------------------------


class TestUnionFindInternals:
    def test_find_path_compression_multi_hop(self):
        """A pre-existing chain (3->2->1->0) forces find()'s second while
        loop to run more than once, compressing every visited node
        directly onto the root rather than just one hop."""
        uf = _UnionFind(4)
        uf._parent = [0, 0, 1, 2]
        root = uf.find(3)
        assert root == 0
        assert uf._parent[3] == 0
        assert uf._parent[2] == 0

    def test_union_is_a_noop_when_already_same_root(self):
        uf = _UnionFind(3)
        uf.union(0, 1)
        root_before = uf.find(0)
        uf.union(0, 1)  # redundant union -- ra == rb, early return
        assert uf.find(0) == root_before
        assert uf.find(1) == root_before

    def test_union_swaps_when_right_root_has_higher_rank(self):
        uf = _UnionFind(2)
        uf._rank = [0, 5]  # force root 1's rank above root 0's
        uf.union(0, 1)
        assert uf.find(0) == 1  # root 1 won the swap, so it stays the root
        assert uf._parent[0] == 1
