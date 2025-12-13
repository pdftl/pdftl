# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/cli/help_data.py

"""Static data for help"""

from collections import OrderedDict

########################################

VERSION_TEMPLATE = """
{whoami} ({package}) {project_version}
Copyright © {years} The {package} developers
Homepage: <{homepage}>
License: MPL-2.0 <https://mozilla.org/MPL/2.0/>
This is free software: you are free to change and redistribute it.
There is NO WARRANTY, to the extent permitted by law.

Core dependencies (and installed versions):
{dependencies}
"""

########################################

SYNOPSIS_TEMPLATE = """
{whoami} <input>... <operation> [<option...>]
{whoami} <input>... <operation> --- <operation>... [<option...>]
{whoami} help [<operation> | <option>]
{whoami} help [{special_help_topics}]
{whoami} --version
"""

########################################

# format of each entry: aliases_tuple: name
# note trailing comma if aliases_tuple has one item, to force an actual tuple

# FIXME: add special help topics to registry instead, with a decorator, one by one, co-located

SPECIAL_HELP_TOPICS_MAP = OrderedDict(
    [
        (("help", "--help"), "help"),
        (
            ("filter", "(omitted)", "omitted", "filter_mode", "filter mode"),
            "filter_mode",
        ),
        (("input", "inputs", "<input>", "Inputs"), "input"),
        (("---", "pipeline", "pipeline syntax"), "pipeline"),
        (
            (
                "pages",
                "range",
                "page range",
                "page ranges",
                "page spec",
                "page specs",
                "page_spec",
                "page_specs",
                "page specification syntax",
                "page specifications",
                "page_specifications",
            ),
            "page_specs",
        ),
        (
            ("output", "outputs", "options", "output_options", "<options>"),
            "output_options",
        ),
        (("examples",), "examples"),
        (("all",), "all"),
    ]
)


########################################
