# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/cli/help_version.py

"""Version help"""

import logging
import sys

logger = logging.getLogger(__name__)

from pdftl.cli.console import get_console
from pdftl.cli.help_data import (
    VERSION_TEMPLATE,
)
from pdftl.cli.metadata import get_dependencies_status, get_project_version
from pdftl.cli.whoami import DOCSPAGE, HOMEPAGE, PACKAGE, WHOAMI


def print_version(dest=None):
    """Prints detailed version information for the package, core, and optional deps."""
    from datetime import date

    not_found_str = "(not found)"
    core_deps, optional_deps = get_dependencies_status()

    # --- 1. Core Dependencies ---
    try:
        import pikepdf

        libqpdf_version = pikepdf.__libqpdf_version__
    except ImportError:
        libqpdf_version = not_found_str

    core_deps.append(("libqpdf", libqpdf_version))
    core_deps.append(("python", sys.version.split()[0]))
    core_deps = sorted(core_deps)
    dependencies = "\n".join(
        [
            f"  - {name} {version if version else not_found_str}"
            for name, version in core_deps
            #  [
            #     ("python", sys.version.split()[0]),
            #     ("pikepdf", pikepdf.__version__),
            #     ("libqpdf", pikepdf.__libqpdf_version__),
            # ]
        ]
    )

    start_year = 2025
    current_year = date.today().year

    output = VERSION_TEMPLATE.strip().format(
        whoami=WHOAMI,
        package=PACKAGE,
        project_version=get_project_version(),
        years=(str(start_year) if current_year <= start_year else f"{start_year}-{current_year}"),
        homepage=HOMEPAGE,
        docspage=DOCSPAGE,
        dependencies=dependencies,
    )

    # Plain text append for optional deps
    if optional_deps:
        output += "\n\nOptional dependencies:\n"
        for name, ver in optional_deps:
            status = ver if ver else not_found_str
            output += f"  - {name}: {status}\n"

    output = output.rstrip()

    # --- 2. Print Output ---
    console = get_console()

    # If writing to file/string, strip rich formatting
    if dest is not None and dest is not sys.stdout and dest is not sys.stderr:
        print(output, file=dest)
        return

    # If writing to terminal, use Rich
    if console is None:
        raise RuntimeError("Rich console is not available")
    console.print(output)
