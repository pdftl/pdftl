# tests/output/test_repack.py
#
# Ground truth: qpdf --check, pikepdf's view of every object, pdfium renders,
# and object-stream counts read straight from the file bytes.

import io
import os
import re
import shutil
import subprocess
import zlib
from unittest.mock import MagicMock

import numpy as np
import pikepdf
import pytest

import pdftl.output.repack as rp
from pdftl.output.save import save_pdf
from pdftl.output.xref_stream import object_numbers, read_xref


def _many_objects(n=450) -> pikepdf.Pdf:
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    page.Contents = pdf.make_stream(b"0 0 1 rg 20 20 160 160 re f")
    annots = pikepdf.Array()
    for i in range(n):
        annots.append(
            pdf.make_indirect(
                pikepdf.Dictionary(
                    Type=pikepdf.Name.Annot,
                    Subtype=pikepdf.Name.Link,
                    Rect=[i % 180, i % 150, i % 180 + 5, i % 150 + 5],
                    Border=[0, 0, 0],
                    A=pikepdf.Dictionary(S=pikepdf.Name.URI, URI=f"https://example.org/{i}"),
                )
            )
        )
    page.Annots = annots
    return pdf


def _qpdf_bytes(pdf) -> bytes:
    buf = io.BytesIO()
    pdf.save(buf, object_stream_mode=pikepdf.ObjectStreamMode.generate)
    return buf.getvalue()


def _object_streams(data: bytes) -> list[int]:
    return [int(n) for n in re.findall(rb"/Type /ObjStm[^>]*?/N (\d+)", data)]


def _render(data: bytes):
    import pypdfium2

    doc = pypdfium2.PdfDocument(data)
    try:
        return [np.asarray(p.render().to_pil()) for p in doc]
    finally:
        doc.close()


def test_object_streams_merge_into_one_with_every_object_unchanged():
    data = _qpdf_bytes(_many_objects())
    assert len(_object_streams(data)) >= 4  # qpdf: at most 100 objects each
    packed = rp.repack_bytes(data)
    assert len(packed) < len(data)
    assert len(_object_streams(packed)) == 1
    assert rp.same_objects(data, packed)
    assert all((a == b).all() for a, b in zip(_render(data), _render(packed)))


@pytest.mark.skipif(shutil.which("qpdf") is None, reason="qpdf not installed")
def test_repacked_file_passes_qpdf_check(tmp_path):
    out = tmp_path / "out.pdf"
    out.write_bytes(rp.repack_bytes(_qpdf_bytes(_many_objects())))
    check = subprocess.run(["qpdf", "--check", str(out)], capture_output=True, text=True)
    assert check.returncode == 0, check.stdout + check.stderr


def test_large_counts_are_split_and_spare_streams_freed(monkeypatch):
    monkeypatch.setattr(rp, "MAX_OBJECTS", 200)
    data = _qpdf_bytes(_many_objects())
    packed = rp._repack(data)
    counts = _object_streams(packed)
    assert max(counts) <= 200 and sum(counts) == sum(_object_streams(data))
    assert rp.same_objects(data, packed)
    with pikepdf.open(io.BytesIO(packed)) as pdf:
        assert not pdf.get_warnings()


def test_repack_file_in_place(tmp_path):
    path = tmp_path / "f.pdf"
    path.write_bytes(_qpdf_bytes(_many_objects()))
    before = path.read_bytes()
    assert rp.repack_file(str(path))
    assert rp.same_objects(before, path.read_bytes())
    assert not rp.repack_file(str(path))  # one stream already: nothing to merge


def test_repack_file_keeps_mode(tmp_path):
    path = tmp_path / "f.pdf"
    path.write_bytes(_qpdf_bytes(_many_objects()))
    os.chmod(path, 0o644)
    assert rp.repack_file(str(path))
    if os.name != "nt":  # Windows keeps only a read-only flag
        assert os.stat(path).st_mode & 0o777 == 0o644


def test_width():
    assert [rp._width(v) for v in (0, 255, 256, 65535, 65536)] == [1, 1, 2, 2, 3]


# --- files left alone ---


def test_files_without_a_qpdf_xref_stream_are_left_alone():
    pdf = _many_objects()
    buf = io.BytesIO()
    pdf.save(buf, object_stream_mode=pikepdf.ObjectStreamMode.disable)
    data = buf.getvalue()
    assert rp.repack_bytes(data) is data


def test_one_object_stream_is_left_alone():
    data = _qpdf_bytes(_many_objects(20))
    assert len(_object_streams(data)) == 1
    assert rp.repack_bytes(data) is data


def test_linearized_and_incremental_files_are_left_alone():
    buf = io.BytesIO()
    _many_objects().save(buf, linearize=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)
    assert rp._repack(buf.getvalue()) is None
    data = _qpdf_bytes(_many_objects())
    xref_at = int(data.rsplit(b"startxref\n", 1)[1].split(b"\n")[0])
    fake_prev = data[:xref_at] + data[xref_at:].replace(b"/Root", b"/Prev 9 /Root", 1)
    assert rp._repack(fake_prev) is None


