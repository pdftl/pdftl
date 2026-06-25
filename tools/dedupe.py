#!/usr/bin/env python

import ast
import subprocess
import sys
import tempfile
from pathlib import Path


def fix_duplicate_functions(file_path_str: str):
    file_path = Path(file_path_str)
    if not file_path.exists():
        print(f"Error: {file_path} not found.")
        return

    source = file_path.read_text()
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source)

    # Dictionary keys will be a tuple: (scope_name, function_name)
    # scope_name is "module" for top-level functions, or the class name.
    func_instances = {}

    # 1. Map out where all functions and class methods live
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            start = node.lineno - 1
            end = node.end_lineno
            func_instances.setdefault(("module", node.name), []).append((start, end))

        elif isinstance(node, ast.ClassDef):
            # Inspect inside the class body for methods
            for sub_node in node.body:
                if isinstance(sub_node, ast.FunctionDef):
                    start = sub_node.lineno - 1
                    end = sub_node.end_lineno
                    func_instances.setdefault((node.name, sub_node.name), []).append((start, end))

    # Filter down to only definitions that actually have duplicates in the same scope
    duplicates = {key: insts for key, insts in func_instances.items() if len(insts) > 1}

    if not duplicates:
        print("No duplicate function or method definitions found by AST parser!")
        return

    print(f"Found {len(duplicates)} duplicated items.\n")
    print("--- ANALYSIS ---")

    # 2. Build a list of modifications bottom-up
    actions = []
    for (scope, name), insts in duplicates.items():
        display_name = f"{scope}.{name}" if scope != "module" else name

        # Get the code for the last instance (the one we want to keep)
        last_start, last_end = insts[-1]
        replacement_text = "".join(lines[last_start:last_end])

        # Get the code for the first instance
        first_start, first_end = insts[0]
        original_text = "".join(lines[first_start:first_end])

        # Check if the contents are identical
        is_identical = original_text == replacement_text

        if is_identical:
            print(
                f" • '{display_name}': Implementation at line {last_start + 1} "
                f"is IDENTICAL to line {first_start + 1}."
            )
            print("   [Action] Replacing top block anyway and deleting trailing duplicates.")
        else:
            print(
                f" ⚠ '{display_name}': Implementation at line {last_start + 1} "
                f"has CHANGES compared to line {first_start + 1}!"
            )
            print("   [Action] OVERWRITING original implementation with the newer version.")

        # Queue the replacement at the first position
        actions.append(("replace", first_start, first_end, replacement_text, display_name))

        # Delete all subsequent duplicate blocks (including the last one we copied)
        for extra_start, extra_end in insts[1:]:
            actions.append(("delete", extra_start, extra_end, "", display_name))

    print("----------------\n")

    # PAUSE FOR READING
    input("Press Enter to view the git diff...")
    print("\n")

    # Sort actions from bottom of the file to the top to preserve indices
    actions.sort(key=lambda x: x[1], reverse=True)

    # 3. Apply the actions to a copy of the lines
    new_lines = list(lines)
    for action_type, start, end, text, name in actions:
        if action_type == "replace":
            new_lines[start:end] = [text]
        elif action_type == "delete":
            new_lines[start:end] = []

    new_source = "".join(new_lines)

    # 4. Use `git diff --no-index` for a beautiful preview
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tmp:
        tmp.write(new_source)
        tmp_path = Path(tmp.name)

    print("--- PROPOSED CHANGES (GIT DIFF) ---")
    try:
        # --color=always forces git to emit color codes even when piping/capturing
        subprocess.run(
            ["git", "diff", "--no-index", "--color=always", str(file_path), str(tmp_path)],
            check=False,
        )
    finally:
        print("-----------------------------------\n")

    # 5. Safety prompt before writing
    try:
        confirm = input("Does this git diff look correct? Write changes? (y/N): ").strip().lower()
        if confirm == "y":
            # Create a backup file just in case
            bak_path = file_path.with_suffix(".py.bak")
            file_path.rename(bak_path)
            print(f"Created backup at {bak_path}")

            # Write the fresh cleaned source
            file_path.write_text(new_source)
            print(f"Successfully updated {file_path}!")
        else:
            print("Aborted. No files were modified.")
    finally:
        # Clean up the temporary file safely
        if tmp_path.exists():
            tmp_path.unlink()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python dedupe.py <path_to_file>")
        sys.exit(1)
    fix_duplicate_functions(sys.argv[1])
