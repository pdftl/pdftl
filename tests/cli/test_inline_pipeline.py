import pikepdf

from pdftl.cli.main import main


def run_main(args):
    return main(["pdftl"] + args)


def test_basic_inline_pipeline(dummy_pdfs, tmp_path):
    """
    Test: R=JOB ... DONE
    """
    input_pdf = dummy_pdfs["a.pdf"]
    output_pdf = tmp_path / "out.pdf"

    args = [
        "R=JOB",
        str(input_pdf),
        "rotate",
        "right",
        "DONE",
        "cat",
        "R",
        "output",
        str(output_pdf),
    ]

    exit_code = run_main(args)
    assert exit_code == 0
    assert output_pdf.exists()

    with pikepdf.open(output_pdf) as pdf:
        assert len(pdf.pages) == 20
        # "right" usually corresponds to 90 degrees clockwise
        assert pdf.pages[0].get("/Rotate") == 90


def test_inline_scope_visibility(dummy_pdfs, tmp_path):
    """
    Test: Outer define 'X', Inner uses 'X'.
    """
    in_a = dummy_pdfs["a.pdf"]
    in_b = dummy_pdfs["b.pdf"]
    output_pdf = tmp_path / "scope_test.pdf"

    # "Y=JOB X cat ..."
    # Load X first so cat has something to append to
    args = [
        f"X={in_a}",
        "Y=JOB",
        "X",
        str(in_b),
        "cat",
        "DONE",
        "cat",
        "Y",
        "output",
        str(output_pdf),
    ]

    exit_code = run_main(args)
    assert exit_code == 0

    with pikepdf.open(output_pdf) as pdf:
        assert len(pdf.pages) == 40


def test_nested_inline_pipeline(dummy_pdfs, tmp_path):
    """
    Test: F=JOB ... I=JOB ... DONE ... DONE
    """
    input_pdf = dummy_pdfs["a.pdf"]
    output_pdf = tmp_path / "nested.pdf"

    # Logic:
    # 1. Start F
    # 2. Start I -> Load input, select page 1 -> End I
    # 3. F continues -> Load I
    args = [
        "F=JOB",
        "I=JOB",
        str(input_pdf),
        "cat",
        "1",
        "DONE",
        "DONE",
        "cat",
        "F",
        "output",
        str(output_pdf),
    ]

    exit_code = run_main(args)
    assert exit_code == 0

    with pikepdf.open(output_pdf) as pdf:
        assert len(pdf.pages) == 1
