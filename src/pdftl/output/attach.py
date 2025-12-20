# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/output/attach.py

"""Methods for parsing attach_file arguments and attaching files to
PDF files"""

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)
from pdftl.core.registry import register_option
from pdftl.exceptions import InvalidArgumentError, MissingArgumentError
from pdftl.utils.io_helpers import can_read_file
from pdftl.utils.page_specs import page_numbers_matching_page_spec
from pdftl.utils.user_input import UserInputContext, filename_completer

_ATTACH_LONG_DESC = """Attach one or more files to the PDF, either
at the document level or associated with specific pages.

The command works by reading a list of arguments from left to
right. Keywords like `to_page` and `relation` apply to all the
filenames that came just before them.

### Keywords

#### `to_page <page_spec>`

Attaches all preceding files (since the last command) to the specified
page(s) as clickable icons. <page_spec> can be a single page (`1`), a
range (`5-10`), or a qualifier (`even`, `odd`). See also the help
topic [[`page_specs`]].

#### `relation <type>`

Sets the metadata relationship for ALL preceding files (since the last
command). This defines the files' purpose. Valid `<type>` values are:
`Source`, `Data`, `Alternative`, `Supplement`, `Unspecified`.

#### `PROMPT`

This keyword can be used in place of a filename or `<page_spec>`, in
order to be prompted interactively.

#### Default Behavior

If a file is listed without a `to_page` keyword following it, it will
be attached at the document level (visible in the "Attachments" panel
of a PDF reader, but not on a specific page).

## Examples

> Attach a file to the whole document:

```
pdftl in.pdf attach_files data.csv output out.pdf
```

> Attach a file to a specific page:

```
pdftl in.pdf attach_files chart.png to_page 5 output out.pdf
```

> Attach multiple files to one page:

```
pdftl in.pdf attach_files a.pdf b.pdf to_page 1 output out.pdf
```

> Set a relationship for a document-level file:

```
pdftl in.pdf attach_files code.zip relation Source output out.pdf
```

> Chain multiple commands:
> `a.pdf` and `b.pdf` are attached to page 1, `c.pdf` is attached to page 5.

```
pdftl in.pdf attach_files a.pdf b.pdf to_page 1 c.pdf to_page 5 output out.pdf
```

> Combine `to_page` and `relation`: Keywords apply to all preceding files in their group.
> Both `a.pdf` and `b.pdf` are attached to page 1 AND are both set with the `Data` relationship.

```
pdftl in.pdf attach_files a.pdf b.pdf to_page 1 relation Data output out.pdf
```

> Use interactive prompts:
> Prompt for a file, then prompt for the page.

```
pdftl in.pdf attach_files PROMPT to_page PROMPT output out.pdf
```
"""


@register_option(
    "attach_files <file>...",
    desc="Attach files to the output PDF",
    type="one or more mandatory arguments",
    long_desc=_ATTACH_LONG_DESC,
    # FIXME: examples here that work with the tests!
)
def _attach_files_option():
    pass


@dataclass
class Attachment:
    """Simple data class for PDF file attachments"""

    path: Path
    pages: list | None = None
    relationship: str | None = None


@dataclass
class ParsedAttachment:
    """Holds the *un-validated* string output from the pure parser."""

    path: str
    page_spec: str | None = None
    relationship: str | None = None


ATTACHMENT_RELATIONSHIPS = {
    "Source",
    "Data",
    "Alternative",
    "Supplement",
    "Unspecified",
}


def _get_attachments_from_options(options, num_pages, input_context):
    """Handles filename retrieval, including interactive prompts."""
    args = options.get("attach_files", [])
    parsed_items = _parse_attach_specs_to_intent(args)
    return _resolve_attachments(parsed_items, num_pages, input_context)


def _parse_attach_specs_to_intent(args: list[str]) -> list[ParsedAttachment]:
    parsed_attachments = []
    i = 0
    while i < len(args):
        i += _process_next_attach_arguments_for_intent(args, i, parsed_attachments)
    return parsed_attachments


def _process_next_attach_arguments_for_intent(
    attach_args: list[str],
    i: int,
    parsed_attachments: list[ParsedAttachment],
):
    """
    Process the next attachment arguments, starting from index i.
    Mutates 'parsed_attachments' in-place.
    Returns the number of arguments processed, either 1 or 2.
    """
    if (keyword := attach_args[i].lower()) in ("relation", "to_page"):
        _raise_exception_if_invalid_after_keyword(
            attach_args, i, parsed_attachments == [], keyword
        )
        next_arg = attach_args[i + 1]
        logger.debug("keyword=%s, next_arg=%s", keyword, next_arg)
        if keyword == "relation":
            _set_relationship_in_parsed_attachments(next_arg.capitalize(), parsed_attachments[:i])
        elif keyword == "to_page":
            # pdftk seems to only accept one to_page argument
            # but we accept: attach_files file1 to_page end file2 file3 to_page 5
            _set_page_specs_in_parsed_attachments(next_arg, parsed_attachments[:i])
        return 2

    filename = attach_args[i]
    parsed_attachments.append(ParsedAttachment(path=filename))
    return 1


def _set_relationship_in_parsed_attachments(relationship, parsed_attachments_to_set):
    """Sets any unset relationship entries in the given list of attachments"""
    for parsed_attachment in parsed_attachments_to_set:
        if parsed_attachment.relationship is None:
            parsed_attachment.relationship = relationship


def _set_page_specs_in_parsed_attachments(page_spec, parsed_attachments_to_set):
    """Sets any unset page entries to the given page in the given list of attachments"""
    for parsed_attachment in parsed_attachments_to_set:
        if parsed_attachment.page_spec is None:
            parsed_attachment.page_spec = page_spec


