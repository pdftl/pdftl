# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Tests for pdftl.utils.attachment_utils"""

from unittest.mock import MagicMock

import pikepdf

from pdftl.utils.attachment_utils import (
    attachment_compression,
    attachment_file_size,
    attachment_metadata,
    attachment_stored_size,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stream(**kwargs):
    """Build a minimal pikepdf stream mock with the given dict items."""
    stream = MagicMock()
    stream.__contains__ = lambda self, key: key in kwargs

    def _get(key, default=None):
        return kwargs.get(key, default)

    stream.get.side_effect = _get
    return stream


def _make_attachment(
    length=None,
    params=None,
    filter_val=None,
    desc=None,
    relationship=None,
    ef_none=False,
    f_none=False,
):
    """Build a mock AttachedFileSpec with configurable stream properties."""
    attachment = MagicMock()
    attachment.description = desc
    if ef_none:
        attachment.obj.get.return_value = None
        attachment.relationship = relationship
        return attachment

    # Build /Params sub-dict
    params_dict = MagicMock()
    params_dict_data = params or {}

    def params_get(key, default=None):
        return params_dict_data.get(key, default)

    params_dict.get.side_effect = params_get

    # Build /F stream
    f_stream = MagicMock()
    f_stream_data = {}
    if length is not None:
        f_stream_data["/Length"] = length
    if filter_val is not None:
        f_stream_data["/Filter"] = filter_val
    if params is not None:
        f_stream_data["/Params"] = params_dict

    def f_get(key, default=None):
        return f_stream_data.get(key, default)

    f_stream.get.side_effect = f_get

    if f_none:
        # /EF exists but /F is None
        ef_dict = MagicMock()
        ef_dict.get.return_value = None
        attachment.obj.get.return_value = ef_dict
        attachment.relationship = relationship
        return attachment

    # Build /EF dict
    ef_dict = MagicMock()

    def ef_get(key, default=None):
        if key == "/F":
            return f_stream
        return default

    ef_dict.get.side_effect = ef_get

    def obj_get(key, default=None):
        if key == "/EF":
            return ef_dict
        if key == "/Desc":
            return desc
        return default

    attachment.obj.get.side_effect = obj_get
    attachment.relationship = relationship
    return attachment


# ---------------------------------------------------------------------------
# attachment_stored_size
# ---------------------------------------------------------------------------


class TestAttachmentStoredSize:
    def test_returns_length(self):
        att = _make_attachment(length=1234)
        assert attachment_stored_size(att) == 1234

    def test_returns_none_when_ef_none(self):
        att = _make_attachment(ef_none=True)
        assert attachment_stored_size(att) is None

    def test_returns_none_when_f_none(self):
        att = _make_attachment(f_none=True)
        assert attachment_stored_size(att) is None

    def test_returns_none_when_length_missing(self):
        att = _make_attachment()  # no length
        assert attachment_stored_size(att) is None

    def test_returns_none_on_type_error(self):
        att = _make_attachment(length="not-an-int-object")
        # int("not-an-int-object") raises ValueError
        assert attachment_stored_size(att) is None

    def test_real_pdf(self, tmp_path):
        pdf = pikepdf.new()
        pdf.add_blank_page()
        pdf.attachments["hello.txt"] = b"Hello World"
        path = tmp_path / "a.pdf"
        pdf.save(path)
        with pikepdf.open(path) as p:
            att = p.attachments["hello.txt"]
            size = attachment_stored_size(att)
            assert isinstance(size, int)
            assert size > 0


# ---------------------------------------------------------------------------
# attachment_file_size
# ---------------------------------------------------------------------------


class TestAttachmentFileSize:
    def test_returns_size(self):
        att = MagicMock()
        att.get_file.return_value.size = 42
        assert attachment_file_size(att) == 42

    def test_returns_none_on_pdf_error(self):
        att = MagicMock()
        att.get_file.side_effect = pikepdf.PdfError("bad")
        assert attachment_file_size(att) is None

    def test_returns_none_on_attribute_error(self):
        att = MagicMock()
        att.get_file.return_value = None  # .size on None raises AttributeError
        assert attachment_file_size(att) is None

    def test_real_pdf(self, tmp_path):
        content = b"Hello World"
        pdf = pikepdf.new()
        pdf.add_blank_page()
        pdf.attachments["hello.txt"] = content
        path = tmp_path / "a.pdf"
        pdf.save(path)
        with pikepdf.open(path) as p:
            att = p.attachments["hello.txt"]
            assert attachment_file_size(att) == len(content)


# ---------------------------------------------------------------------------
# attachment_compression
# ---------------------------------------------------------------------------


class TestAttachmentCompression:
    def test_returns_filter_string(self):
        att = _make_attachment(filter_val=pikepdf.Name("/FlateDecode"))
        result = attachment_compression(att)
        assert result == "/FlateDecode"

    def test_returns_none_when_no_filter(self):
        att = _make_attachment()  # no /Filter
        assert attachment_compression(att) is None

    def test_returns_none_when_ef_none(self):
        att = _make_attachment(ef_none=True)
        assert attachment_compression(att) is None

    def test_returns_none_when_f_none(self):
        att = _make_attachment(f_none=True)
        assert attachment_compression(att) is None

    def test_real_compressed(self, tmp_path):
        pdf = pikepdf.new()
        pdf.add_blank_page()
        pdf.attachments["f.txt"] = b"x" * 1000
        path = tmp_path / "a.pdf"
        pdf.save(path)
        with pikepdf.open(path) as p:
            att = p.attachments["f.txt"]
            comp = attachment_compression(att)
            # may be None (small content) or a filter string — just check type
            assert comp is None or isinstance(comp, str)

    def test_real_uncompressed(self, tmp_path):
        # potato.png from earlier tests has no FlateDecode — use a known PNG bytes
        pdf = pikepdf.new()
        pdf.add_blank_page()
        # attach without compression by saving an uncompressed stream
        pdf.attachments["img.png"] = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        path = tmp_path / "a.pdf"
        pdf.save(path)
        with pikepdf.open(path) as p:
            att = p.attachments["img.png"]
            comp = attachment_compression(att)
            assert comp is None or isinstance(comp, str)


# ---------------------------------------------------------------------------
# attachment_metadata
# ---------------------------------------------------------------------------


class TestAttachmentMetadata:
    def test_empty_when_ef_none(self):
        att = _make_attachment(ef_none=True)
        assert attachment_metadata(att) == {}

    def test_empty_when_f_none(self):
        att = _make_attachment(f_none=True)
        assert attachment_metadata(att) == {}

    def test_stored_size_from_length(self):
        att = _make_attachment(length=500)
        meta = attachment_metadata(att)
        assert meta["stored_size"] == 500

    def test_compression_included(self):
        att = _make_attachment(filter_val=pikepdf.Name("/FlateDecode"))
        meta = attachment_metadata(att)
        assert meta["compression"] == "/FlateDecode"

    def test_no_compression_key_when_absent(self):
        att = _make_attachment()
        meta = attachment_metadata(att)
        assert "compression" not in meta

    def test_file_size_from_params(self):
        att = _make_attachment(params={"/Size": 1024})
        meta = attachment_metadata(att)
        assert meta["file_size"] == 1024

    def test_created_from_params(self):
        att = _make_attachment(params={"/CreationDate": "D:20210101"})
        meta = attachment_metadata(att)
        assert meta["created"] == "D:20210101"

    def test_modified_from_params(self):
        att = _make_attachment(params={"/ModDate": "D:20220202"})
        meta = attachment_metadata(att)
        assert meta["modified"] == "D:20220202"

    def test_missing_params_keys_absent(self):
        att = _make_attachment(params={})
        meta = attachment_metadata(att)
        assert "file_size" not in meta
        assert "created" not in meta
        assert "modified" not in meta

    def test_no_params_keys_absent(self):
        att = _make_attachment()
        meta = attachment_metadata(att)
        assert "file_size" not in meta

    def test_description_included(self):
        att = _make_attachment(desc=pikepdf.String("My description"))
        meta = attachment_metadata(att)
        assert meta["description"] == "My description"

    def test_empty_description_excluded(self):
        att = _make_attachment(desc=pikepdf.String(""))
        meta = attachment_metadata(att)
        assert "description" not in meta

    def test_none_description_excluded(self):
        att = _make_attachment(desc=None)
        meta = attachment_metadata(att)
        assert "description" not in meta

    def test_relationship_included(self):
        att = _make_attachment(relationship=pikepdf.Name("/Source"))
        meta = attachment_metadata(att)
        assert meta["relationship"] == "Source"

    def test_none_relationship_excluded(self):
        att = _make_attachment(relationship=None)
        meta = attachment_metadata(att)
        assert "relationship" not in meta

    def test_mime_type_included(self):
        att = _make_attachment(
            params={},
            filter_val=None,
        )
        # inject /Subtype manually via a more complete mock
        f_stream_data = {"/Subtype": pikepdf.Name("/text/plain")}
        ef_dict = MagicMock()
        f_stream = MagicMock()
        f_stream.get.side_effect = lambda k, d=None: f_stream_data.get(k, d)
        ef_dict.get.return_value = f_stream
        att.obj.get.side_effect = lambda k, d=None: ef_dict if k == "/EF" else d
        att.relationship = None
        meta = attachment_metadata(att)
        assert meta.get("mime_type") == "/text/plain"

    def test_real_pdf_full_metadata(self, tmp_path):
        pdf = pikepdf.new()
        pdf.add_blank_page()
        pdf.attachments["hello.txt"] = b"Hello World"
        path = tmp_path / "a.pdf"
        pdf.save(path)
        with pikepdf.open(path) as p:
            att = p.attachments["hello.txt"]
            meta = attachment_metadata(att)
            assert isinstance(meta, dict)
            # stored_size must always be present for a valid attachment
            assert "stored_size" in meta
            assert isinstance(meta["stored_size"], int)
