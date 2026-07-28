"""
Standalone byte-level patch: force an explicit FontMatrix operator into a
compiled CFF's Top DICT, bypassing fontTools' CFFFontSet.compile() entirely
for this one operator.

Why this exists: fontTools' compiled output silently omits a Top DICT
FontMatrix operator even when rawDict carries an explicit (and even
epsilon-perturbed, non-default) value going into compile() -- confirmed by
direct extraction (`top FontMatrix: None` post-compile). Rather than keep
guessing at *why* compile() drops it, this module edits the already-
serialized bytes directly: locate the Top DICT INDEX, decode its one
DICT's operator list, insert (or overwrite) the FontMatrix operator+operands
ourselves, re-encode just that INDEX, and shift every absolute offset
operand still inside the Top DICT (charset/CharStrings/Private/FDArray/
FDSelect/Encoding) by however many bytes the INDEX grew. Nothing after the
Top DICT INDEX needs to move or be re-parsed -- only its *position*
changes, not its content, since all CFF INDEX-to-INDEX references are
absolute byte offsets from the start of the CFF table.

Only handles the Top DICT's own FontMatrix (the reported bug). FDArray-level
FontMatrix splicing is a separate, smaller problem (each FD DICT is a
sub-DICT inside the FDArray INDEX, at a self-contained absolute offset with
no downstream offset operands of its own to fix up) and is not implemented
here since the reported symptom was top-level only.
"""

from __future__ import annotations

import struct

# --- CFF DICT operand encoding (Adobe TN#5176 Table 3) ---------------------


def _encode_int(value: int) -> bytes:
    if -107 <= value <= 107:
        return bytes([value + 139])
    if 108 <= value <= 1131:
        value -= 108
        return bytes([(value >> 8) + 247, value & 0xFF])
    if -1131 <= value <= -108:
        value = -value - 108
        return bytes([(value >> 8) + 251, value & 0xFF])
    if -32768 <= value <= 32767:
        return b"\x1c" + struct.pack(">h", value)
    return b"\x1d" + struct.pack(">i", value)


def _encode_real(value: float) -> bytes:
    # Render via repr-ish shortest form; CFF real encoding just needs a
    # standard decimal/exponent ASCII form we can nibble-encode.
    s = repr(float(value))
    if s.endswith(".0"):
        s = s[:-2]
    s = s.replace("e+", "E").replace("e-", "E-").replace("e", "E")
    nibbles = _real_string_to_nibbles(s)
    nibbles.append(0xF)  # end marker
    if len(nibbles) % 2:
        nibbles.append(0xF)
    data = bytearray()
    for a, b in zip(nibbles[0::2], nibbles[1::2]):
        data.append((a << 4) | b)
    return b"\x1e" + bytes(data)


def _real_string_to_nibbles(s: str) -> list[int]:
    nibbles = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "E" and i + 1 < len(s) and s[i + 1] == "-":
            nibbles.append(0xC)  # E-
            i += 2
            continue
        if c == "E":
            nibbles.append(0xB)  # E+
            i += 1
            continue
        if c == "-":
            nibbles.append(0xE)
            i += 1
            continue
        if c == ".":
            nibbles.append(0xA)
            i += 1
            continue
        nibbles.append(int(c))
        i += 1
    return nibbles


def _encode_operand(value) -> bytes:
    if isinstance(value, int):
        return _encode_int(value)
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 2**31:
            # Still encode as real if it came in as a float logically, but
            # CFF allows plain ints for integral values too -- prefer the
            # more compact int form when it's exact.
            return _encode_int(int(value))
        return _encode_real(value)
    raise TypeError(f"Unsupported CFF DICT operand type: {type(value)!r}")


def _encode_operator(op) -> bytes:
    if isinstance(op, tuple):
        return bytes([12, op[1]])
    return bytes([op])


# --- CFF DICT decoding -------------------------------------------------------


def _decode_real_operand(data: bytes, i: int) -> tuple[float, int]:
    """Decodes a real-number operand (b0 == 30) starting at `i` (pointing at
    the 0x1e byte). Returns (value, index_after_operand)."""
    i += 1
    n = len(data)
    digits = []
    nibble_map = "0123456789.EE?-?"
    done = False
    while not done and i < n:
        byte = data[i]
        i += 1
        for nibble in (byte >> 4, byte & 0xF):
            if nibble == 0xF:
                done = True
                break
            elif nibble == 0xC:
                digits.append("E-")
            else:
                digits.append(nibble_map[nibble])
    return float("".join(digits)), i


