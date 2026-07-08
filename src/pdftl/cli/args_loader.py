# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/cli/args_loader.py

"""Argument loading pre-processor for pdftl."""

import logging
from pathlib import Path

from pdftl.core.core_types import HelpExample
from pdftl.core.registry import register_help_topic
from pdftl.exceptions import UserCommandLineError

logger = logging.getLogger(__name__)

MAX_DEPTH = 10


@register_help_topic(
    "args",
    title="loading arguments",
    desc="Load command-line arguments from a YAML file",
    examples=[
        HelpExample(
            desc="Load arguments from a YAML file",
            cmd="--args my_pipeline.yml",
            test_setup={"copy_assets": {"my_pipeline.yml": "my_pipeline.yml"}},
        ),
        HelpExample(
            desc="Mix direct arguments and loaded arguments",
            cmd="in.pdf --args operations.yml output out.pdf",
            test_setup={
                "copy_assets": {"2_page.pdf": "2_page.pdf", "operations.yml": "operations.yml"}
            },
        ),
    ],
)
def _args_help_topic():
    r"""You can use `--args <file>` anywhere in your command line to load arguments
    from a YAML file. This is recommended for complex workflows, escaping
    issues, or bypassing your operating system's command-line length limits.

    ### YAML Format

    The file must be a flat YAML list of strings.

    ```yaml
    - in.pdf
    - cat
    - 1-3
    - output
    - out.pdf
    ```

    Nested lists are flattened. This may be useful for organizing your files:

    ```yaml
    - # Input files
      - a.pdf
      - b.pdf
    - cat
    ```

    ### Pipeline Separators

    If you use `---` to separate YAML documents, it acts exactly like the pipeline
    separator (`---`) on the command line.

    ```yaml
    - in.pdf
    - rotate
    - right
    ---
    - stamp
    - logo.pdf
    - output
    - out.pdf
    ```

    ### Escaping and Windows Paths

    One consequence of using YAML is that unquoted strings treat
    backslashes and spaces as literal characters. This makes Windows paths
    safe without escaping.

    ```yaml
    - C:\Users\Name\My Document.pdf
    ```

    If your filename contains a space followed by a hash (e.g., `File #1.pdf`),
    YAML will treat the hash as the start of a comment.
    To fix this, wrap the line in single quotes:

    ```yaml
    - 'C:\Users\Name\File #1.pdf'
    ```

    ### Comments

    You can use `#` to add comments to your argument files to document your workflow.

    ```yaml
    # Apply the final watermark
    - stamp
    - watermark.pdf
    ```
    """
    pass


def flatten_and_stringify(items):
    """
    Recursively flattens lists and stringifies scalars.
    Raises an error if a dictionary is encountered.
    """
    if isinstance(items, dict):
        raise UserCommandLineError(
            "YAML dictionaries are not supported in arguments files. Please use a flat list."
        )

    if not isinstance(items, list):
        return [str(items)] if items is not None else []

    flat_list = []
    for item in items:
        flat_list.extend(flatten_and_stringify(item))
    return flat_list


def load_yaml_args(file_path):
    """Loads and parses a YAML file into a flat list of string arguments."""
    from pdftl.utils.dependencies import ensure_dependencies

    ensure_dependencies(
        feature_name="argument loading",
        dependencies={"yaml": "PyYAML"},
        extra_tag="yaml",
    )

    import yaml

    path = Path(file_path)
    if not path.exists() or path.is_dir():
        raise UserCommandLineError(f"Arguments file not found: {file_path}")

    final_args = []
    try:
        with open(path, encoding="utf-8") as f:
            documents = list(yaml.safe_load_all(f))
    except yaml.YAMLError as exc:
        raise UserCommandLineError(f"Error parsing YAML args file: {exc}") from exc
    except OSError as exc:
        raise UserCommandLineError(f"Error reading args file '{file_path}': {exc}") from exc

    valid_docs = [doc for doc in documents if doc is not None]
    for i, doc in enumerate(valid_docs):
        if isinstance(doc, str):
            raise UserCommandLineError(
                f"Pipeline segment {i + 1} in '{file_path}' was parsed as a single string.\n"
                "Did you forget to start your arguments with '- ' (hyphen and space)?"
            )

        try:
            flattened = flatten_and_stringify(doc)
        except UserCommandLineError as exc:
            raise UserCommandLineError(
                f"Error in pipeline segment {i + 1} of '{file_path}': {exc}"
            ) from exc

        final_args.extend(flattened)

        # Re-inject the pipeline separator natively between valid documents
        if i < len(valid_docs) - 1:
            final_args.append("---")

    return final_args


def expand_args(arg_list, depth=0, expansions=None, parent_path=None):
    """
    Pre-processes the argument list to recursively expand `--args <file>`.
    """
    if depth > MAX_DEPTH:
        raise UserCommandLineError(
            f"Maximum argument file inclusion depth ({MAX_DEPTH}) exceeded. "
            "Circular reference suspected."
        )

    expanded = []
    idx = 0
    while idx < len(arg_list):
        item = arg_list[idx]
        if item == "--args":
            if idx + 1 >= len(arg_list):
                raise UserCommandLineError(
                    "The '--args' flag was provided without a target file path."
                )
            file_path = arg_list[idx + 1]

            if expansions is not None:
                expansions.append(file_path)

            logger.debug(
                "Expanding argument file: %s (depth=%d, parent=%s)", file_path, depth, parent_path
            )

            try:
                loaded_args = load_yaml_args(file_path)
                nested_expanded = expand_args(
                    loaded_args, depth + 1, expansions, parent_path=file_path
                )
            except UserCommandLineError as exc:
                raise UserCommandLineError(
                    f"Error expanding '{file_path}' (depth {depth}): {exc}"
                ) from exc

            expanded.extend(nested_expanded)

            idx += 2
        else:
            expanded.append(item)
            idx += 1

    return expanded
