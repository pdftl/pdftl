# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/replace.py

"""Perform replacements in page content streams"""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Pdf


import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.keyval_parser import parse_keyval_list
from pdftl.utils.normalize import get_normalized_page_content_stream, normalize_page_content_stream
from pdftl.utils.page_specs import page_numbers_matching_page_spec
from pdftl.utils.string_utils import split_escaped

logger = logging.getLogger(__name__)

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

By default, the replacement also recurses into Form XObjects referenced
by the page. Pass ``recurse=false`` to restrict replacement to the
top-level page content stream only.

*See also* the `dump_streams` operation, which lets you examine normalized content
streams so you can formulate a regular expression to use with `replace`.

"""

_REPLACE_EXAMPLES = [
    {
        "cmd": "in.pdf replace '1-3/1 0 0 (RG|rg)/0 0 1 \\1/' output out.pdf",
        "desc": "Replace red with blue on pages 1-3",
    },
    {
        "cmd": "in.pdf replace recurse=false '/foo/bar/' output out.pdf",
        "desc": "Replace foo with bar on all pages, skipping Form XObjects",
    },
]


@register_operation(
    "replace",
    tags=["in_place", "content_stream", "dangerous"],
    type="single input operation",
    desc="Regex replacement on page content streams",
    long_desc=_REPLACE_LONG_DESC,
    usage="<input> replace [<spec>...] output <output>",
    examples=_REPLACE_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def replace_in_content_streams(
    pdf, specs, normalize_input=True, normalize_output=True
) -> OpResult:
    """
    Replace in page content streams.
    """
    replace_specs: list[str] = []
    kvs = parse_keyval_list(
        specs,
        bare_tokens=replace_specs,
        allowed_keys=["recurse"],
        lowercase_values=True,
        context="replace",
    )
    recurse = kvs.get("recurse", "true") != "false"
    for spec in replace_specs:
        _apply_replace_spec_in_content_streams(
            pdf, spec, normalize_input, normalize_output, recurse
        )
    return OpResult(success=True, pdf=pdf)


def _apply_replace_spec_in_content_streams(
    pdf, spec, normalize_input, normalize_output, recurse=True
):
    if not spec:
        return
    num_pages = len(pdf.pages)
    page_spec, replacer = _parse_replace_spec(
        pdf, spec, normalize_input, normalize_output, recurse
    )
    for page_num in page_numbers_matching_page_spec(page_spec, num_pages):
        replacer.apply(page_num)


def _parse_replace_spec(pdf, spec, normalize_input, normalize_output, recurse=True):
    import re

    count_match = re.match("^(.*?)([0-9]*)$", spec)
    if not count_match:
        raise InvalidArgumentError(
            f"Replacement specification '{spec}' does not look correct."
            " Could not parse count suffix."
        )
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
        RegexReplaceContentStream(
            pdf, from_re, to_re, count, normalize_input, normalize_output, recurse
        ),
    )


@dataclass
class RegexReplaceContentStream:
    """A regular expression replacer for PDF content streams"""

    pdf: "Pdf"
    from_re: bytes = b""
    to_re: bytes = b""
    count: int = 0
    normalize_input: bool = True
    normalize_output: bool = True
    recurse: bool = True

    def __post_init__(self):
        self._processed_objgens = set()

    def apply(self, page_num: int):
        """Apply the replacement"""
        page = self.pdf.pages[page_num - 1]
        self._apply_to_stream(page, is_page=True)
        if self.recurse and "/Resources" in page:
            self._recurse_resources(page.Resources)

    def _extract_bytes(self, container, is_page: bool) -> bytes | None:
        """Safely extract raw content stream bytes, handling layouts and normalization
        configurations."""
        import pikepdf

        if is_page:
            # Check containment / property existence safely for Mock support
            if "/Contents" not in container and not hasattr(container, "Contents"):
                return None

            if self.normalize_input:
                return get_normalized_page_content_stream(container)

            contents = container.Contents
            if isinstance(contents, pikepdf.Array):
                return b"".join(s.read_bytes() for s in contents)
            return contents.read_bytes()

        # Form XObject Stream
        if self.normalize_input:
            return pikepdf.unparse_content_stream(pikepdf.parse_content_stream(container))
        return container.read_bytes()

    def _write_bytes(self, container, is_page: bool, new_content_stream: bytes):
        """Safely write stream changes back and normalize output structural alignment if
        requested."""
        import pikepdf

        if is_page:
            container.Contents = self.pdf.make_stream(new_content_stream)
            if self.normalize_output:
                normalize_page_content_stream(self.pdf, container)
        else:
            container.write(new_content_stream)
            if self.normalize_output:
                try:
                    normalized = pikepdf.unparse_content_stream(
                        pikepdf.parse_content_stream(container)
                    )
                    container.write(normalized)
                except pikepdf.PdfError as e:
                    logger.warning(
                        "Could not normalize output XObject stream: %s. Keeping raw stream.", e
                    )

    def _apply_to_stream(self, container, is_page: bool):
        """Apply the regex replacement to a single content stream container (Page dict or XObject
        stream)."""
        import re

        content_stream = self._extract_bytes(container, is_page)
        if content_stream is None:
            return

        logger.debug("from_re=%s, to_re=%s, count=%s", self.from_re, self.to_re, self.count)

        if self.from_re:
            new_content_stream = re.sub(self.from_re, self.to_re, content_stream, self.count)
        else:
            new_content_stream = content_stream

        self._write_bytes(container, is_page, new_content_stream)

    def _recurse_resources(self, resources):
        """Walk Form XObjects in a resource dict and apply the replacement to each."""
        if "/XObject" not in resources:
            return
        for name, xobj in resources.XObject.items():
            if xobj.objgen in self._processed_objgens:
                continue
            if xobj.get("/Subtype") != "/Form":
                continue
            logger.debug("Recursing into Form XObject %s (%s)", name, xobj.objgen)
            self._processed_objgens.add(xobj.objgen)
            self._apply_to_stream(xobj, is_page=False)
            if "/Resources" in xobj:
                self._recurse_resources(xobj.Resources)
