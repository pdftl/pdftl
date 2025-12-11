# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/core/cli_data.py

"""Static data for CLI interface"""

# FIXME: co-locate extra help topics! Use registry-adding decorator pattern.

CLI_DATA = {
    "extra help topics": {
        "pipeline": {
            "title": "pipeline syntax",
            "desc": "Using --- to pipe multiple operations together",
            "long_desc": """
Multiple operations can be chained together using '---' as a
separator. The output of one stage becomes the input for the next
stage.

If the next stage has no input files, the result from the previous
is used automatically. For multi-input commands where order matters,
you can use the special '_' handle to refer to the piped-in input.
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
                    "cmd": "in.pdf output out.pdf uncompress",
                    "desc": "uncompress the input file, making minimal changes",
                }
            ],
        },
    },
}
