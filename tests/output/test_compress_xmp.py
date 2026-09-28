# tests/output/test_compress_xmp.py

import io
import logging
import os
import shutil
import subprocess
import zlib
from unittest.mock import MagicMock, patch

import pikepdf
import pymupdf
import pytest
from pikepdf import Name

from pdftl.output import compress_xmp as cx
from pdftl.output import xref_stream as xs
from pdftl.output.save import _should_compress_xmp, save_pdf

PDFA_NS = "http://www.aiim.org/pdfa/ns/id/"


def _xmp(part=None, pad=3000, encoding="utf-8"):
    ident = (
        f'<rdf:Description rdf:about="" xmlns:pdfaid="{PDFA_NS}">'
        f"<pdfaid:part>{part}</pdfaid:part><pdfaid:conformance>B</pdfaid:conformance>"
        "</rdf:Description>"
        if part is not None
        else ""
    )
    text = (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        f"{ident}"
        '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:format>application/pdf</dc:format></rdf:Description>"
        "</rdf:RDF></x:xmpmeta>\n" + " " * pad + '\n<?xpacket end="w"?>'
    )
    return text.encode(encoding)


def _pdf(xmp=None, pages=3):
    pdf = pikepdf.new()
    for i in range(pages):
        pdf.add_blank_page(page_size=(200, 200))
        page = pdf.pages[i]
        page.Contents = pdf.make_stream(b"0 0 1 rg %d 20 50 50 re f" % (20 + 10 * i))
        # a stream after the metadata, so its offset must shift
        page.Resources = pikepdf.Dictionary(
            XObject=pikepdf.Dictionary(Im0=_image(pdf, i)),
        )
    if xmp is not None:
        pdf.Root.Metadata = pdf.make_stream(xmp, Type=Name.Metadata, Subtype=Name.XML)
    return pdf


def _image(pdf, seed):
    img = pdf.make_stream(bytes((seed * 7 + n) % 256 for n in range(64)))
    img.Type, img.Subtype = Name.XObject, Name.Image
    img.Width, img.Height, img.BitsPerComponent = 8, 8, 8
    img.ColorSpace = Name.DeviceGray
    return img


def _save(pdf, path, **options):
    save_pdf(pdf, output_filename=str(path), input_context=MagicMock(), options=options)
    return path.read_bytes()


def _raw_metadata(data):
    """(filter, raw bytes) of the catalog metadata stream, read by MuPDF."""
    doc = pymupdf.open(stream=data, filetype="pdf")
    assert not doc.is_repaired
    xref = int(doc.xref_get_key(doc.pdf_catalog(), "Metadata")[1].split()[0])
    filt = doc.xref_get_key(xref, "Filter")[1]
    return filt, doc.xref_stream_raw(xref)


def _objects_by_mupdf(data):
    """Every object but the cross-reference stream, as MuPDF reads it."""
    doc = pymupdf.open(stream=data, filetype="pdf")
    assert not doc.is_repaired
    out = {}
    for xref in range(1, doc.xref_length()):
        if doc.xref_get_key(xref, "Type")[1] == "/XRef":
            continue
        stream = doc.xref_stream(xref) if doc.xref_is_stream(xref) else None
        out[xref] = (doc.xref_object(xref, compressed=True), stream)
    return out


def _renders(data):
    return [page.get_pixmap(dpi=36).samples for page in pymupdf.open(stream=data, filetype="pdf")]


# --- end to end ---


