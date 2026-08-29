# tests/utils/test_object_equivalence.py
"""Tests for check_object_equivalence, ported from qpdf's libtests/objects.cc
test_3 (qpdf PR #1665) -- adapted from qpdf's C++ "_qpdf"-literal-parsing
test helper to real pikepdf.Pdf()/Dictionary/Array/String/Name objects.

Not every qpdf scenario is ported. Several test qpdf's own C++-internal
object representation (a stale ot_reference handle after replaceObject(),
the sparse-array storage optimization triggered above 100 nulls, an
"uninitialized handle" state, cross-QPDF-instance object identity) that
either has no pikepdf equivalent or isn't reachable through pikepdf's
Python API. Each skipped scenario is noted below with why.

Scenario-number comments refer to the numbering in the qpdf diff pasted
into the handoff chat (libtests/objects.cc test_3), not to test order
here.
"""

from __future__ import annotations

import pytest
import pikepdf
from pikepdf import Dictionary, Array, Name

from pdftl.utils.object_equivalence import check_object_equivalence as ceq


@pytest.fixture
def pdf():
    p = pikepdf.Pdf.new()
    yield p
    p.close()


# --- Scenario 1: basic equality, name vs scalar type mismatch ---------


def test_name_equals_itself():
    assert ceq(Name("/Test"), Name("/Test"))


def test_name_not_equal_to_integer():
    assert not ceq(Name("/Test"), 42)


# --- Scenario 2: int/real cross-equivalence ----------------------------


def test_int_and_real_cross_equivalent():
    # A PDF real, once parsed by pikepdf, is a decimal.Decimal -- not a
    # bare Python float -- so it must come through pikepdf.Object.parse
    # here, not a Python float literal (that was an earlier bug in this
    # test itself, not in check_object_equivalence: a bare `1.0` isn't
    # recognized as PDF-numeric by the isinstance(_NUMERIC) check).
    one_int = 1
    one_real = pikepdf.Object.parse(b"1.0")
    assert ceq(one_int, one_real)
    assert ceq(one_real, one_int)


# --- Scenario 3: array order sensitivity -------------------------------


def test_array_order_sensitive():
    a1 = pikepdf.Object.parse(b"[1 2]")
    a2 = pikepdf.Object.parse(b"[2 1]")
    assert not ceq(a1, a2)
    assert not ceq(a2, a1)


# --- Scenario 4: dict key-order insensitivity --------------------------


def test_dict_key_order_insensitive():
    d1 = Dictionary(A=1, B=2)
    d2 = Dictionary(B=2, A=1)
    assert ceq(d1, d2)
    assert ceq(d2, d1)


# --- Scenario 5 / 29: direct vs indirect scalar equality ---------------


def test_direct_vs_indirect_integer(pdf):
    direct = 100
    indirect = pdf.make_indirect(pikepdf.Object.parse(b"100"))
    assert ceq(direct, indirect)
    assert ceq(indirect, direct)


# --- Scenario 6: diamond graph isomorphism (shared indirect subobject) -


def test_diamond_graph_isomorphism(pdf):
    d = pdf.make_indirect(pikepdf.Object.parse(b"99"))
    b = pdf.make_indirect(Array([d]))
    c = pdf.make_indirect(Array([d]))
    assert ceq(Array([b, c]), Array([b, c]))


# --- Scenario 7 / 26: self-referential cycles must return False, not --
# --- hang. Python has no native stack-safety net for this like the   --
# --- qpdf C++ implementation apparently assumes, so this gets an     --
# --- explicit timeout guard rather than trusting the assertion alone.-


@pytest.mark.timeout(5)
def test_self_referential_arrays_are_not_equivalent(pdf):
    a1 = pdf.make_indirect(pikepdf.Object.parse(b"[]"))
    a1.append(a1)
    a2 = pdf.make_indirect(pikepdf.Object.parse(b"[]"))
    a2.append(a2)
    assert not ceq(a1, a2)
    assert not ceq(a1, a2)  # idempotency: repeated calls still terminate


