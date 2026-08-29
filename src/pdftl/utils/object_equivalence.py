# src/pdftl/utils/object_equivalence.py
"""Structural object-equivalence check per PDF Annex J (ISO 32000-2),
ported from qpdf's BaseHandle::equivalent_to (qpdf PR #1665, itself
factored out of PR #1663's image-deduplication feature). Used by
deduplicate_images to decide whether two image XObjects are the "same"
image even when they're distinct PDF objects.
"""

from __future__ import annotations

from decimal import Decimal

# int and Decimal are pikepdf's Python-side representations of a PDF
# integer and real respectively (see pikepdf.Object.parse boxing rules).
# bool must be excluded/checked separately since bool is a subclass of
# int in Python.
_NUMERIC = (int, Decimal)


def check_object_equivalence(obj1, obj2, depth: int = 10) -> bool:
    """Public entry point. Thin wrapper around `_check_object_equivalence`
    that also guards against `RecursionError`: unlike qpdf's C++
    implementation (which has a much larger native stack to work with),
    a Python `RecursionError` is a real risk here for a large `depth`
    passed against a genuinely deep structure -- verified directly: 500
    levels of nesting compared with `depth=500` raises `RecursionError`
    against Python's default `sys.getrecursionlimit()` of 1000, well
    before qpdf's C++ equivalent would give up. Rather than let that
    propagate as a crash, it's treated the same way an exhausted `depth`
    budget already is: "not equivalent". This only needs to wrap the
    outermost call -- the recursive descent below calls the private
    helper directly, so the exception unwinds cleanly to exactly one
    handler regardless of how deep it went.
    """
    try:
        return _check_object_equivalence(obj1, obj2, depth)
    except RecursionError:
        return False


def _both_bool(obj1, obj2) -> bool:
    return isinstance(obj1, bool) and isinstance(obj2, bool) and obj1 == obj2


def _either_bool(obj1, obj2) -> bool:
    return isinstance(obj1, bool) or isinstance(obj2, bool)


def _both_numeric(obj1, obj2) -> bool:
    return isinstance(obj1, _NUMERIC) and isinstance(obj2, _NUMERIC)


def _numeric_equivalent(obj1, obj2) -> bool:
    return float(obj1) == float(obj2)


def _names_equivalent(obj1, obj2) -> bool:
    import pikepdf

    return isinstance(obj1, pikepdf.Name) and isinstance(obj2, pikepdf.Name) and obj1 == obj2


def _strings_equivalent(obj1, obj2) -> bool:
    import pikepdf

    return (
        isinstance(obj1, pikepdf.String)
        and isinstance(obj2, pikepdf.String)
        and bytes(obj1) == bytes(obj2)
    )


def _arrays_equivalent(obj1, obj2, depth: int) -> bool | None:
    """None means "not applicable" (not both arrays) -- distinct from a
    real False result -- so the caller can tell "try the next type pair"
    from "these are arrays and they don't match"."""
    import pikepdf

    if not (isinstance(obj1, pikepdf.Array) and isinstance(obj2, pikepdf.Array)):
        return None
    if len(obj1) != len(obj2):
        return False
    return all(_check_object_equivalence(a, b, depth - 1) for a, b in zip(obj1, obj2))


def _streams_equivalent(obj1, obj2, depth: int) -> bool | None:
    import pikepdf

    if not (isinstance(obj1, pikepdf.Stream) and isinstance(obj2, pikepdf.Stream)):
        return None
    if not _dict_equivalent(obj1, obj2, depth - 1):
        return False
    return obj1.read_raw_bytes() == obj2.read_raw_bytes()


def _dicts_equivalent(obj1, obj2, depth: int) -> bool | None:
    import pikepdf

    if not (isinstance(obj1, pikepdf.Dictionary) and isinstance(obj2, pikepdf.Dictionary)):
        return None
    return _dict_equivalent(obj1, obj2, depth)


def _check_object_equivalence(obj1, obj2, depth: int) -> bool:
    """True if `obj1` and `obj2` are structurally equivalent under Annex
    J's rules: numeric int/real are cross-comparable by value, dictionary
    comparison ignores key order, arrays are order-sensitive, and streams
    compare their dictionary plus raw (undecoded) bytes -- matching a
    deliberate deviation qpdf's own implementation notes: filtered/
    decoded content is not compared.

    Note on nulls: qpdf's Annex J spec treats an absent dictionary key as
    equivalent to one holding an explicit PDF null (J.3.6). This doesn't
    need special-casing here: pikepdf's own parser already collapses
    explicit-null dictionary *values* away entirely at load time -- a
    dictionary can never actually contain a key whose value is null, only
    keys that are simply absent (verified against installed pikepdf
    10.5.1: parsing `<< /A 1 /N null >>` yields a dict with only `/A`).
    So dict-key comparison here is just `.keys()` set equality. A `null`
    *array element*, however, does survive as a literal Python `None`
    (`[1 null 3]` parses to `[1, None, 3]`), so `None` is handled directly
    as one of the compared types below.

    `depth` bounds recursion. A PDF object graph can be cyclic (a
    dictionary that, through some chain, contains itself); rather than
    doing real cycle detection, a bounded depth just treats a
    still-recursing-past-`depth` comparison as "not equivalent". The
    default of 10 comfortably covers realistic structures (an image
    with an /SMask, a form field with an /AP), but pass a higher value
    for deliberately deep fixtures.

    Indirect references need no special handling: pikepdf transparently
    resolves them to their target's Python type on attribute/item access,
    so `obj1`/`obj2` arriving here are always already-resolved scalars or
    pikepdf.Array/Dictionary/Stream/Name/String objects -- never a raw
    "reference" type. Dispatch below uses `isinstance` against pikepdf's
    public classes, per pikepdf's own recommendation over the low-level
    (and underscore-prefixed) `Object._type_code`.

    Dispatch is delegated to small per-type-pair helpers above, each
    returning `None` for "not this type pair, try the next one" (except
    the bool/numeric/name/string checks, which are cheap enough to fold
    their own applicability test into a single bool return).
    """
    if depth < 0:
        return False

    if obj1 is None or obj2 is None:
        return obj1 is None and obj2 is None

    if _either_bool(obj1, obj2):
        return _both_bool(obj1, obj2)

    if _both_numeric(obj1, obj2):
        return _numeric_equivalent(obj1, obj2)

    if _names_equivalent(obj1, obj2):
        return True

    if _strings_equivalent(obj1, obj2):
        return True

    result = _arrays_equivalent(obj1, obj2, depth)
    if result is not None:
        return result

    result = _streams_equivalent(obj1, obj2, depth)
    if result is not None:
        return result

    result = _dicts_equivalent(obj1, obj2, depth)
    if result is not None:
        return result

    # Mismatched types (that aren't the numeric int/Decimal cross-case
    # above), or one of /Operator / inline-image pseudo-objects, which
    # only ever appear inside a parsed content-stream instruction list
    # and never as a dictionary or array value in a well-formed document.
    return False


def _dict_equivalent(d1, d2, depth: int) -> bool:
    """Two dictionaries (or a stream's dictionary portion) are
    equivalent if they have the same set of keys and each shared key's
    value is equivalent. See the null note in `check_object_equivalence`
    for why no explicit absent-vs-null handling is needed here.
    """
    keys1 = set(d1.keys())
    keys2 = set(d2.keys())
    if keys1 != keys2:
        return False
    return all(_check_object_equivalence(d1[k], d2[k], depth - 1) for k in keys1)
