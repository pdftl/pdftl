# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/keyval_parser.py

"""Shared utility for parsing key=value argument strings."""

from pdftl.exceptions import InvalidArgumentError


def parse_keyval_token(
    token: str,
    *,
    allowed_keys: list[str] | None = None,
    lowercase_keys: bool = True,
    lowercase_values: bool = False,
    context: str = "",
) -> tuple[str, str]:
    """
    Parse a single ``key=value`` token into a ``(key, value)`` tuple.

    Raises ``InvalidArgumentError`` if the token contains no ``=``, or if
    the key is not in ``allowed_keys``.

    Args:
        token:            A single token string, e.g. ``"threshold=0.01"``.
        allowed_keys:     Optional whitelist of valid keys.
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

    if allowed_keys is not None and k not in allowed_keys:
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
) -> dict[str, str]:
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
) -> dict[str, str]:
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
