from pdftl.utils.pdf_text.bboxes import (
    _should_group_with_line,
    _group_into_lines,
    _is_contained,
    merge_horizontal_boxes,
    merge_bounding_boxes,
)


def test_should_group_with_line():
    # 1. Vertical Overlap > 0.5
    anchor = [10, 10, 20, 20]
    box = [30, 12, 40, 22]  # Intersects vertically from 12 to 20 (height 8)
    assert _should_group_with_line(anchor, box, 0.5) is True

    # 2. Same Baseline Check
    box2 = [30, 14, 40, 24]  # Minimal overlap (6), less than half. Y diff = 4.
    # Max height = 10. y_epsilon = 0.5. 4 < 5.0 -> True
    assert _should_group_with_line(anchor, box2, 0.5) is True

    # 3. Neither (Different line entirely)
    box3 = [30, 50, 40, 60]
    assert _should_group_with_line(anchor, box3, 0.5) is False

    # 4. Zero height edge case (prevent division by zero)
    anchor_zero = [10, 10, 20, 10]
    box_zero = [30, 10, 40, 10]
    assert _should_group_with_line(anchor_zero, box_zero, 0.5) is True


def test_group_into_lines():
    boxes = [
        [10, 10, 20, 20],  # Line 1
        [30, 10, 40, 20],  # Line 1
        [10, 50, 20, 60],  # Line 2
    ]
    lines = _group_into_lines(boxes, 0.5)
    assert len(lines) == 2
    assert len(lines[0]) == 2
    assert len(lines[1]) == 1

    assert _group_into_lines([], 0.5) == []


def test_is_contained():
    # Complete containment (> 80%)
    parent = [0, 0, 100, 100]
    child = [10, 10, 20, 20]
    assert _is_contained(parent, child) is True

    # Less than 80% containment
    partial = [90, 90, 110, 110]
    assert _is_contained(parent, partial) is False

    # Degenerate touching box (next_box_area == 0, but intersect_h > 0)
    degenerate = [50, 50, 50, 60]
    assert _is_contained(parent, degenerate) is True

    # No overlap
    separate = [200, 200, 210, 210]
    assert _is_contained(parent, separate) is False


def test_merge_horizontal_boxes():
    # Empty
    assert merge_horizontal_boxes([], 2.75) == []

    # Two boxes close enough to merge
    line = [[10, 10, 20, 20], [25, 10, 35, 20]]
    res = merge_horizontal_boxes(line, 2.75)
    assert res == [[10, 10, 35, 20]]

    # Two boxes too far (gap > max_height * 2.75)
    line2 = [[10, 10, 20, 20], [60, 10, 70, 20]]
    res2 = merge_horizontal_boxes(line2, 2.75)
    assert res2 == [[10, 10, 20, 20], [60, 10, 70, 20]]

    # Contained boxes
    line3 = [[10, 10, 50, 50], [20, 20, 30, 30]]
    res3 = merge_horizontal_boxes(line3, 2.75)
    assert res3 == [[10, 10, 50, 50]]


def test_merge_bounding_boxes():
    # Fallbacks
    assert merge_bounding_boxes([]) == []
    assert merge_bounding_boxes([[1, 2, 3, 4]]) == [[1, 2, 3, 4]]

    # Full orchestration
    bboxes = [
        [30, 10, 40, 20],  # Line 1, Item 2
        [10, 10, 20, 20],  # Line 1, Item 1
        [10, 50, 20, 60],  # Line 2, Item 1
        [100, 50, 110, 60],  # Line 2, Item 2 (Far apart)
    ]
    merged = merge_bounding_boxes(bboxes, x_epsilon=2.75, y_epsilon=0.5)
    assert len(merged) == 3
    assert merged[0] == [10, 10, 40, 20]
    assert merged[1] == [10, 50, 20, 60]
    assert merged[2] == [100, 50, 110, 60]
