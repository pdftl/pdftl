# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/utils/test_text_templates.py

import pytest
import pikepdf

from pdftl.utils.text_templates import build_page_context, render_template


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pdf(num_pages=6):
    pdf = pikepdf.new()
    for _ in range(num_pages):
        pdf.add_blank_page()
    return pdf


def _static(
    num_pages=6,
    filename="report.pdf",
    filename_base="report",
    filepath="/docs/report.pdf",
):
    """Build a static context dict directly, bypassing build_static_context
    and its dependency on pdf.filename being readable. This keeps the tests
    cross-platform and independent of pikepdf internals."""
    return {
        "total": num_pages,
        "metadata": {},
        "filename": filename,
        "filename_base": filename_base,
        "filepath": filepath,
        "date": "2025-01-01",
        "time": "12:00:00",
        "datetime": "2025-01-01T12:00:00",
    }


def _render(template, page_num=1, num_pages=6, **static_kwargs):
    pdf = _make_pdf(num_pages)
    static = _static(num_pages=num_pages, **static_kwargs)
    ctx = build_page_context(static, pdf.pages[page_num - 1], page_num)
    return render_template(template, ctx)


# ---------------------------------------------------------------------------
# Simple variables
# ---------------------------------------------------------------------------


class TestSimpleVariables:
    def test_page_variable(self):
        assert _render("Chapter {page}", page_num=3) == "Chapter 3"

    def test_total_variable(self):
        assert _render("{total} pages", num_pages=6) == "6 pages"

    def test_filename_variable(self):
        assert _render("{filename}", filename="report.pdf") == "report.pdf"

    def test_filename_base_variable(self):
        assert _render("{filename_base}", filename_base="report") == "report"

    def test_filepath_variable(self):
        assert _render("{filepath}", filepath="/docs/report.pdf") == "/docs/report.pdf"

    def test_no_variables(self):
        assert _render("Static Title") == "Static Title"

    def test_combined_variables(self):
        result = _render(
            "{filename_base} - p.{page} of {total}",
            page_num=2,
            num_pages=6,
            filename_base="report",
        )
        assert result == "report - p.2 of 6"

    def test_date_variable(self):
        pdf = _make_pdf()
        static = _static()
        static["date"] = "2025-06-01"
        ctx = build_page_context(static, pdf.pages[0], 1)
        assert render_template("{date}", ctx) == "2025-06-01"

    def test_datetime_variable(self):
        pdf = _make_pdf()
        static = _static()
        static["datetime"] = "2025-06-01T12:00:00"
        ctx = build_page_context(static, pdf.pages[0], 1)
        assert render_template("{datetime}", ctx) == "2025-06-01T12:00:00"


# ---------------------------------------------------------------------------
# Arithmetic and formatting
# ---------------------------------------------------------------------------


class TestArithmeticAndFormatting:
    def test_arithmetic_offset(self):
        assert _render("{page+100}", page_num=1) == "101"

    def test_arithmetic_large_offset(self):
        assert _render("{page+5000}", page_num=1) == "5001"

    def test_formatting_zero_padded(self):
        assert _render("{page:06d}", page_num=1) == "000001"

    def test_arithmetic_and_formatting(self):
        assert _render("{page+5000:06d}", page_num=1) == "005001"

    def test_total_minus_page(self):
        assert _render("{total-page}", page_num=4, num_pages=6) == "2"

    def test_arithmetic_on_non_numeric_raises(self):
        with pytest.raises(ValueError):
            _render("{filename+1}")

    def test_n_variable(self):
        pdf = _make_pdf()
        static = _static()
        ctx = build_page_context(static, pdf.pages[0], 1)
        ctx["n"] = 3
        assert render_template("{n}", ctx) == "3"

    def test_n_variable_arithmetic(self):
        pdf = _make_pdf()
        static = _static()
        ctx = build_page_context(static, pdf.pages[0], 1)
        ctx["n"] = 1
        assert render_template("{n+3}", ctx) == "4"


# ---------------------------------------------------------------------------
# Metadata variables
# ---------------------------------------------------------------------------


class TestMetadataVariables:
    def test_meta_key_present(self):
        pdf = _make_pdf()
        static = _static()
        static["metadata"] = {"Title": "My Report"}
        ctx = build_page_context(static, pdf.pages[0], 1)
        assert render_template("{meta:Title}", ctx) == "My Report"

    def test_meta_key_absent_gives_empty_string(self):
        pdf = _make_pdf()
        static = _static()
        static["metadata"] = {}
        ctx = build_page_context(static, pdf.pages[0], 1)
        assert render_template("{meta:Author}", ctx) == ""


# ---------------------------------------------------------------------------
# Unknown variables
# ---------------------------------------------------------------------------