@pytest.mark.timeout(5)
def test_single_self_loop_array_vs_itself_terminates(pdf):
    a1 = pdf.make_indirect(pikepdf.Object.parse(b"[]"))
    a1.append(a1)
    # Even comparing the cyclic object to itself must terminate (not rely
    # on `obj1 is obj2` short-circuiting, since our port has no such
    # identity fast-path the way qpdf's `obj == other.obj` does).
    result = ceq(a1, a1)
    assert result in (True, False)


# --- Scenario 9/10: stream content match / mismatch ---------------------


def test_stream_content_match(pdf):
    s1 = pdf.make_indirect(pikepdf.Stream(pdf, b"Stream data"))
    s2 = pdf.make_indirect(pikepdf.Stream(pdf, b"Stream data"))
    assert ceq(s1, s2)


def test_stream_content_mismatch(pdf):
    s1 = pdf.make_indirect(pikepdf.Stream(pdf, b"Data A"))
    s2 = pdf.make_indirect(pikepdf.Stream(pdf, b"Data B"))
    assert not ceq(s1, s2)
    assert not ceq(s2, s1)


# --- Scenario 11: stream dictionary differences --------------------------


def test_stream_dict_differences(pdf):
    s1 = pdf.make_indirect(pikepdf.Stream(pdf, b"same"))
    s2 = pdf.make_indirect(pikepdf.Stream(pdf, b"same"))
    s2.Extra = Name("/Value")
    assert not ceq(s1, s2)
    assert not ceq(s2, s1)


# --- Scenario 12: J.3.6 absent-key-vs-null. Adapted: pikepdf's own -----
# --- parser already collapses an explicit-null dictionary value away  --
# --- at load time (verified separately against installed pikepdf     --
# --- 10.5.1), so the interesting case here is just confirming that   --
# --- invariant holds, and that a key holding any *other* value is    --
# --- correctly NOT treated as equivalent to that key being absent.   --


def test_dict_with_only_a_null_valued_key_is_effectively_empty():
    # `<< /Present null >>` -- per Annex J this should be equivalent to
    # an empty dict, and pikepdf's parser actually drops the key
    # entirely, landing on the same result by a different mechanism.
    d0 = Dictionary()
    d1 = pikepdf.Object.parse(b"<< /Present null >>")
    assert ceq(d0, d1)
    assert ceq(d1, d0)


def test_absent_key_not_equivalent_to_key_with_real_value():
    d0 = Dictionary()
    d2 = pikepdf.Object.parse(b"<< /Present << >> >>")
    d3 = pikepdf.Object.parse(b"<< /Present [] >>")
    assert not ceq(d0, d2)
    assert not ceq(d2, d0)
    assert not ceq(d0, d3)
    assert not ceq(d3, d0)
    assert not ceq(d2, d3)


# --- Scenario 13: string syntax, hex vs literal --------------------------


def test_string_hex_vs_literal_syntax():
    literal = pikepdf.Object.parse(b"(A)")
    hexstr = pikepdf.Object.parse(b"<41>")
    assert ceq(literal, hexstr)
    assert ceq(hexstr, literal)


# --- Scenario 14: name #xx-escape decoding -------------------------------


def test_name_hash_escape_decoding():
    name1 = pikepdf.Object.parse(b"/Name")
    name2 = pikepdf.Object.parse(b"/Na#6d#65")
    assert ceq(name1, name2)
    assert ceq(name2, name1)


# --- Scenario 15 (partial): octal string escape, numeric edge values ----


def test_octal_string_escape_matches_literal():
    lit_a = pikepdf.Object.parse(b"(A)")
    oct_a = pikepdf.Object.parse(rb"(\101)")
    assert ceq(lit_a, oct_a)
    assert ceq(oct_a, lit_a)