@pytest.mark.parametrize("part", [None, 2, 3, 4])
def test_catalog_xmp_is_compressed_losslessly(tmp_path, part):
    xmp = _xmp(part)
    plain = _save(_pdf(xmp), tmp_path / "plain.pdf")
    packed = _save(_pdf(xmp), tmp_path / "packed.pdf", compress_xmp=True)

    plain_filter, plain_raw = _raw_metadata(plain)
    packed_filter, packed_raw = _raw_metadata(packed)
    assert plain_filter == "null"  # qpdf writes the catalog XMP uncompressed
    assert packed_filter == "/FlateDecode"
    assert zlib.decompress(packed_raw) == plain_raw
    assert len(packed) < len(plain)

    before, after = _objects_by_mupdf(plain), _objects_by_mupdf(packed)
    assert before.keys() == after.keys()
    changed = [x for x in before if before[x] != after[x]]
    assert len(changed) == 1  # only the metadata stream's dictionary differs
    assert before[changed[0]][1] == after[changed[0]][1]
    assert _renders(plain) == _renders(packed)


@pytest.mark.skipif(shutil.which("qpdf") is None, reason="needs the qpdf command")
def test_compressed_file_passes_qpdf_check(tmp_path):
    _save(_pdf(_xmp(2)), tmp_path / "out.pdf", compress_xmp=True)
    result = subprocess.run(
        ["qpdf", "--check", str(tmp_path / "out.pdf")], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "xmp",
    [
        _xmp(1),  # PDF/A-1 forbids a /Filter on the catalog XMP
        _xmp(1).replace(b"<pdfaid:part>1</pdfaid:part>", b"<pdfaid:part>x</pdfaid:part>"),
        b"<pdfaid:part>2 " + PDFA_NS.encode() + b" " * 3000,  # not XML: part unknown
        _xmp(None, encoding="utf-16"),
    ],
    ids=["pdfa1", "bad-part", "unreadable", "utf16"],
)
def test_catalog_xmp_left_uncompressed(tmp_path, xmp):
    data = _save(_pdf(xmp), tmp_path / "out.pdf", compress_xmp=True)
    assert _raw_metadata(data)[0] == "null"


def test_without_metadata_nothing_changes(tmp_path):
    plain = _save(_pdf(None), tmp_path / "plain.pdf")
    packed = _save(_pdf(None), tmp_path / "packed.pdf", compress_xmp=True)
    assert plain == packed


def test_stdout_output_is_compressed(tmp_path):
    with patch("sys.stdout") as stdout:
        save_pdf(_pdf(_xmp()), "-", MagicMock(), options={"compress_xmp": True})
    data = b"".join(call.args[0] for call in stdout.buffer.write.call_args_list)
    filt, raw = _raw_metadata(data)
    assert filt == "/FlateDecode"
    assert zlib.decompress(raw) == _decoded_xmp(data)


def test_stdout_without_compress_xmp_is_plain():
    with patch("sys.stdout") as stdout:
        save_pdf(_pdf(_xmp()), "-", MagicMock(), options={})
    data = b"".join(call.args[0] for call in stdout.buffer.write.call_args_list)
    assert _raw_metadata(data)[0] == "null"


@pytest.mark.parametrize(
    "options,conflict",
    [
        ({"uncompress": True}, "uncompress"),
        ({"fast": True}, "fast"),
        ({"linearize": True}, "linearize"),
        ({"user_pw": "u", "owner_pw": "o"}, "encryption"),
    ],
)
def test_conflicting_options_skip_it(tmp_path, caplog, options, conflict):
    with caplog.at_level(logging.INFO):
        _save(_pdf(_xmp()), tmp_path / "out.pdf", compress_xmp=True, **options)
    assert f"conflicts with {conflict}" in caplog.text
    with pikepdf.open(tmp_path / "out.pdf", password="o") as out:
        # encrypted metadata is compressed by qpdf itself; otherwise it stays plain
        assert ("/Filter" in out.Root.Metadata) == (conflict == "encryption")


def test_signing_skips_it(caplog):
    pdf = _pdf(_xmp())
    save_opts = {"linearize": False, "encryption": False}
    with caplog.at_level(logging.INFO):
        assert not _should_compress_xmp(pdf, {"compress_xmp": True}, save_opts, True)
    assert "conflicts with signing" in caplog.text
    assert _should_compress_xmp(pdf, {"compress_xmp": True}, save_opts, False)
    assert not _should_compress_xmp(pdf, {}, save_opts, False)