class TestUnknownVariables:
    def test_unknown_variable_raises(self):
        with pytest.raises(ValueError, match="Unknown variable"):
            _render("{unknown_var}")


# ---------------------------------------------------------------------------
# Escaping
# ---------------------------------------------------------------------------


class TestEscaping:
    def test_escaped_braces(self):
        assert _render("{{page}}") == "{page}"

    def test_escaped_braces_mixed(self):
        assert _render("p.{page} {{literal}}") == "p.1 {literal}"


# ---------------------------------------------------------------------------
# Source metadata fallback (no pipeline stash)
# ---------------------------------------------------------------------------


class TestSourceMetadataFallback:
    def test_source_filename_falls_back_to_filename(self):
        pdf = _make_pdf()
        static = _static(filename="doc.pdf", filepath="/a/doc.pdf")
        ctx = build_page_context(static, pdf.pages[0], 1)
        assert ctx["source_filename"] == "doc.pdf"

    def test_source_page_falls_back_to_page_num(self):
        pdf = _make_pdf()
        static = _static()
        ctx = build_page_context(static, pdf.pages[2], 3)
        assert ctx["source_page"] == 3

    def test_source_orientation_portrait(self):
        # Default blank page is 612x792 — portrait
        pdf = _make_pdf()
        static = _static()
        ctx = build_page_context(static, pdf.pages[0], 1)
        assert ctx["source_orientation"] == "Portrait"

    def test_source_orientation_landscape(self):
        # A landscape page (wider than tall) should yield "Landscape"
        pdf = pikepdf.new()
        pdf.pages.append(
            pikepdf.Page(pikepdf.Dictionary(Type=pikepdf.Name.Page, MediaBox=[0, 0, 792, 612]))
        )
        static = _static()
        ctx = build_page_context(static, pdf.pages[0], 1)
        assert ctx["source_orientation"] == "Landscape"

    def test_rotation_90_swaps_dimensions(self):
        # A 612x792 page rotated 90 degrees should have visual dims 792x612
        pdf = pikepdf.new()
        pdf.pages.append(
            pikepdf.Page(
                pikepdf.Dictionary(
                    Type=pikepdf.Name.Page,
                    MediaBox=[0, 0, 612, 792],
                    Rotate=90,
                )
            )
        )
        static = _static()
        ctx = build_page_context(static, pdf.pages[0], 1)
        assert ctx["source_width"] == 792.0
        assert ctx["source_height"] == 612.0
        assert ctx["source_orientation"] == "Landscape"


# ---------------------------------------------------------------------------
# Source metadata present (pipeline stash path)
# ---------------------------------------------------------------------------


class TestSourceMetadataPipeline:
    def test_pipeline_stash_used_when_present(self):
        # Simulate a page that went through the pipeline (cat/shuffle),
        # which stashes source info under PDFTL_SOURCE_INFO_KEY.
        import pdftl.core.constants as c
        from pikepdf import Dictionary

        pdf = _make_pdf()
        page = pdf.pages[0]
        static = _static(filename="output.pdf")

        # Stash pipeline source info directly onto the page dict,
        # matching what _stash_page_source_data does.
        stash = Dictionary(
            {
                "/source_filename": "original.pdf",
                "/source_path": "/data/original.pdf",
                "/source_page": 3,
                "/source_rotation": 0,
                "/source_width": 595.0,
                "/source_height": 842.0,
                "/source_orientation": "Portrait",
                "/source_cropbox": "[0, 0, 595, 842]",
                "/source_mediabox": "[0, 0, 595, 842]",
                "/source_filesize": "",
            }
        )
        page["/" + c.PDFTL_SOURCE_INFO_KEY] = stash

        ctx = build_page_context(static, page, 1)

        # Pipeline values should win over static/fallback values
        assert ctx["source_filename"] == "original.pdf"
        assert ctx["source_path"] == "/data/original.pdf"
        assert ctx["source_page"] == 3


# ---------------------------------------------------------------------------
# build_static_context — docinfo exception branch
# ---------------------------------------------------------------------------


class TestBuildStaticContext:
    def test_docinfo_exception_gives_empty_metadata(self):
        from unittest.mock import MagicMock, PropertyMock, patch
        from pdftl.utils.text_templates import build_static_context

        pdf = pikepdf.new()
        pdf.add_blank_page()

        # Make docinfo.items() raise to trigger the except branch (lines 73-75)
        mock_docinfo = MagicMock()
        mock_docinfo.items.side_effect = ValueError("corrupt docinfo")

        with patch.object(
            type(pdf), "docinfo", new_callable=PropertyMock, return_value=mock_docinfo
        ):
            ctx = build_static_context(pdf)

        assert ctx["metadata"] == {}
        assert ctx["total"] == 1
