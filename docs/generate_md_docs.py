#!/usr/bin/env python

# docs/generate_md_docs.py

"""
Generate .md and .rst source files for documentation.
"""

import sys
import os
sys.path.insert(0, os.path.abspath("../src"))

import io
import inspect
from pathlib import Path
from shutil import copyfile as cp

from common import get_docs_data

import pdftl.api
from pdftl.cli.help import print_help
from pdftl.core.types import HelpTopic, Operation


def write_help_topic_to_file(topic, filepath):
    """Write a help topic to a file (in md)"""
    buffer = io.StringIO()
    print_help(command=topic, dest=buffer, raw=True)
    markdown = buffer.getvalue().replace("# pdftl: help for", "# ")
    with open(filepath, "w") as f:
        f.write(markdown)


def write_api_reference(operations, filepath):
    """
    Generates the Python API Reference (RST format).
    """
    print(f"--- [md_gen] Generating API reference at {filepath}...")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("Python API Reference\n====================\n\n")
        f.write(".. module:: pdftl.api\n\n")
        f.write("This reference documents the dynamic Python API exposed by ``pdftl``.\n")
        f.write("All operations return an :class:`pdftl.core.types.OpResult` object.\n\n")
        f.write(
            ".. note::\n   These functions are generated dynamically at runtime via ``pdftl.api``.\n\n"
        )

        for name, op_data in operations:
            func = getattr(op_data, "function", None)
            raw_doc = None
            if func and func.__doc__:
                raw_doc = func.__doc__
            elif hasattr(op_data, "long_desc"):
                raw_doc = op_data.long_desc
            elif hasattr(op_data, "desc"):
                raw_doc = op_data.desc

            if not raw_doc:
                raw_doc = "No documentation available."

            cleaned_doc = inspect.cleandoc(raw_doc)

            try:
                sig = str(pdftl.api._create_signature(name))
            except Exception:
                sig = "(...)"

            f.write(f".. py:function:: {name}{sig}\n\n")

            for line in cleaned_doc.strip().split("\n"):
                if line.strip():
                    f.write(f"   {line}\n")
                else:
                    f.write(f"\n")

            f.write("\n\n")


def generate_md_docs(app_data, topics, output_dir="source"):
    """Generates all necessary .md and .rst files."""
    print(f"--- [md_gen] Starting docs generation in '{output_dir}'...")
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
        f.write(
            "pdftl is a capable PDF manipulation tool that works as both a CLI and a Python library.\n\n"
        )

        def heading(title):
            return f"\n.. toctree::\n   :maxdepth: 1\n   :caption: {title}:\n\n"

        f.write(heading("Overview"))
        include_project_mdfile(f, output_dir, "README.md")
        f.write(incl("overview"))
        write_help_topic_to_file(None, Path(output_dir) / "overview.md")

        # --- CLI Reference Section ---
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
            (general_topics, "CLI General topics", "general"),
            (operations, "CLI Operations", "operations"),
            (misc, "Misc", "misc"),
        ]:
            process(*x)

        # --- Python API Section ---
        f.write(heading("Python API"))
        # 1. The Tutorial
        copy_local_file(f, output_dir, "api_tutorial.md")

        # 2. The Reference
        f.write(incl("api_reference"))
        write_api_reference(operations, Path(output_dir) / "api_reference.rst")

        # --- Project files ---
        f.write(heading("Project files"))
        for x in ("CHANGELOG.md", "NOTICE.md"):
            include_project_mdfile(f, output_dir, x)
    print("--- [md_gen] Finished")


def include_project_mdfile(f, output_dir, x, y=None):
    """Copies file from PROJECT ROOT (..) to source/project and includes it"""
    project_dir = Path(output_dir) / "project"
    project_dir.mkdir(exist_ok=True)
    if y is None:
        y = x
    # Source is one level up (..) from docs/
    cp(Path("..") / x, project_dir / y)
    f.write(incl("project/" + y.replace(".md", "")))


def copy_local_file(f, output_dir, filename):
    """Copies file from DOCS ROOT (.) to source/ and includes it"""
    # Simply copy from current dir to output_dir
    cp(Path(filename), Path(output_dir) / filename)
    f.write(incl(filename.replace(".md", "")))


def incl(filetitle):
    return f"   {filetitle}\n"


if __name__ == "__main__":
    app_info, all_topics = get_docs_data()
    generate_md_docs(app_info, all_topics)
