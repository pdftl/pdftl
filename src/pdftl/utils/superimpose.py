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
    # Matrix.shorthand provides the 6 numbers as a string, e.g., "1 0 0 1 50 50"
    # But for safety, we access components directly or use simple formatting.
    # pikepdf 7+ Matrix objects are iterable.
    a, b, c, d, e, f = matrix

    # Create the content stream command
    # q = save state, cm = apply matrix, Do = draw XObject, Q = restore state
    cmd = b"q %f %f %f %f %f %f cm /%s Do Q" % (a, b, c, d, e, f, name)

    target_page.contents_add(cmd)


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    import pikepdf

    pdf1 = pikepdf.open(args[0])
    pdf2 = pikepdf.open(args[1])
    superimpose_page(pdf1.pages[0], pdf2.pages[0], pikepdf.Array(map(float, args[2:])))
    pdf1.save("/tmp/testout.pdf")
