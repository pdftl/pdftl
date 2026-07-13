# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/keyval_parser.py

"""Shared utility for parsing key=value argument strings with schema and range validation."""

from typing import Any
from collections.abc import Callable
from pdftl.exceptions import InvalidArgumentError


def constrained_int(
    min_val: int | None = None, max_val: int | None = None
) -> Callable[[str], int]:
    """Generates a validator function that coerces a string to an integer
    and enforces strict boundary bounds.
    """

    def validator(val: str) -> int:
        try:
            i = int(val)
        except ValueError:
            raise ValueError("must be a valid integer")
        if min_val is not None and i < min_val:
            raise ValueError(f"must be >= {min_val}")
        if max_val is not None and i > max_val:
            raise ValueError(f"must be <= {max_val}")
        return i

    return validator


def parse_keyval_token(
    token: str,
    *,
    allowed_keys: list[str] | None = None,
    schema: dict[str, Callable[[str], Any]] | None = None,
    lowercase_keys: bool = True,
    lowercase_values: bool = False,
    context: str = "",
) -> tuple[str, Any]:
    """
    Parse a single ``key=value`` token into a ``(key, value)`` tuple.

    Raises ``InvalidArgumentError`` if the token contains no ``=``, or if
    the key is not in ``allowed_keys`` / ``schema``.

    Args:
        token:            A single token string, e.g. ``"threshold=0.01"``.
        allowed_keys:     Optional whitelist of valid keys.
        schema:           Optional dict mapping keys to type-casting callables (e.g. `int`).
                          If provided, `allowed_keys` is ignored and inferred from the schema.
        lowercase_keys:   Normalise keys to lowercase (default: ``True``).
        lowercase_values: Normalise values to lowercase (default: ``False``).
        context:          Operation name used in error messages.
    """
    prefix = f"{context}: " if context else ""
    token = token.strip()

    if "=" not in token:
        raise InvalidArgumentError(f"{prefix}missing '=' in argument '{token}'.")

    k, v = token.split("=", 1)
    k = k.strip().lower() if lowercase_keys else k.strip()
    v = v.strip().lower() if lowercase_values else v.strip()

    if schema is not None:
        if k not in schema:
            raise InvalidArgumentError(
                f"{prefix}unknown parameter '{k}'."
                + f" Expected one of: {', '.join(schema.keys())}."
            )
        try:
            v = schema[k](v)
        except (ValueError, TypeError) as e:
            error_detail = f" ({e})" if str(e) else ""
            raise InvalidArgumentError(f"{prefix}invalid value for '{k}': '{v}'{error_detail}")

    elif allowed_keys is not None and k not in allowed_keys:
        raise InvalidArgumentError(
            f"{prefix}unknown parameter '{k}'."
            + (f" Expected one of: {', '.join(allowed_keys)}." if allowed_keys else "")
        )

    return k, v


def parse_keyval_list(
    tokens: list[str],
    *,
    bare_tokens: bool | list[str] | None = None,
    **kwargs,
) -> dict[str, Any]:
    """
    Parse a list of token strings into a dictionary of key-value pairs.

    Tokens containing ``=`` are parsed as ``key=value`` pairs.
    Tokens without ``=`` are handled according to ``bare_tokens``:

    - ``None`` (default): raise ``InvalidArgumentError``.
    - ``True``: silently skip.
    - ``list``: append to the list for the caller to inspect.

    Accepts the same keyword arguments as ``parse_keyval_token``.

    Args:
        tokens:      A list of token strings.
        bare_tokens: Controls handling of tokens with no ``=``.
    """
    result = {}
    for token in tokens:
        if "=" not in token.strip():
            if bare_tokens is None:
                parse_keyval_token(token, **kwargs)  # raises
            elif isinstance(bare_tokens, list):
                bare_tokens.append(token)
            # True: silently skip
        else:
            k, v = parse_keyval_token(token, **kwargs)
            result[k] = v
    return result


def parse_keyval_string(
    params_str: str,
    **kwargs,
) -> dict[str, Any]:
    """
    Parse a comma-separated ``key=value`` string into a dictionary.

    Splits on commas then delegates to ``parse_keyval_list``.
    Accepts the same keyword arguments as ``parse_keyval_list``.

    Args:
        params_str: A comma-separated string, e.g. ``"threshold=0.01,dpi=72"``.
    """
    if not params_str:
        return {}
    tokens = [t for t in params_str.split(",") if t.strip()]
    return parse_keyval_list(tokens, **kwargs)
