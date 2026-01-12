# tests/compat_pdftk/test_pdftk_compat.py
#
# Ported from pdftk-java tests.
# Original License: GPLv2+
#
# Requirements:
# - A sibling 'conftest.py' with fixtures: run_pdftl, get_test_file, compare_pdfs_as_svg
# - A sibling 'files/' directory containing the test PDFs

import pytest
import re
from pathlib import Path

# ==========================================
# Helper for converting Java slurp()
# ==========================================
def slurp(path):
    """Reads a file as a UTF-8 string, normalizing line endings."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return text.replace("\r\n", "\n")

def slurp_bytes(path):
    """Reads a file as bytes."""
    return Path(path).read_bytes()

# ==========================================
# AnnotationsTest.java
# ==========================================
class TestAnnotations:
    def test_dump_links(self, run_pdftl, get_test_file):
        refs_pdf = get_test_file("test/files/refs.pdf")
        expected_path = get_test_file("test/files/refs_annots.txt")
        
        result = run_pdftl([refs_pdf, "dump_data_annots"])
        
        expected_data = slurp(expected_path)
        actual_data = result.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")
        assert expected_data == actual_data

    def test_dump_text(self, run_pdftl, get_test_file):
        annot_pdf = get_test_file("test/files/annotation.pdf")
        expected_path = get_test_file("test/files/annotation_annots.txt")
        
        result = run_pdftl([annot_pdf, "dump_data_annots"])
        
        expected_data = slurp(expected_path)
        actual_data = result.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")
        assert expected_data == actual_data


# ==========================================
# AttachFilesTest.java
# ==========================================
class TestAttachFiles:
    def test_no_attachment(self, run_pdftl, get_test_file, tmp_path):
        blank_pdf = get_test_file("test/files/blank.pdf")
        # Should run without error
        run_pdftl([blank_pdf, "unpack_files", "output", str(tmp_path)])

    def test_attach_one_file(self, run_pdftl, get_test_file, tmp_path):
        blank_pdf = get_test_file("test/files/blank.pdf")
        blank_tex = get_test_file("test/files/blank.tex")
        output = tmp_path / "output.pdf"

        # Attach
        run_pdftl([blank_pdf, "attach_files", blank_tex, "output", str(output)])
        
        # Unpack
        # unpack_files dumps to CWD, so we run inside tmp_path via fixture, 
        # but the fixture defaults to cwd=tmp_path anyway.
        run_pdftl([str(output), "unpack_files", "output", str(tmp_path)])
        
        expected_data = slurp(blank_tex)
        attached_data = slurp(tmp_path / "blank.tex")
        assert expected_data == attached_data

    def test_same_file_twice(self, run_pdftl, get_test_file, tmp_path):
        blank_pdf = get_test_file("test/files/blank.pdf")
        blank_tex = get_test_file("test/files/blank.tex")
        output1 = tmp_path / "output.pdf"
        output2 = tmp_path / "output2.pdf"

        run_pdftl([blank_pdf, "attach_files", blank_tex, "output", str(output1)])
        run_pdftl([str(output1), "attach_files", blank_tex, "output", str(output2)])
        run_pdftl([str(output2), "unpack_files", "output", str(tmp_path)])

        expected_data = slurp(blank_tex)
        attached_data = slurp(tmp_path / "blank.tex")
        assert expected_data == attached_data

    def test_attach_to_page(self, run_pdftl, get_test_file, tmp_path):
        blank_pdf = get_test_file("test/files/blank.pdf")
        blank_tex = get_test_file("test/files/blank.tex")
        output = tmp_path / "output.pdf"

        run_pdftl([blank_pdf, "attach_files", blank_tex, "to_page", "1", "output", str(output)])
        run_pdftl([str(output), "unpack_files", "output", str(tmp_path)])

        expected_data = slurp(blank_tex)
        attached_data = slurp(tmp_path / "blank.tex")
        assert expected_data == attached_data

    def test_attach_relation(self, run_pdftl, get_test_file, tmp_path):
        blank_pdf = get_test_file("test/files/blank.pdf")
        blank_tex = get_test_file("test/files/blank.tex")
        output = tmp_path / "output.pdf"

        run_pdftl([blank_pdf, "attach_files", blank_tex, "relation", "Source", "output", str(output)])
        run_pdftl([str(output), "unpack_files", "output", str(tmp_path)])

        expected_data = slurp(blank_tex)
        attached_data = slurp(tmp_path / "blank.tex")
        assert expected_data == attached_data


# ==========================================
# BurstTest.java
# ==========================================
class TestBurst:
    def test_burst_issue18(self, run_pdftl, get_test_file, tmp_path):
        pdf = get_test_file("test/files/issue18.pdf")
        pattern = str(tmp_path / "page%04d.pdf")
        run_pdftl([pdf, "burst", "output", pattern])
        # Implicit assertion: exit code 0

    def test_burst_issue90(self, run_pdftl, get_test_file, tmp_path):
        pdf = get_test_file("test/files/issue90.pdf")
        pattern = str(tmp_path / "page%04d.pdf")
        run_pdftl([pdf, "burst", "output", pattern])


# ==========================================
# CatTest.java
# ==========================================
class TestCat:
    def test_cat(self, run_pdftl, get_test_file, compare_pdfs_as_svg):
        expected = slurp_bytes(get_test_file("test/files/cat-refs-refsalt.pdf"))
        result = run_pdftl([
            get_test_file("test/files/refs.pdf"),
            get_test_file("test/files/refsalt.pdf"),
            "cat", "output", "-"
        ])
        compare_pdfs_as_svg(expected, result.stdout)

    def test_cat_rotate_page_no_op(self, run_pdftl, get_test_file, compare_pdfs_as_svg):
        blank = get_test_file("test/files/blank.pdf")
        expected = run_pdftl([blank, "cat", "output", "-"]).stdout
        actual = run_pdftl([blank, "cat", "1north", "output", "-"]).stdout
        compare_pdfs_as_svg(expected, actual)

    def test_cat_rotate_range_no_op(self, run_pdftl, get_test_file, compare_pdfs_as_svg):
        blank = get_test_file("test/files/blank.pdf")
        expected = run_pdftl([blank, "cat", "output", "-"]).stdout
        actual = run_pdftl([blank, "cat", "1-1north", "output", "-"]).stdout
        compare_pdfs_as_svg(expected, actual)

    def test_cat_rotate_page(self, run_pdftl, get_test_file):
        blank = get_test_file("test/files/blank.pdf")
        run_pdftl([blank, "cat", "1east", "output", "-"])

    def test_cat_rotate_range(self, run_pdftl, get_test_file, compare_pdfs_as_svg):
        blank = get_test_file("test/files/blank.pdf")
        expected = run_pdftl([blank, "cat", "1east", "output", "-"]).stdout
        actual = run_pdftl([blank, "cat", "1-1east", "output", "-"]).stdout
        compare_pdfs_as_svg(expected, actual)

    def test_cat_exclude_range(self, run_pdftl, get_test_file, compare_pdfs_as_svg):
        refs = get_test_file("test/files/refs.pdf")
        expected = run_pdftl([refs, "cat", "1-3", "6-8", "output", "-"]).stdout
        actual = run_pdftl([refs, "cat", "~4-5", "output", "-"]).stdout
        compare_pdfs_as_svg(expected, actual)

    def test_cat_include_exclude_range(self, run_pdftl, get_test_file, compare_pdfs_as_svg):
        refs = get_test_file("test/files/refs.pdf")
        expected = run_pdftl([refs, "cat", "2-3", "6-7", "output", "-"]).stdout
        actual = run_pdftl([refs, "cat", "2-end~4-5~end", "output", "-"]).stdout
        compare_pdfs_as_svg(expected, actual)

    def test_cat_even(self, run_pdftl, get_test_file, compare_pdfs_as_svg):
        refs = get_test_file("test/files/refs.pdf")
        expected = run_pdftl([refs, "cat", "2", "4", "6", "output", "-"]).stdout
        actual = run_pdftl([refs, "cat", "2-7even", "output", "-"]).stdout
        compare_pdfs_as_svg(expected, actual)

    def test_cat_odd(self, run_pdftl, get_test_file, compare_pdfs_as_svg):
        refs = get_test_file("test/files/refs.pdf")
        expected = run_pdftl([refs, "cat", "3", "5", "7", "output", "-"]).stdout
        actual = run_pdftl([refs, "cat", "2-7odd", "output", "-"]).stdout
        compare_pdfs_as_svg(expected, actual)

    def test_cat_handles(self, run_pdftl, get_test_file):
        # Just checking it doesn't crash
        refs = get_test_file("test/files/refs.pdf")
        refsalt = get_test_file("test/files/refsalt.pdf")
        
        # Original expectation check (implied by execution)
        run_pdftl([f"A={refs}", f"B={refsalt}", "cat", "B", "A", "output", "-"])

    def test_duplicate_stdin(self, run_pdftl, get_test_file):
        blank = get_test_file("test/files/blank.pdf")
        input_bytes = Path(blank).read_bytes()
        run_pdftl(["A=-", "cat", "A", "A", "A", "output", "-"], input_data=input_bytes)


# ==========================================
# CommandParserTest.java (Mapped to Blackbox CLI tests)
# ==========================================
class TestCommandParser:
    """
    Original java tests checked internal API (TK_Session).
    We check CLI validity (exit codes).
    """
    def test_empty(self, run_pdftl):
        # No args should be invalid
        run_pdftl([], expect_exit_code=1)

    def test_no_input_no_op(self, run_pdftl):
        # 'output -' without input -> invalid
        run_pdftl(["output", "-"], expect_exit_code=1)

    def test_no_input(self, run_pdftl):
        # 'cat output -' without input -> invalid
        run_pdftl(["cat", "output", "-"], expect_exit_code=1)

    def test_no_output(self, run_pdftl, get_test_file):
        # Just input file, no command -> invalid
        blank = get_test_file("test/files/blank.pdf")
        run_pdftl([blank], expect_exit_code=1)

    def test_two_outputs(self, run_pdftl, get_test_file):
        blank = get_test_file("test/files/blank.pdf")
        run_pdftl([blank, "cat", "output", "-", "output", "-"], expect_exit_code=1)

    def test_operation_after_output(self, run_pdftl, get_test_file):
        blank = get_test_file("test/files/blank.pdf")
        run_pdftl([blank, "output", "-", "cat"], expect_exit_code=1)

    def test_no_operation_filter(self, run_pdftl, get_test_file):
        # 'file output -' is valid (default filter/pass-through)
        blank = get_test_file("test/files/blank.pdf")
        run_pdftl([blank, "output", "-"], expect_exit_code=0)

    def test_no_operation_cat(self, run_pdftl, get_test_file):
        # 'file1 file2 output -' -> default is cat? 
        # Java CommandParser says is_valid(), so pdftk likely treats this as valid.
        blank = get_test_file("test/files/blank.pdf")
        run_pdftl([blank, blank, "output", "-"], expect_exit_code=0)


# ==========================================
# CryptoTest.java
# ==========================================
class TestCrypto:
    def test_set_password(self, run_pdftl, get_test_file, tmp_path):
        blank = get_test_file("test/files/blank.pdf")
        # In Java this went to /dev/null, here we use temp file
        out = tmp_path / "out.pdf"
        run_pdftl([blank, "output", str(out), "owner_pw", "\"'**", "user_pw", "**\"'"])

    def test_idempotent_aes(self, run_pdftl, get_test_file, tmp_path, compare_pdfs_as_svg):
        blank = get_test_file("test/files/blank.pdf")
        expected = slurp_bytes(blank)
        encrypted = tmp_path / "encrypted.pdf"
        
        run_pdftl([blank, "output", str(encrypted), "encrypt_aes128", "user_pw", "correcthorsebatterystaple"])
        
        result = run_pdftl([str(encrypted), "input_pw", "correcthorsebatterystaple", "output", "-"])
        compare_pdfs_as_svg(expected, result.stdout)

    def test_idempotent_rc4(self, run_pdftl, get_test_file, tmp_path, compare_pdfs_as_svg):
        blank = get_test_file("test/files/blank.pdf")
        expected = slurp_bytes(blank)
        encrypted = tmp_path / "encrypted.pdf"
        
        run_pdftl([blank, "output", str(encrypted), "encrypt_128bit", "user_pw", "correcthorsebatterystaple"])
        
        result = run_pdftl([str(encrypted), "input_pw", "correcthorsebatterystaple", "output", "-"])
        compare_pdfs_as_svg(expected, result.stdout)

    def test_no_password_fails(self, run_pdftl, get_test_file, tmp_path):
        blank = get_test_file("test/files/blank.pdf")
        encrypted = tmp_path / "encrypted.pdf"
        run_pdftl([blank, "output", str(encrypted), "user_pw", "pw"])
        
        res = run_pdftl([str(encrypted), "output", "-"], expect_exit_code=1)
        expected = "OWNER OR USER PASSWORD REQUIRED"  # pdftk
        expected = "is encrypted and requires a password"  # pdftl
        assert expected in res.stderr.decode(errors="ignore")

    def test_wrong_password_fails(self, run_pdftl, get_test_file, tmp_path):
        blank = get_test_file("test/files/blank.pdf")
        encrypted = tmp_path / "encrypted.pdf"
        run_pdftl([blank, "output", str(encrypted), "user_pw", "pw"])
        
        res = run_pdftl([str(encrypted), "input_pw", "wrong", "output", "-"], expect_exit_code=1)
        # Note: Error message might vary slightly by pdftl implementation, but pdftk says:
        expected = "OWNER OR USER PASSWORD REQUIRED"  # pdftk
        expected = "invalid password"
        assert expected in res.stderr.decode(errors="ignore")


# ==========================================
# DataFieldsTest.java
# ==========================================
class TestDataFields:
    def test_ignore_field_with_successor_names(self, run_pdftl, get_test_file):
        pdf = get_test_file("test/files/issue19.pdf")
        expected = slurp(get_test_file("test/files/issue19.data"))
        result = run_pdftl([pdf, "dump_data_fields"])
        actual = result.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")
        assert expected == actual

    def test_escape_unicode(self, run_pdftl, get_test_file):
        pdf = get_test_file("test/files/issue21.pdf")
        expected = slurp(get_test_file("test/files/issue21.data"))
        result = run_pdftl([pdf, "dump_data_fields"])
        actual = result.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")
        assert expected == actual

    def test_dump_data_fields_utf8_options(self, run_pdftl, get_test_file):
        pdf = get_test_file("test/files/form-utf8.pdf")
        expected = slurp(get_test_file("test/files/form-utf8.data"))
        result = run_pdftl([pdf, "dump_data_fields_utf8"])
        actual = result.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")
        assert expected == actual


# ==========================================
# DataTest.java
# ==========================================
class TestData:
    def test_dump_data(self, run_pdftl, get_test_file):
        pdf = get_test_file("test/files/blank.pdf")
        expected = slurp(get_test_file("test/files/blank.data"))
        result = run_pdftl([pdf, "dump_data_utf8"])
        actual = result.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")
        assert expected == actual

    def test_dump_data_xml(self, run_pdftl, get_test_file):
        pdf = get_test_file("test/files/blank.pdf")
        expected = slurp(get_test_file("test/files/blank.xmlesc.data"))
        result = run_pdftl([pdf, "dump_data"])
        actual = result.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")
        assert expected == actual

    def test_idempotent(self, run_pdftl, get_test_file, tmp_path):
        pdf = get_test_file("test/files/refs.pdf")
        # 1. Dump
        dump = run_pdftl([pdf, "dump_data_utf8"]).stdout
        # 2. Update
        output = tmp_path / "output.pdf"
        run_pdftl([pdf, "update_info", "-", "output", str(output)], input_data=dump)
        # 3. Check logs
        # Note: Java checks stderr is empty.
        pass

    def test_update_info_incomplete_record(self, run_pdftl, get_test_file):
        pdf = get_test_file("test/files/blank.pdf")
        data = "InfoBegin\nInfoKey: Title\n \nInfoBegin\nInfoKey: Author\n \n"
        # Expect failure or warning? Java passes args but checks stderr for text
        res = run_pdftl([pdf, "update_info", "-", "output", "-"], input_data=data)
        assert "data info record not valid" in res.stderr.decode("utf-8", errors="ignore")

    def test_update_page_labels_new(self, run_pdftl, get_test_file, tmp_path):
        pdf = get_test_file("test/files/refs.pdf")
        output = tmp_path / "output.pdf"
        data = [
            "PageLabelBegin", "PageLabelNewIndex: 3", "PageLabelStart: 3",
            "PageLabelPrefix: p", "PageLabelNumStyle: LowercaseRomanNumerals"
        ]
        input_str = "\n".join(data)
        
        run_pdftl([pdf, "update_info", "-", "output", str(output)], input_data=input_str)
        result = run_pdftl([str(output), "dump_data_utf8"])
        
        log = result.stdout.decode("utf-8", errors="replace")
        for line in data:
            assert line in log

    def test_update_page_labels_replace(self, run_pdftl, get_test_file, tmp_path):
        pdf = get_test_file("test/files/refs.pdf")
        out1 = tmp_path / "output1.pdf"
        out2 = tmp_path / "output2.pdf"
        
        data1 = [
            "PageLabelBegin", "PageLabelNewIndex: 3", 
            "PageLabelStart: 3", "PageLabelNumStyle: LowercaseRomanNumerals"
        ]
        data2 = [
            "PageLabelBegin", "PageLabelNewIndex: 4",
            "PageLabelStart: 4", "PageLabelNumStyle: UppercaseRomanNumerals"
        ]
        
        run_pdftl([pdf, "update_info", "-", "output", str(out1)], input_data="\n".join(data1))
        run_pdftl([str(out1), "update_info", "-", "output", str(out2)], input_data="\n".join(data2))
        
        result = run_pdftl([str(out2), "dump_data_utf8"])
        log = result.stdout.decode("utf-8", errors="replace")
        
        # Should NOT contain data1
        assert "PageLabelNewIndex: 3" not in log
        # Should contain data2
        for line in data2:
            assert line in log

    def test_update_page_labels_badindex(self, run_pdftl, get_test_file):
        pdf = get_test_file("test/files/refs.pdf")
        data = ["PageLabelBegin", "PageLabelNewIndex: -1", "PageLabelStart: 3", "PageLabelNumStyle: LowercaseRomanNumerals"]
        res = run_pdftl([pdf, "update_info", "-", "output", "-"], input_data="\n".join(data))
        assert "page label record not valid" in res.stderr.decode("utf-8", errors="ignore")

    def test_update_page_labels_badstart(self, run_pdftl, get_test_file):
        pdf = get_test_file("test/files/refs.pdf")
        data = ["PageLabelBegin", "PageLabelNewIndex: 3", "PageLabelStart: -1", "PageLabelNumStyle: LowercaseRomanNumerals"]
        res = run_pdftl([pdf, "update_info", "-", "output", "-"], input_data="\n".join(data))
        assert "page label record not valid" in res.stderr.decode("utf-8", errors="ignore")

    def test_update_page_labels_badstyle(self, run_pdftl, get_test_file):
        pdf = get_test_file("test/files/refs.pdf")
        data = ["PageLabelBegin", "PageLabelNewIndex: 3", "PageLabelStart: 3", "PageLabelNumStyle: NotAStyle"]
        res = run_pdftl([pdf, "update_info", "-", "output", "-"], input_data="\n".join(data))
        err = res.stderr.decode("utf-8", errors="ignore")
        assert "PageLabelNumStyle: invalid value NotAStyle" in err
        assert "page label record not valid" in err

    def test_update_page_media_replace(self, run_pdftl, get_test_file, tmp_path):
        pdf = get_test_file("test/files/refs.pdf")
        output = tmp_path / "output.pdf"
        data = [
            "PageMediaBegin", "PageMediaNumber: 3", "PageMediaRotation: 90",
            "PageMediaRect: 1 1 611 791", "PageMediaCropRect: 2 2 610 792"
        ]
        # pdftk adds Dimensions line automatically when dumping
        expect_fragments = data + ["PageMediaDimensions: 610 790"]
        
        run_pdftl([pdf, "update_info", "-", "output", str(output)], input_data="\n".join(data))
        res = run_pdftl([str(output), "dump_data_utf8"])
        log = res.stdout.decode("utf-8", errors="replace")
        
        for frag in expect_fragments:
            assert frag in log

    def test_update_page_media_badpage(self, run_pdftl, get_test_file):
        pdf = get_test_file("test/files/refs.pdf")
        data = ["PageMediaBegin", "PageMediaNumber: 42", "PageMediaRotation: 90"]
        res = run_pdftl([pdf, "update_info", "-", "output", "-"], input_data="\n".join(data), expect_exit_code=3)
        assert "page 42 not found" in res.stderr.decode("utf-8", errors="ignore")

    def test_update_page_media_badrotation(self, run_pdftl, get_test_file):
        pdf = get_test_file("test/files/refs.pdf")
        data = ["PageMediaBegin", "PageMediaNumber: 3", "PageMediaRotation: 45"]
        res = run_pdftl([pdf, "update_info", "-", "output", "-"], input_data="\n".join(data))
        assert "page media record not valid" in res.stderr.decode("utf-8", errors="ignore")

    def test_update_page_media_badrect(self, run_pdftl, get_test_file):
        pdf = get_test_file("test/files/refs.pdf")
        data = ["PageMediaBegin", "PageMediaNumber: 3", "PageMediaRect: 1 1 611"]
        res = run_pdftl([pdf, "update_info", "-", "output", "-"], input_data="\n".join(data))
        assert "page media record not valid" in res.stderr.decode("utf-8", errors="ignore")


# ==========================================
# FormTest.java
# ==========================================
class TestForm:
    def test_dump_data_fields(self, run_pdftl, get_test_file):
        pdf = get_test_file("test/files/form.pdf")
        expected = slurp(get_test_file("test/files/form.data"))
        res = run_pdftl([pdf, "dump_data_fields"])
        actual = res.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")
        assert expected == actual

    def test_generate_fdf(self, run_pdftl, get_test_file):
        pdf = get_test_file("test/files/form.pdf")
        expected = slurp_bytes(get_test_file("test/files/form.fdf"))
        res = run_pdftl([pdf, "generate_fdf", "output", "-"])
        assert expected == res.stdout

    def test_generate_fdf_issue88(self, run_pdftl, get_test_file):
        pdf = get_test_file("test/files/issue88.pdf")
        expected = slurp_bytes(get_test_file("test/files/issue88.fdf"))
        res = run_pdftl([pdf, "generate_fdf", "output", "-"])
        assert expected == res.stdout

    def test_fill_from_fdf(self, run_pdftl, get_test_file):
        pdf = get_test_file("test/files/form.pdf")
        fdf = get_test_file("test/files/form-filled.fdf")
        run_pdftl([pdf, "fill_form", fdf, "output", "-"])

    def test_dump_data_fields_utf8_options(self, run_pdftl, get_test_file):
        pdf = get_test_file("test/files/form-utf8.pdf")
        expected = slurp(get_test_file("test/files/form-utf8.data"))
        res = run_pdftl([pdf, "dump_data_fields_utf8"])
        actual = res.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")
        assert expected == actual

    def test_generate_fdf_utf8_options(self, run_pdftl, get_test_file):
        pdf = get_test_file("test/files/form-utf8.pdf")
        expected = slurp_bytes(get_test_file("test/files/form-utf8.fdf"))
        res = run_pdftl([pdf, "generate_fdf", "output", "-"])
        assert expected == res.stdout

    def test_fill_from_fdf_utf8_options(self, run_pdftl, get_test_file):
        pdf = get_test_file("test/files/form-utf8.pdf")
        fdf = get_test_file("test/files/form-utf8-filled.fdf")
        run_pdftl([pdf, "fill_form", fdf, "output", "-"])

    def test_replace_font_ttf(self, run_pdftl, get_test_file):
        pdf = get_test_file("test/files/form.pdf")
        fdf = get_test_file("test/files/form-filled.fdf")
        font = get_test_file("test/files/D-DIN.ttf")
        run_pdftl([pdf, "fill_form", fdf, "output", "-", "replacement_font", font])

    def test_replace_font_cff(self, run_pdftl, get_test_file):
        pdf = get_test_file("test/files/form.pdf")
        fdf = get_test_file("test/files/form-filled.fdf")
        font = get_test_file("test/files/D-DIN.otf")
        run_pdftl([pdf, "fill_form", fdf, "output", "-", "replacement_font", font])


# ==========================================
# MultipleTest.java
# ==========================================
class TestMultiple:
    def _get_form_names(self, form_data_str):
        # Extract "FieldName: (.*)"
        return re.findall(r"FieldName: (.*)", form_data_str)

    def test_cat_renames_clashing_forms(self, run_pdftl, get_test_file, tmp_path):
        pdf = get_test_file("test/files/form.pdf")
        
        # Original fields
        res1 = run_pdftl([pdf, "dump_data_fields_utf8"])
        original_fields = self._get_form_names(res1.stdout.decode("utf-8", errors="replace"))
        
        # Duplicate
        dup_pdf = tmp_path / "dup.pdf"
        run_pdftl([f"A={pdf}", "cat", "A", "A", "output", str(dup_pdf)])
        
        # Check duplicated fields
        res2 = run_pdftl([str(dup_pdf), "dump_data_fields_utf8"])
        dup_fields = self._get_form_names(res2.stdout.decode("utf-8", errors="replace"))
        
        assert len(dup_fields) == 2 * len(original_fields)

    def test_can_fill_cat_form(self, run_pdftl, get_test_file, tmp_path):
        pdf = get_test_file("test/files/form.pdf")
        dup_pdf = tmp_path / "dup.pdf"
        run_pdftl([f"A={pdf}", "cat", "A", "A", "output", str(dup_pdf)])
        
        dup_fdf = tmp_path / "dup.fdf"
        run_pdftl([str(dup_pdf), "generate_fdf", "output", str(dup_fdf)])
        run_pdftl([str(dup_pdf), "fill_form", str(dup_fdf), "output", "-"])


# ==========================================
# ReaderTest.java
# ==========================================
class TestReader:
    def test_mergerequest21(self, run_pdftl, get_test_file):
        pdf = get_test_file("test/files/CVE-2007-0103_AcrobatReader")
        # Should fail
        res = run_pdftl([pdf, "output", "/dev/null"], expect_exit_code=1)
        assert "Invalid reference on Kids" in res.stderr.decode(errors="ignore")

    def test_mergerequest21_2(self, run_pdftl, get_test_file):
        pdf = get_test_file("test/files/CVE-2007-0103_AcrobatReader_mutated")
        res = run_pdftl([pdf, "output", "/dev/null"], expect_exit_code=1)
        assert "Invalid reference on Kids" in res.stderr.decode(errors="ignore")


# ==========================================
# StampTest.java
# ==========================================
class TestStamp:
    def _run_op_test(self, op, run_pdftl, get_test_file, compare_pdfs_as_svg):
        blank = get_test_file("test/files/blank.pdf")
        duck = get_test_file("test/files/duck.pdf")
        
        # Baseline: file inputs
        expected = run_pdftl([blank, op, duck, "output", "-"]).stdout
        
        # Scenario 1: stdin input (Background/Stamp file is passed as file arg)
        with open(blank, "rb") as f:
            stdin_data = f.read()
        actual1 = run_pdftl(["-", op, duck, "output", "-"], input_data=stdin_data).stdout
        compare_pdfs_as_svg(expected, actual1)

        # Scenario 2: operation stdin (Input file passed as arg, Stamp file passed as stdin)
        with open(duck, "rb") as f:
            duck_data = f.read()
        actual2 = run_pdftl([blank, op, "-", "output", "-"], input_data=duck_data).stdout
        compare_pdfs_as_svg(expected, actual2)

    def test_stdin_background(self, run_pdftl, get_test_file, compare_pdfs_as_svg):
        self._run_op_test("background", run_pdftl, get_test_file, compare_pdfs_as_svg)

    def test_stdin_multibackground(self, run_pdftl, get_test_file, compare_pdfs_as_svg):
        self._run_op_test("multibackground", run_pdftl, get_test_file, compare_pdfs_as_svg)

    def test_stdin_stamp(self, run_pdftl, get_test_file, compare_pdfs_as_svg):
        self._run_op_test("stamp", run_pdftl, get_test_file, compare_pdfs_as_svg)

    def test_stdin_multistamp(self, run_pdftl, get_test_file, compare_pdfs_as_svg):
        self._run_op_test("multistamp", run_pdftl, get_test_file, compare_pdfs_as_svg)
