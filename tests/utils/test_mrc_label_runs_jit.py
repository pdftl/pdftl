# tests/utils/test_mrc_label_runs_jit.py

"""Tests for the run-labelling kernel, via `.py_func` for coverage."""

import numpy as np
import pytest

from pdftl.utils.mrc import _label_runs_jit as mod


@pytest.fixture
def pure(monkeypatch):
    """`label_runs` with every helper swapped for its pure-Python body."""
    for name in ("_find", "_union", "_merge_rows"):
        monkeypatch.setattr(mod, name, getattr(mod, name).py_func)
    return mod.label_runs.py_func


def _image(art: str) -> np.ndarray:
    lines = [ln.strip() for ln in art.strip().splitlines()]
    return np.array([[c == "#" for c in ln] for ln in lines], dtype=bool)


def _runs(ink: np.ndarray):
    """Row-runs `(row, x0, x1)`, x1 exclusive, by a plain scan."""
    rows, x0, x1 = [], [], []
    for r, line in enumerate(ink):
        c = 0
        while c < line.size:
            if line[c]:
                s = c
                while c < line.size and line[c]:
                    c += 1
                rows.append(r)
                x0.append(s)
                x1.append(c)
            else:
                c += 1
    return (np.array(v, dtype=np.int64) for v in (rows, x0, x1))


def _call(fn, ink: np.ndarray):
    rows, x0, x1 = _runs(ink)
    height = ink.shape[0]
    row_start = np.searchsorted(rows, np.arange(height + 1)).astype(np.int64)
    return fn(rows, x0, x1, row_start, height), (rows, x0, x1)


def _pixel_labels(ink, roots, runs) -> np.ndarray:
    rows, x0, x1 = runs
    out = np.full(ink.shape, -1, dtype=np.int64)
    for k in range(rows.size):
        out[rows[k], x0[k] : x1[k]] = roots[k]
    return out


def _same_partition(a: np.ndarray, b: np.ndarray, ink: np.ndarray) -> bool:
    """True if `a` and `b` group the ink pixels identically."""
    pa, pb = a[ink], b[ink]
    fwd, back = {}, {}
    for u, v in zip(pa.tolist(), pb.tolist()):
        if fwd.setdefault(u, v) != v or back.setdefault(v, u) != u:
            return False
    return True


def _groups(roots) -> list[set[int]]:
    by = {}
    for k, r in enumerate(roots.tolist()):
        by.setdefault(r, set()).add(k)
    return sorted(by.values(), key=min)


# Each case: art, expected run groups (by run index in raster order).
CASES = {
    "empty": ("...\n...", []),
    "single_row_two_runs": ("#.#", [{0}, {1}]),
    "vertical_bar": ("#\n#\n#", [{0, 1, 2}]),
    "diagonal_touch": ("#.\n.#", [{0, 1}]),
    "anti_diagonal_touch": (".#\n#.", [{0, 1}]),
    "gap_of_one_column": ("#..\n..#", [{0}, {1}]),
    "blank_row_separates": ("#\n.\n#", [{0}, {1}]),
    "u_shape": ("#.#\n###", [{0, 1, 2}]),
    "n_shape": ("###\n#.#", [{0, 1, 2}]),
    "ring": (".#.\n#.#\n.#.", [{0, 1, 2, 3}]),
    "o_shape": ("###\n#.#\n###", [{0, 1, 2, 3}]),
    "zigzag_staircase": ("#...\n.#..\n..#.\n...#", [{0, 1, 2, 3}]),
    "w_shape": ("#.#.#\n.#.#.", [{0, 1, 2, 3, 4}]),
    "two_blobs": ("##..##\n##..##\n......\n.####.", [{0, 2}, {1, 3}, {4}]),
    "comb": ("#.#.#\n#####", [{0, 1, 2, 3}]),
    "late_bridge": ("#...#\n#...#\n#####", [{0, 1, 2, 3, 4}]),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_hand_drawn_components(pure, name):
    art, expected = CASES[name]
    roots, _ = _call(pure, _image(art))
    assert _groups(roots) == expected


@pytest.mark.parametrize("name", sorted(CASES))
def test_roots_are_smallest_index_in_component(pure, name):
    roots, _ = _call(pure, _image(CASES[name][0]))
    for group in _groups(roots):
        assert all(roots[k] == min(group) for k in group)


def test_single_row_height_skips_merging(pure):
    roots, _ = _call(pure, _image("##.#.##"))
    assert roots.tolist() == [0, 1, 2]


def test_zero_height():
    empty = np.zeros(0, dtype=np.int64)
    roots = mod.label_runs.py_func(empty, empty, empty, np.zeros(1, dtype=np.int64), 0)
    assert roots.size == 0


def test_find_halves_path():
    parent = np.array([0, 0, 1, 2], dtype=np.int64)
    assert mod._find.py_func(parent, 3) == 0
    assert parent.tolist() == [0, 0, 1, 1]


@pytest.mark.parametrize(
    ("i", "j", "expected"),
    [(1, 2, [0, 1, 1]), (2, 1, [0, 1, 1]), (1, 1, [0, 1, 2])],
)
def test_union_keeps_smaller_root(i, j, expected):
    parent = np.array([0, 1, 2], dtype=np.int64)
    mod._union.py_func(parent, i, j)
    assert parent.tolist() == expected


def test_union_same_set_is_noop():
    parent = np.array([0, 0, 0], dtype=np.int64)
    mod._union.py_func(parent, 1, 2)
    assert parent.tolist() == [0, 0, 0]


def test_merge_rows_empty_ranges_leave_parent():
    parent = np.arange(2)
    x = np.array([0, 0], dtype=np.int64)
    mod._merge_rows.py_func(parent, x, x + 1, 0, 0, 0, 2)
    mod._merge_rows.py_func(parent, x, x + 1, 0, 2, 2, 2)
    assert parent.tolist() == [0, 1]


EIGHT = np.ones((3, 3), dtype=int)


def _random_images(seed: int, count: int):
    rng = np.random.default_rng(seed)
    for _ in range(count):
        h, w = rng.integers(1, 12, size=2)
        yield rng.random((h, w)) < rng.uniform(0.1, 0.9)


@pytest.mark.parametrize("seed", range(4))
def test_matches_scipy_8_connectivity(pure, seed):
    scipy_ndimage = pytest.importorskip("scipy.ndimage")
    for ink in _random_images(seed, 150):
        roots, runs = _call(pure, ink)
        ref, _ = scipy_ndimage.label(ink, structure=EIGHT)
        assert _same_partition(_pixel_labels(ink, roots, runs), ref, ink), ink.astype(int)


def test_differs_from_4_connectivity_on_diagonal(pure):
    scipy_ndimage = pytest.importorskip("scipy.ndimage")
    ink = _image("#.\n.#")
    roots, runs = _call(pure, ink)
    ref4, n4 = scipy_ndimage.label(ink)
    assert n4 == 2
    assert not _same_partition(_pixel_labels(ink, roots, runs), ref4, ink)


@pytest.mark.parametrize("seed", range(2))
def test_compiled_matches_py_func(pure, seed):
    for ink in _random_images(100 + seed, 100):
        compiled, _ = _call(mod.label_runs, ink)
        python, _ = _call(pure, ink)
        assert compiled.tolist() == python.tolist()