def _first_objstm(data: bytes) -> re.Match:
    return re.search(rb"\d+ 0 obj\n<< /Type /ObjStm[^>]*>>\nstream\n", data)


@pytest.mark.parametrize(
    "old,new",
    [
        (b"/Type /ObjStm", b"/Type /ObjStX"),
        (b"/Filter /FlateDecode", b"/Filter /FlateDecodX"),
        (b" /First ", b" /Extends 5 0 R /First "),
        (b" /First ", b" /DecodeParms 5 0 R /First "),
        (b" /N ", b" /M "),
    ],
)
def test_unexpected_object_stream_dictionaries_are_left_alone(old, new):
    data = _qpdf_bytes(_many_objects())
    head = _first_objstm(data)
    patched_head = head.group(0).replace(old, new, 1)
    pad = len(head.group(0)) - len(patched_head)
    if pad > 0:
        patched_head = patched_head.replace(b">>\nstream", b" " * pad + b">>\nstream")
    damaged = data[: head.start()] + patched_head + data[head.end() :]
    assert rp._repack(damaged) is None


def test_damaged_object_stream_data_is_left_alone():
    data = _qpdf_bytes(_many_objects())
    head = _first_objstm(data)
    damaged = data[: head.end()] + b"\0" * 8 + data[head.end() + 8 :]
    assert rp._repack(damaged) is None


def test_object_stream_whose_table_disagrees_with_n_is_left_alone(monkeypatch):
    real = rp._object_stream_members

    def short(data, row, number):
        head = rp._OBJSTM_HEADER.match(data, row[1])
        entries = head.group(2)
        n = int(rp._KEY[b"N"].search(entries).group(1))
        fake = entries.replace(b"/N %d" % n, b"/N %d" % (n + 1))
        patched = data[: head.start(2)] + fake + data[head.end(2) :]
        return real(patched, row, number)

    monkeypatch.setattr(rp, "_object_stream_members", short)
    assert rp._repack(_qpdf_bytes(_many_objects())) is None


def test_member_rows_must_match_their_container(monkeypatch):
    data = _qpdf_bytes(_many_objects())
    assert rp._object_stream_members(data, None, 1) is None
    assert rp._object_stream_members(data, [2, 1, 0], 1) is None
    head = _first_objstm(data)
    number = int(head.group(0).split(b" ")[0])
    assert rp._object_stream_members(data, [1, head.start(), 0], number + 1) is None
    monkeypatch.setattr(rp, "_object_stream_members", lambda d, row, n: {})
    assert rp._repack(data) is None  # a member missing from its stream


def test_result_that_is_not_smaller_or_not_identical_is_discarded(monkeypatch):
    data = _qpdf_bytes(_many_objects())
    monkeypatch.setattr(rp, "_repack", lambda d: d + b"%padding")
    assert rp.repack_bytes(data) is data
    monkeypatch.setattr(rp, "_repack", lambda d: d[:-1])
    monkeypatch.setattr(rp, "same_objects", lambda a, b: False)
    assert rp.repack_bytes(data) is data


def test_same_objects_detects_differences():
    one, two = _many_objects(150), _many_objects(150)
    two.pages[0].Contents = two.make_stream(b"1 0 0 rg 0 0 10 10 re f")
    a, b = _qpdf_bytes(one), _qpdf_bytes(two)
    assert rp.same_objects(a, a)
    assert not rp.same_objects(a, b)
    assert not rp.same_objects(a, b"not a pdf")


def test_same_objects_refuses_a_file_that_opens_with_warnings():
    data = _qpdf_bytes(_many_objects(150))
    broken = data.replace(b"startxref\n", b"startxref\n1", 1)  # a wrong xref offset
    assert not rp.same_objects(data, broken)


# --- save integration ---


def _save(pdf, target, **options):
    save_pdf(pdf, target, input_context=MagicMock(), options=options)


def test_recompressing_save_repacks_files_and_buffers(tmp_path):
    out = tmp_path / "out.pdf"
    _save(_many_objects(), str(out), recompress=True)
    assert len(_object_streams(out.read_bytes())) == 1
    buf = io.BytesIO()
    _save(_many_objects(), buf, recompress=True)
    assert len(_object_streams(buf.getvalue())) == 1


def test_plain_and_conflicting_saves_are_not_repacked(tmp_path):
    out = tmp_path / "out.pdf"
    _save(_many_objects(), str(out))
    assert len(_object_streams(out.read_bytes())) > 1
    _save(_many_objects(), str(out), recompress=True, linearize=True)
    assert rp._repack(out.read_bytes()) is None or len(_object_streams(out.read_bytes())) != 1


def test_recompressing_save_to_stdout_repacks(monkeypatch):
    import sys

    captured = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", MagicMock(buffer=captured))
    _save(_many_objects(), "-", recompress=True)
    assert len(_object_streams(captured.getvalue())) == 1