def test_zero_int_equals_negative_zero_real():
    assert ceq(0, pikepdf.Object.parse(b"-0.0"))


def test_real_precision_padding_equivalent():
    r1 = pikepdf.Object.parse(b"12.345")
    r2 = pikepdf.Object.parse(b"12.345000000000000")
    assert ceq(r1, r2)


# --- Scenario 16: nested containers --------------------------------------


def test_nested_containers():
    d1 = Dictionary(K=pikepdf.Object.parse(b"[5]"))
    d2 = Dictionary(K=pikepdf.Object.parse(b"[5]"))
    assert ceq(d1, d2)


# --- Scenario 17: boolean/null mismatches --------------------------------


def test_boolean_and_null_mismatches():
    b_true = True
    b_false = False
    null = None
    one = 1
    zero = 0
    assert ceq(null, None)
    assert not ceq(b_true, b_false)
    assert not ceq(b_true, null)
    assert not ceq(b_true, one)
    assert not ceq(b_true, zero)
    assert not ceq(b_false, null)
    assert not ceq(b_false, one)
    assert not ceq(b_false, zero)
    assert not ceq(null, one)
    assert not ceq(null, zero)
    assert not ceq(one, zero)


# --- Scenario 18: stream semantics are decode-blind (core deviation) ----


def test_streams_compare_raw_bytes_not_decoded_content(pdf):
    s1 = pdf.make_indirect(pikepdf.Stream(pdf, b"test stream"))
    s2 = pdf.make_indirect(pikepdf.Stream(pdf, b"DIFFERENT_RAW_BYTES"))
    s3 = pdf.make_indirect(pikepdf.Stream(pdf, b"test stream"))
    s2.Filter = Name("/FlateDecode")
    s3.Filter = Name("/FlateDecode")
    assert not ceq(s1, s2)
    assert not ceq(s2, s1)
    assert not ceq(s1, s3)  # same raw bytes as s1 but /Filter dict differs
    assert not ceq(s3, s1)
    assert not ceq(s2, s3)
    assert not ceq(s3, s2)


# --- Scenario 19: dict value type mismatch (int vs string) --------------


def test_dict_value_type_mismatch():
    d1 = pikepdf.Object.parse(b"<< /Key 1 >>")
    d2 = pikepdf.Object.parse(b"<< /Key (1) >>")
    assert not ceq(d1, d2)
    assert not ceq(d2, d1)


# --- Scenario 20 / 30: mixed direct vs indirect nesting ------------------


def test_mixed_direct_vs_indirect_nesting(pdf):
    direct_arr = Array([7])
    indirect_arr = Array([pdf.make_indirect(pikepdf.Object.parse(b"7"))])
    assert ceq(direct_arr, indirect_arr)


def test_nested_diamond_direct_and_indirect(pdf):
    a1 = Array(
        [
            pdf.make_indirect(pikepdf.Object.parse(b"[42]")),
            pdf.make_indirect(pikepdf.Object.parse(b"[42]")),
        ]
    )
    a2 = Array(
        [
            pdf.make_indirect(pikepdf.Object.parse(b"[42]")),
            pdf.make_indirect(pikepdf.Object.parse(b"[42]")),
        ]
    )
    assert ceq(a1, a2)


# --- Scenario 21: dict subset vs superset --------------------------------


def test_dict_subset_vs_superset_not_equivalent():
    d1 = pikepdf.Object.parse(b"<< /A 1 /B 2 >>")
    d2 = pikepdf.Object.parse(b"<< /A 1 >>")
    assert not ceq(d1, d2)
    assert not ceq(d2, d1)


# --- Scenario 22: decoded content matching, raw bytes different ---------


def test_semantically_equivalent_decoded_content_still_not_equivalent(pdf):
    s1 = pdf.make_indirect(pikepdf.Stream(pdf, b"Hello World"))
    s2 = pdf.make_indirect(pikepdf.Stream(pdf, b"HELLO WORLD RAW"))
    s2.Filter = Name("/FlateDecode")
    s2.DecodeParms = Dictionary()
    assert not ceq(s1, s2)
    assert not ceq(s2, s1)