def _decode_numeric_operand(data: bytes, i: int) -> tuple[object, int]:
    """Decodes a single numeric operand (any b0 >= 28) starting at `i`.
    Returns (value, index_after_operand)."""
    b0 = data[i]
    if b0 == 28:
        return struct.unpack(">h", data[i + 1 : i + 3])[0], i + 3
    if b0 == 29:
        return struct.unpack(">i", data[i + 1 : i + 5])[0], i + 5
    if b0 == 30:
        return _decode_real_operand(data, i)
    if 32 <= b0 <= 246:
        return b0 - 139, i + 1
    if 247 <= b0 <= 250:
        return (b0 - 247) * 256 + data[i + 1] + 108, i + 2
    if 251 <= b0 <= 254:
        return -(b0 - 251) * 256 - data[i + 1] - 108, i + 2
    raise ValueError(f"Reserved/invalid CFF DICT byte {b0} at offset {i}")


def _decode_dict(data: bytes) -> list[tuple[object, list]]:
    """Returns an ordered list of (operator, operands) exactly as they
    appear in `data`. operator is an int (0-21) or a (12, b1) tuple."""
    entries = []
    operands: list = []
    i = 0
    n = len(data)
    while i < n:
        b0 = data[i]
        if b0 <= 21:
            if b0 == 12:
                op = (12, data[i + 1])
                i += 2
            else:
                op = b0
                i += 1
            entries.append((op, operands))
            operands = []
        else:
            value, i = _decode_numeric_operand(data, i)
            operands.append(value)
    return entries


def _encode_dict(entries: list[tuple[object, list]]) -> bytes:
    out = bytearray()
    for op, operands in entries:
        for value in operands:
            out += _encode_operand(value)
        out += _encode_operator(op)
    return bytes(out)


# --- CFF INDEX parsing/building ----------------------------------------------


def _read_index(data: bytes, pos: int):
    """Returns (entries: list[bytes], start_of_index, end_of_index)."""
    count = struct.unpack(">H", data[pos : pos + 2])[0]
    if count == 0:
        return [], pos, pos + 2
    off_size = data[pos + 2]
    offsets_start = pos + 3
    offsets = []
    for i in range(count + 1):
        chunk = data[offsets_start + i * off_size : offsets_start + (i + 1) * off_size]
        offsets.append(int.from_bytes(chunk, "big"))
    data_start = offsets_start + (count + 1) * off_size - 1
    entries = [data[data_start + offsets[i] : data_start + offsets[i + 1]] for i in range(count)]
    end = data_start + offsets[-1]
    return entries, pos, end


def _off_size_for(max_offset: int) -> int:
    if max_offset <= 0xFF:
        return 1
    if max_offset <= 0xFFFF:
        return 2
    if max_offset <= 0xFFFFFF:
        return 3
    return 4


def _build_index(entries: list[bytes]) -> bytes:
    count = len(entries)
    out = bytearray()
    out += struct.pack(">H", count)
    if count == 0:
        return bytes(out)
    lengths = [len(e) for e in entries]
    offsets = [1]
    for length in lengths:
        offsets.append(offsets[-1] + length)
    off_size = _off_size_for(offsets[-1])
    out.append(off_size)
    for off in offsets:
        out += off.to_bytes(off_size, "big")
    for e in entries:
        out += e
    return bytes(out)


# --- Top DICT operator keys that hold absolute offsets into the CFF table ---

# operator -> which operand index(es) are absolute offsets that must shift.
# Private is (size, offset) -- only the offset (index 1) shifts.
_OFFSET_OPERATORS = {
    15: [0],  # charset
    16: [0],  # Encoding
    17: [0],  # CharStrings
    18: [1],  # Private: [size, offset]
    (12, 36): [0],  # FDArray
    (12, 37): [0],  # FDSelect
}

_FONT_MATRIX_OP = (12, 7)


