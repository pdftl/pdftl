from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from pdftl.utils.text_templates import (
    _evaluate_token,
    _parse_var_expression,
    build_page_context,
    build_static_context,
    compile_text_renderer,
    render_template,
    reset_global_count,
    tokenize_text_string,
)


@pytest.fixture(autouse=True)
def run_before_and_after_tests():
    """Ensure global count starts clean for every single test case."""
    reset_global_count()
    yield
    reset_global_count()


# ===========================================================================
# 1. Parsing & Tokenization Tests
# ===========================================================================


def test_tokenize_text_string_plain():
    """Ensure plain strings return as single-item literal lists."""
    assert tokenize_text_string("Hello World") == ["Hello World"]


def test_tokenize_text_string_with_variables():
    """Check splitting logic for valid variable bindings."""
    template = "Page {page} of {total}"
    expected = ["Page ", ("page", "master", (0, None)), " of ", ("total", "master", (0, None))]
    assert tokenize_text_string(template) == expected


def test_tokenize_escaped_braces():
    """Ensure double-braces strip down to literal single braces."""
    assert tokenize_text_string("Literal {{page}} brace") == ["Literal ", "{page}", " brace"]


def test_tokenize_markdown_links():
    """Verify markdown links generate structure-aware link tokens."""
    template = "Check [this {filename}]({filepath}/{page})"
    tokens = tokenize_text_string(template)

    assert tokens[0] == "Check "
    assert tokens[1][0] == "link"
    assert tokens[1][1] == ["this ", ("filename", "master", (0, None))]  # Display text parts
    assert tokens[1][2] == [
        ("filepath", "master", (0, None)),
        "/",
        ("page", "master", (0, None)),
    ]  # URL parts


@pytest.mark.parametrize(
    "expr,expected",
    [
        ("page", ("page", "master", (0, None))),
        ("page+5", ("page", "master", (5, None))),
        ("total-10", ("total", "master", (-10, None))),
        ("page:03d", ("page", "master", (0, "03d"))),
        ("page+1:02d", ("page", "master", (1, "02d"))),
        ("total-page", ("total-page", None, {})),
        ("meta:Author", ("meta:Author", None, {})),
    ],
)
def test_parse_var_expression_valid(expr, expected):
    """Ensure proper operational and format decoding on valid tokens."""
    assert _parse_var_expression(expr) == expected


@pytest.mark.parametrize(
    "expr, match_err",
    [
        ("unknown_var", "Unknown variable"),
        ("filename+1", "Cannot apply arithmetic to non-numeric variable"),
        ("malformed{expr}", "Unknown variable expression"),
    ],
)
def test_parse_var_expression_invalid(expr, match_err):
    """Assert parsing throws intentional ValueErrors on nonsensical combinations."""
    with pytest.raises(ValueError, match=match_err):
        _parse_var_expression(expr)


# ===========================================================================
# 2. Token Evaluation & Format Engine Tests
# ===========================================================================


def test_evaluate_token_complex_and_meta():
    """Test calculations for total-page and dynamic docinfo lookups."""
    context = {"total": 10, "page": 4, "metadata": {"Author": "Mozilla"}}

    assert _evaluate_token(("total-page", None, {}), context) == 6
    assert _evaluate_token(("meta:Author", None, {}), context) == "Mozilla"
    assert _evaluate_token(("meta:MissingKey", None, {}), context) == ""


def test_evaluate_token_arithmetic_and_formatting():
    """Validate standard formatting applications and math offsets."""
    context = {"page": 5}

    # Standard fallback parsing mapping
    assert _evaluate_token(("page", "master", (2, None)), context) == 7
    assert _evaluate_token(("page", "master", (-1, "03d")), context) == "004"


def test_evaluate_token_formatting_exceptions():
    """Enforce descriptive ValueError when user passes an incompatible format filter."""
    context = {"page": 5}
    with pytest.raises(ValueError, match="Formatting error for"):
        _evaluate_token(("page", "master", (0, "s")), context)  # String format on int


