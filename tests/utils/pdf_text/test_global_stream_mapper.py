import pytest
from unittest.mock import MagicMock
from pdftl.utils.pdf_text.global_stream_mapper import GlobalStreamMapper


@pytest.fixture
def mock_dependencies():
    tp = MagicMock()
    tp.get_text.side_effect = ["PageOne", "PageTwoText"]

    doc = MagicMock()
    page0 = MagicMock()
    page0.get_size.return_value = (500.0, 800.0)
    page1 = MagicMock()
    page1.get_size.return_value = (500.0, 800.0)
    doc.__getitem__.side_effect = lambda x: [page0, page1][x]

    margins = {"top": 50, "bottom": 50, "left": 50, "right": 50}
    return tp, doc, margins


def test_build_stream_map(mock_dependencies):
    tp, doc, margins = mock_dependencies
    mapper = GlobalStreamMapper(tp, 2, doc, margins)

    assert mapper.page_offsets == [0, 7]  # len("PageOne") == 7
    assert mapper.global_text == ["PageOne", "PageTwoText"]
    assert mapper.full_stream == "PageOnePageTwoText"
    assert mapper.page_sizes == [(500.0, 800.0), (500.0, 800.0)]


def test_is_inside_margin(mock_dependencies):
    tp, doc, margins = mock_dependencies
    mapper = GlobalStreamMapper(tp, 2, doc, margins)

    page_w, page_h = 500, 800

    # Inside top margin (y1 >= 750)
    assert mapper._is_inside_margin((100, 740, 200, 760), page_w, page_h) is True
    # Inside bottom margin (y0 <= 50)
    assert mapper._is_inside_margin((100, 40, 200, 60), page_w, page_h) is True
    # Inside left margin (x0 <= 50)
    assert mapper._is_inside_margin((40, 100, 60, 200), page_w, page_h) is True
    # Inside right margin (x1 >= 450)
    assert mapper._is_inside_margin((440, 100, 460, 200), page_w, page_h) is True

    # Safe text area
    assert mapper._is_inside_margin((100, 100, 200, 200), page_w, page_h) is False

    # Test with 0 margins
    mapper.margins = {"top": 0, "bottom": 0, "left": 0, "right": 0}
    assert mapper._is_inside_margin((10, 790, 20, 800), page_w, page_h) is False


def test_resolve_span(mock_dependencies):
    tp, doc, margins = mock_dependencies
    mapper = GlobalStreamMapper(tp, 2, doc, margins)

    # Invalid span
    assert mapper.resolve_span(5, 5) == []

    # Single page span
    # "PageOne" -> indices 0-6
    spans = mapper.resolve_span(2, 5)
    assert spans == [(0, 2, 5)]

    # Cross-page span
    # indices 5 to 10 ("ne" from Page 1, "Pag" from Page 2)
    spans2 = mapper.resolve_span(5, 10)
    assert spans2 == [
        (0, 5, 7),  # local 5 to 7 (end of page 1)
        (1, 0, 3),  # local 0 to 3 (start of page 2)
    ]
