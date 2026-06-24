#!/usr/bin/env python3
import sys
import re
from pathlib import Path

# Dynamically fetch the description from your app's registry
try:
    from pdftl.core.registry import registry
except ImportError:
    print("❌ Error: Could not import pdftl. Make sure it is installed or in your PYTHONPATH.")
    sys.exit(1)


def print_usage_and_exit():
    print("Usage: ./finalize_operation.py <operation-name> ['<changelog-entry>']")
    sys.exit(1)


def get_op_name_from_row(row):
    """Safely extracts the raw operation name from a markdown table row."""
    match = re.search(r"\|\s*\[`([^`]+)`\]", row)
    if match:
        return match.group(1).lower()
    return None


def get_pipe_indices(line):
    """Finds the character index of the middle and right-hand pipes in a markdown table row."""
    pipes = [i for i, char in enumerate(line) if char == "|"]
    if len(pipes) >= 3:
        # Returns the indices for the separator pipe and the ending pipe
        return pipes[1], pipes[2]
    return None, None


def main():
    if len(sys.argv) not in (2, 3):
        print_usage_and_exit()

    op_name = sys.argv[1]

    if len(sys.argv) == 3:
        changelog_entry = sys.argv[2]
    else:
        changelog_entry = f"- `{op_name}` operation"

    if not changelog_entry.strip().startswith("-"):
        changelog_entry = f"- {changelog_entry}"

    try:
        readme_desc = registry.operations[op_name].desc
        print(f"ℹ️ Found description in registry: '{readme_desc}'")
    except KeyError:
        print(f"❌ Error: Operation '{op_name}' not found in pdftl registry.")
        sys.exit(1)

    complete_py_path = Path("src/pdftl/cli/complete.py")
    readme_path = Path("README.md")
    changelog_path = Path("CHANGELOG.md")

    # --- Step 4: Surgical Insertion into HARDCODED_KEYWORDS ---
    if complete_py_path.exists():
        content = complete_py_path.read_text()
        match = re.search(r"(HARDCODED_KEYWORDS = \{)(.*?)(\})", content, re.DOTALL)
        if match:
            prefix = match.group(1)
            body = match.group(2)
            suffix = match.group(3)

            lines = body.splitlines(keepends=True)
            inserted = False

            already_exists = any(
                f'"{op_name}"' in line or f"'{op_name}'" in line for line in lines
            )

            if not already_exists:
                for i, line in enumerate(lines):
                    kw_match = re.search(r'["\']([^"\']+)["\']', line)
                    if kw_match:
                        kw = kw_match.group(1)
                        if kw > op_name:
                            indent = re.match(r"^(\s*)", line).group(1) or "    "
                            lines.insert(i, f'{indent}"{op_name}",\n')
                            inserted = True
                            break

                if not inserted:
                    indent = "    "
                    for line in reversed(lines):
                        if re.search(r'["\']([^"\']+)["\']', line):
                            indent = re.match(r"^(\s*)", line).group(1) or "    "
                            break
                    lines.append(f'{indent}"{op_name}",\n')

                updated_content = (
                    content[: match.start()]
                    + prefix
                    + "".join(lines)
                    + suffix
                    + content[match.end() :]
                )
                complete_py_path.write_text(updated_content)
                print(f"✅ Updated {complete_py_path} (inserted '{op_name}' cleanly)")
            else:
                print(f"ℹ️ '{op_name}' already exists in keywords; skipping.")
    else:
        print(f"❌ Error: {complete_py_path} not found.")

    # --- Step 5: Surgical Update of README.md (Aligned and Zero-Churn) ---
    op_url_base = "https://pdftl.readthedocs.io/en/latest/operations"
    if readme_path.exists():
        lines = readme_path.read_text().splitlines(keepends=True)
        out_lines = []
        in_table = False
        inserted = False

        for line in lines:
            if line.strip().startswith("|"):
                if "Operation" in line and "Description" in line:
                    in_table = True
                    out_lines.append(line)
                    continue

                # Skip the markdown separator row (e.g. |---|---| )
                if in_table and "---" not in line:
                    current_op = get_op_name_from_row(line)

                    # If we hit the first row alphabetically > op_name, insert right above it
                    if current_op and current_op > op_name and not inserted:
                        m_idx, r_idx = get_pipe_indices(line)
                        link = f"[`{op_name}`]({op_url_base}/{op_name}.html)"

                        if m_idx and r_idx:
                            # Pad the cells out to push the pipes to the exact same visual columns
                            c1_padded = f" {link} ".ljust(m_idx - 1)
                            c2_padded = f" {readme_desc} ".ljust(r_idx - m_idx - 1)
                            new_row = f"|{c1_padded}|{c2_padded}|\n"
                        else:
                            # Fallback if parsing fails
                            new_row = (
                                f"| [`{op_name}`]({op_url_base}/{op_name}.html) "
                                f"| {readme_desc} |\n"
                            )

                        out_lines.append(new_row)
                        inserted = True

            else:
                # If we exit the table and haven't inserted yet, append to the end of the table
                if in_table and not inserted:
                    prev_line = out_lines[-1] if out_lines else ""
                    m_idx, r_idx = get_pipe_indices(prev_line)
                    link = f"[`{op_name}`]({op_url_base}/{op_name}.html)"

                    if m_idx and r_idx:
                        c1_padded = f" {link} ".ljust(m_idx - 1)
                        c2_padded = f" {readme_desc} ".ljust(r_idx - m_idx - 1)
                        new_row = f"|{c1_padded}|{c2_padded}|\n"
                    else:
                        new_row = (
                            f"| [`{op_name}`]({op_url_base}/{op_name}.html) | {readme_desc} |\n"
                        )

                    out_lines.append(new_row)
                    inserted = True

                in_table = False

            out_lines.append(line)

        readme_path.write_text("".join(out_lines))
        print(f"✅ Updated {readme_path} (inserted '{op_name}' with matching pipe alignment)")
    else:
        print(f"❌ Error: {readme_path} not found.")

    # --- Step 6: Update CHANGELOG.md (Honoring Blank Line Convention) ---
    if changelog_path.exists():
        changelog_content = changelog_path.read_text()

        if not re.search(r"^## \[?Unreleased\]?", changelog_content, re.IGNORECASE | re.MULTILINE):
            changelog_content = re.sub(
                r"^(# .*)$", r"\1\n\n## Unreleased", changelog_content, count=1, flags=re.MULTILINE
            )

        unreleased_pattern = r"(^## \[?Unreleased\]?.*?)(?=\n## |\Z)"
        match = re.search(
            unreleased_pattern, changelog_content, re.IGNORECASE | re.DOTALL | re.MULTILINE
        )

        if match:
            unreleased_block = match.group(1)
            if changelog_entry.strip() in unreleased_block:
                print("ℹ️ Changelog entry already exists; skipping.")
            else:
                if re.search(r"^### Added", unreleased_block, re.IGNORECASE | re.MULTILINE):
                    updated_block = re.sub(
                        r"(^### Added\s*\n)",
                        f"### Added\n\n{changelog_entry}\n\n",
                        unreleased_block,
                        count=1,
                        flags=re.IGNORECASE | re.MULTILINE,
                    )
                else:
                    updated_block = re.sub(
                        r"(^## \[?Unreleased\]?\s*\n)",
                        f"## Unreleased\n\n### Added\n\n{changelog_entry}\n\n",
                        unreleased_block,
                        count=1,
                        flags=re.IGNORECASE | re.MULTILINE,
                    )

                updated_block = re.sub(r"\n{3,}", "\n\n", updated_block)
                changelog_content = changelog_content.replace(unreleased_block, updated_block)
                changelog_path.write_text(changelog_content)
                print(
                    f"✅ Updated {changelog_path} "
                    "(inserted entry conforming to spacing conventions)"
                )
    else:
        print(f"❌ Error: {changelog_path} not found.")


if __name__ == "__main__":
    main()