# --- may_compress_xmp ---


def test_may_compress_needs_a_metadata_stream():
    pdf = _pdf(None)
    assert not cx.may_compress_xmp(pdf)
    pdf.Root.Metadata = pikepdf.Dictionary()
    assert not cx.may_compress_xmp(pdf)


def test_may_compress_unreadable_stream():
    pdf = _pdf(None)
    pdf.Root.Metadata = pdf.make_stream(b"not flate", Type=Name.Metadata, Subtype=Name.XML)
    pdf.Root.Metadata.Filter = Name.FlateDecode
    assert not cx.may_compress_xmp(pdf)


# --- compress_saved_file / compress_saved_bytes fall back to the input ---


def _qpdf_bytes(pdf, **save_opts):
    buf = io.BytesIO()
    save_opts.setdefault("object_stream_mode", pikepdf.ObjectStreamMode.generate)
    pdf.save(buf, **save_opts)
    return buf.getvalue()


def test_compress_saved_file_in_place(tmp_path):
    path = tmp_path / "out.pdf"
    path.write_bytes(_qpdf_bytes(_pdf(_xmp())))
    os.chmod(path, 0o640)
    before = path.read_bytes()
    assert cx.compress_saved_file(str(path))
    assert len(path.read_bytes()) < len(before)
    assert _raw_metadata(path.read_bytes())[0] == "/FlateDecode"
    if os.name != "nt":  # Windows keeps only a read-only flag
        assert os.stat(path).st_mode & 0o777 == 0o640
    assert os.listdir(tmp_path) == ["out.pdf"]


def test_compress_saved_file_unchanged_when_not_patchable(tmp_path):
    path = tmp_path / "out.pdf"
    original = _qpdf_bytes(_pdf(None))
    path.write_bytes(original)
    assert not cx.compress_saved_file(str(path))
    assert path.read_bytes() == original
    assert os.listdir(tmp_path) == ["out.pdf"]


def test_compress_saved_file_unchanged_when_verification_fails(tmp_path, monkeypatch):
    path = tmp_path / "out.pdf"
    original = _qpdf_bytes(_pdf(_xmp()))
    path.write_bytes(original)
    monkeypatch.setattr(cx, "_verify", lambda source, xmp: False)
    assert not cx.compress_saved_file(str(path))
    assert path.read_bytes() == original
    assert os.listdir(tmp_path) == ["out.pdf"]


@pytest.mark.parametrize(
    "make",
    [
        lambda: b"not a pdf at all",
        lambda: _qpdf_bytes(_pdf(None)),
        lambda: _qpdf_bytes(_pdf(_xmp()), object_stream_mode=pikepdf.ObjectStreamMode.disable),
        lambda: _qpdf_bytes(_pdf(_xmp()), linearize=True),
        lambda: _qpdf_bytes(
            _pdf(_xmp()),
            encryption=pikepdf.Encryption(owner="o", user="", metadata=False),
        ),
        lambda: _qpdf_bytes(_pdf(b"<x/>"), fix_metadata_version=False),  # nothing to gain
        lambda: _qpdf_bytes(_pdf(_xmp())) + b"% trailing junk\n",
    ],
    ids=["garbage", "no-xmp", "xref-table", "linearized", "encrypted", "tiny", "junk"],
)
def test_compress_saved_bytes_returns_input_when_not_patchable(make):
    data = make()
    assert cx.compress_saved_bytes(data) is data


def test_compress_saved_bytes_returns_input_when_verification_fails(monkeypatch):
    data = _qpdf_bytes(_pdf(_xmp()))
    monkeypatch.setattr(cx, "_verify", lambda source, xmp: False)
    assert cx.compress_saved_bytes(data) is data


# --- verification ---


def _decoded_xmp(data):
    doc = pymupdf.open(stream=data, filetype="pdf")
    return doc.xref_stream(int(doc.xref_get_key(doc.pdf_catalog(), "Metadata")[1].split()[0]))


