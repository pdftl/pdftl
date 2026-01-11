import dataclasses
from typing import Any, ClassVar, Protocol, TypeVar

from pdftl.exceptions import PdftlConfigError


class DataclassInstance(Protocol):
    __dataclass_fields__: ClassVar[dict[str, Any]]


T = TypeVar("T", bound=DataclassInstance)


def safe_create(cls: type[T], data: dict[str, Any]) -> T:
    """
    Dynamically validates and creates a dataclass instance.
    Raises PdftlInputError if mandatory fields are missing.
    """
    # 1. Get all fields from the dataclass
    fields = dataclasses.fields(cls)

    # 2. Identify mandatory fields (those without a default value)
    # We check both 'default' and 'default_factory'
    mandatory_fields = [
        f.name
        for f in fields
        if f.default == dataclasses.MISSING and f.default_factory == dataclasses.MISSING
    ]

    # 3. Check for missing keys in the data dict
    missing = [field for field in mandatory_fields if field not in data]

    if missing:
        # Raise the clean error instead of letting Python crash
        raise PdftlConfigError(
            f"Cannot create '{cls.__name__}'. Missing required arguments: {', '.join(missing)}"
        )

    # 4. Filter data to only include valid fields (prevents 'unexpected keyword argument' error)
    # Optional: Remove this block if you want it to fail on extra unknown args
    valid_keys = {f.name for f in fields}
    filtered_data = {k: v for k, v in data.items() if k in valid_keys}

    # 5. Create the instance safely
    return cls(**filtered_data)
