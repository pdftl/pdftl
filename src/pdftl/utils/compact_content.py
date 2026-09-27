# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/compact_content.py

"""Content streams in their shortest equivalent form, and token accounting.

Compaction works on qpdf's tokens: whitespace and comments go, a single
space returns only between tokens that would otherwise run together, and
numbers take their shortest spelling (0.50 -> .5, 2.0 -> 2). Inline image
data is copied byte for byte. The output must hold as many significant
tokens as the input.
"""

from __future__ import annotations

import functools
from decimal import Decimal, InvalidOperation

# Whitespace and delimiters: a token ending or starting with one needs no space.
_SEPARATES = frozenset(b" \t\r\n\x0c\x00()<>[]{}/%")


@functools.lru_cache(maxsize=65536)
def _number(value: Decimal) -> bytes:
    if value == value.to_integral_value():
        return str(int(value)).encode()
    text = format(value, "f").rstrip("0")
    if text.startswith("0."):
        text = text[1:]
    elif text.startswith("-0."):
        text = "-" + text[2:]
    return text.encode()


def _is_shortest(raw: bytes) -> bool:
    """Whether a number spelled `raw` has no sign, zero or point to drop."""
    body = raw[1:] if raw[:1] == b"-" else raw
    if not body or body[:1] in b"+0" and body != b"0" or raw == b"-0":
        return False
    return b"." not in body or not body.endswith((b"0", b"."))


@functools.lru_cache(maxsize=65536)
def _shortest(raw: bytes) -> bytes:
    if _is_shortest(raw):
        return raw
    try:
        return _number(Decimal(raw.decode("ascii")))
    except (InvalidOperation, UnicodeDecodeError):
        return raw


@functools.cache
def _filters():
    """The token filters, defined on first use: importing pikepdf is slow."""
    import pikepdf

    space = pikepdf.Token(pikepdf.TokenType.space, b" ")
    # Token types, compared by identity: hashing enum members is slow per token.
    types = pikepdf.TokenType
    bad, eof, white, comment = types.bad, types.eof, types.space, types.comment
    integer, real, word = types.integer, types.real, types.word

    class Compactor(pikepdf.TokenFilter):
        """Rewrites a content stream token by token; counts its significant tokens."""

        def __init__(self):
            super().__init__()
            self.count = 0
            self.bad = False
            self.inline = False  # between BI and EI: copied verbatim
            self.last: int | None = None  # the last byte written

        def handle_token(self, token):
            kind = token.type_
            if kind is white or kind is comment:
                if self.inline:
                    self.last = token.raw_value[-1]
                    return token
                return None
            if kind is bad:
                self.bad = True
                return None
            if kind is eof:
                return None
            raw = bytes(token.raw_value)
            self.count += 1
            if self.inline:
                self.inline = not (kind is word and raw == b"EI")
                self.last = raw[-1]
                return token
            if kind is integer or kind is real:
                short = _shortest(raw)
                if short != raw:
                    raw, token = short, pikepdf.Token(kind, short)
            elif kind is word and raw == b"BI":
                self.inline = True
            needs_space = (
                self.last is not None and self.last not in _SEPARATES and raw[0] not in _SEPARATES
            )
            self.last = raw[-1]
            return [space, token] if needs_space else token

    class Count(pikepdf.TokenFilter):
        """Counts significant tokens; writes nothing back."""

        def __init__(self):
            super().__init__()
            self.count, self.bad = 0, False

        def handle_token(self, token):
            kind = token.type_
            if kind is white or kind is comment or kind is eof:
                return
            if kind is bad:
                self.bad = True
            else:
                self.count += 1

    return Compactor, Count


def _filtered(data: bytes, token_filter) -> bytes:
    import pikepdf

    scratch = pikepdf.new()
    page = scratch.add_blank_page()
    page.Contents = scratch.make_stream(data)
    return page.get_filtered_contents(token_filter)


def compact_stream(data: bytes) -> bytes | None:
    """The shortest equivalent spelling of content stream `data`, or None if it
    holds a malformed token or an unterminated inline image."""
    compactor = _filters()[0]()
    out = _filtered(data, compactor)
    if compactor.bad or compactor.inline:
        return None
    return out if significant_tokens(out) == compactor.count else None


def significant_tokens(data: bytes) -> int | None:
    """Tokens other than whitespace and comments in `data`; None if any is malformed.

    The content parser drops what it cannot read without complaint, so this
    independent count is what shows that nothing was dropped.
    """
    counter = _filters()[1]()
    _filtered(data, counter)
    return None if counter.bad else counter.count


def _operand_tokens(obj) -> int:
    import pikepdf

    if isinstance(obj, pikepdf.Array):
        return 2 + sum(_operand_tokens(x) for x in obj)
    if isinstance(obj, pikepdf.Dictionary):
        return 2 + sum(1 + _operand_tokens(obj[k]) for k in obj.keys())
    return 1


def instruction_tokens(instructions) -> int:
    """Significant tokens a faithful serialization of `instructions` holds."""
    import pikepdf

    total = 0
    for inst in instructions:
        if isinstance(inst, pikepdf.ContentStreamInlineImage):
            total += significant_tokens(inst.iimage.unparse()) or 0
        else:
            operands, _ = inst
            total += 1 + len(operands)
            total += sum(
                _operand_tokens(o) - 1
                for o in operands
                if isinstance(o, (pikepdf.Array, pikepdf.Dictionary))
            )
    return total


def parsed_completely(original: bytes, instructions) -> bool:
    """Whether `instructions` account for every token of `original`."""
    tokens = significant_tokens(original)
    return tokens is not None and tokens == instruction_tokens(instructions)
