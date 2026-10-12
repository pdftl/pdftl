import pikepdf
from pikepdf import Matrix

from pdftl.utils.affix_content import affix_content, apply_content_matrix, padded_content

UNTERMINATED = b"q 0 0 m 10 10 l S Q"


def _literal_ops(pdf, page):
    """Operators of the page's streams joined byte-for-byte, as a
    concatenating consumer such as CUPS pdftopdf reads them."""
    contents = page.obj.Contents
    streams = contents if isinstance(contents, pikepdf.Array) else [contents]
    joined = pdf.make_stream(b"".join(s.read_bytes() for s in streams))
    return [str(op) for _, op in pikepdf.parse_content_stream(joined)]


def _page_with_unterminated_content():
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    page.Contents = pdf.make_stream(UNTERMINATED)
    return pdf, page


def test_prepend():
    pdf = pikepdf.new()
    pdf.add_blank_page()
    page = pdf.pages[0]
    words = ["Yo, ", "hello", "", " world"]
    affix_content(page, words[1], "head")
    affix_content(page, words[3], "tail")
    affix_content(page, words[0], "head")
    assert len(page.Contents) == 4
    answers = [b"\nYo, \n", b"\nhello\n", b"", b"\n world\n"]
    assert [x.read_bytes() for x in page.Contents] == answers


def test_padded_content_accepts_str_and_bytes():
    assert padded_content("0 g") == b"\n0 g\n"
    assert padded_content(b"0 g") == b"\n0 g\n"


def test_affixed_code_does_not_fuse_with_unterminated_content():
    pdf, page = _page_with_unterminated_content()
    affix_content(page, "2 0 0 2 0 0 cm", "head")
    affix_content(page, "0 g", "tail")
    assert _literal_ops(pdf, page) == ["cm", "q", "m", "l", "S", "Q", "g"]


def test_apply_content_matrix_does_not_fuse_with_unterminated_content():
    pdf, page = _page_with_unterminated_content()
    apply_content_matrix(page, Matrix(2, 0, 0, 2, 0, 0))
    assert _literal_ops(pdf, page) == ["q", "cm", "q", "m", "l", "S", "Q", "Q"]
