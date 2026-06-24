# src/pdftl/utils/scope_tracker.py

"""
Tracks paired scope operators in a PDF content stream, assigning a shared
ID to each open/close pair so the two ends can be correlated at a glance.

Supported scope types and their ID prefixes:
  q / Q            -> gs#N   (graphics state save/restore)
  BT / ET          -> bt#N   (text object)
  BMC / BDC / EMC  -> mc#N   (marked content)
  BX / EX          -> bx#N   (compatibility section)
"""

from __future__ import annotations


class ScopeTracker:
    """
    Call push(op) for every operator in the stream in order.
    It returns a scope annotation string like "gs#1 open" / "gs#1 close",
    or None if the operator is not a scope boundary.

    current_depth reflects the total nesting depth across all scope types
    after the most recent push() call, for use as comment indentation.
    """

    _OPEN = {
        "q": "gs",
        "BT": "bt",
        "BMC": "mc",
        "BDC": "mc",
        "BX": "bx",
    }
    _CLOSE = {
        "Q": "gs",
        "ET": "bt",
        "EMC": "mc",
        "EX": "bx",
    }

    def __init__(self) -> None:
        # One stack per scope type; each entry is the assigned ID for that scope.
        self._stacks: dict[str, list[int]] = {
            "gs": [],
            "bt": [],
            "mc": [],
            "bx": [],
        }
        # Monotonically increasing counter per scope type.
        self._counters: dict[str, int] = {
            "gs": 0,
            "bt": 0,
            "mc": 0,
            "bx": 0,
        }

    @property
    def current_depth(self) -> int:
        """Total nesting depth across all scope types."""
        return sum(len(s) for s in self._stacks.values())

    def push(self, op: str) -> str | None:
        """
        Process one operator. Returns a scope annotation string or None.

        For open operators the annotation reflects the depth AFTER opening,
        so callers should read current_depth after push() to get the inner depth.
        For close operators the annotation is emitted at the outer depth, and
        current_depth after push() reflects the depth after closing.

        Never raises — unbalanced streams get a "?" marker rather than an exception.
        """
        if op in self._OPEN:
            scope = self._OPEN[op]
            self._counters[scope] += 1
            n = self._counters[scope]
            self._stacks[scope].append(n)
            return f"{scope}#{n} open"

        if op in self._CLOSE:
            scope = self._CLOSE[op]
            if self._stacks[scope]:
                n = self._stacks[scope].pop()
                return f"{scope}#{n} close"
            else:
                # Unbalanced close — stream is malformed but we carry on.
                return f"{scope}#? close"

        return None
