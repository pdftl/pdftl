# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/core/cli_data.py

"""Static data for CLI interface"""

# FIXME: co-locate extra help topics! Use registry-adding decorator pattern.

CLI_DATA = {
    "extra help topics": {
        "help": {
            "title": "pdftl help",
            "desc": "Get help",
            "long_desc": """ If a `help` argument is given, the
remaining arguments are scanned for a keyword. This can be
one of the operation names, or an option name, or a
special help topic, or an alias. If a match is found, the
help is printed.

By default, colors are used if printing directly to the
terminal, and usually not in other situations (e.g., if
the output is redirected). If the environment variable
`FORCE_COLORS` is set, then colors should appear in all
cases.

The special help topic `all` is particularly interesting.
            """,
            "examples": [
                {
                    "desc": "Get all help. This is nice if you set `FORCE_COLORS=1` and pipe the output to `less -R`, with the complete command `FORCE_COLORS=1 pdftl help all | less -R`.",
                    "cmd": "help all",
                },
            ],
        },
        "pipeline": {
            "title": "pipeline syntax",
            "desc": "Using `---` to pipe multiple operations together",
            "long_desc": """
Multiple operations can be chained together using `---` as a
separator. The output of one stage becomes the input for the next
stage.

If the next stage has no input files, the result from the previous
is used automatically. For multi-input commands where order matters,
you can use the special `_` handle to refer to the piped-in input.
""",
            "examples": [
                {
                    "desc": "Shuffle two documents, then crop the resulting pages to A4",
                    "cmd": "a.pdf b.pdf shuffle --- crop '(a4)' output out.pdf",
                },
                {
                    "desc": "Shuffle doc_B with the even pages of doc_A, with B's pages first:\n"
                    "'_' is required to place the piped-in pages second in the given order.",
                    "cmd": "doc_A.pdf cat even --- B=doc_B.pdf shuffle B _ output final.pdf",
                },
                {
                    "desc": (
                        "Crop all pages to A3 in landscape,\n"
                        "and preview the effect of cropping odd pages to A4"
                    ),
                    "cmd": "in.pdf crop (A3_l) --- crop odd(A4) output out.pdf",
                },
            ],
        },
        "input": {
            "title": "Inputs",
            "desc": "Specifying input files and passwords",
            "long_desc": """
The general syntax for providing input to an operation is:

  <inputs> [ input_pw <password...> ]

<inputs> is a space-separated list of one or more input PDF
sources. Each source can be:

  - A file path: my_doc.pdf

  - A handle assignment (for referring to files in
    operations): A=my_doc.pdf

  - A single dash '-' to read from standard input (stdin).

  - The keyword 'PROMPT' to be interactively asked for a
    file path.

[ input_pw <password...> ] is an optional block to provide
owner passwords for encrypted files. The passwords in the
<password...> list can be assigned in two ways:

  - By position: Passwords are applied sequentially to the
    encrypted input files in the order they appear, as in:

      enc1.pdf plain.pdf enc2.pdf input_pw pass1 pass2

  - By handle: If an input file has a handle (e.g.,
    A=file.pdf), its password can be assigned using the same
    handle. This is the most reliable method when using
    multiple encrypted files. As in:

      A=enc1.pdf B=enc2.pdf input_pw B=pass2 A=pass1

The keyword 'PROMPT' can be used in the list to be securely
prompted for a password. This is recommended.
""",
        },
        "filter_mode": {
            "title": "filter mode",
            "desc": "Filter mode: apply output options only",
            "long_desc": """
If no operation is given and one input PDF file is specified,
then filter mode is activated. The input PDF file is processed
minimally and saved with the given output options. This is
likely to be less destructive than using cat.""",
            "examples": [
                {
                    "cmd": "in.pdf output out.pdf",
                    "desc": "Do nothing",
                },
                {
                    "cmd": "in.pdf filter output out.pdf",
                    "desc": "Do nothing",
                },
                {
                    "cmd": "in.pdf output out.pdf uncompress",
                    "desc": "uncompress the input file, making minimal changes",
                },
            ],
        },
        "page_specs": {
            "title": "page specification syntax",
            "desc": "Specifying collections of pages and transformations",
            "long_desc": """ The page specification syntax is a powerful mechanism
used by commands like `cat`, `delete`, and `rotate` to
select pages and optionally apply transformations to them as
they are processed.

A complete page specification string combines up to three
optional components in the following order:

1. Page range: Which pages to select.

2. Qualifiers and omissions: Filtering the selected pages by
parity (even/odd) and omitted ranges.

3. Transformation modifiers: Applying rotation or scaling to
the selected pages. This is ignored by some operations.

### 1. Page Ranges

A page range defines the starting and ending page
numbers. If omitted, the specification applies to all pages.

A page identifier can be:

  an integer (e.g., `5`) representing that page (numbered
  from page 1, the first page of the PDF file, regardless of
  any page labelling),

  the keyword `end`,

  or `r` followed by one of the two above types,
  representing reverse numbering. So `r1` means the same as
  `end`, and `rend` means the same as `1`.

The following page range formats are supported:

`<I>`: A single page identifier

`<I>-<J>`: A range of pages (e.g., `1-5`). If the start page
number is higher than the end page number (e.g., `5-1`),
then the pages are treated in reverse order.

### 2. Page qualifiers and omissions

#### Parity qualifiers

Parity qualifiers filter the selected pages based on their
number. They are added immediately after the page
range. Valid qualifiers are:

`even`: selects only even-numbered pages in the range (e.g.,
`1-10even`).

`odd`: selects only odd-numbered pages in the range (e.g.,
`odd` alone selects all odd pages).

#### Omissions

The `~` operator is used to exclude pages from the selection
defined by the preceding page range and qualifiers.

`~<N>-<M>`: Omits a range of pages (e.g., `1-end~5-10` selects
all pages except 5 through 10).

`~<N>`: Omits a single page (e.g., `1-10~5` selects all pages
from 1 to 10 except page 5).

`~r<N>`: Omits a single page counting backwards from the end
(e.g., `~r1` omits the last page).

### 3. Transformation Modifiers


These optional modifiers can be chained after the range and
qualifiers to apply changes to the page content.

#### Rotation (relative)

These modifiers adjust the page's current rotation property
by adding or subtracting degrees.

right: Rotates 90 degrees clockwise (+90),

left: Rotates 90 degrees counter-clockwise (-90),

down: Rotates 180 degrees (+180).

#### Rotation (absolute)

These modifiers reset and set the page's rotation property
to a fixed orientation (0, 90, 180, or 270 degrees) relative
to the page's natural (unrotated) state.

`north`: Resets rotation to the natural page orientation,

`east`: Sets rotation to 90 degrees clockwise,

`south`: Sets rotation to 180 degrees,

`west`: Sets rotation to 270 degrees clockwise or 90 degrees
counter-clockwise.

#### Scale and zoom

`x<N>`: Scales the page content by factor N. N is typically an
integer or decimal (e.g., `x2` doubles the size, `x0.5`
halves it).

`z<N>`: Zoom in by N steps (or out if N is negative), where a
zoom of 1 step corresponds to enlarging A4 paper to A3. More
technically, we scale by factor of 2^(N/2). (N can be any
number). For example, z1 will scale A4 pages up to A3, and
`z-1` scales A4 pages down to A5.

### Examples

`1-5eastx2` selects pages 1 through 5, rotating them 90
degrees clockwise (east) and scaling them by 2x.


`oddleftz-1` selects only the odd pages from the beginning
to the end, rotating them 90 degrees counter-clockwise
(left) and applying a zoom factor of z-1.


`1-end~3-5` or equivalently `~3-5` selects all pages except
pages 3-5.

`~2downz1` selects all pages except page 2, rotating them by
180 degrees and zooming in 1 step. This will likely need to
be quoted to prevent your shell misinterpreting it. (The
same goes for `~3-5`).

`end-r4` selects the last 4 pages, in reverse order.

""",
        },
    },  #  "extra help topics"
}  # CLI_DATA
