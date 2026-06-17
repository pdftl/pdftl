# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/cli/completion_setup.py

import os
import sys

from pdftl.core.core_types import HelpExample
from pdftl.core.registry import register_help_topic
from pdftl.utils.dependencies import ensure_dependencies


@register_help_topic(
    "shell_completion",
    title="shell completion",
    desc="Getting tab completions for pdftl in your shell",
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
        HelpExample(
            desc="attempt to guess your shell and output appropriate completion code.",
            cmd="--completion",
            test_example=False,
        ),
    ],
)
def shell_completion_help_topic():
    """To set up basic tab completion for pdftl for bash, zsh or powershell:

    ### Completion in bash
    ```
    $ source <(pdftl --completion bash)
    ```

    *Bash hint:*  For improved completion behaviour in bash
    and other readline-enabled software, add the line
    ```
    set skip-completed-text on
    ```
    to `~/.inputrc` (or create that file if it does not exist).
    Then
    ```
    pdftl in.pdf render<LEFT><LEFT><TAB>
    ```
    should complete correctly to `render`
    rather than `renderer` (with unwanted trailing characters `er` after the cursor).

    ### Completion in zsh
    ```
    % source <(pdftl --completion zsh)
    ```

    ### Completion in powershell
    ```
    PS > pdftl --completion powershell | Out-String | Invoke-Expression
    ```

    ### Guessing your shell
    A bare
    ```
    pdftl --completion
    ```
    will try to guess your shell.
    This might not work, so be careful.

    ### Caching

    The first time you use completion, a cache file is generated to speed up
    future runs, which means the first run might feel a bit slow.

    The cache file lives in your user cache directory.

    ### Notes

    - ensure that pdftl is in your PATH if you want to use completion.

    - `pdftk --completion` will attempt to guess your shell and output appropriate
       code for you to source from the shell. It might fail, or be wrong.
    """
    pass


def _infer_active_shell() -> str | None:
    """Heuristic to detect bash/zsh on Unix or default to powershell on Windows."""
    # 1. Check $SHELL for Unix environments
    unix_shells = ["zsh", "bash"]

    shell_env = os.environ.get("SHELL", "").lower()
    for known_shell in unix_shells:
        if known_shell in shell_env:
            return known_shell

    try:
        shell = _infer_unix_shell(unix_shells)
        if shell is not None:
            return shell
    except OSError:
        # just continue
        pass

    if sys.platform == "win32":
        return "powershell"

    return None


def _infer_unix_shell(unix_shells):
    ppid = os.getppid()
    comm_path = f"/proc/{ppid}/comm"
    if os.path.exists(comm_path):
        # Linux and Cygwin
        with open(comm_path) as f:
            parent_name = f.read().strip().lower()
    elif sys.platform == "darwin":
        # macOS
        import ctypes
        import ctypes.util

        libproc_path = ctypes.util.find_library("proc")
        if libproc_path:
            libproc = ctypes.CDLL(libproc_path)
            buf = ctypes.create_string_buffer(1024)
            libproc.proc_name(ppid, buf, len(buf))
            parent_name = buf.value.decode("utf-8", errors="ignore").lower()
        else:
            parent_name = ""
    else:
        parent_name = ""

    for known_shell in unix_shells:
        if known_shell in parent_name:
            return known_shell

    return None


def completion_setup(shell=None):
    """Outputs a shell completion script with baked-in fast paths."""
    ensure_dependencies(
        feature_name="shell completion",
        dependencies=["cloudpickle", "lark"],
        extra_tag="shell-completion",
    )

    # Infer if not explicitly provided
    if shell is None:
        shell = _infer_active_shell()

    # Handle the fallback failure securely
    if shell is None:
        print(
            "Error: Could not automatically detect your shell.\n"
            "Please specify it explicitly, e.g.: pdftl --completion bash",
            file=sys.stderr,
        )
        return 1

    completion_scripts = _get_completion_scripts()

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
