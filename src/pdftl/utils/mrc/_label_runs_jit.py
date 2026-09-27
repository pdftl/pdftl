# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/mrc/_label_runs_jit.py

"""Numba kernel for run-based connected-component labelling.

Imported lazily by segmentation.py; the leading underscore keeps
registry auto-discovery from importing numba.
"""

import numpy as np
from numba import njit


@njit(cache=True)
def _find(parent: np.ndarray, x: int) -> int:
    """Root of `x`, halving the path on the way up."""
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


@njit(cache=True)
def _union(parent: np.ndarray, i: int, j: int) -> None:
    """Join the sets of `i` and `j` under the smaller root."""
    ri = _find(parent, i)
    rj = _find(parent, j)
    if ri < rj:
        parent[rj] = ri
    elif rj < ri:
        parent[ri] = rj


@njit(cache=True)
def _merge_rows(
    parent: np.ndarray, x0: np.ndarray, x1: np.ndarray, a: int, a_end: int, b: int, b_end: int
) -> None:
    """Union every touching pair between runs `a:a_end` and `b:b_end`."""
    i, j = a, b
    while i < a_end and j < b_end:
        if x0[i] <= x1[j] and x0[j] <= x1[i]:
            _union(parent, i, j)
        if x1[i] < x1[j]:
            i += 1
        else:
            j += 1


@njit(cache=True)
def label_runs(
    rows: np.ndarray, x0: np.ndarray, x1: np.ndarray, row_start: np.ndarray, height: int
) -> np.ndarray:
    """Return the 8-connected component root of each row-run.

    Runs are `[x0, x1)` in raster order; runs of row `r` are
    `row_start[r]:row_start[r + 1]`. Each root is the smallest run index
    in its component.

    Union-find over runs, pairing adjacent rows with a two-pointer sweep.
    With exclusive `x1`, `x0[i] <= x1[j] and x0[j] <= x1[i]` also accepts
    diagonal corner contact. Compiled because the loop is sequential and
    runs over very many runs per page.
    """
    n = rows.shape[0]
    parent = np.arange(n)
    for r in range(1, height):
        _merge_rows(parent, x0, x1, row_start[r - 1], row_start[r], row_start[r], row_start[r + 1])
    for i in range(n):
        root = i
        while parent[root] != root:
            root = parent[root]
        parent[i] = root
    return parent
