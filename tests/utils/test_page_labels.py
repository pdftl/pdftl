# tests/utils/test_page_labels.py

import pytest
import pikepdf

from pdftl.utils.page_labels import (
    get_all_page_label_dicts,
    remap_page_labels,
    _build_dst_page_dicts,
    _fill_inserted_plain,
    _fill_inserted_inherited,
    _compress_rules,
    _write_page_labels,
)


def make_pdf(n_pages):
    """Build a fresh in-memory pdf with n_pages blank pages."""
    pdf = pikepdf.new()
    for _ in range(n_pages):
        pdf.add_blank_page()
    return pdf


def set_labels(pdf, rules: dict):
    """Write a raw Nums-based /PageLabels dict directly onto pdf.Root, mirroring
    _write_page_labels's own flat-leaf construction, so tests don't depend on
    the function under test to set up their fixtures."""
    nums = []
    for k in sorted(rules):
        nums.append(k)
        nums.append(rules[k])
    pdf.Root.PageLabels = pdf.make_indirect(pikepdf.Dictionary(Nums=pikepdf.Array(nums)))


# ---------------------------------------------------------------------------
# get_all_page_label_dicts / _flatten_rules
# ---------------------------------------------------------------------------


class TestGetAllPageLabelDicts:
    def test_no_page_labels_key(self):
        pdf = make_pdf(3)
        assert get_all_page_label_dicts(pdf) == [None, None, None]

    def test_simple_decimal_from_zero(self):
        pdf = make_pdf(3)
        set_labels(pdf, {0: pikepdf.Dictionary(St=1)})
        result = get_all_page_label_dicts(pdf)
        assert result == [{"St": 1}, {"St": 2}, {"St": 3}]

    def test_gap_before_first_rule_yields_none(self):
        pdf = make_pdf(3)
        # first rule starts at page index 1, so page 0 has no coverage -> None
        set_labels(pdf, {1: pikepdf.Dictionary(St=1)})
        result = get_all_page_label_dicts(pdf)
        assert result[0] is None
        assert result[1] == {"St": 1}
        assert result[2] == {"St": 2}

    def test_prefix_and_style_are_carried(self):
        pdf = make_pdf(2)
        set_labels(
            pdf,
            {0: pikepdf.Dictionary(St=5, P=pikepdf.String("App-"), S=pikepdf.Name("/D"))},
        )
        result = get_all_page_label_dicts(pdf)
        assert result[0]["St"] == 5
        assert result[0]["P"] == "App-"
        assert result[0]["S"] == pikepdf.Name("/D")
        assert result[1]["St"] == 6

    def test_multiple_rules_reset_start(self):
        pdf = make_pdf(4)
        set_labels(
            pdf,
            {
                0: pikepdf.Dictionary(St=1, S=pikepdf.Name("/r")),
                2: pikepdf.Dictionary(St=1, S=pikepdf.Name("/D")),
            },
        )
        result = get_all_page_label_dicts(pdf)
        assert result[0] == {"St": 1, "S": pikepdf.Name("/r")}
        assert result[1] == {"St": 2, "S": pikepdf.Name("/r")}
        assert result[2] == {"St": 1, "S": pikepdf.Name("/D")}
        assert result[3] == {"St": 2, "S": pikepdf.Name("/D")}

    def test_corrupt_page_labels_falls_back_to_none(self):
        pdf = make_pdf(2)
        # A /PageLabels dict with no /Nums and no /Kids is not a valid NumberTree
        # root; pikepdf.NumberTree() raises when it can't find either.
        pdf.Root.PageLabels = pikepdf.Dictionary()
        result = get_all_page_label_dicts(pdf)
        assert result == [None, None]

    def test_empty_pdf_no_labels(self):
        pdf = make_pdf(0)
        assert get_all_page_label_dicts(pdf) == []


# ---------------------------------------------------------------------------
# remap_page_labels - guard clause
# ---------------------------------------------------------------------------


class TestRemapPageLabelsGuard:
    def test_raises_when_src_is_dst_and_indices_stale(self):
        pdf = make_pdf(3)
        set_labels(pdf, {0: pikepdf.Dictionary(St=1)})
        # Simulate calling this *after* a page was already removed: index 5
        # is out of range for the current (still 3-page) pdf.
        with pytest.raises(ValueError, match="out of range"):
            remap_page_labels(pdf, pdf, [0, 1, 5])

    def test_does_not_raise_when_src_is_not_dst(self):
        src = make_pdf(2)
        dst = make_pdf(2)
        set_labels(src, {0: pikepdf.Dictionary(St=1)})
        # index 99 would be invalid for src, but the guard only applies
        # when src_pdf is dst_pdf, so this must not raise here.
        remap_page_labels(src, dst, [0, 99])
        assert "/PageLabels" in dst.Root

    def test_none_entries_are_ignored_by_guard(self):
        pdf = make_pdf(2)
        set_labels(pdf, {0: pikepdf.Dictionary(St=1)})
        # None (inserted-page marker) must never trip the int-range check.
        remap_page_labels(pdf, pdf, [0, None, 1])


