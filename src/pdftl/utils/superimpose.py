# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/superimpose.py

"""Superimpose one page onto another, with an arbitrary transformation matrix"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Matrix, Page


def superimpose_page(target_page: "Page", source_page: "Page", matrix: "Matrix"):
    """
    Places the source_page onto target_page using the calculated matrix.
    """
    import pikepdf

    # 1. Create the Form XObject (The "Stamp")
    src_form = source_page.as_form_xobject()

    # 2. Add it to the target's resources
    name = target_page.add_resource(src_form, pikepdf.Name.XObject)

    # 3. Draw it using the Matrix
    # Use .encode() to get the bytes "a b c d e f" directly
    matrix_bytes = matrix.encode()

    # Create the content stream command
    # q = save state, cm = apply matrix, Do = draw XObject, Q = restore state
    cmd = b"q %s cm /%s Do Q" % (matrix_bytes, name)

    target_page.contents_add(cmd)
