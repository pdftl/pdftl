# tests/operations/helpers/test_marks_types.py
import pytest

from pdftl.operations.helpers.marks_types import MarksConfig


class TestMarksConfigDefaults:
    def test_defaults(self):
        config = MarksConfig()
        assert config.cropmarks == "western"
        assert config.registration is True
        assert config.colorbars is False
        assert config.startarget is False
        assert config.weight == 0.25
        assert config.offset == 9.0
        assert config.length == 18.0

    def test_wants_anything_true_by_default(self):
        assert MarksConfig().wants_anything is True

    def test_wants_anything_false_when_everything_disabled(self):
        config = MarksConfig(
            cropmarks=None, registration=False, colorbars=False, pageinfo=False, startarget=False
        )
        assert config.wants_anything is False

    def test_wants_anything_true_when_only_startarget_enabled(self):
        config = MarksConfig(cropmarks=None, registration=False, colorbars=False, startarget=True)
        assert config.wants_anything is True


class TestMarksConfigValidation:
    def test_rejects_bad_cropmarks_style(self):
        with pytest.raises(ValueError, match="cropmarks must be one of"):
            MarksConfig(cropmarks="chinese")

    def test_rejects_bad_weight(self):
        with pytest.raises(ValueError, match="weight must be one of"):
            MarksConfig(weight=1.0)

    def test_rejects_negative_offset(self):
        with pytest.raises(ValueError, match="offset must be zero or more"):
            MarksConfig(offset=-1.0)

    def test_rejects_zero_or_negative_length(self):
        with pytest.raises(ValueError, match="length must be positive"):
            MarksConfig(length=0.0)

    def test_accepts_none_cropmarks(self):
        # explicit "no crop marks" must not raise
        MarksConfig(cropmarks=None)