def test_verify_rejects_wrong_content_and_uncompressed_and_garbage():
    data = _qpdf_bytes(_pdf(_xmp()))
    xmp = _decoded_xmp(data)
    assert not cx._verify(io.BytesIO(data), xmp)  # still uncompressed
    packed = cx.compress_saved_bytes(data)
    assert cx._verify(io.BytesIO(packed), xmp)
    assert not cx._verify(io.BytesIO(packed), xmp + b" ")
    assert not cx._verify(io.BytesIO(b"garbage"), xmp)
    no_metadata = _qpdf_bytes(_pdf(None))
    assert not cx._verify(io.BytesIO(no_metadata), xmp)


def test_verify_rejects_a_file_qpdf_had_to_repair():
    packed = cx.compress_saved_bytes(_qpdf_bytes(_pdf(_xmp())))
    xmp = _decoded_xmp(packed)
    start = int(packed.rsplit(b"startxref\n", 1)[1].split(b"\n")[0])
    broken = packed.replace(b"startxref\n%d\n" % start, b"startxref\n%d\n" % (start - 7))
    assert not cx._verify(io.BytesIO(broken), xmp)


# --- the cross-reference stream parser, on qpdf's output and on damaged copies ---


def _xref_of(data):
    return cx._read_xref(io.BytesIO(data), len(data))


def test_read_xref_matches_qpdf_offsets():
    data = _qpdf_bytes(_pdf(_xmp()))
    xref = _xref_of(data)
    in_use = [(n, row) for n, row in zip(xref.numbers, xref.rows) if row[0] == 1]
    assert in_use
    for num, (_, offset, gen) in in_use:
        assert data[offset:].startswith(b"%d %d obj" % (num, gen))
    assert xref.rows[0][0] == 0  # object 0 is always free


def _replace_xref_header(data, old, new):
    xref_at = int(data.rsplit(b"startxref\n", 1)[1].split(b"\n")[0])
    head, tail = data[:xref_at], data[xref_at:]
    assert old in tail
    return head + tail.replace(old, new, 1)


def test_read_xref_rejects_unexpected_layouts():
    data = _qpdf_bytes(_pdf(_xmp()))
    assert _xref_of(data[:-10]) is None  # no startxref at the end
    assert _xref_of(_replace_xref_header(data, b"/Type /XRef", b"/Type /XRuf")) is None
    assert _xref_of(_replace_xref_header(data, b">>\nstream\n", b">>\nstreax\n")) is None
    assert _xref_of(_replace_xref_header(data, b"/W [ 1 ", b"/W [ 2 ")) is None
    xref_at = int(data.rsplit(b"startxref\n", 1)[1].split(b"\n")[0])
    body_at = data.index(b"stream\n", xref_at) + 7
    damaged = data[:body_at] + b"\0" * 8 + data[body_at + 8 :]
    assert _xref_of(damaged) is None  # stream is no longer zlib
    size = int(xs.SIZE.search(data[xref_at:]).group(1))
    fewer = _replace_xref_header(data, b"/Size %d" % size, b"/Size %d" % (size - 1))
    assert _xref_of(fewer) is None  # one more row than objects


def test_object_numbers_from_index():
    assert xs.object_numbers(b"<< /Index [ 0 2 7 3 ] >>") == [0, 1, 7, 8, 9]
    assert xs.object_numbers(b"<< /Index [ 0 2 7 ] >>") is None
    assert xs.object_numbers(b"<< /Size 3 >>") == [0, 1, 2]


def test_decode_rows_png_filters():
    columns, widths = 3, (1, 1, 1)
    # rows 1 2 3 and 4 6 8: the second sent with the Up filter as 3 4 5
    good = zlib.compress(bytes([0, 1, 2, 3, 2, 3, 4, 5]))
    assert xs.decode_rows(good, columns, widths) == [[1, 2, 3], [4, 6, 8]]
    assert xs.decode_rows(zlib.compress(bytes([1, 1, 2, 3])), columns, widths) is None
    assert xs.decode_rows(zlib.compress(bytes([0, 1, 2])), columns, widths) is None
    assert xs.decode_rows(b"junk", columns, widths) is None