# ---------------------------------------------------------------------------
# remap_page_labels - no-labels short circuit
# ---------------------------------------------------------------------------


class TestRemapPageLabelsNoSourceLabels:
    def test_source_without_labels_writes_nothing(self):
        src = make_pdf(2)
        dst = make_pdf(2)
        remap_page_labels(src, dst, [0, 1])
        assert "/PageLabels" not in dst.Root

    def test_source_without_labels_clears_existing_dst_labels(self):
        src = make_pdf(2)
        dst = make_pdf(2)
        set_labels(dst, {0: pikepdf.Dictionary(St=1)})
        remap_page_labels(src, dst, [0, 1])
        assert "/PageLabels" not in dst.Root


# ---------------------------------------------------------------------------
# remap_page_labels - end to end scenarios
# ---------------------------------------------------------------------------


class TestRemapPageLabelsEndToEnd:
    def test_deletion_preserves_surviving_labels(self):
        pdf = make_pdf(4)
        set_labels(pdf, {0: pikepdf.Dictionary(St=1)})  # 1,2,3,4
        # delete old index 1 ("2") -> survivors are old indices 0,2,3
        remap_page_labels(pdf, pdf, [0, 2, 3])
        del pdf.pages[1]  # actually mutate pages to match new_to_old
        result = get_all_page_label_dicts(pdf)
        assert [d["St"] for d in result] == [1, 3, 4]

    def test_insert_default_is_plain_sequential(self):
        pdf = make_pdf(2)
        set_labels(pdf, {0: pikepdf.Dictionary(St=1, S=pikepdf.Name("/r"))})  # i, ii
        # insert a blank page before old index 1
        remap_page_labels(pdf, pdf, [0, None, 1])
        pdf.pages.insert(1, pdf.pages[0])  # actually mutate pages to match new_to_old
        result = get_all_page_label_dicts(pdf)
        assert result[0] == {"St": 1, "S": pikepdf.Name("/r")}
        # plain default: no /S key -> decimal, St = new_idx + 1 = 2
        assert result[1] == {"St": 2}
        assert result[2] == {"St": 2, "S": pikepdf.Name("/r")}

    def test_insert_with_inherit_style_mid_sequence(self):
        pdf = make_pdf(2)
        set_labels(pdf, {0: pikepdf.Dictionary(St=1)})  # 1, 2
        remap_page_labels(pdf, pdf, [0, None, 1], inherit_style=True)
        pdf.pages.insert(1, pdf.pages[0])  # actually mutate pages to match new_to_old
        result = get_all_page_label_dicts(pdf)
        # Surviving old page 1 (originally "2") keeps its own logical number;
        # the inserted page in between continues the sequence from its
        # predecessor ("1" -> "2"), producing a duplicate-looking "2, 2" -
        # this is expected: remap_page_labels guarantees surviving pages
        # never change their own number, even if inserts around them do.
        assert [d["St"] for d in result] == [1, 2, 2]

    def test_insert_with_inherit_style_at_very_start_borrows_following(self):
        pdf = make_pdf(2)
        set_labels(pdf, {0: pikepdf.Dictionary(St=5, S=pikepdf.Name("/D"))})
        remap_page_labels(pdf, pdf, [None, 0, 1], inherit_style=True)
        result = get_all_page_label_dicts(pdf)
        assert result[0] == {"St": 1, "S": pikepdf.Name("/D")}

    def test_insert_with_inherit_style_at_very_start_no_following_labels(self):
        # First entry is inserted, and no later entry maps to a labeled
        # source page at all -> nxt stays None -> falls back to St=new_idx+1.
        pdf = make_pdf(1)
        set_labels(pdf, {0: pikepdf.Dictionary(St=1)})
        src_dicts = get_all_page_label_dicts(pdf)
        dst = _build_dst_page_dicts(src_dicts, [None], inherit_style=True)
        assert dst[0] == {"St": 1}


# ---------------------------------------------------------------------------
# _build_dst_page_dicts / _fill_inserted_plain / _fill_inserted_inherited
# (direct unit tests, isolated from PDF I/O)
# ---------------------------------------------------------------------------


class TestBuildDstPageDicts:
    def test_survivor_copies_source_dict(self):
        src = [{"St": 1}, {"St": 2}]
        dst = _build_dst_page_dicts(src, [1, 0])
        assert dst == [{"St": 2}, {"St": 1}]

    def test_out_of_range_int_treated_as_inserted(self):
        src = [{"St": 1}]
        dst = _build_dst_page_dicts(src, [5])
        assert dst == [{"St": 1}]  # plain default fallback, new_idx=0 -> St=1

    def test_non_int_item_treated_as_inserted(self):
        src = [{"St": 1}]
        dst = _build_dst_page_dicts(src, ["not-an-int"])
        assert dst == [{"St": 1}]


