#!/usr/bin/env python

# docs/generate_md_docs.py

"""
Generate .md source files
"""

import io
import os
import re
from datetime import date
from pathlib import Path
from shutil import copyfile as cp

from common import get_docs_data

from pdftl.cli.help import print_help
from pdftl.core.types import HelpTopic, Operation, Option


def write_help_topic_to_file(topic, filepath):
    """Write a help topic to a file (in md)"""
    buffer = io.StringIO()
    print_help(command=topic, dest=buffer, raw=True)
    markdown = buffer.getvalue().replace("# pdftl: help for", "# ")
    with open(filepath, "w") as f:
        f.write(markdown)


def generate_md_docs(app_data, topics, output_dir="source"):
    """Generates all necessary .md files."""
    print(f"--- [md_gen] Starting md source generation in '{output_dir}'...")
    operations = sorted([item for item in topics.items() if isinstance(item[1], Operation)])
    general_topics = sorted([item for item in topics.items() if isinstance(item[1], HelpTopic)])
    misc = sorted(
        [item for item in topics.items() if item not in operations and item not in general_topics]
    )

    print(f"--- [md_gen] Found {len(operations)} operations.")
    print(
        f"--- [md_gen] Found {len(general_topics)} general topics: {[t[0] for t in general_topics]}"
    )

    # --- Generate index.rst ---
    print("--- [md_gen] Generating index.rst...")
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    with open(os.path.join(output_dir, "index.rst"), "w", encoding="utf-8") as f:
        f.write("pdftl Documentation\n===================\n\n")
        f.write("Welcome to the documentation for pdftl.\n\n")
        f.write("pdftl is self-documenting: try pdftl help.\n\n")

        def heading(title):
            return f"\n.. toctree::\n   :maxdepth: 1\n   :caption: {title}:\n\n"

        f.write(heading("Overview"))
        include_project_mdfile(f, output_dir, "README.md")
        f.write(incl("overview"))
        write_help_topic_to_file(None, Path(output_dir) / "overview.md")

        def process(topic_list, title, folder="."):
            if topic_list:
                f.write(heading(title))
                for name, _data in topic_list:
                    write_dir = Path(output_dir) / Path(folder)
                    Path(write_dir).mkdir(exist_ok=True)
                    filename = write_dir / (name + ".md")
                    f.write(incl(f"{folder}/{name}"))
                    write_help_topic_to_file(name, filename)

        for x in [
            (general_topics, "General topics", "general"),
            (operations, "Operations", "operations"),
            (misc, "Misc", "misc"),
        ]:
            process(*x)

        f.write(heading("Project files"))
        for x in ("CHANGELOG.md", "NOTICE.md"):
            include_project_mdfile(f, output_dir, x)
    print("--- [md_gen] Finished")


def include_project_mdfile(f, output_dir, x, y=None):
    project_dir = Path(output_dir) / "project"
    project_dir.mkdir(exist_ok=True)
    if y is None:
        y = x
    cp(Path("..") / x, project_dir / y)
    f.write(incl("project/" + y.replace(".md", "")))


def incl(filetitle):
    return f"   {filetitle}\n"


if __name__ == "__main__":
    app_info, all_topics = get_docs_data()
    generate_md_docs(app_info, all_topics)
