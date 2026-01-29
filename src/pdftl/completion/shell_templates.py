# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/completion/shell_templates.py

BASH_TEMPLATE = """
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
"""

ZSH_TEMPLATE = """
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
"""

POWERSHELL_TEMPLATE = """
$pdftl_completer = {{
    param($wordToComplete, $commandAst, $cursorPosition)

    # 1. Extract arguments to pass to Python
    if ($null -ne $commandAst) {{
        $rawTokens = $commandAst.CommandElements | ForEach-Object {{ $_.Value }}
    }} else {{
        $rawTokens = @()
    }}

    # Remove the command itself from the start
    if ($rawTokens.Count -gt 1) {{
        $argsForPython = $rawTokens[1..($rawTokens.Count - 1)]
    }} else {{
        $argsForPython = @()
    }}

    # 2. Ask Python for the plan
    $pythonExe = "{ps_python}"
    $scriptPath = "{ps_script}"

    $output = & $pythonExe $scriptPath $argsForPython 2>$null

    $results = @()

    if ($null -ne $output) {{
        foreach ($line in $output) {{

            # --- CASE A: Python wants PDF Files ---
            if ($line -eq "__PDF_FILE__") {{
                if ($wordToComplete -notlike "-*") {{
                    $userTypedPrefix = $wordToComplete -replace '[^/\\\\]*$', ''

                    # Get files matching prefix: Directories OR files ending in .pdf
                    Get-ChildItem -Path "$wordToComplete*" |
                        Where-Object {{ $_.PSIsContainer -or $_.Name -like "*.pdf" }} |
                        ForEach-Object {{
                            if ($userTypedPrefix) {{
                                $completionText = "$userTypedPrefix$($_.Name)"
                            }} else {{ $completionText = $_.Name }}

                            # Add trailing slash for directories
                            if ($_.PSIsContainer) {{ $completionText += "/" }}

                            # Handle Spaces
                            if ($completionText -match ' ') {{
                                $completionText = "'$completionText'"
                            }}

                            $results += [System.Management.Automation.CompletionResult]::new(
                                $completionText, $completionText, 'ParameterValue', $completionText
                            )
                        }}
                }}
            }}

            # --- CASE B: Python wants ALL Files ---
            elseif ($line -eq "__FILE__") {{
                if ($wordToComplete -notlike "-*") {{
                    $userTypedPrefix = $wordToComplete -replace '[^/\\\\]*$', ''

                    Get-ChildItem -Path "$wordToComplete*" | ForEach-Object {{
                        if ($userTypedPrefix) {{ $completionText = "$userTypedPrefix$($_.Name)" }}
                        else {{ $completionText = $_.Name }}

                        if ($_.PSIsContainer) {{ $completionText += "/" }}
                        if ($completionText -match ' ') {{ $completionText = "'$completionText'" }}

                        $results += [System.Management.Automation.CompletionResult]::new(
                            $completionText, $completionText, 'ParameterValue', $completionText)
                    }}
                }}
            }}

            # --- CASE C: Python returned a Keyword ---
            elseif (
                $line -ne "__PDF_FILE__" -and
                $line -ne "__FILE__" -and
                $line -like "$wordToComplete*") {{
                $results += [System.Management.Automation.CompletionResult]::new(
                    $line, $line, 'ParameterValue', $line)
            }}
        }}
    }}
    return $results
}}

Register-ArgumentCompleter -CommandName '{whoami}' -ScriptBlock $pdftl_completer
"""