def test_object_stream_data_is_deflated_by_the_shared_deflate():
    items = [(5, b"<< /A 1 >>"), (6, b"(x)")]
    stream = rp._object_stream(9, items)
    head, body = stream.split(b"stream\n", 1)
    assert b"/N 2" in head and b"/First 9" in head  # "5 0 6 11\n"
    decoded = zlib.decompress(body.rsplit(b"\nendstream", 1)[0])
    assert decoded == b"5 0 6 11\n<< /A 1 >>\n(x)\n"


def _signed_like(n=20) -> pikepdf.Pdf:
    pdf = _many_objects(n)
    sig = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Sig,
            Filter=pikepdf.Name("/Adobe.PPKLite"),
            ByteRange=[0, 0, 0, 0],
            Contents=pikepdf.String(b"\0" * 4000),
        )
    )
    field = pdf.make_indirect(
        pikepdf.Dictionary(FT=pikepdf.Name.Sig, T=pikepdf.String("Sig1"), V=sig, Rect=[0, 0, 0, 0])
    )
    pdf.Root.AcroForm = pikepdf.Dictionary(Fields=[field], SigFlags=3)
    return pdf


def _loose_objects(data: bytes) -> int:
    xref = read_xref(io.BytesIO(data), len(data))
    return sum(
        1
        for n, r in zip(xref.numbers, xref.rows)
        if r[0] == 1
        and n != xref.num
        and rp._loose_object(data[r[1] :].split(b"endobj\n", 1)[0] + b"endobj\n", n) is not None
    )


def test_signature_dictionary_moves_into_the_object_stream():
    data = _qpdf_bytes(_signed_like())
    assert len(_object_streams(data)) == 1 and _loose_objects(data) == 1  # qpdf keeps it out
    packed = rp.repack_bytes(data)
    assert len(packed) < len(data) - 3000  # the zero padding now compresses
    assert _loose_objects(packed) == 0
    assert rp.same_objects(data, packed)


def test_more_chunks_than_streams_to_reuse_is_left_alone(monkeypatch):
    monkeypatch.setattr(rp, "MAX_OBJECTS", 5)
    assert rp._repack(_qpdf_bytes(_signed_like())) is None


def test_loose_object_extraction():
    assert rp._loose_object(b"7 0 obj\n<< /A 1 >>\nstream\nxx\nendstream\nendobj\n", 7) is None
    assert rp._loose_object(b"7 0 obj\n<< /A 1 >>\nendobj\n", 8) is None
    assert rp._loose_object(b"7 0 obj\n<< /A 1 >>\nendobj\n", 7) == b"<< /A 1 >>"


def test_indirect_scalar_objects_are_compared_by_number():
    # pikepdf hands indirect integers back as plain ints, without an object number.
    pdf = _many_objects(250)
    pdf.Root.Counter = pdf.make_indirect(pikepdf.Object.parse(b"42"))
    pdf.Root.Ratio = pdf.make_indirect(pikepdf.Object.parse(b"0.5"))
    data = _qpdf_bytes(pdf)
    packed = rp.repack_bytes(data)
    assert len(_object_streams(packed)) == 1
    other = _many_objects(250)
    other.Root.Counter = other.make_indirect(pikepdf.Object.parse(b"43"))
    other.Root.Ratio = other.make_indirect(pikepdf.Object.parse(b"0.5"))
    assert not rp.same_objects(data, _qpdf_bytes(other))


def test_object_stream_table_with_a_non_number_is_left_alone():
    body = zlib.compress(b"<< 0\n1 ")
    data = (
        b"5 0 obj\n<< /Type /ObjStm /Length %d /Filter /FlateDecode /N 1 /First 5 >>\nstream\n"
        % len(body)
        + body
    )
    assert rp._object_stream_members(data, [1, 0, 0], 5) is None
    good = zlib.compress(b"7 0\n1 ")
    data = (
        b"5 0 obj\n<< /Type /ObjStm /Length %d /Filter /FlateDecode /N 1 /First 4 >>\nstream\n"
        % len(good)
        + good
    )
    assert rp._object_stream_members(data, [1, 0, 0], 5) == {7: b"1"}


def test_xref_header_without_size_or_index_has_no_object_numbers():
    assert object_numbers(b"<< /Type /XRef /W [ 1 2 1 ] >>\n") is None
    assert object_numbers(b"<< /Type /XRef /Size 3 >>\n") == [0, 1, 2]
    assert object_numbers(b"<< /Type /XRef /Index [ 4 2 9 1 ] >>\n") == [4, 5, 9]


def test_buffer_save_with_nothing_to_repack_is_untouched():
    buf = io.BytesIO()
    _save(_many_objects(20), buf, recompress=True)  # one object stream: nothing to merge
    data = buf.getvalue()
    assert len(_object_streams(data)) == 1
    with pikepdf.open(io.BytesIO(data)) as pdf:
        assert len(pdf.pages) == 1
