# tests/utils/test_scope_tracker.py

import pytest
from pdftl.utils.scope_tracker import ScopeTracker


def test_scope_tracker_initial_state():
    """Verifies that a new tracker starts with a depth of 0."""
    tracker = ScopeTracker()
    assert tracker.current_depth == 0


@pytest.mark.parametrize(
    "open_op,close_op,prefix",
    [
        ("q", "Q", "gs"),
        ("BT", "ET", "bt"),
        ("BMC", "EMC", "mc"),
        ("BDC", "EMC", "mc"),
        ("BX", "EX", "bx"),
    ],
)
def test_scope_tracker_all_supported_scopes(open_op, close_op, prefix):
    """Ensures all standard PDF scope pairs are recognized and track sequence numbers accurately."""
    tracker = ScopeTracker()

    # First open/close sequence
    assert tracker.push(open_op) == f"{prefix}#1 open"
    assert tracker.current_depth == 1

    assert tracker.push(close_op) == f"{prefix}#1 close"
    assert tracker.current_depth == 0

    # Second sequence should increment the tracker's counter
    assert tracker.push(open_op) == f"{prefix}#2 open"
    assert tracker.push(close_op) == f"{prefix}#2 close"


def test_scope_tracker_nested_and_interleaved_depth():
    """Validates cumulative depth calculation across distinct, nested scope types."""
    tracker = ScopeTracker()

    assert tracker.push("q") == "gs#1 open"
    assert tracker.current_depth == 1

    assert tracker.push("BT") == "bt#1 open"
    assert tracker.current_depth == 2

    assert tracker.push("ET") == "bt#1 close"
    assert tracker.current_depth == 1

    assert tracker.push("Q") == "gs#1 close"
    assert tracker.current_depth == 0


def test_scope_tracker_non_scope_operator():
    """Checks that regular content operators are safely ignored and return None."""
    tracker = ScopeTracker()
    assert tracker.push("Tj") is None
    assert tracker.push("cm") is None
    assert tracker.current_depth == 0


def test_scope_tracker_unbalanced_close():
    """Confirms that closing an unopened scope fails gracefully without breaking depth calculations."""
    tracker = ScopeTracker()

    # Close without open
    assert tracker.push("Q") == "gs#? close"
    assert tracker.current_depth == 0

    # Ensure subsequent valid operations still function properly
    assert tracker.push("q") == "gs#1 open"
    assert tracker.current_depth == 1
