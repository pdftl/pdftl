# tests/utils/test_stream_dedup.py
"""Direct tests for the generic stream-dedup mechanics (stream_length,
rewrite_references, find_duplicates_of, build_replacement_map,
apply_replacements), independent of any particular candidate-discovery
scheme (images vs fonts). Feature-specific integration coverage lives
in test_dedupe_images_core.py and test_dedupe_fonts_core.py.
"""

from __future__ import annotations

import pytest
import pikepdf
from pikepdf import Array, Dictionary

from pdftl.utils.stream_dedup import (
    apply_replacements,
    build_replacement_map,
    find_duplicates_of,
    rewrite_references,
    stream_length,
)


@pytest.fixture
def pdf():
    p = pikepdf.Pdf.new()
    yield p
    p.close()


def test_stream_length_matches_declared_length(pdf):
    data = b"12345678901234567890"
    stream = pdf.make_indirect(pikepdf.Stream(pdf, data))
    assert stream_length(stream) == 20


def test_stream_length_falls_back_to_raw_bytes_when_length_not_yet_set(pdf):
    # Regression test for a real failure found running the images/fonts
    # suites: a Stream built via pikepdf.Stream(pdf, data, **kwargs) --
    # as opposed to pdf.make_stream(data) -- has no /Length key at all
    # until the Pdf is saved, so stream_length must not assume /Length
    # is always present the moment a Stream object exists.
    data = b"not yet saved, no /Length key set"
    stream = pdf.make_indirect(pikepdf.Stream(pdf, data, Type=pikepdf.Name("/XObject")))
    assert stream_length(stream) == len(data)


def test_build_replacement_map_groups_equivalent_streams(pdf):
    data = b"duplicate content " * 5
    s1 = pdf.make_indirect(pikepdf.Stream(pdf, data))
    s2 = pdf.make_indirect(pikepdf.Stream(pdf, data))
    s3 = pdf.make_indirect(pikepdf.Stream(pdf, b"different content " * 5))
    candidates = sorted([s1, s2, s3], key=stream_length)

    replacements, bytes_saved = build_replacement_map(candidates, threshold=0)

    assert len(replacements) == 1
    assert bytes_saved == len(data)


def test_build_replacement_map_respects_threshold(pdf):
    data = b"small"
    s1 = pdf.make_indirect(pikepdf.Stream(pdf, data))
    s2 = pdf.make_indirect(pikepdf.Stream(pdf, data))

    replacements, bytes_saved = build_replacement_map([s1, s2], threshold=1000)

    assert replacements == {}
    assert bytes_saved == 0


def test_build_replacement_map_empty_candidates_returns_empty(pdf):
    replacements, bytes_saved = build_replacement_map([], threshold=0)
    assert replacements == {}
    assert bytes_saved == 0


def test_find_duplicates_of_stops_at_first_length_mismatch(pdf):
    master = pdf.make_indirect(pikepdf.Stream(pdf, b"AAAA"))
    same_len_match = pdf.make_indirect(pikepdf.Stream(pdf, b"AAAA"))
    longer = pdf.make_indirect(pikepdf.Stream(pdf, b"AAAAAAAA"))
    replacements: dict = {}

    bytes_saved = find_duplicates_of(master, 4, [same_len_match, longer], replacements)

    assert bytes_saved == 4
    assert same_len_match.objgen in replacements
    assert longer.objgen not in replacements


def test_find_duplicates_of_skips_already_claimed_candidates(pdf):
    master = pdf.make_indirect(pikepdf.Stream(pdf, b"AAAA"))
    other_master = pdf.make_indirect(pikepdf.Stream(pdf, b"BBBB"))
    dup = pdf.make_indirect(pikepdf.Stream(pdf, b"AAAA"))
    replacements = {dup.objgen: other_master}

    bytes_saved = find_duplicates_of(master, 4, [dup], replacements)

    assert bytes_saved == 0
    assert replacements[dup.objgen] is other_master  # left untouched


def test_rewrite_references_replaces_dict_value(pdf):
    old = pdf.make_indirect(pikepdf.Stream(pdf, b"old"))
    new = pdf.make_indirect(pikepdf.Stream(pdf, b"new"))
    holder = pdf.make_indirect(Dictionary(Ref=old))

    rewrite_references(holder, {old.objgen: new})

    assert holder.Ref.objgen == new.objgen


def test_rewrite_references_replaces_array_element(pdf):
    old = pdf.make_indirect(pikepdf.Stream(pdf, b"old"))
    new = pdf.make_indirect(pikepdf.Stream(pdf, b"new"))
    holder = pdf.make_indirect(Array([old]))

    rewrite_references(holder, {old.objgen: new})

    assert holder[0].objgen == new.objgen


def test_rewrite_references_recurses_into_direct_nested_dict(pdf):
    old = pdf.make_indirect(pikepdf.Stream(pdf, b"old"))
    new = pdf.make_indirect(pikepdf.Stream(pdf, b"new"))
    nested = Dictionary(Ref=old)  # direct, not made indirect
    holder = pdf.make_indirect(Dictionary(Nested=nested))

    rewrite_references(holder, {old.objgen: new})

    assert holder.Nested.Ref.objgen == new.objgen


def test_rewrite_references_no_match_leaves_untouched(pdf):
    unrelated = pdf.make_indirect(pikepdf.Stream(pdf, b"leave me alone"))
    holder = pdf.make_indirect(Dictionary(Ref=unrelated))

    rewrite_references(holder, {})

    assert holder.Ref.objgen == unrelated.objgen


def test_rewrite_references_on_bare_scalar_is_a_noop(pdf):
    # apply_replacements' top-level loop calls rewrite_references on
    # every indirect object in the document, including a bare indirect
    # scalar (neither Dictionary/Stream nor Array) -- must not raise,
    # and obviously has nothing to rewrite inside it.
    scalar = pdf.make_indirect(pikepdf.String("just a string"))
    rewrite_references(scalar, {})  # must not raise
    assert str(scalar) == "just a string"


def test_apply_replacements_rewrites_across_whole_document(pdf):
    old = pdf.make_indirect(pikepdf.Stream(pdf, b"old"))
    new = pdf.make_indirect(pikepdf.Stream(pdf, b"new"))
    holder1 = pdf.make_indirect(Dictionary(Ref=old))
    holder2 = pdf.make_indirect(Array([old]))

    apply_replacements(pdf, {old.objgen: new})

    assert holder1.Ref.objgen == new.objgen
    assert holder2[0].objgen == new.objgen


def test_apply_replacements_skips_non_object_indirect_entries(pdf):
    pdf.make_indirect(42)  # a bare int -- no .objgen, must not raise
    old = pdf.make_indirect(pikepdf.Stream(pdf, b"old"))
    new = pdf.make_indirect(pikepdf.Stream(pdf, b"new"))
    holder = pdf.make_indirect(Dictionary(Ref=old))

    apply_replacements(pdf, {old.objgen: new})  # must not raise

    assert holder.Ref.objgen == new.objgen
