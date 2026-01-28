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
    from pdftl.cli import complete  # Import this to find its path

    python_exe = sys.executable
    script_path = os.path.abspath(complete.__file__)
    completion_scripts = {}

    completion_scripts[
        "bash"
    ] = f"""
_pdftl_completions() {{
    local cur="${{COMP_WORDS[COMP_CWORD]}}"
    local python_exe="{python_exe}"
    local script_path="{script_path}"

    local output=$("$python_exe" "$script_path" "${{COMP_WORDS[@]:1:$((COMP_CWORD))}}" 2>/dev/null)

    COMPREPLY=()

    if [[ "$output" == *"__PDF_FILE__"* ]]; then
        compopt -o filenames 2>/dev/null
        while IFS= read -r line; do
            COMPREPLY+=("$line"); done < <(compgen -d -- "$cur")
        while IFS= read -r line; do
            COMPREPLY+=("$line"); done < <(compgen -f -X "!*.pdf" -- "$cur")
    fi

    if [[ "$output" == *"__FILE__"* ]]; then
        compopt -o filenames 2>/dev/null
        while IFS= read -r line; do COMPREPLY+=("$line"); done < <(compgen -f -- "$cur")
    fi

    local keywords=$(echo "$output" | sed -e 's/__PDF_FILE__//g' -e 's/__FILE__//g' | xargs)
    if [[ -n "$keywords" ]]; then
        while IFS= read -r line; do
            COMPREPLY+=("$line"); done < <(compgen -W "$keywords" -- "$cur")
    fi
}}
complete -F _pdftl_completions pdftl
"""  # end bash

    completion_scripts[
        "zsh"
    ] = f"""
# Check if compdef is available, if not, try to initialize it
if ! interpolation_check=$(type compdef >/dev/null 2>&1); then
    autoload -Uz compinit
    compinit
fi
_pdftl_zsh_completions() {{
    local python_exe="{python_exe}"
    local script_path="{script_path}"

    # words is the zsh array of the current command line.
    # CURRENT is the 1-based index of the cursor position.
    local output=$("$python_exe" "$script_path" "${{words[@]:1:$((CURRENT-1))}}" 2>/dev/null)

    # 1. PDF Files and Directories
    if [[ "$output" == *"__PDF_FILE__"* ]]; then
        _path_files -g '*(/)'         # Complete directories
        _path_files -g '*.pdf(.)'     # Complete PDF files
    fi

    # 2. All Files
    if [[ "$output" == *"__FILE__"* ]]; then
        _path_files -f
    fi

    # 3. Keywords (Native Zsh array filtering)
    local -a keywords
    # Split the raw output into an array by lines
    keywords=(${{(f)output}})

    # Filter out special markers and empty strings
    keywords=(${{keywords:#__PDF_FILE__}})
    keywords=(${{keywords:#__FILE__}})
    keywords=(${{keywords:#}})

    if (( ${{#keywords}} > 0 )); then
        # compadd adds the keywords to the completion list
        compadd -a keywords
    fi
}}
# Register the function
compdef _pdftl_zsh_completions pdftl
        """  # end zsh

    ps_python = python_exe.replace("\\", "\\\\")
    ps_script = script_path.replace("\\", "\\\\")
    whoami = "pdftl"

    completion_scripts[
        "powershell"
    ] = f"""
$pdftl_completer = {{
    param($wordToComplete, $commandAst, $cursorPosition)

    if ($null -ne $commandAst) {{
        $rawTokens = $commandAst.CommandElements | ForEach-Object {{ $_.Value }}
    }} else {{
        $rawTokens = @()
    }}

    if ($rawTokens.Count -gt 1) {{
        $argsForPython = $rawTokens[1..($rawTokens.Count - 1)]
    }} else {{
        $argsForPython = @()
    }}

    $output = & "{ps_python}" "{ps_script}" $argsForPython 2>$null

    if ($null -ne $output) {{
        $results = @()
        foreach ($line in $output) {{
            if ($line -eq "__PDF_FILE__" -or $line -eq "__FILE__") {{
                if ($wordToComplete -notlike "-*") {{
                    $userTypedPrefix = $wordToComplete -replace '[^/\\\\]*$', ''

                    Get-ChildItem -Path "$wordToComplete*" | ForEach-Object {{
                        if ($userTypedPrefix) {{
                            $completionText = "$userTypedPrefix$($_.Name)"
                        }} else {{
                            $completionText = $_.Name
                        }}

                        if ($completionText -match ' ') {{ $completionText = "'$completionText'" }}

                        $results += [System.Management.Automation.CompletionResult]::new(
                               $completionText, $completionText, 'ParameterValue', $completionText)
                    }}
                }}
            }} elseif ($line -like "$wordToComplete*") {{
                $results += [System.Management.Automation.CompletionResult]::new(
                                              $line, $line, 'ParameterValue', $line)
            }}
        }}
        return $results
    }}
}}

Register-ArgumentCompleter -CommandName '{whoami}' -ScriptBlock $pdftl_completer
        """  # end powershell
    return completion_scripts
