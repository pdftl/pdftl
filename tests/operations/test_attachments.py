# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Tests for pdftl.operations.attachments (dump_files, unpack_files)"""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.core.core_types import OpResult
from pdftl.operations.attachments import (
    dump_files,
    dump_files_cli_hook,
    unpack_files,
    unpack_files_cli_hook,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pdf_with_attachment(tmp_path):
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.attachments["hello.txt"] = b"Hello World"
    path = tmp_path / "attached.pdf"
    pdf.save(path)
    return str(path)


@pytest.fixture
def pdf_with_two_attachments(tmp_path):
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.attachments["a.txt"] = b"aaa"
    pdf.attachments["b.txt"] = b"bbbbbb"
    path = tmp_path / "two.pdf"
    pdf.save(path)
    return str(path)


@pytest.fixture
def pdf_no_attachment(tmp_path):
    pdf = pikepdf.new()
    pdf.add_blank_page()
    path = tmp_path / "clean.pdf"
    pdf.save(path)
    return str(path)


# ---------------------------------------------------------------------------
# dump_files — core behaviour
# ---------------------------------------------------------------------------


class TestDumpFiles:
    def test_returns_success(self, pdf_with_attachment):
        with pikepdf.open(pdf_with_attachment) as pdf:
            result = dump_files("f.pdf", pdf)
        assert result.success is True

    def test_returns_list(self, pdf_with_attachment):
        with pikepdf.open(pdf_with_attachment) as pdf:
            result = dump_files("f.pdf", pdf)
        assert isinstance(result.data, list)
        assert len(result.data) == 1

    def test_record_has_key_and_filename(self, pdf_with_attachment):
        with pikepdf.open(pdf_with_attachment) as pdf:
            result = dump_files("f.pdf", pdf)
        rec = result.data[0]
        assert "key" in rec
        assert "filename" in rec

    def test_filename_is_hello_txt(self, pdf_with_attachment):
        with pikepdf.open(pdf_with_attachment) as pdf:
            result = dump_files("f.pdf", pdf)
        assert result.data[0]["filename"] == "hello.txt"

    def test_stored_size_is_int(self, pdf_with_attachment):
        with pikepdf.open(pdf_with_attachment) as pdf:
            result = dump_files("f.pdf", pdf)
        assert isinstance(result.data[0]["stored_size"], int)

    def test_no_pages_key_for_document_level(self, pdf_with_attachment):
        with pikepdf.open(pdf_with_attachment) as pdf:
            result = dump_files("f.pdf", pdf)
        assert "pages" not in result.data[0]

    def test_empty_pdf_returns_empty_list(self, pdf_no_attachment):
        with pikepdf.open(pdf_no_attachment) as pdf:
            result = dump_files("f.pdf", pdf)
        assert result.success is True
        assert result.data == []

    def test_output_file_in_meta(self, pdf_with_attachment):
        with pikepdf.open(pdf_with_attachment) as pdf:
            result = dump_files("f.pdf", pdf, output_file="/tmp/out.json")
        from pdftl.core.constants import META_OUTPUT_FILE

        assert result.meta[META_OUTPUT_FILE] == "/tmp/out.json"

    def test_output_file_none_by_default(self, pdf_with_attachment):
        with pikepdf.open(pdf_with_attachment) as pdf:
            result = dump_files("f.pdf", pdf)
        from pdftl.core.constants import META_OUTPUT_FILE

        assert result.meta[META_OUTPUT_FILE] is None

    def test_empty_meta_has_output_file_key(self, pdf_no_attachment):
        with pikepdf.open(pdf_no_attachment) as pdf:
            result = dump_files("f.pdf", pdf)
        from pdftl.core.constants import META_OUTPUT_FILE

        assert META_OUTPUT_FILE in result.meta

    def test_two_attachments(self, pdf_with_two_attachments):
        with pikepdf.open(pdf_with_two_attachments) as pdf:
            result = dump_files("f.pdf", pdf)
        assert len(result.data) == 2

    def test_input_filename_in_meta(self, pdf_with_attachment):
        with pikepdf.open(pdf_with_attachment) as pdf:
            result = dump_files("myfile.pdf", pdf)
        assert result.meta["input_filename"] == "myfile.pdf"

    def test_pages_field_for_page_level_attachment(self):
        """Attachment mapped to a page via annotation should have pages field."""
        mock_pdf = MagicMock()
        mock_fs = MagicMock()
        mock_fs.objgen = (5, 0)

        mock_annot = MagicMock()
        mock_annot.get.side_effect = lambda k: {
            "/Subtype": "/FileAttachment",
            "/FS": mock_fs,
        }.get(k)

        mock_page = MagicMock()
        mock_page.__contains__ = lambda self, k: True
        mock_page.Annots = [mock_annot]
        mock_pdf.pages = [mock_page]

        mock_att = MagicMock()
        mock_att.obj.objgen = (5, 0)
        mock_att.filename = "pinned.pdf"
        mock_att.obj.get.return_value = None
        mock_att.relationship = None
        mock_pdf.attachments = {"pinned.pdf": mock_att}

        result = dump_files("input.pdf", mock_pdf)
        assert result.success is True
        assert result.data[0]["pages"] == [1]

    def test_no_pages_key_when_not_pinned(self):
        mock_pdf = MagicMock()
        mock_pdf.pages = []
        mock_att = MagicMock()
        mock_att.obj.objgen = (1, 0)
        mock_att.filename = "doc.txt"
        mock_att.obj.get.return_value = None
        mock_att.relationship = None
        mock_pdf.attachments = {"doc.txt": mock_att}

        result = dump_files("input.pdf", mock_pdf)
        assert "pages" not in result.data[0]

    def test_metadata_merged_into_record(self, pdf_with_attachment):
        with pikepdf.open(pdf_with_attachment) as pdf:
            result = dump_files("f.pdf", pdf)
        rec = result.data[0]
        # stored_size comes from attachment_metadata
        assert "stored_size" in rec


# ---------------------------------------------------------------------------
# dump_files_cli_hook
# ---------------------------------------------------------------------------


class TestDumpFilesCliHook:
    def test_prints_json(self, pdf_with_attachment, capsys):
        with pikepdf.open(pdf_with_attachment) as pdf:
            result = dump_files("f.pdf", pdf)
        dump_files_cli_hook(result, None, None)
        out = capsys.readouterr().out
        parsed = json.loads(out)
        atts = parsed["attachments"]
        assert isinstance(atts, list)
        assert atts[0]["filename"] == "hello.txt"

    def test_empty_prints_empty_array(self, pdf_no_attachment, capsys):
        with pikepdf.open(pdf_no_attachment) as pdf:
            result = dump_files("f.pdf", pdf)
        dump_files_cli_hook(result, None, None)
        out = capsys.readouterr().out
        assert json.loads(out) == {"attachments": []}

    def test_failure_returns_silently(self, capsys):
        result = OpResult(success=False)
        dump_files_cli_hook(result, None, None)
        assert capsys.readouterr().out == ""

    def test_missing_meta_raises(self):
        result = OpResult(success=True, meta=None)
        with pytest.raises(AttributeError, match="Missing metadata"):
            dump_files_cli_hook(result, None, None)

    def test_output_to_file(self, pdf_with_attachment, tmp_path):
        out_path = str(tmp_path / "out.json")
        with pikepdf.open(pdf_with_attachment) as pdf:
            result = dump_files("f.pdf", pdf, output_file=out_path)
        dump_files_cli_hook(result, None, None)
        data = json.loads(Path(out_path).read_text())
        assert data["attachments"][0]["filename"] == "hello.txt"

    def test_pages_array_compacted(self, capsys):
        """pages array should appear on one line (compact_json_string)."""
        mock_pdf = MagicMock()
        mock_att = MagicMock()
        mock_att.obj.objgen = (1, 0)
        mock_att.filename = "f.txt"
        mock_att.obj.get.return_value = None
        mock_att.relationship = None
        mock_pdf.attachments = {"f.txt": mock_att}

        # Give it pages
        mock_fs = MagicMock()
        mock_fs.objgen = (1, 0)
        mock_annot = MagicMock()
        mock_annot.get.side_effect = lambda k: {"/Subtype": "/FileAttachment", "/FS": mock_fs}.get(
            k
        )
        mock_page = MagicMock()
        mock_page.__contains__ = lambda self, k: True
        mock_page.Annots = [mock_annot]
        mock_pdf.pages = [mock_page]

        result = dump_files("x.pdf", mock_pdf)
        dump_files_cli_hook(result, None, None)
        out = capsys.readouterr().out
        # pages should be on one line, not split across lines
        assert '"pages": [1]' in out or '"pages": [ 1 ]' in out


# ---------------------------------------------------------------------------
# unpack_files — core behaviour
# ---------------------------------------------------------------------------


class TestUnpackFiles:
    def test_returns_success(self, pdf_with_attachment):
        with pikepdf.open(pdf_with_attachment) as pdf:
            result = unpack_files(pdf, None)
        assert result.success is True

    def test_extracts_file(self, pdf_with_attachment, tmp_path):
        with pikepdf.open(pdf_with_attachment) as pdf:
            result = unpack_files(pdf, None, output_dir=str(tmp_path))
            unpack_files_cli_hook(result, None, None)
        assert (tmp_path / "hello.txt").exists()
        assert (tmp_path / "hello.txt").read_bytes() == b"Hello World"

    def test_extracts_to_cwd_by_default(self, pdf_with_attachment, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pikepdf.open(pdf_with_attachment) as pdf:
            result = unpack_files(pdf, None)
            unpack_files_cli_hook(result, None, None)
        assert (tmp_path / "hello.txt").exists()

    def test_strips_path_traversal(self, tmp_path):
        """Filenames with directory components must be sanitised."""
        mock_pdf = MagicMock()
        mock_att = MagicMock()
        mock_att.filename = "/home/user/evil.txt"
        mock_att.get_file.return_value.read_bytes.return_value = b"data"
        mock_pdf.attachments = {"evil.txt": mock_att}

        result = unpack_files(mock_pdf, None, output_dir=str(tmp_path))
        unpack_files_cli_hook(result, None, None)

        assert (tmp_path / "evil.txt").exists()
        assert not (tmp_path / "home" / "user" / "evil.txt").exists()

    def test_prompt_for_dir(self, pdf_with_attachment, tmp_path):
        with pikepdf.open(pdf_with_attachment) as pdf:
            result = unpack_files(pdf, lambda msg, **kw: str(tmp_path), output_dir="PROMPT")
            unpack_files_cli_hook(result, None, None)
        assert (tmp_path / "hello.txt").exists()

    def test_skips_corrupted_attachment(self, caplog):
        mock_pdf = MagicMock()
        mock_att = MagicMock()
        mock_att.filename = "bad.txt"
        mock_att.get_file.return_value.read_bytes.side_effect = AttributeError("corrupt")
        mock_pdf.attachments = {"bad.txt": mock_att}

        with caplog.at_level(logging.WARNING):
            result = unpack_files(mock_pdf, None)
            extracted = list(result.data)

        assert len(extracted) == 0
        assert "Skipping" in caplog.text

    def test_skips_pikepdf_error(self, caplog):
        mock_pdf = MagicMock()
        mock_att = MagicMock()
        mock_att.filename = "bad.txt"
        mock_att.get_file.side_effect = pikepdf.PdfError("bad stream")
        mock_pdf.attachments = {"bad.txt": mock_att}

        with caplog.at_level(logging.WARNING):
            result = unpack_files(mock_pdf, None)
            extracted = list(result.data)

        assert len(extracted) == 0

    def test_no_attachments_yields_nothing(self, pdf_no_attachment):
        with pikepdf.open(pdf_no_attachment) as pdf:
            result = unpack_files(pdf, None)
            extracted = list(result.data)
        assert extracted == []

    def test_two_attachments_extracted(self, pdf_with_two_attachments, tmp_path):
        with pikepdf.open(pdf_with_two_attachments) as pdf:
            result = unpack_files(pdf, None, output_dir=str(tmp_path))
            unpack_files_cli_hook(result, None, None)
        assert (tmp_path / "a.txt").exists()
        assert (tmp_path / "b.txt").exists()


# ---------------------------------------------------------------------------
# unpack_files_cli_hook
# ---------------------------------------------------------------------------


class TestUnpackFilesCliHook:
    def test_failure_returns_silently(self, capsys):
        result = OpResult(success=False)
        unpack_files_cli_hook(result, None, None)

    def test_missing_meta_raises(self):
        result = OpResult(success=True, meta=None)
        with pytest.raises(AttributeError, match="Missing metadata"):
            unpack_files_cli_hook(result, None, None)

    def test_invalid_output_dir_logs_error(self, tmp_path, caplog):
        file_path = tmp_path / "im_a_file"
        file_path.touch()

        mock_pdf = MagicMock()
        mock_pdf.attachments = {}

        with caplog.at_level(logging.ERROR):
            result = unpack_files(mock_pdf, None, output_dir=str(file_path))
            unpack_files_cli_hook(result, None, None)

        assert "does not seem to be a directory" in caplog.text

    def test_write_error_logs_warning(self, pdf_with_attachment, tmp_path, caplog):
        caplog.set_level(logging.WARNING)
        with pikepdf.open(pdf_with_attachment) as pdf:
            with patch("builtins.open", side_effect=OSError("Disk full")):
                result = unpack_files(pdf, None, output_dir=str(tmp_path))
                unpack_files_cli_hook(result, None, None)
        assert "Could not write file" in caplog.text

    def test_no_attachments_logs_debug(self, pdf_no_attachment, caplog):
        with caplog.at_level(logging.DEBUG, logger="pdftl"):
            with pikepdf.open(pdf_no_attachment) as pdf:
                result = unpack_files(pdf, None)
                unpack_files_cli_hook(result, None, None)
        assert "No attachments found" in caplog.text