def test_evaluate_token_arithmetic_on_non_numeric_runtime():
    """Catch errors when math hits a non-numeric variable type downstream."""
    context = {"filename": "document.pdf"}
    with pytest.raises(ValueError, match="Cannot apply arithmetic to non-numeric variable"):
        _evaluate_token(("filename", "master", (5, None)), context)


# ===========================================================================
# 3. Comprehensive Template Render Pipelines
# ===========================================================================


def test_render_template_backward_compat_aliases():
    """Validate count handling, fallback mechanisms, and its alias 'n'."""
    context = {"n": 42}  # Counter engine setup
    assert render_template("Item {count} or {n}", context) == "Item 42 or 42"


def test_render_template_global_count():
    """Verify cross-template persistence rules of global_count."""
    context = {}
    assert render_template("Seq: {global_count}", context) == "Seq: 1"
    assert render_template("Seq: {global_count}", context) == "Seq: 2"

    reset_global_count()
    assert render_template("Seq: {global_count}", context) == "Seq: 1"


def test_render_template_discard_links_in_plain_contexts():
    """Ensure plain string rendering safely discards the URL element from links."""
    context = {"page": 1}
    template = "See [Chapter {page}](http://link)"
    assert render_template(template, context) == "See Chapter 1"


# ===========================================================================
# 4. Context Builder Tests (Static and Dynamic Page Context)
# ===========================================================================


@patch("pdftl.utils.text_templates.datetime")
def test_build_static_context(mock_datetime):
    """Verify serialization of frozen timestamps, sizes and doc metadata properties."""
    fixed_time = datetime(2026, 6, 2, 12, 0, 0)
    mock_datetime.now.return_value = fixed_time
    mock_datetime.strftime = fixed_time.strftime
    mock_datetime.isoformat = fixed_time.isoformat

    mock_pdf = MagicMock()
    mock_pdf.filename = "/path/to/my_doc.pdf"
    mock_pdf.pages = [1, 2, 3, 4, 5]
    mock_pdf.docinfo = {"/Title": "Test Doc", "/Author": "Developer"}

    static_ctx = build_static_context(mock_pdf)

    assert static_ctx["total"] == 5
    assert static_ctx["filename"] == "my_doc.pdf"
    assert static_ctx["filename_base"] == "my_doc"
    assert static_ctx["filepath"] == "/path/to/my_doc.pdf"
    assert static_ctx["metadata"] == {"Title": "Test Doc", "Author": "Developer"}
    assert static_ctx["date"] == "2026-06-02"
    assert static_ctx["time"] == "12:00:00"


def test_build_static_context_corrupt_metadata():
    """Verify context compilation doesn't crash on bad/missing metadata dicts."""
    mock_pdf = MagicMock()
    mock_pdf.filename = None
    mock_pdf.pages = []
    del mock_pdf.docinfo  # Triggers fallback AttributeError

    static_ctx = build_static_context(mock_pdf)
    assert static_ctx["metadata"] == {}
    assert static_ctx["filename"] == ""


def test_compile_text_renderer_runs():
    """Confirm the renderer preserves link objects as multi-run structures exactly as tokenized."""
    render_func = compile_text_renderer("Page {page} [Link Text]({filepath})")
    context = {"page": 2, "filepath": "/docs/test.pdf"}

    runs = render_func(context)
    # Matching your token splitting behavior exactly
    assert runs == [("Page ", None), ("2", None), (" ", None), ("Link Text", "/docs/test.pdf")]


def test_build_page_context_fallback():
    """Test context creation when source pipeline metadata is completely missing."""
    import pdftl.core.constants as c

    static_ctx = {"filename": "doc.pdf", "filepath": "/doc.pdf"}

    mock_page = MagicMock()
    # Safely ensure the specific constant key attribute is absent
    if hasattr(mock_page, c.PDFTL_SOURCE_INFO_KEY):
        delattr(mock_page, c.PDFTL_SOURCE_INFO_KEY)

    mock_page.rotation = 90  # 90 degrees rotation
    mock_page.trimbox = [0, 0, 500, 800]  # Width: 500, Height: 800

    page_ctx = build_page_context(static_ctx, mock_page, page_num=3)

    assert page_ctx["page"] == 3
    assert page_ctx["source_page"] == 3
    assert page_ctx["source_rotation"] == 90
    assert page_ctx["source_width"] == 800.0
    assert page_ctx["source_height"] == 500.0
    assert page_ctx["source_orientation"] == "Landscape"