##################################################


def _resolve_attachments(
    parsed_items: list[ParsedAttachment],
    num_pages: int,
    input_context: UserInputContext,
) -> list[Attachment]:
    resolved_list = []
    for parsed in parsed_items:
        final_filename = parsed.path
        if final_filename == "PROMPT":
            final_filename = _resolve_prompt_to_filename(input_context, len(resolved_list) + 1)

        if not can_read_file(final_filename):
            logger.warning("Cannot read attachment '%s'. Skipping.", final_filename)
            continue

        pages = (
            _validate_topage_and_convert_to_ints(parsed.page_spec, num_pages)
            if parsed.page_spec is not None
            else None
        )

        resolved_list.append(
            Attachment(
                path=Path(final_filename),
                pages=pages,
                relationship=_get_relationship(parsed),
            )
        )

    return resolved_list


def _get_relationship(parsed):
    if not parsed.relationship:
        return None
    relationship = parsed.relationship.capitalize()
    if relationship not in ATTACHMENT_RELATIONSHIPS:
        raise InvalidArgumentError(
            f"Invalid attachment relationship '{parsed.relationship}'. "
            f"Must be one of {ATTACHMENT_RELATIONSHIPS}"
        )
    return relationship


def _resolve_prompt_to_filename(input_context, attachment_num):
    def get_filename(prefix=""):
        filename = input_context.get_input(
            f"{prefix}Enter a filename for attachment {attachment_num}: ",
            completer=filename_completer,
        )
        if filename != "" and not can_read_file(filename):
            return get_filename(f"Cannot read file '{filename}'. ")
        return filename

    return get_filename()


def _raise_exception_if_invalid_after_keyword(
    attach_args: list[str], i: int, attachments_is_empty: bool, keyword: str
):
    """Raise an exception if this keyword makes no sense.

    In particular, guarantee that if we don't raise an exception here,
    then attach_args[i+1] and attachments[-1] are both defined.

    """
    if attachments_is_empty:
        raise MissingArgumentError(f"Missing filename before '{keyword}' in 'attach_files'")
    if i + 1 >= len(attach_args):
        raise MissingArgumentError(f"Missing argument after '{keyword}' in 'attach_files'")


def _validate_topage_and_convert_to_ints(spec, num_pages):
    """Validate and convert a to_page page number passed by the user"""
    try:
        page_nums = page_numbers_matching_page_spec(spec, num_pages)
        if not page_nums:
            raise InvalidArgumentError(
                f"Invalid attachment to_page specification '{spec}' did not yield any pages"
            )
        return page_nums
    except ValueError as exc:
        raise InvalidArgumentError(
            f"Attachment to_page specification '{spec}' gave an error: {exc}"
        ) from exc


def _attach_attachment_to_document(pdf, attachment):
    import pikepdf

    attachment_filespec = pikepdf.AttachedFileSpec.from_filepath(pdf, attachment.path)
    _update_relationship_in_filespec(attachment_filespec, attachment.relationship)
    pdf.attachments[attachment.path.name] = attachment_filespec


def _update_relationship_in_filespec(filespec, relationship):
    import pikepdf

    if relationship is not None:
        filespec.relationship = pikepdf.Name("/" + relationship)


def _attach_attachment(pdf, attachment, num_attached_by_page):
    """attach an attachment to a pdf"""
    logger.debug("%s", attachment)
    if attachment.pages is not None:
        for page in attachment.pages:
            _attach_attachment_to_page(
                pdf,
                attachment,
                page,
                num_attached_by_page[page - 1],
            )
            num_attached_by_page[page - 1] += 1
        return
    _attach_attachment_to_document(pdf, attachment)


def _attachment_rect(rect, index):
    offset = 10 + index * 30
    side_length = 27
    return [
        a := min(rect[0], rect[2]) + offset,
        b := max(rect[1], rect[3]) - offset,
        a + side_length,
        b - side_length,
    ]


def _attach_attachment_to_page(pdf, attachment, page_num, num_previous_attachments):
    import pikepdf

    logger.debug("%s", page_num)
    if attachment.path.name not in pdf.attachments:
        logger.debug("new attachment_filespec object required")
        attachment_filespec = pikepdf.AttachedFileSpec.from_filepath(pdf, attachment.path)
        pdf.attachments[attachment.path.name] = attachment_filespec

    attachment_filespec = pdf.attachments[attachment.path.name]
    _update_relationship_in_filespec(attachment_filespec, attachment.relationship)

    page = pdf.pages[page_num - 1]
    if "/Annots" not in page:
        page["/Annots"] = pikepdf.Array()
    # with open(attachment.path, "rb") as file_handle:
    #     attachment_stream = pikepdf.Stream(pdf, data=file_handle.read())
    note_dict = {
        "/Contents": pikepdf.String(attachment.path.name),
        "/Subtype": pikepdf.Name.FileAttachment,
        "/FS": attachment_filespec.obj,
        "/Rect": _attachment_rect(page.cropbox, num_previous_attachments),
    }
    new_annotation = pikepdf.Annotation(pikepdf.Dictionary(note_dict))
    page.Annots.append(new_annotation.obj)


def attach_files(pdf, options, input_context):
    """Attach files to a PDF document, according to the specified options"""
    num_pages = len(pdf.pages)
    num_attached_by_page = [0] * num_pages
    for attachment in _get_attachments_from_options(options, num_pages, input_context):
        _attach_attachment(pdf, attachment, num_attached_by_page)
