import pikepdf

from pdftl.utils.affix_content import affix_content


def test_prepend():
    pdf = pikepdf.new()
    pdf.add_blank_page()
    page = pdf.pages[0]
    words = ["Yo, ", "hello", "", " world"]
    affix_content(page, words[1], "head")
    affix_content(page, words[3], "tail")
    affix_content(page, words[0], "head")
    assert len(page.Contents) == 4
    answers = [bytes(x, "utf-8") for x in words]
    for i, x in enumerate(page.Contents):
        assert x.read_bytes() == answers[i]
