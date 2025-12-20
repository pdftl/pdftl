# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/replace.py

"""Perform replacements in page content streams"""

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Pdf


logger = logging.getLogger(__name__)
from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.normalize import (
    get_normalized_page_content_stream,
    normalize_page_content_stream,
)
from pdftl.utils.page_specs import page_numbers_matching_page_spec
from pdftl.utils.string import split_escaped

_REPLACE_LONG_DESC = """

The `replace` operation performs replacement of parts of
page content streams, based on regular expressions. in the
PDF file.  Page ranges can be specified. The default page
range is all pages. The `<spec>` specification is:

```
  [optional page range]/<from>/<to>/[count]
```

where `<from>` and `<to>` are strings describing regular
expressions, as described at
<https://docs.python.org/3/library/re.html>.

The delimiter `/` can be replaced with any other non-alphnumeric
character. It must break the `<spec>` into exactly 4 parts (where the
first may be empty). The delimiter is defined as the final character
of `<spec>`, ignoring digits.

Any trailing digits are interpreted as `count`, which is the
maximum number of times the expression will be matched for
each page content stream.

Before and after the replacement is applied, the page
content stream is normalized (see the `normalize` operation), which
results in it appearing with one operator per line.

"""

_REPLACE_EXAMPLES = [
    {
        "cmd": "in.pdf replace '1-3/1 0 0 (RG|rg)/0 0 1 \\1/' output out.pdf",
        "desc": "Replace red with blue on pages 1-3",
    }
]


@register_operation(
    "replace",
    tags=["in_place", "content_stream", "dangerous"],
    type="single input operation",
    desc="Regex replacement on page content streams",
    long_desc=_REPLACE_LONG_DESC,
    usage="<input> replace [<spec>...] output <output>",
    examples=_REPLACE_EXAMPLES,
    args=(["input_pdf", "operation_args"], {}),
)
def replace_in_content_streams(pdf, specs, normalize_input=True, normalize_output=True):
    """
    Replace in page content streams.
    """
    for spec in specs:
        _apply_replace_spec_in_content_streams(pdf, spec, normalize_input, normalize_output)
    return pdf


def _apply_replace_spec_in_content_streams(pdf, spec, normalize_input, normalize_output):
    if not spec:
        return
    num_pages = len(pdf.pages)
    page_spec, replacer = _parse_replace_spec(pdf, spec, normalize_input, normalize_output)
    for page_num in page_numbers_matching_page_spec(page_spec, num_pages):
        replacer.apply(page_num)


def _parse_replace_spec(pdf, spec, normalize_input, normalize_output):
    count_match = re.match("^(.*?)([0-9]*)$", spec)
    count = int(count_match[2] or 0)
    countless_spec = count_match[1]
    spec_parts = split_escaped(countless_spec, countless_spec[-1])
    if len(spec_parts) != 4:
        raise InvalidArgumentError(
            f"Replacement specification '{spec}' does not look correct."
            " After splitting on the final character, I expected 4 parts but got"
            f" {len(spec_parts)}"
        )
    from_re, to_re = (bytes(spec_parts[i], "utf-8") for i in (1, 2))
    return (
        spec_parts[0],
        RegexReplaceContentStream(pdf, from_re, to_re, count, normalize_input, normalize_output),
    )


@dataclass
class RegexReplaceContentStream:
    """A regular expression replacer for PDF content streams"""

    pdf: "Pdf"
    from_re: str = ""
    to_re: str = ""
    count: int = 0
    normalize_input: bool = True
    normalize_output: bool = True

    def apply(self, page_num: int):
        """Apply the replacement"""
        page = self.pdf.pages[page_num - 1]
        if self.normalize_input:
            content_stream = get_normalized_page_content_stream(page)
        else:
            content_stream = page.Contents.read_bytes()
        logger.debug("from_re=%s, to_re=%s, count=%s", self.from_re, self.to_re, self.count)
        if self.from_re:
            new_content_stream = re.sub(self.from_re, self.to_re, content_stream, self.count)
        else:
            new_content_stream = content_stream
        page.Contents = self.pdf.make_stream(new_content_stream)
        if self.normalize_output:
            normalize_page_content_stream(self.pdf, page)
