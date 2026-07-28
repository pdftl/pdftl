# tests/visual/test_subset_fonts_corpus.py

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""
Visual regression harness for subset_fonts, run against a private corpus
of real-world PDFs at tests/files/private/fonts_corpus/*.pdf.

This corpus is intentionally NOT checked into the repo (real-world PDFs
commonly carry copyrighted/private content) -- the whole module is skipped
when the directory is absent or empty, so CI and other contributors' local
runs are unaffected. Populate it locally by dropping real-world PDFs into
that directory; this mirrors (and replaces the manual-inspection step of)
the ad-hoc loop:

    ls *.pdf | shuf | while read x; do
      pdftl "$x" subset_fonts output subsetted/"$x" || (echo crash; cp "$x" crashed)
      diff-pdf --per-page-pixel-tolerance 20 "$x" subsetted/"$x" || diffpdf "$x" subsetted/"$x" &
    done

Unlike tests/visual/conftest.py's `assert_pdf_match` (which compares a test
PDF against a COMMITTED baseline file), this compares each corpus PDF
against ITSELF post-subsetting -- there is no baseline to store, since
subset_fonts is defined as a purely lossless-rendering operation: it must
never change which glyphs paint, only drop unused ones from the font
program. So "before" and "after" are rendered from the exact same input
file on every run, with no baseline drift to maintain.

Reuses conftest.compare_rendered_pages -- the same page-by-page diff/
tolerance/artifact-saving logic assert_pdf_match itself uses -- rather
than reimplementing comparison here, so both fixtures stay in sync if
tolerance or diff-saving behavior ever changes.
"""

from __future__ import annotations

import io
from pathlib import Path

import pikepdf
import pytest

from pdftl.operations.subset_fonts import subset_fonts
from tests.visual.conftest import compare_rendered_pages, render_pdf_to_images

_CORPUS_DIR = Path(__file__).parent.parent / "files" / "private" / "fonts_corpus"


def _discover_corpus_pdfs() -> list[Path]:
    if not _CORPUS_DIR.is_dir():
        return []
    return sorted(_CORPUS_DIR.glob("*.pdf"))


_CORPUS_PDFS = _discover_corpus_pdfs()

pytestmark = pytest.mark.skipif(
    not _CORPUS_PDFS,
    reason=(
        f"No private font corpus found at {_CORPUS_DIR} -- this test module "
        "is a local-only regression harness against real-world PDFs that "
        "are not checked into the repo. Populate the directory with PDFs "
        "to exercise it."
    ),
)


@pytest.mark.parametrize("pdf_path", _CORPUS_PDFS, ids=[p.name for p in _CORPUS_PDFS])
def test_subset_fonts_preserves_visual_rendering(pdf_path: Path, tmp_path):
    """
    For each PDF in the private corpus: runs subset_fonts, then renders
    the original and subsetted versions page-by-page and asserts they are
    visually identical (via the same tolerance/diff logic assert_pdf_match
    uses).

    A page-count mismatch or a rendering exception in either version fails
    the test outright, distinctly from a visual-diff failure, so a crash
    (e.g. subset_fonts corrupting the file so pypdfium2 can't even open
    it) is reported distinguishably from "it opened fine but glyphs moved".
    """
    original_bytes = pdf_path.read_bytes()

    try:
        pdf = pikepdf.open(pdf_path)
    except pikepdf.PdfError as e:
        pytest.fail(f"{pdf_path.name}: input PDF itself failed to open: {e}")

    try:
        result = subset_fonts(pdf, [])
    except Exception as e:  # noqa: BLE001 -- a corpus PDF crashing subset_fonts IS the failure
        pytest.fail(f"{pdf_path.name}: subset_fonts raised: {e!r}")

    assert result.success, f"{pdf_path.name}: subset_fonts reported failure"

    buf = io.BytesIO()
    pdf.save(buf)
    subsetted_bytes = buf.getvalue()

    try:
        original_images = render_pdf_to_images(original_bytes)
    except Exception as e:  # noqa: BLE001
        pytest.fail(f"{pdf_path.name}: original PDF failed to render: {e!r}")

    try:
        subsetted_images = render_pdf_to_images(subsetted_bytes)
    except Exception as e:  # noqa: BLE001
        pytest.fail(
            f"{pdf_path.name}: subsetted output failed to render "
            f"(likely a corrupted font program): {e!r}"
        )

    compare_rendered_pages(original_images, subsetted_images, tmp_path, pdf_path.stem)
