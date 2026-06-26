#!/usr/bin/env python3
import re
import sys
from pathlib import Path

from pdftl.core.registry import registry


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from tools.api_stub_gen import generate as generate_stubs


def print_usage_and_exit():
    print("Usage: ./finalize_operation.py <operation-name> ['<changelog-entry>']")
    sys.exit(1)


def get_op_name_from_row(row):
    """Extracts the raw operation name from a markdown table row."""
    match = re.search(r"\|\s*\[`([^`]+)`\]", row)
    if match:
        return match.group(1).lower()
    return None


def get_pipe_indices(line):
    """Finds the character index of the middle and right-hand pipes in a markdown table row."""
    pipes = [i for i, char in enumerate(line) if char == "|"]
    if len(pipes) >= 3:
        return pipes[1], pipes[2]
    return None, None


def update_autocomplete_keywords(complete_py_path: Path, op_name: str):
    """Inserts the operation name into HARDCODED_KEYWORDS in autocomplete configuration."""
    if not complete_py_path.exists():
        print(f"❌ Error: {complete_py_path} not found.")
        return None

    content = complete_py_path.read_text()
    match = re.search(r"(HARDCODED_KEYWORDS = \{)(.*?)(\})", content, re.DOTALL)
    if not match:
        print("❌ Error: HARDCODED_KEYWORDS not found.")
        return None

    prefix, body, suffix = match.group(1), match.group(2), match.group(3)
    lines = body.splitlines(keepends=True)

    already_exists = any(f'"{op_name}"' in line or f"'{op_name}'" in line for line in lines)
    if already_exists:
        print(f"ℹ️ '{op_name}' already exists in keywords; skipping.")
        return False

    inserted = False
    for i, line in enumerate(lines):
        kw_match = re.search(r'["\']([^"\']+)["\']', line)
        if kw_match and kw_match.group(1) > op_name:
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
        content[: match.start()] + prefix + "".join(lines) + suffix + content[match.end() :]
    )
    complete_py_path.write_text(updated_content)
    print(f"✅ Updated {complete_py_path} (inserted '{op_name}' cleanly)")
    return True


def build_aligned_row(op_name: str, readme_desc: str, m_idx: int | None, r_idx: int | None) -> str:
    """Calculates column lengths and returns a visually padded markdown table row."""
    op_url_base = "https://pdftl.readthedocs.io/en/latest/operations"
    link = f"[`{op_name}`]({op_url_base}/{op_name}.html)"

    if m_idx and r_idx:
        c1_padded = f" {link} ".ljust(m_idx - 1)
        c2_padded = f" {readme_desc} ".ljust(r_idx - m_idx - 1)
        return f"|{c1_padded}|{c2_padded}|\n"

    return f"| {link} | {readme_desc} |\n"


def _process_table_row(line: str, op_name: str, state: dict) -> None:
    """Updates table alignment columns, matches existing keys, and tracks insertion index."""
    if state["m_idx"] is None:
        state["m_idx"], state["r_idx"] = get_pipe_indices(line)

    current_op = get_op_name_from_row(line)
    if current_op == op_name:
        state["already_exists"] = True

    if current_op and current_op > op_name and state["insert_index"] == -1:
        state["insert_index"] = state["current_index"]


def _find_table_insert_position(lines: list[str], op_name: str):
    """Finds layout bounds, target insertion point, and columns for alignment."""
    state = {
        "table_start": -1,
        "table_end": -1,
        "insert_index": -1,
        "m_idx": None,
        "r_idx": None,
        "already_exists": False,
        "current_index": -1,
    }
    inside_table = False

    for i, line in enumerate(lines):
        state["current_index"] = i
        clean_line = line.strip()

        if clean_line.startswith("|") and "Operation" in line and "Description" in line:
            state["table_start"] = i
            inside_table = True
            continue

        if not inside_table:
            continue

        if not clean_line.startswith("|"):
            state["table_end"] = i
            inside_table = False
            continue

        if "---" not in clean_line:
            _process_table_row(line, op_name, state)

    if inside_table and state["table_end"] == -1:
        state["table_end"] = len(lines)

    return (
        state["table_start"],
        state["table_end"],
        state["insert_index"],
        state["m_idx"],
        state["r_idx"],
        state["already_exists"],
    )


def update_readme_table(readme_path: Path, op_name: str, readme_desc: str):
    """Inserts the operation and description alphabetically into the README markdown table."""
    if not readme_path.exists():
        print(f"❌ Error: {readme_path} not found.")
        return None

    lines = readme_path.read_text().splitlines(keepends=True)
    table_start, table_end, insert_index, m_idx, r_idx, already_exists = (
        _find_table_insert_position(lines, op_name)
    )

    if already_exists:
        print(f"ℹ️ '{op_name}' already exists in README table; skipping.")
        return False

    if table_start == -1:
        print("❌ Error: table_start == -1.")
        return None

    if insert_index == -1:
        insert_index = table_end if table_end != -1 else len(lines)
        prev_line = lines[insert_index - 1] if insert_index > 0 else ""
        m_idx, r_idx = get_pipe_indices(prev_line)

    new_row = build_aligned_row(op_name, readme_desc, m_idx, r_idx)
    out_lines = lines[:insert_index] + [new_row] + lines[insert_index:]

    readme_path.write_text("".join(out_lines))
    print(f"✅ Updated {readme_path} (inserted '{op_name}' with matching pipe alignment)")
    return True


def update_changelog(changelog_path: Path, changelog_entry: str):
    """Appends the changelog entry cleanly inside the Unreleased section header block."""
    if not changelog_path.exists():
        print(f"❌ Error: {changelog_path} not found.")
        return None

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
            return False

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
        print(f"✅ Updated {changelog_path} (inserted entry conforming to spacing conventions)")
        return True


def main():
    if len(sys.argv) not in (2, 3):
        print_usage_and_exit()

    op_name = sys.argv[1]
    changelog_entry = sys.argv[2] if len(sys.argv) == 3 else f"- `{op_name}` operation"

    if not changelog_entry.strip().startswith("-"):
        changelog_entry = f"- {changelog_entry}"

    try:
        readme_desc = registry.operations[op_name].desc
        print(f"ℹ️ Found description in registry: '{readme_desc}'")
    except KeyError:
        print(f"❌ Error: Operation '{op_name}' not found in pdftl registry.")
        sys.exit(1)

    update_autocomplete_keywords(Path("src/pdftl/cli/complete.py"), op_name)
    update_readme_table(Path("README.md"), op_name, readme_desc)
    update_changelog(Path("CHANGELOG.md"), changelog_entry)

    print("🔄 Re-generating type stubs...")
    generate_stubs()


if __name__ == "__main__":
    main()
