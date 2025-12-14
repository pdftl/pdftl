# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/whatisit.py

"""
Tools to identify (and convert) pikepdf objects
"""


def whatis(x):
    """Return a list of matching types"""
    from pikepdf import (
        Array,
        Dictionary,
        Name,
        NameTree,
        Object,
        Page,
        Pdf,
        Stream,
        String,
    )

    return [
        t
        for t in [
            Pdf,
            Page,
            Name,
            Array,
            String,
            Dictionary,
            NameTree,
            Stream,
            Object,
            int,
            float,
            list,
            dict,
            str,
        ]
        if isinstance(x, t)
    ]


def whatis_guess(x):
    """Guess the most relevant type"""
    guess = whatis(x)
    return guess[0] if len(guess) > 0 else None


def is_page(obj):
    """Is this a pikepdf page?"""
    from pikepdf import Dictionary, Name

    return (
        isinstance(obj, Dictionary)
        and hasattr(obj, "Type")
        and obj.Type == Name.Page
        and hasattr(obj, "objgen")
    )
