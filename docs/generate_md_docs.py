#!/usr/bin/env python

# docs/generate_md_docs.py

"""
Generate .md and .rst source files for documentation.
"""

import os
import sys

sys.argv[0] = "pdftl"  # for WHOAMI
sys.path.insert(0, os.path.abspath("../src"))

import inspect
import io
from pathlib import Path
from shutil import copyfile as cp

from common import get_docs_data

import pdftl.api
from pdftl.cli.help import print_help
from pdftl.core.core_types import HelpTopic, Operation


# docs/generate_md_docs.py

import re


def write_help_topic_to_file(topic, filepath, folder=".", all_link_targets=None):
    """Write a help topic to a file (in md) and inject relative autolinks."""
    buffer = io.StringIO()
    print_help(command=topic, dest=buffer, raw=True)
    markdown = buffer.getvalue().replace("# pdftl: help for", "# ")

    # Run the autolinker if targets are supplied
    if all_link_targets:
        markdown = _apply_autolinks(markdown, topic, folder, all_link_targets)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(markdown)


def _apply_autolinks(text: str, current_topic: str, current_folder: str, targets: dict) -> str:
    """
    Scans generated markdown and transforms bare operation/topic words
    into explicit relative markdown links using a reliable single-pass or isolated match strategy.
    """
    # Sort targets by length descending so longer words like 'dump_data_fields'
    # match before shorter substrings like 'dump_data'
    sorted_names = sorted(targets.keys(), key=len, reverse=True)

    for name in sorted_names:
        if name == current_topic:
            continue

        target_folder = targets[name]

        # Calculate paths safely
        if current_folder == ".":
            rel_path = f"{target_folder}/{name}.md"
        else:
            if current_folder == target_folder:
                rel_path = f"{name}.md"
            else:
                rel_path = f"../{target_folder}/{name}.md"

        # A more robust pattern that strictly hunts for backtick targets and captures them cleanly
        pattern = rf"`({re.escape(name)})`"

        # Only replace if it hasn't already been converted to a markdown link format
        text = re.sub(pattern, rf"[`\1`](<{rel_path}>)", text)

    return text


def write_api_reference(operations, filepath):
    """
    Generates the Python API Reference (RST format) by translating
    Markdown artifacts into clean, proper native RST elements.
    """
    print(f"--- [md_gen] Generating API reference at {filepath}...")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("Python API Reference\n====================\n\n")
        f.write(".. module:: pdftl.api\n\n")
        f.write("This reference documents the dynamic Python API exposed by ``pdftl``.\n")
        f.write("All operations return an :class:`pdftl.core.types.OpResult` object.\n\n")
        f.write(
            ".. note::\n   These functions are generated dynamically at runtime "
            "via ``pdftl.api``.\n\n"
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
            except Exception:  # noqa: BLE001
                sig = "(...)"

            f.write(f".. py:function:: {name}{sig}\n\n")

            lines = cleaned_doc.strip().split("\n")
            in_code_block = False

            for line in lines:
                stripped = line.strip()

                # 1. Handle Markdown Code Fence Blocks
                if stripped.startswith("```"):
                    if not in_code_block:
                        in_code_block = True
                        lang = stripped.replace("```", "").strip() or "text"
                        f.write(f"   .. code-block:: {lang}\n\n")
                    else:
                        in_code_block = False
                        f.write("\n")
                    continue

                # If we are rendering inside a code fence block, preserve standard spacing
                if in_code_block:
                    f.write(f"      {line}\n")
                    continue

                if stripped:
                    # 2. Convert Markdown Headers (e.g., ### Options) to bold RST text
                    if stripped.startswith("### ") or stripped.startswith("## "):
                        header_text = stripped.lstrip("# ").strip()
                        f.write(f"   **{header_text}**\n")

                    # 3. Clean inline backtick references (Markdown backticks work in RST
                    # paragraphs)
                    else:
                        # Clean backtick escapes if they got messy
                        processed = line.replace(r"\`", "`")
                        f.write(f"   {processed}\n")
                else:
                    f.write("\n")

            f.write("\n\n")


def generate_md_docs(app_data, topics, output_dir="source"):
    """Generates all necessary .md and .rst files with active cross-links."""
    print(f"--- [md_gen] Starting docs generation in '{output_dir}'...")
    operations = sorted([item for item in topics.items() if isinstance(item[1], Operation)])
    general_topics = sorted([item for item in topics.items() if isinstance(item[1], HelpTopic)])
    misc = sorted(
        [item for item in topics.items() if item not in operations and item not in general_topics]
    )

    # 1. BUILD A LOOKUP MAP OF ALL AUTOLINK TARGETS AND THEIR FOLDERS
    link_targets = {}
    for name, _ in general_topics:
        link_targets[name] = "general"
    for name, _ in operations:
        link_targets[name] = "operations"
    for name, _ in misc:
        link_targets[name] = "misc"

    print(f"--- [md_gen] Map compiled. Tracking {len(link_targets)} autolink terms.")

    # --- Generate index.rst ---
    print("--- [md_gen] Generating index.rst...")
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    with open(os.path.join(output_dir, "index.rst"), "w", encoding="utf-8") as f:
        f.write("pdftl Documentation\n===================\n\n")
        f.write("Welcome to the documentation for pdftl.\n\n")
        f.write(
            "pdftl is a capable PDF manipulation tool that works as both a CLI and "
            "a Python library.\n\n"
        )

        def heading(title):
            return f"\n.. toctree::\n   :maxdepth: 1\n   :caption: {title}:\n\n"

        f.write(heading("Overview"))
        include_project_mdfile(f, output_dir, "README.md")
        f.write(incl("overview"))

        # Write overview at root folder level
        write_help_topic_to_file(
            None, Path(output_dir) / "overview.md", folder=".", all_link_targets=link_targets
        )

        # --- CLI Reference Section ---
        def process(topic_list, title, folder="."):
            if topic_list:
                f.write(heading(title))
                for name, _data in topic_list:
                    write_dir = Path(output_dir) / Path(folder)
                    Path(write_dir).mkdir(exist_ok=True)
                    filename = write_dir / (name + ".md")
                    f.write(incl(f"{folder}/{name}"))

                    # Pass the folder tracker down into the engine
                    write_help_topic_to_file(
                        name, filename, folder=folder, all_link_targets=link_targets
                    )

        for x in [
            (general_topics, "CLI General topics", "general"),
            (operations, "CLI Operations", "operations"),
            (misc, "Misc", "misc"),
        ]:
            process(*x)

        # --- Python API Section ---
        f.write(heading("Python API"))
        copy_local_file(f, output_dir, "api_tutorial.md")

        f.write(incl("api_reference"))
        write_api_reference(operations, Path(output_dir) / "api_reference.rst")

        # --- Project files ---
        f.write(heading("Project files"))
        for x in ("CHANGELOG.md", "NOTICE.md"):
            include_project_mdfile(f, output_dir, x)

        # Dynamically recreate _static and copy custom.css after clean target wipes it
        static_dir = Path(output_dir) / "_static"
        static_dir.mkdir(parents=True, exist_ok=True)
        if os.path.exists("custom.css"):
            cp("custom.css", static_dir / "custom.css")
            print("--- [md_gen] Copied custom.css to source/_static/")

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
