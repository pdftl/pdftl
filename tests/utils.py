# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/utils.py

from pikepdf import Pdf


def create_test_pdf(filename, num_pages=1):
    pdf = Pdf.new()
    for _ in range(num_pages):
        pdf.add_blank_page()
    pdf.save(filename)


def get_page_count(filename):
    with Pdf.open(filename) as pdf:
        return len(pdf.pages)
