# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/affix_content.py

"""Inject PDF code at the start or end of a page content stream"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Matrix


def affix_content(page, code, position):
    """
    Inject code into page content stream
    at given position, either 'head' or 'tail'
    """
    page.contents_add(bytes(code, "utf-8"), prepend=position == "head")


def apply_content_matrix(page, matrix: "Matrix") -> None:
    """Wraps a page's content stream in `q ... cm` / `Q` using the given matrix."""
    matrix_str = matrix.encode().decode("utf-8")
    affix_content(page, "Q", "tail")
    affix_content(page, f"q {matrix_str} cm ", "head")