def _with_font_matrix_forced(
    dict_entries: list[tuple[object, list]], matrix: tuple[float, ...]
) -> list[tuple[object, list]]:
    """Returns a copy of `dict_entries` with FontMatrix set to `matrix`,
    overwriting it in place if present or appending it if absent."""
    working = [(op, list(operands)) for op, operands in dict_entries]
    for i, (op, _operands) in enumerate(working):
        if op == _FONT_MATRIX_OP:
            working[i] = (op, list(matrix))
            return working
    working.append((_FONT_MATRIX_OP, list(matrix)))
    return working


def _with_offsets_shifted(
    dict_entries: list[tuple[object, list]], delta: int
) -> list[tuple[object, list]]:
    """Returns a copy of `dict_entries` with every absolute-offset operand
    (per _OFFSET_OPERATORS) bumped by `delta`."""
    if not delta:
        return dict_entries
    working = []
    for op, operands in dict_entries:
        idxs = _OFFSET_OPERATORS.get(op)
        if not idxs:
            working.append((op, operands))
            continue
        new_operands = list(operands)
        for idx in idxs:
            new_operands[idx] = operands[idx] + delta
        working.append((op, new_operands))
    return working


def _splice_top_dict_index(
    dict_entries: list[tuple[object, list]], matrix: tuple[float, ...], original_index_len: int
) -> bytes:
    """Iterates to a fixpoint on the byte-length delta caused by forcing
    FontMatrix and shifting offset operands, since shifting an offset can
    itself change that offset's encoded width. Returns the rebuilt Top DICT
    INDEX bytes."""
    delta = 0
    for _ in range(5):
        working = _with_font_matrix_forced(dict_entries, matrix)
        working = _with_offsets_shifted(working, delta)

        new_dict_bytes = _encode_dict(working)
        new_index_bytes = _build_index([new_dict_bytes])
        new_delta = len(new_index_bytes) - original_index_len
        if new_delta == delta:
            return new_index_bytes
        delta = new_delta
    raise RuntimeError("FontMatrix splice offset-fixup did not converge")


def splice_top_font_matrix(cff_bytes: bytes, matrix: tuple[float, ...]) -> bytes:
    """
    Returns a new bytes object: `cff_bytes` with the Top DICT's FontMatrix
    operator forced to `matrix` (6 floats), inserting the operator if it's
    absent and overwriting it if present. All downstream absolute offsets
    (both the Top DICT INDEX's own position relative to nothing -- it's
    first -- and every offset-valued operator inside the Top DICT itself)
    are correctly shifted by however many bytes the Top DICT INDEX grows.

    Assumes a single font in the CFF (cff.fontNames has exactly one entry),
    which holds for every /FontFile3 embedded in a PDF.
    """
    header_size = cff_bytes[2]
    pos = header_size

    # Name INDEX -- untouched, just skip over it.
    _, _, pos = _read_index(cff_bytes, pos)

    # Top DICT INDEX -- the one we're editing.
    top_entries, top_index_start, top_index_end = _read_index(cff_bytes, pos)
    if len(top_entries) != 1:
        raise ValueError(f"Expected exactly 1 Top DICT, found {len(top_entries)}")

    dict_entries = _decode_dict(top_entries[0])
    new_index_bytes = _splice_top_dict_index(dict_entries, matrix, top_index_end - top_index_start)

    return cff_bytes[:top_index_start] + new_index_bytes + cff_bytes[top_index_end:]


if __name__ == "__main__":  # pragma: no cover -- CLI smoke-test entry point, not library logic
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "font-0012.cid"
    data = open(path, "rb").read()
    patched = splice_top_font_matrix(data, (0.001, 0, 0, 0.001, 0, 0))
    out_path = path + ".patched"
    with open(out_path, "wb") as f:
        f.write(patched)
    print(
        f"wrote {out_path}, {len(data)} -> {len(patched)} bytes (delta {len(patched) - len(data)})"
    )

    # Verify with fontTools itself.
    from fontTools.cffLib import CFFFontSet
    from io import BytesIO

    cff = CFFFontSet()
    cff.decompile(BytesIO(patched), otFont=None)
    td = cff[cff.fontNames[0]]
    print("top FontMatrix (post-splice):", td.rawDict.get("FontMatrix"))
