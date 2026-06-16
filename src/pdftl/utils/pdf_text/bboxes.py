# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/pdf_text/bboxes.py


def _should_group_with_line(anchor_box, box, y_epsilon_mult):
    """
    Determines if a box belongs to the current line based on vertical
    overlap or baseline alignment.
    """
    box_h = max(0.1, box[3] - box[1])
    anchor_h = max(0.1, anchor_box[3] - anchor_box[1])

    # 1. Vertical Overlap Check
    v_overlap = max(0, min(anchor_box[3], box[3]) - max(anchor_box[1], box[1]))
    is_overlapping = (v_overlap / min(box_h, anchor_h)) > 0.5

    # 2. Traditional Baseline Check
    max_height = max(anchor_h, box_h)
    y_min_diff = abs(box[1] - anchor_box[1])
    is_same_baseline = y_min_diff < (max_height * y_epsilon_mult)

    return is_overlapping or is_same_baseline


def _group_into_lines(sorted_boxes, y_epsilon_mult):
    """
    Groups sorted bounding boxes into vertical lines.
    """
    lines = []
    current_line = []

    for box in sorted_boxes:
        if not current_line:
            current_line.append(box)
            continue

        # Use the helper to determine if the box belongs to the current line
        if _should_group_with_line(current_line[0], box, y_epsilon_mult):
            current_line.append(box)
        else:
            lines.append(current_line)
            current_line = [box]

    if current_line:
        lines.append(current_line)

    return lines


def _is_contained(current_box, next_box):
    # --- 1. OVERLAP CHECK ---
    # Calculate width and height of the intersection rectangle
    intersect_w = max(0, min(current_box[2], next_box[2]) - max(current_box[0], next_box[0]))
    intersect_h = max(0, min(current_box[3], next_box[3]) - max(current_box[1], next_box[1]))
    intersect_area = intersect_w * intersect_h

    # Calculate area of the next_box
    next_box_w = max(0, next_box[2] - next_box[0])
    next_box_h = max(0, next_box[3] - next_box[1])
    next_box_area = next_box_w * next_box_h

    # Flag if next_box is > 80% contained within current_box
    if next_box_area > 0:
        if (intersect_area / next_box_area) > 0.80:
            return True
    elif intersect_w > 0 or intersect_h > 0:
        # Fallback: Catch degenerate 0-area micro-artifacts that physically touch/intersect
        return True
    return False


def merge_horizontal_boxes(line, x_epsilon_mult):
    """
    Merges bounding boxes horizontally within a single line using the envelope method.
    Includes an overlap check for narrow/micro-artifact boxes.
    """
    if not line:
        return []

    # Sort left-to-right
    sorted_line = sorted(line, key=lambda b: b[0])

    merged_line_boxes = []
    current_merged = sorted_line[0]

    for next_box in sorted_line[1:]:
        contained = _is_contained(current_merged, next_box)
        # --- 2. GAP CHECK ---
        gap = next_box[0] - current_merged[2]
        max_height = max(current_merged[3] - current_merged[1], next_box[3] - next_box[1])

        # If highly contained/overlapped OR gap is within horizontal tolerance, swallow into a
        # single envelope
        if contained or gap < (max_height * x_epsilon_mult):
            current_merged = [
                min(current_merged[0], next_box[0]),  # x_min
                min(current_merged[1], next_box[1]),  # y_min
                max(current_merged[2], next_box[2]),  # x_max
                max(current_merged[3], next_box[3]),  # y_max
            ]
        else:
            # Gap is too large (e.g., column break); save current and start new
            merged_line_boxes.append(current_merged)
            current_merged = next_box

    # Ensure the final bounding box of the line is appended
    merged_line_boxes.append(current_merged)

    return merged_line_boxes


def merge_bounding_boxes(bboxes, x_epsilon=2.75, y_epsilon=0.5):
    """
    Orchestrates the bounding box merging heuristic.

    Args:
        bboxes (list): A list of bounding boxes [x_min, y_min, x_max, y_max].
        x_epsilon (float): Multiplier for horizontal gap tolerance.
        y_epsilon (float): Multiplier for vertical baseline tolerance.
    """
    if not bboxes or len(bboxes) == 1:
        return bboxes

    sorted_boxes = sorted(bboxes, key=lambda b: (b[1], b[0]))
    lines = _group_into_lines(sorted_boxes, y_epsilon)

    merged_bboxes = []
    for line in lines:
        merged_bboxes.extend(merge_horizontal_boxes(line, x_epsilon))

    return merged_bboxes
