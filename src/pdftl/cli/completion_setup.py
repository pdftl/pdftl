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
            cmd="--completion=bash",
        ),
    ],
)
def shell_completion_help_topic():
    """To set up basic tab completion for pdftl in `bash`:

    ```
    $ source <(pdftl --completion=bash)
    ```

    The first time you use completion for pdftl, a cache file is
    generated to speed up future runs. So the first run might feel a
    bit slow.

    Shells other than `bash` are not yet supported.

    """
    pass


def completion_setup(shell):
    """Outputs a shell completion script with baked-in fast paths."""
    ensure_dependencies(
        feature_name="shell completion",
        dependencies=["cloudpickle", "lark"],
        extra_tag="shell-completion",
    )

    from pdftl.cli import complete  # Import this to find its path

    if shell != "bash":
        raise NotImplementedError(
            f"Shell completion for '{shell}' not implemented."
            " Only bash works for now. Hint: pdftl help completion"
        )

    # 1. Find the Python interpreter running THIS venv (the fast one)
    python_exe = sys.executable

    # 2. Find the absolute path to complete.py on the user's disk
    # This resolves to something like:
    # /home/user/.local/pipx/venvs/pdftl/lib/python3.10/site-packages/pdftl/cli/complete.py
    script_path = os.path.abspath(complete.__file__)

    # 3. Generate the script
    # We bake these paths in as string literals.
    script = f"""
_pdftl_completions() {{
    local cur="${{COMP_WORDS[COMP_CWORD]}}"

    # BAKED PATHS for raw speed (bypassing entry points)
    local python_exe="{python_exe}"
    local script_path="{script_path}"

    # Call the script directly. Zero overhead.
    local output=$("$python_exe" "$script_path" "${{COMP_WORDS[@]:1:$((COMP_CWORD))}}" 2>/dev/null)

    COMPREPLY=()

    # 1. PDF Files Only
    if [[ "$output" == *"__PDF_FILE__"* ]]; then
        compopt -o filenames 2>/dev/null
        while IFS= read -r line; do
            COMPREPLY+=("$line"); done < <(compgen -d -- "$cur")
        while IFS= read -r line; do
            COMPREPLY+=("$line"); done < <(compgen -f -X "!*.pdf" -- "$cur")
    fi

    # 2. All Files
    if [[ "$output" == *"__FILE__"* ]]; then
        compopt -o filenames 2>/dev/null
        while IFS= read -r line; do COMPREPLY+=("$line"); done < <(compgen -f -- "$cur")
    fi

    # 3. Keywords
    local keywords=$(echo "$output" | sed -e 's/__PDF_FILE__//g' -e 's/__FILE__//g')
    keywords=$(echo "$keywords" | xargs)

    if [[ -n "$keywords" ]]; then
        while IFS= read -r line; do
            COMPREPLY+=("$line")
        done < <(compgen -W "$keywords" -- "$cur")
    fi
}}
complete -F _pdftl_completions pdftl
"""
    print(script)

    return 0
