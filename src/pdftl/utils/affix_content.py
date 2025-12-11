# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/affix_content.py

"""Inject PDF code at the start or end of a page content stream"""


def affix_content(page, code, position):
    """
    Inject code into page content stream
    at given position, either 'head' or 'tail'
    """
    page.contents_add(bytes(code, "utf-8"), prepend=position == "head")