# --- Scenario 24 / 40: recursion depth limit / stack safety -------------


def _make_nested_array(levels: int) -> pikepdf.Object:
    root = pikepdf.Object.parse(b"1")
    for _ in range(levels):
        arr = pikepdf.Object.parse(b"[]")
        arr.append(root)
        root = arr
    return root


def test_depth_limit_default_fails_deep_structure():
    a1 = _make_nested_array(200)
    a2 = _make_nested_array(200)
    assert not ceq(a1, a2)  # default depth=10 is exhausted well before 200
    assert ceq(a1, a2, depth=500)  # explicit sufficient depth passes


def test_depth_limit_exact_boundary_small_scale():
    # Small-scale version of qpdf's exact-boundary scenario (their C++
    # test uses 500/501 levels). That doesn't port directly -- see the
    # RecursionError test below for why -- so this checks the same
    # off-by-one boundary behavior at a depth safely away from Python's
    # native recursion limit.
    levels = 12
    h1 = _make_nested_array(levels)
    h2 = _make_nested_array(levels)
    assert not ceq(h1, h2, depth=levels - 1)
    assert ceq(h1, h2, depth=levels)
    assert ceq(h1, h2, depth=levels + 1)


def test_recursion_error_from_deep_structure_returns_false_not_crash():
    # qpdf's C++ test asserts exact pass/fail at 500 vs 501 levels of
    # nesting, relying on a native call stack that comfortably fits
    # hundreds of recursive frames. Python's default recursion limit
    # (sys.getrecursionlimit(), 1000) is much tighter, and each level of
    # PDF-object nesting costs several Python stack frames here (the
    # function call itself, plus the `all()` builtin and the generator
    # expression driving it) -- verified directly: 500 levels of nesting
    # compared with depth=500 already raises RecursionError in a bare
    # script, and the exact ceiling shifts lower still once there's
    # additional caller stack already in use (e.g. running under
    # pytest), so no fixed depth reliably reproduces qpdf's 500/501
    # boundary here. What's actually load-bearing for this port is that
    # a `depth` value deep enough to blow the Python stack fails safe
    # (returns False) rather than crashing the caller -- that's what
    # this checks, with a depth chosen deliberately larger than any
    # plausible real PDF structure or safe Python recursion budget.
    levels = 900
    h1 = _make_nested_array(levels)
    h2 = _make_nested_array(levels)
    # Must not raise -- check_object_equivalence catches RecursionError
    # internally and treats it as "not equivalent".
    result = ceq(h1, h2, depth=100_000)
    assert result is False


# --- Scenario 25: wide graph fan-out (many siblings, not deep nesting) --


def test_wide_array_fanout():
    a1 = pikepdf.Object.parse(b"[]")
    a2 = pikepdf.Object.parse(b"[]")
    a3 = pikepdf.Object.parse(b"[]")
    for i in range(200):
        a1.append(i)
        a2.append(i)
        a3.append(i)
    a3.append(200)
    assert ceq(a1, a2)
    assert not ceq(a1, a3)
    assert not ceq(a3, a1)


# --- Scenario 27: nested dict reuse / shared indirect object ------------


def test_shared_indirect_object_in_multiple_dicts(pdf):
    shared_array = pdf.make_indirect(pikepdf.Object.parse(b"[42 99]"))
    dict1 = Dictionary(Unique1=Name("/A"), Shared=shared_array)
    dict2 = Dictionary(Unique1=Name("/A"), Shared=shared_array)
    dict3 = Dictionary(Unique1=Name("/B"), Shared=shared_array)
    assert ceq(dict1, dict2)
    assert not ceq(dict1, dict3)
    assert not ceq(dict3, dict1)