class TestFillInsertedPlain:
    def test_skips_survivors_fills_inserted(self):
        dst = [{"St": 9}, None, None]
        _fill_inserted_plain(dst, [0, "x", "y"], [{"St": 9}])
        assert dst == [{"St": 9}, {"St": 2}, {"St": 3}]


class TestFillInsertedInherited:
    def test_inherits_prefix_and_style_from_prev(self):
        dst = [{"St": 5, "P": "Ch1-", "S": pikepdf.Name("/D")}, None]
        _fill_inserted_inherited(dst, [0, "x"], [dst[0]])
        assert dst[1] == {"St": 6, "P": "Ch1-", "S": pikepdf.Name("/D")}

    def test_no_prev_no_next_falls_back_to_plain(self):
        dst = [None]
        _fill_inserted_inherited(dst, ["x"], [])
        assert dst[0] == {"St": 1}


# ---------------------------------------------------------------------------
# _compress_rules
# ---------------------------------------------------------------------------


class TestCompressRules:
    def test_empty_list_yields_no_rules(self):
        assert _compress_rules([]) == {}

    def test_all_none_yields_no_rules(self):
        assert _compress_rules([None, None]) == {}

    def test_leading_none_then_run_writes_one_rule_at_start_of_run(self):
        dicts = [None, {"St": 1}, {"St": 2}]
        rules = _compress_rules(dicts)
        assert set(rules.keys()) == {1}
        assert rules[1] == pikepdf.Dictionary(St=1)

    def test_consecutive_incrementing_same_style_compresses_to_one_rule(self):
        dicts = [{"St": 1}, {"St": 2}, {"St": 3}]
        rules = _compress_rules(dicts)
        assert set(rules.keys()) == {0}
        assert rules[0] == pikepdf.Dictionary(St=1)

    def test_style_change_starts_new_rule(self):
        dicts = [
            {"St": 1, "S": pikepdf.Name("/r")},
            {"St": 2, "S": pikepdf.Name("/r")},
            {"St": 1, "S": pikepdf.Name("/D")},
        ]
        rules = _compress_rules(dicts)
        assert set(rules.keys()) == {0, 2}
        assert rules[2] == pikepdf.Dictionary(St=1, S=pikepdf.Name("/D"))

    def test_non_incrementing_start_forces_new_rule(self):
        dicts = [{"St": 1}, {"St": 5}]
        rules = _compress_rules(dicts)
        assert set(rules.keys()) == {0, 1}
        assert rules[1] == pikepdf.Dictionary(St=5)

    def test_prefix_included_in_rule_args(self):
        dicts = [{"St": 1, "P": "App-"}]
        rules = _compress_rules(dicts)
        assert rules[0] == pikepdf.Dictionary(St=1, P="App-")

    def test_gap_after_active_style_writes_empty_terminator_rule(self):
        dicts = [{"St": 1}, {"St": 2}, None]
        rules = _compress_rules(dicts)
        assert set(rules.keys()) == {0, 2}
        assert rules[2] == pikepdf.Dictionary()

    def test_none_with_no_active_style_is_pure_skip(self):
        # A leading None (no style active yet) must not emit a terminator rule.
        dicts = [None, None, {"St": 1}]
        rules = _compress_rules(dicts)
        assert set(rules.keys()) == {2}

    def test_none_gap_then_new_run_after_terminator(self):
        dicts = [{"St": 1}, None, {"St": 1}]
        rules = _compress_rules(dicts)
        assert set(rules.keys()) == {0, 1, 2}
        assert rules[1] == pikepdf.Dictionary()
        assert rules[2] == pikepdf.Dictionary(St=1)


# ---------------------------------------------------------------------------
# _write_page_labels
# ---------------------------------------------------------------------------


class TestWritePageLabels:
    def test_writes_nums_array_when_rules_present(self):
        pdf = make_pdf(2)
        _write_page_labels(pdf, {0: pikepdf.Dictionary(St=1)})
        assert "/PageLabels" in pdf.Root
        nt = pikepdf.NumberTree(pdf.Root.PageLabels)
        assert int(list(nt.items())[0][0]) == 0

    def test_deletes_existing_page_labels_when_rules_empty(self):
        pdf = make_pdf(2)
        set_labels(pdf, {0: pikepdf.Dictionary(St=1)})
        _write_page_labels(pdf, {})
        assert "/PageLabels" not in pdf.Root

    def test_noop_when_rules_empty_and_no_existing_labels(self):
        pdf = make_pdf(2)
        assert "/PageLabels" not in pdf.Root
        _write_page_labels(pdf, {})
        assert "/PageLabels" not in pdf.Root
