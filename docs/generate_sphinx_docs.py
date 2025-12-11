# docs/generate_sphinx_docs.py
"""
Generates .rst source files and a default conf.py for a Sphinx documentation site.
"""

import os
import re
from datetime import date

from common import get_docs_data

from pdftl.cli.help import print_main_help


def rst_escape(text):
    """Escapes characters for reStructuredText."""
    return text.replace("*", "\\*").replace("_", "\\_").replace("`", "\\`")


def format_rst_prose(text, bold_literals):
    """Formats a block of prose, bolding quoted keywords."""

    def repl(match):
        word = match.group(1)
        if word in bold_literals:
            return f"**{rst_escape(word)}**"
        return f"'{rst_escape(word)}'"

    return re.sub(r"'(\w+)'", repl, rst_escape(text))


def create_sphinx_config(app_data, output_dir):
    """Creates a default conf.py in the output directory if one does not exist."""
    conf_path = os.path.join(output_dir, "conf.py")
    if os.path.exists(conf_path):
        return

    print(f"--- [sphinx_gen] Creating a default 'conf.py' in {output_dir}.")

    year = date.today().year
    project_name = app_data.get("name", "pdftl")

    conf_content = f"""# ... (conf.py content as before) ...
project = '{project_name}'
copyright = '{year}, The {project_name} developers'
author = 'The {project_name} developers'
html_theme = 'sphinx_rtd_theme'
html_static_path = ['_static']
"""
    with open(conf_path, "w", encoding="utf-8") as f:
        f.write(conf_content)


def generate_sphinx_docs(app_data, topics, output_dir="source"):
    """Generates all necessary .rst files for the Sphinx project."""
    print(f"--- [sphinx_gen] Starting Sphinx source generation in '{output_dir}'...")
    commands_dir = os.path.join(output_dir, "commands")
    static_dir = os.path.join(output_dir, "_static")
    os.makedirs(commands_dir, exist_ok=True)
    os.makedirs(static_dir, exist_ok=True)

    create_sphinx_config(app_data, output_dir)

    bold_literals = set(topics.keys())
    if app_data.get("options"):
        for name in app_data["options"].keys():
            bold_literals.add(name.split(" ", 1)[0])

    # --- Generate index.rst ---
    print("--- [sphinx_gen] Generating index.rst...")
    with open(os.path.join(output_dir, "index.rst"), "w", encoding="utf-8") as f:
        f.write("pdftl Documentation\n===================\n\n")
        f.write("Welcome to the documentation for pdftl.\n\n")
        f.write("pdftl is self-documenting: try pdftl help.\n\n")
        f.write(
            "This static documentation is automatically generated and contains numerous bugs.\n\n"
        )

        operations = sorted(
            [item for item in topics.items() if item[1]["type"] == "operation"]
        )
        general_topics = sorted(
            [item for item in topics.items() if item[1]["type"] == "topic"]
        )

        print(f"--- [sphinx_gen] Found {len(operations)} operations.")
        print(
            f"--- [sphinx_gen] Found {len(general_topics)} general topics: {[t[0] for t in general_topics]}"
        )

        f.write("\n.. toctree::\n   :maxdepth: 2\n   :caption: Overview:\n\n")
        f.write("   overview\n")

        f.write("\n.. toctree::\n   :maxdepth: 2\n   :caption: General Topics:\n\n")
        for name, data in general_topics:
            f.write(f"   commands/{name}\n")

        f.write(".. toctree::\n   :maxdepth: 2\n   :caption: Operations:\n\n")
        for name, data in operations:
            f.write(f"   commands/{name}\n")

    # --- Generate a file for each command and topic ---
    print("--- [sphinx_gen] Generating overview,rst...")
    filepath = f"{output_dir}/overview.rst"
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("Overview\n=======\n\n::\n\n")

        def hprint(text="\n"):
            f.write("  " + text.replace("\n", "\n  ") + "\n  ")

        print_main_help(hprint)

    print(
        f"--- [sphinx_gen] Generating individual .rst files for {len(topics)} topics..."
    )
    for name, data in topics.items():
        print(f"--- [sphinx_gen]   -> Creating commands/{name}.rst")
        filepath = os.path.join(commands_dir, f"{name}.rst")
        with open(filepath, "w", encoding="utf-8") as f:
            title = data.get("title", name)
            f.write(f"{title}\n{'=' * len(title)}\n\n")
            if data.get("desc"):
                f.write(f"*{rst_escape(data['desc'])}*\n\n")
            if data.get("usage"):
                f.write("**Usage**\n\n.. code-block:: shell\n\n")
                f.write(f"   {app_data['whoami']} {data['usage']}\n\n")
            if data.get("details"):
                f.write("**Details**\n\n")
                # ** THE FIX IS HERE **
                # The 'output_options' topic has pre-formatted RST, so write it directly.
                # For all other topics, format the plain text as prose.
                if name == "output_options":
                    f.write(data["details"] + "\n\n")
                else:
                    details_text = data["details"].strip()
                    f.write(format_rst_prose(details_text, bold_literals) + "\n\n")
            if data.get("examples"):
                f.write("**Examples**\n\n")
                for ex in data["examples"]:
                    if ex.get("desc"):
                        f.write(f"{rst_escape(ex['desc'])}\n\n")
                    f.write(".. code-block:: shell\n\n")
                    f.write(f"   {app_data['whoami']} {ex['cmd']}\n\n")

    print("--- [sphinx_gen] Successfully generated Sphinx source files.")


if __name__ == "__main__":
    app_info, all_topics = get_docs_data()
    generate_sphinx_docs(app_info, all_topics)
