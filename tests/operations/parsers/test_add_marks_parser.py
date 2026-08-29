# tests/operations/parsers/test_add_marks_parser.py
import pytest

from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.helpers.marks_types import MarksConfig
from pdftl.operations.parsers.add_marks_parser import parse_add_marks_args


class TestBareSpecs:
    def test_no_args_applies_defaults_to_every_page(self):
        result = parse_add_marks_args([], total_pages=3)
        assert set(result.keys()) == {1, 2, 3}
        assert all(config == MarksConfig() for config in result.values())

    def test_bare_page_range_uses_defaults(self):
        result = parse_add_marks_args(["2-3"], total_pages=5)
        assert set(result.keys()) == {2, 3}
        assert result[2] == MarksConfig()


class TestOptionParsing:
    def test_pageinfo_key_is_parsed(self):
        result = parse_add_marks_args(["1(pageinfo=true)"], total_pages=1)
        assert result[1].pageinfo is True

    def test_startarget_key_is_parsed(self):
        result = parse_add_marks_args(["1(startarget=true)"], total_pages=1)
        assert result[1].startarget is True

    def test_parses_all_recognized_keys(self):
        result = parse_add_marks_args(
            [
                "1(cropmarks=japanese,registration=false,colorbars=true,"
                "weight=0.5,offset=1cm,length=2cm)"
            ],
            total_pages=1,
        )
        config = result[1]
        assert config.cropmarks == "japanese"
        assert config.registration is False
        assert config.colorbars is True
        assert config.weight == 0.5
        assert config.offset == pytest.approx(28.3465, abs=0.01)  # 1cm in pt
        assert config.length == pytest.approx(56.6929, abs=0.01)

    def test_cropmarks_false_maps_to_none(self):
        result = parse_add_marks_args(["1(cropmarks=false)"], total_pages=1)
        assert result[1].cropmarks is None

    def test_unknown_key_raises(self):
        with pytest.raises(InvalidArgumentError):
            parse_add_marks_args(["1(bogus=true)"], total_pages=1)

    def test_bad_weight_raises(self):
        with pytest.raises(InvalidArgumentError):
            parse_add_marks_args(["1(weight=1.0)"], total_pages=1)

    def test_missing_closing_paren_raises(self):
        with pytest.raises(InvalidArgumentError):
            parse_add_marks_args(["1(cropmarks=japanese"], total_pages=1)


class TestMultipleSpecsAndMerging:
    def test_later_spec_wins_for_overlapping_pages(self):
        result = parse_add_marks_args(["2-4", "3(colorbars=true)"], total_pages=5)
        # page 3 gets ONLY the second spec's config (full replace, not merge)
        assert result[3] == MarksConfig(colorbars=True)
        assert result[2] == MarksConfig()
        assert result[4] == MarksConfig()

    def test_disjoint_specs_each_apply_independently(self):
        result = parse_add_marks_args(["2-4", "8-10(offset=1cm,length=2cm)"], total_pages=12)
        assert set(result.keys()) == {2, 3, 4, 8, 9, 10}
        assert result[2] == MarksConfig()
        assert result[9].offset != MarksConfig().offset


# MarksConfig's own post-schema validation raising through the parser ---


class TestDimensionAndConfigErrors:
    def test_invalid_dimension_string_raises(self):
        with pytest.raises(InvalidArgumentError):
            parse_add_marks_args(["1(offset=notanumber)"], total_pages=1)

    def test_semantically_invalid_config_raises_after_dimension_parses(self):
        # 0pt parses fine as a dimension (no ValueError from dim_str_to_pts),
        # but MarksConfig itself rejects a non-positive length -- this
        # exercises the second, post-schema validation layer distinct from
        # _dimension's own try/except.
        with pytest.raises(InvalidArgumentError, match="length must be positive"):
            parse_add_marks_args(["1(length=0pt)"], total_pages=1)


# (via monkeypatch, since dim_str_to_pts's real error type bypasses it),
# plus the new 'remove' token handling ---

from pdftl.operations.parsers import add_marks_parser


class TestDimensionValueErrorReraise:
    def test_value_error_from_dim_str_to_pts_is_reraised_and_wrapped(self, monkeypatch):
        def _boom(*_args, **_kwargs):
            raise ValueError("simulated bad dimension")

        monkeypatch.setattr(add_marks_parser, "dim_str_to_pts", _boom)
        with pytest.raises(InvalidArgumentError):
            parse_add_marks_args(["1(offset=1cm)"], total_pages=1)


class TestRemoveToken:
    def test_bare_remove_maps_to_wants_nothing_config(self):
        result = parse_add_marks_args(["4(remove)"], total_pages=5)
        assert result[4] == MarksConfig(
            cropmarks=None, registration=False, colorbars=False, startarget=False
        )
        assert result[4].wants_anything is False

    def test_remove_combined_with_other_option_raises(self):
        with pytest.raises(InvalidArgumentError, match="cannot be combined"):
            parse_add_marks_args(["1(remove,colorbars=true)"], total_pages=1)

    def test_unrecognized_bare_token_raises(self):
        with pytest.raises(InvalidArgumentError, match="unrecognized token"):
            parse_add_marks_args(["1(bogus_bare_token)"], total_pages=1)

    def test_multiple_bare_tokens_raises(self):
        with pytest.raises(InvalidArgumentError, match="unrecognized token"):
            parse_add_marks_args(["1(remove,remove)"], total_pages=1)

    def test_remove_only_affects_its_own_page_range(self):
        result = parse_add_marks_args(["1-3", "2(remove)"], total_pages=3)
        assert result[1] == MarksConfig()
        assert result[2].wants_anything is False
        assert result[3] == MarksConfig()
