# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/cli/completion_setup.py

import os
import sys

from pdftl.core.registry import register_help_topic
from pdftl.core.types import HelpExample
from pdftl.utils.dependencies import ensure_dependencies


@register_help_topic(
    "shell completion",
    title="shell completion",
    desc="Getting tab completions in your shell",
    examples=[
        HelpExample(
            desc="output bash completion code. This must be sourced from bash.",
            cmd="--completion bash",
        ),
        HelpExample(
            desc="output zsh completion code. This must be sourced from zsh.",
            cmd="--completion zsh",
        ),
        HelpExample(
            desc="output powershell completion code. This must be sourced from powershell.",
            cmd="--completion powershell",
        ),
    ],
)
def shell_completion_help_topic():
    """To set up basic tab completion for pdftl:

    Bash:
    ```
    $ source <(pdftl --completion bash)
    ```

    Zsh:
    ```
    % source <(pdftl --completion zsh)
    ```

    Powershell:
    ```
    PS > pdftl --completion powershell | Out-String | Invoke-Expression
    ```

    The first time you use completion, a cache file is generated to speed up
    future runs, which means the first run might feel a bit slow.

    **Note**: ensure that pdftl is in your PATH if you want to use completion.
    """
    pass


def completion_setup(shell):
    """Outputs a shell completion script with baked-in fast paths."""
    ensure_dependencies(
        feature_name="shell completion",
        dependencies=["cloudpickle", "lark"],
        extra_tag="shell-completion",
    )

    completion_scripts = _get_completion_scripts()

    # 1. Common baked paths

    if shell in completion_scripts:
        print(completion_scripts[shell])
    else:
        raise NotImplementedError(
            f"Shell completion for '{shell}' is not available. Hint: pdftl help completion"
        )
    return 0


def _get_completion_scripts():
    import pdftl.completion.shell_templates as templates
    from pdftl.cli import complete  # Import this to find its path

    python_exe = sys.executable
    script_path = os.path.abspath(complete.__file__)
    ps_python = python_exe.replace("\\", "\\\\")
    ps_script = script_path.replace("\\", "\\\\")
    whoami = "pdftl"

    template_vars = {
        "python_exe": python_exe,
        "script_path": script_path,
        "ps_python": ps_python,
        "ps_script": ps_script,
        "whoami": whoami,
    }

    shells = ["bash", "zsh", "powershell"]
    completion_scripts = {
        shell: getattr(templates, f"{shell.upper()}_TEMPLATE").format(**template_vars)
        for shell in shells
    }
    return completion_scripts
