# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl-tests.zip/tests/comparison_helpers.py

import math
from pathlib import Path

import fitz  # PyMuPDF
from pikepdf import Pdf
from PIL import Image, ImageChops


def compare_page_count(path_py: Path, path_tk: Path):
    """Asserts that two PDFs have the same number of pages."""
    # print(path_py, path_tk)
    with Pdf.open(path_py) as pdf_py, Pdf.open(path_tk) as pdf_tk:
        [pdf_py_len, pdf_tk_len] = [len(x.pages) for x in (pdf_py, pdf_tk)]
        # print(pdf_py_len, pdf_tk_len)
        assert pdf_py_len == pdf_tk_len, f"Page counts do not match: {pdf_py_len} vs {pdf_tk_len}."


def compare_visuals(path_py: Path, path_tk: Path, tolerance: float = 10.0):
    """Asserts that two PDFs are visually similar within a tolerance."""

    doc_py = fitz.open(path_py)
    doc_tk = fitz.open(path_tk)

    assert len(doc_py) == len(doc_tk), "Cannot compare visuals, page counts differ."
    num_pages = len(doc_py)

    # check page sizes
    py_sizes = [p.rect for p in doc_py]
    tk_sizes = [q.rect for q in doc_tk]
    for i, (p, q) in enumerate(zip(doc_py, doc_tk)):
        assert all(
            [math.isclose(p.rect[k], q.rect[k], rel_tol=0.00001) for k in range(4)]
        ), f"page {i+1} has differing sizes: {p.rect}, {q.rect}"

    pix_pys = [doc_py.load_page(i).get_pixmap() for i, _ in enumerate(doc_py)]
    doc_py.close()
    pix_tks = [doc_tk.load_page(i).get_pixmap() for i, _ in enumerate(doc_tk)]
    doc_tk.close()
    for i in range(num_pages):
        pix_py = pix_pys[i]
        pix_tk = pix_tks[i]

        img_py = Image.frombytes("RGB", [pix_py.width, pix_py.height], pix_py.samples)
        img_tk = Image.frombytes("RGB", [pix_tk.width, pix_tk.height], pix_tk.samples)

        diff = ImageChops.difference(img_py, img_tk)
        # # attempt to free some memory
        # pix_py, pix_t, img_py, img_tk = None, None, None, None

        if diff.getbbox() is not None:  # getbbox is None if images are identical
            # Simplified difference metric
            stat = diff.getextrema()
            max_diff_percent = max(s[1] for s in stat) / 255 * 100
            pix_py.save(Path(str(path_py) + ".png"))
            pix_tk.save(Path(str(path_tk) + ".png"))
            diff.save(Path(str(path_py) + ".diff.png"))
            assert (
                max_diff_percent < tolerance
            ), f"Page {i+1} is visually different by {max_diff_percent}%, more than tolerance={tolerance}%"
