# tests/core/test_types_coverage.py

from dataclasses import dataclass

import pytest

from pdftl.core.types import LegacyDictAccess, OpResult


@dataclass
class MockLegacyObj(LegacyDictAccess):
    existing_attr: str = "value"


from dataclasses import dataclass


def test_legacy_dict_access_missing_key():
    """
    Covers lines 56-57: raise KeyError(key) from e
    """
    obj = MockLegacyObj()
    with pytest.raises(KeyError):
        _ = obj["non_existent"]


def test_legacy_dict_access_setitem():
    """
    Covers line 61: setattr(self, key, value)
    """
    obj = MockLegacyObj()
    obj["new_attr"] = 123
    assert obj.new_attr == 123


def test_op_result_repr():
    """
    Covers line 173: return ( ... ) in __repr__
    """
    res = OpResult(success=True, data="test_data", error="oops")
    rep = repr(res)
    assert "OpResult" in rep
    assert "test_data" in rep
    assert "oops" in rep