# --- locating the metadata object ---


def _parts(data):
    num, xmp = cx._saved_metadata(io.BytesIO(data))
    return num, xmp, _xref_of(data)


def test_locate_metadata_finds_the_uncompressed_stream():
    data = _qpdf_bytes(_pdf(_xmp()))
    num, xmp, xref = _parts(data)
    start, end, header = cx._locate_metadata(io.BytesIO(data), xref, num, xmp)
    assert data[start:].startswith(b"%d 0 obj\n<<" % num)
    assert data[:end].endswith(b"endobj\n")
    assert xmp in data[start:end]
    assert header.endswith(b">>\n")


def test_locate_metadata_rejects_mismatches():
    data = _qpdf_bytes(_pdf(_xmp()))
    num, xmp, xref = _parts(data)
    src = io.BytesIO(data)
    assert cx._locate_metadata(src, xref, xref.num, xmp) is None  # the xref stream itself
    assert cx._locate_metadata(src, xref, 10**6, xmp) is None  # no such object
    assert cx._locate_metadata(src, xref, num, xmp + b"x") is None
    in_objstm = next(n for n, row in zip(xref.numbers, xref.rows) if row[0] == 2)
    assert cx._locate_metadata(src, xref, in_objstm, xmp) is None
    rows = dict(zip(xref.numbers, xref.rows))
    other_stream = next(n for n, row in rows.items() if row[0] == 1 and n not in (num, xref.num))
    assert cx._locate_metadata(src, xref, other_stream, xmp) is None


@pytest.mark.parametrize(
    "old,new",
    [
        (b" obj\n<<", b" obj\n <<"),
        (b"/Type /Metadata", b"/Filter /Metadata"),
        (b"/Type /Metadata", b"/Length 1 /Type"),
        (b"stream\n", b"strean\n"),
    ],
)
def test_locate_metadata_rejects_odd_headers(old, new):
    data = _qpdf_bytes(_pdf(_xmp()))
    num, xmp, xref = _parts(data)
    offset = dict(zip(xref.numbers, xref.rows))[num][1]
    header_end = data.index(b"stream\n", offset) + 7
    patched = data[:offset] + data[offset:header_end].replace(old, new, 1) + data[header_end:]
    assert cx._locate_metadata(io.BytesIO(patched), xref, num, xmp) is None


def test_locate_metadata_rejects_a_bad_ending():
    data = _qpdf_bytes(_pdf(_xmp()))
    num, xmp, xref = _parts(data)
    offset = dict(zip(xref.numbers, xref.rows))[num][1]
    end = data.index(b"endobj\n", offset)
    patched = data[:end] + b"endobk\n" + data[end + 7 :]
    assert cx._locate_metadata(io.BytesIO(patched), xref, num, xmp) is None


# --- offsets ---


def test_offsets_valid_checks_every_in_use_entry():
    data = _qpdf_bytes(_pdf(_xmp()))
    num, xmp, xref = _parts(data)
    start, end, _ = cx._locate_metadata(io.BytesIO(data), xref, num, xmp)
    src = io.BytesIO(data)
    assert cx._offsets_valid(src, xref, start, end)
    assert not cx._offsets_valid(src, xref, start - 1, end)  # metadata's own entry inside
    moved = next(row for row in xref.rows if row[0] == 1 and row[1] > end)
    moved[1] += 1
    assert not cx._offsets_valid(src, xref, start, end)


def test_copy_in_chunks():
    out = io.BytesIO()
    cx._copy(io.BytesIO(b"abcdefghij"), out, 2, 9, chunk=3)
    assert out.getvalue() == b"cdefghi"
