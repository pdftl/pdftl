# docs/common.py
"""
A common module to load and structure application data for documentation generation.
This acts as a single source of truth for the doc generation scripts.
"""
import sys
from pathlib import Path

# Add the project's 'src' directory to the Python path to allow direct imports.
# This assumes the script is run from the project root (e.g., python docs/script.py)
try:
    print("--- [common.py] Trying to import pdftl modules...")
    src_path = str(Path(__file__).parent.parent / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

    from pdftl.cli.help import get_synopsis
    from pdftl.cli.whoami import HOMEPAGE, PACKAGE, WHOAMI
    from pdftl.core.registry import registry
    from pdftl.registry_init import initialize_registry

    print("--- [common.py] Imports successful.")
except ImportError as e:
    print(
        "Error: Could not import from 'pdftl'. Is 'src' the correct source directory?",
        file=sys.stderr,
    )
    print(f"Details: {e}", file=sys.stderr)
    sys.exit(1)


def get_docs_data():
    """
    Initializes the application registry and collates all data needed for
    documentation into a structured format.
    """
    print("--- [common.py] Initializing registry...")
    initialize_registry()
    print("--- [common.py] Registry initialized.")

    # Main application metadata
    app_info = {
        "name": PACKAGE,
        "whoami": WHOAMI,
        "description": "A wannabe CLI compatible clone/extension of pdftk",
        "homepage": HOMEPAGE,
        "synopsis": get_synopsis(),
        # This is the complete, merged list of options
        "options": {
            **registry.options,
        },
    }

    # Collate all operations and extra help topics into a single dictionary
    all_topics = {}

    # Process operations from the registry
    for name, data in registry.operations.items():
        all_topics[name] = data

    # Process extra help topics from the static data file
    for name, data in registry.help_topics.items():
        all_topics[name] = data

    # build complete options topic **
    if app_info["options"]:
        print("--- [common.py] Found output options. Constructing 'output_options' topic...")
        details_lines = []
        # Sort options alphabetically for consistent output
        sorted_options = sorted(app_info["options"].items())

        for name, data in sorted_options:
            details_lines.append(f"``{name}``")
            desc = data.get("desc", "")
            details_lines.append(f"  {desc}")
            if data.get("long_desc"):
                # Add a blank line for spacing and then the indented block
                details_lines.append("")
                # Correctly indent the pre-formatted long description
                indented_long_desc = "\n".join(
                    f"    {line}" for line in data["long_desc"].strip().split("\n")
                )
                details_lines.append(indented_long_desc)
            details_lines.append("")  # Add a blank line for spacing between entries

        all_topics["output_options"] = {
            "title": "Output Options",
            "desc": "Options to control PDF output processing.",
            "details": "\n".join(details_lines),
            "type": "topic",
        }
        print("--- [common.py] 'output_options' topic created successfully.")
    else:
        print("--- [common.py] No output options found to document.")

    # # For all topics, rename 'long_desc' to 'details' for consistency.
    # for name, data in all_topics.items():
    #     desc_key = "long_desc" if "long_desc" in data else None
    #     if desc_key:
    #         data["details"] = data.pop(desc_key)

    print(f"--- [common.py] Finished data prep. Returning {len(all_topics)} total topics.")
    return app_info, all_topics