# --- Scenario 28: shared indirect leaves reached via two paths ----------


def test_shared_indirect_leaves_via_two_paths(pdf):
    leaf1 = pdf.make_indirect(pikepdf.Object.parse(b"[1]"))
    leaf2 = pdf.make_indirect(pikepdf.Object.parse(b"[2]"))
    mid1 = pdf.make_indirect(Dictionary())
    mid1.Leaf1 = leaf1
    mid1.Leaf2 = leaf2
    mid2 = pdf.make_indirect(Dictionary())
    mid2.Leaf1 = leaf1
    mid2.Leaf2 = leaf2
    assert ceq(Array([mid1, mid2]), Array([mid1, mid2]))


# --- Scenario 31/32: the actual dedup use case -- images sharing an -----
# --- SMask, and images with two distinct-but-identical SMasks -----------


def test_images_sharing_one_smask_are_equivalent(pdf):
    smask = pdf.make_indirect(pikepdf.Stream(pdf, b"mask data"))
    img1 = pdf.make_indirect(pikepdf.Stream(pdf, b"image1 data"))
    img1.SMask = smask
    img2 = pdf.make_indirect(pikepdf.Stream(pdf, b"image1 data"))
    img2.SMask = smask
    assert ceq(img1, img2)


def test_images_with_distinct_but_identical_smasks_are_equivalent(pdf):
    smask1 = pdf.make_indirect(pikepdf.Stream(pdf, b"mask data"))
    smask2 = pdf.make_indirect(pikepdf.Stream(pdf, b"mask data"))
    img1 = pdf.make_indirect(pikepdf.Stream(pdf, b"image1 data"))
    img1.SMask = smask1
    img2 = pdf.make_indirect(pikepdf.Stream(pdf, b"image1 data"))
    img2.SMask = smask2
    assert ceq(img1, img2)


def test_images_with_different_smasks_are_not_equivalent(pdf):
    smask1 = pdf.make_indirect(pikepdf.Stream(pdf, b"mask data A"))
    smask2 = pdf.make_indirect(pikepdf.Stream(pdf, b"mask data B"))
    img1 = pdf.make_indirect(pikepdf.Stream(pdf, b"image1 data"))
    img1.SMask = smask1
    img2 = pdf.make_indirect(pikepdf.Stream(pdf, b"image1 data"))
    img2.SMask = smask2
    assert not ceq(img1, img2)


# --- Not ported, and why -------------------------------------------------
#
# Scenario 8 (cross-QPDF-instance comparison): tests that qpdf's
# equivalent_to works across two separate QPDF objects. pikepdf's Object
# is likewise not restricted to a single owning Pdf for direct-value
# comparison, but constructing two independent pikepdf.Pdf() instances
# and moving/copying objects between them for this test adds ownership
# complexity (copy_foreign) that isn't representative of how
# deduplicate_images actually calls this function (always within one
# open Pdf), so it's skipped as low-value here.
#
# Scenario 34/35/36/37/38/39 (uninitialized handle vs null, broken
# references to missing objects): these test qpdf's C++-level
# "uninitialized QPDFObjectHandle" state and its `getObject(id, gen)` on
# nonexistent objects, both of which resolve to a null-like state that
# has no equivalent reachable through pikepdf's Python API -- there is
# no way to construct a pikepdf "handle to nothing" the way qpdf's
# default-constructed QPDFObjectHandle works.
#
# Scenario 41 (sparse array threshold): tests qpdf's internal
# >100-nulls sparse-array storage optimization, an implementation detail
# of QPDF_Array not exposed through pikepdf's Array interface.
#
# Scenario 42 (stale ot_reference post-replaceObject): tests a specific
# qpdf C++ object-lifecycle quirk (a previously-held handle silently
# becoming a reference wrapper after replaceObject() moves the
# underlying object). pikepdf's object model doesn't expose this
# reference/replace mechanism the same way, so there's no direct port.
