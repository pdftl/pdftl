import pikepdf

from pdftl.commands.dump_annots import dump_annots, dump_annots_cli_hook
from pdftl.commands.dump_data import dump_data_cli_hook, pdf_info
from pdftl.commands.dump_text import dump_text

# --- 1. Test dumping to a file (Paths) ---


def test_dump_data_to_file(two_page_pdf, tmp_path):
    output_path = tmp_path / "data.txt"
    with pikepdf.open(two_page_pdf) as pdf:
        result = pdf_info("dump_data", pdf, "dummy.pdf", [], output_file=str(output_path))
        dump_data_cli_hook(result, None)

    assert output_path.exists()
    content = output_path.read_text(encoding="utf-8")
    assert "NumberOfPages: 2" in content


def test_dump_annots_to_file(two_page_pdf, tmp_path):
    output_path = tmp_path / "annots.json"
    with pikepdf.open(two_page_pdf) as pdf:
        result = dump_annots(pdf, output_file=str(output_path))
        dump_annots_cli_hook(result, None)
    assert output_path.exists()


# --- 2. Test dumping to STDOUT ---


def test_dump_data_to_stdout(two_page_pdf, capsys):
    with pikepdf.open(two_page_pdf) as pdf:
        result = pdf_info("dump_data", pdf, "dummy.pdf", [], output_file=None)

        assert result.success
        assert result.is_discardable

        dump_data_cli_hook(result, None)

    captured = capsys.readouterr()
    assert "NumberOfPages: 2" in captured.out


def test_dump_text_to_stdout(two_page_pdf, capsys):
    dump_text(str(two_page_pdf), input_password="", output_file=None)

    captured = capsys.readouterr()
    # Assuming the dummy PDF is empty text, just check for no crash/errors
    assert captured.err == ""


def test_dump_annots_to_stdout(two_page_pdf, capsys):
    with pikepdf.open(two_page_pdf) as pdf:
        dump_annots(pdf, output_file=None)

    captured = capsys.readouterr()
    assert captured.err == ""