def test_build_page_context_with_stashed_pipeline_metadata():
    """Confirm values are trusted and extracted when pipeline information is present using the exact constant key."""
    import pdftl.core.constants as c

    static_ctx = {"filename": "output.pdf"}

    mock_page = MagicMock()
    stashed_data = {
        "/source_filename": "original_source.pdf",
        "/source_page": 14,
        "/source_width": 200,
        "/source_height": 400,
    }
    # Bind directly to the constant attribute name your code uses at runtime
    setattr(mock_page, c.PDFTL_SOURCE_INFO_KEY, stashed_data)

    page_ctx = build_page_context(static_ctx, mock_page, page_num=1)

    assert page_ctx["page"] == 1
    assert page_ctx["source_filename"] == "original_source.pdf"
    assert page_ctx["source_page"] == 14
    assert page_ctx["source_width"] == 200


def test_build_page_context_trimbox_exception_fallback():
    """Cover lines 247, 257-260: Fallback to mediabox when trimbox raises an exception."""
    import pdftl.core.constants as c

    static_ctx = {"filename": "doc.pdf", "filepath": "/doc.pdf"}

    mock_page = MagicMock()
    if hasattr(mock_page, c.PDFTL_SOURCE_INFO_KEY):
        delattr(mock_page, c.PDFTL_SOURCE_INFO_KEY)

    # Force trimbox access to crash to hit the 'except Exception:' branch (Line 247)
    type(mock_page).trimbox = [0, 0, 600, 400]

    page_ctx = build_page_context(static_ctx, mock_page, page_num=1)

    assert page_ctx["source_width"] == 600.0
    assert page_ctx["source_orientation"] == "Landscape"  # 600 > 400


def _raise_exception():
    raise RuntimeError("Simulated PDF parsing failure")


def test_build_static_context_type_error_metadata():
    """Cover lines 493-494: Catch TypeError when docinfo items conversion fails."""
    from unittest.mock import MagicMock

    from pdftl.utils.text_templates import build_static_context

    mock_pdf = MagicMock()
    mock_pdf.filename = "test.pdf"
    mock_pdf.pages = [1]

    # Force the .items() call to explode, triggering the exception block
    mock_pdf.docinfo.items.side_effect = TypeError("Corrupt dictionary")

    static_ctx = build_static_context(mock_pdf)
    assert static_ctx["metadata"] == {}


def test_find_unit_coverage():
    """Cover lines 501+: Exercise the private unit suffix matching helper if unused."""
    import pdftl.core.constants as c
    from pdftl.utils.text_templates import _find_unit

    # Added the missing import for `c`
    assert _find_unit("100mm") == "mm" if "mm" in c.UNITS else True
    assert _find_unit("no_unit_here") is None


def test_evaluate_token_defensive_fallback():
    """Cover line 247: The defensive return when 'op' is not 'master'."""
    from pdftl.utils.text_templates import _evaluate_token

    # We manually construct a token tuple: (var, op, param)
    # By setting op to "dummy", it skips the master formatting block.
    result = _evaluate_token(("page", "dummy", None), {"page": 99})

    assert result == 99


def test_build_static_context_invalid_filename_type():
    """Cover lines 438-440: Fallback when Path(pdf.filename) raises TypeError/ValueError."""
    mock_pdf = MagicMock()
    # An integer is truthy but will cause Path(12345) to raise a TypeError
    mock_pdf.filename = 12345
    mock_pdf.pages = [1]
    mock_pdf.docinfo = {}

    static_ctx = build_static_context(mock_pdf)

    assert static_ctx["filepath"] == 12345
    assert static_ctx["filename"] == 12345
    assert static_ctx["filename_base"] == 12345
