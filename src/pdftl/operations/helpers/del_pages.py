# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/helpers/del_pages.py

"""Helpers for page deletion"""

from pdftl.utils.page_labels import remap_page_labels

import logging

logger = logging.getLogger(__name__)


def del_pages(pdf, pages_to_delete, preserve_page_labels=True):
    pages_list = list(pages_to_delete)
    new_pages_list = [i for i in range(len(pdf.pages)) if (i + 1) not in pages_list]

    if preserve_page_labels:
        remap_page_labels(pdf, pdf, new_pages_list)

    for page_num in sorted(pages_list, reverse=True):
        _del_page(pdf, page_num)


def _del_page(pdf, page_num):
    # See https://github.com/pikepdf/pikepdf/issues/196
    # idea is to remove /Contents, /Resources first
    # to avoid them sticking around after page deletion
    page = pdf.pages[page_num - 1]
    logger.debug("deleting page_num=%s", page_num)
    for key in page.keys():
        del page[key]
    del pdf.pages[page_num - 1]
