import dataclasses

import pytest

from pdftl.exceptions import PdftlConfigError
from pdftl.utils.type_helpers import safe_create


@dataclasses.dataclass
class _TestDC:
    required: str
    optional: str = "default"


def test_safe_create_success():
    obj = safe_create(_TestDC, {"required": "val", "optional": "x"})
    assert obj.required == "val"
    assert obj.optional == "x"


def test_safe_create_missing_required():
    with pytest.raises(PdftlConfigError, match="Missing required"):
        safe_create(_TestDC, {})


def test_safe_create_ignores_extra_keys():
    obj = safe_create(_TestDC, {"required": "val", "unknown": "ignored"})
    assert obj.required == "val"
    assert obj.optional == "default"
