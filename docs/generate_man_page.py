# docs/generate_man_page.py
"""
Generates a man page for pdftl in the troff format, with debugging capabilities.
"""
# Set to True to inject debugging comments (starting with .\") into the man page source.
DEBUG = True

import os
import re
from datetime import date

from common import get_docs_data


def troff_escape(text):
    """Escapes special characters for troff."""
    if not isinstance(text, str):
        text = str(text)
    text = text.replace("\\", "\\\\")
    text = text.replace("-", "\\-")
    return text


def debug_log(f, message):
    """Writes a debug comment to the troff file if DEBUG is True."""
    if DEBUG:
        f.write(f'.\\" DEBUG: {message}\n')


def _format_token(f, word, bold_literals, is_prose=False):
    """
    Formats a single word token based on whether it's in a prose section or not.
    This version uses a robust iterative parser instead of a flawed regex.
    """
    debug_log(f, f"Formatting token: '{word}' (is_prose={is_prose})")

    # ** THE FIX IS HERE: New, more robust parsing logic **
    # First, separate general punctuation from the core, potentially-quoted word.
    # Note: Single quote is handled separately as a quoting character.
    punctuation = '()[].,:;"'
    start_idx = 0
    while start_idx < len(word) and word[start_idx] in punctuation:
        start_idx += 1

    end_idx = len(word)
    while end_idx > start_idx and word[end_idx - 1] in punctuation:
        end_idx -= 1

    leading_punc = word[:start_idx]
    core_with_quotes = word[start_idx:end_idx]
    trailing_punc = word[end_idx:]

    # Now, check if the core is quoted and extract the actual word.
    is_quoted = False
    core_word = core_with_quotes
    if core_word.startswith("'") and core_word.endswith("'") and len(core_word) > 1:
        is_quoted = True
        core_word = core_word[1:-1]

    # --- PROSE FORMATTING LOGIC ---
    if is_prose:
        # Special exception: '---' should always be bold.
        if core_word == "---":
            debug_log(f, "  -> Prose: Found special keyword '---'. Formatting as BOLD.")
            return leading_punc + f"\\fB{troff_escape(core_word)}\\fR" + trailing_punc

        # For prose, only bold if the original token was quoted AND it's a known keyword.
        if is_quoted and core_word in bold_literals:
            debug_log(
                f,
                f"  -> Prose: Found quoted keyword '{core_word}'. Formatting as BOLD.",
            )
            return leading_punc + f"\\fB{troff_escape(core_word)}\\fR" + trailing_punc

        debug_log(f, "  -> Prose: Not a formatted keyword. Returning plain text.")
        return troff_escape(word)

    # --- CODE/SYNTAX FORMATTING LOGIC ---
    # Handle placeholders: <input>... -> input...
    if core_word.startswith("<"):
        end_bracket_pos = core_word.find(">")
        if end_bracket_pos != -1:
            placeholder = core_word[1:end_bracket_pos]
            rest = core_word[end_bracket_pos + 1 :]
            formatted = f"\\fI{troff_escape(placeholder)}\\fR{troff_escape(rest)}"
            debug_log(
                f, f"  -> Code: Italic Placeholder: '{placeholder}' with rest '{rest}'"
            )
            return leading_punc + formatted + trailing_punc

    # Handle bold literals for code/usage sections
    if core_word in bold_literals:
        debug_log(f, f"  -> Code: Is a keyword '{core_word}'. Formatting as BOLD.")
        return leading_punc + f"\\fB{troff_escape(core_word)}\\fR" + trailing_punc

    debug_log(f, "  -> Code: Not a keyword. Formatting as plain text.")
    return troff_escape(word)


def format_line(f, line, bold_literals, is_prose=False):
    """Formats a full line of text, token by token, applying the correct mode."""
    return " ".join(
        _format_token(f, word, bold_literals, is_prose) for word in line.split()
    )


def format_example_cmd(f, cmd_line, whoami, bold_literals):
    """Specialized formatter for example commands to distinguish keywords from arguments."""
    parts = cmd_line.split()
    formatted_parts = [f"\\fB{troff_escape(whoami)}\\fR"]

    for part in parts:
        formatted_parts.append(_format_token(f, part, bold_literals, is_prose=False))

    return " ".join(formatted_parts)


def render_multiline_text(f, text, bold_literals):
    """
    Writes a multi-line string, preserving paragraphs and applying prose formatting,
    while allowing troff to handle line wrapping.
    """
    paragraphs = text.strip().split("\n\n")

    for para in paragraphs:
        if not para.strip():
            continue

        lines = para.strip().splitlines()
        is_permission_list = False
        if len(lines) > 1:
            is_permission_list = all(
                re.match(r"^\s*([A-Z][a-zA-Z]+)\s*$", lines[i])
                and re.match(r"^\s*([A-Z].*)\s*$", lines[i + 1])
                for i in range(0, len(lines) - 1, 2)
            )

        if is_permission_list:
            f.write(".P\n")
            for i in range(0, len(lines), 2):
                term = lines[i].strip()
                definition = lines[i + 1].strip() if i + 1 < len(lines) else ""
                f.write(".TP\n")
                f.write(f"\\fB{troff_escape(term)}\\fR\n")
                f.write(f"{troff_escape(definition)}\n")
        else:
            f.write(".P\n")
            single_line_para = " ".join(line.strip() for line in lines)
            f.write(format_line(f, single_line_para, bold_literals, is_prose=True))
            f.write("\n")


def generate_man_page(app_data, topics, output_dir="."):
    """Generates and writes the pdftl.1 man page file."""
    whoami = app_data["whoami"]

    bold_literals = set()
    bold_literals.add(whoami)

    bold_literals.update(topics.keys())
    if app_data.get("options"):
        for name in app_data["options"].keys():
            bold_literals.add(name.split(" ", 1)[0])

    bold_literals.update(["PROMPT", "head", "tail", "preview", "input_pw", "---"])
    if app_data.get("options", {}).get("allow <perm>..."):
        long_desc = app_data["options"]["allow <perm>..."].get("long_desc", "")
        permissions = re.findall(r"^\s*([A-Z][a-zA-Z]+)\s*$", long_desc, re.MULTILINE)
        bold_literals.update(permissions)

    filepath = os.path.join(output_dir, f"{whoami}.1")

    with open(filepath, "w", encoding="utf-8") as f:
        debug_log(
            f, f"Starting man page generation. Found {len(bold_literals)} keywords."
        )

        today = date.today().strftime("%Y-%m-%d")
        f.write(
            f'.TH {app_data["name"].upper()} 1 "{today}" "{whoami}" "User Commands"\n'
        )
        f.write(f'.SH NAME\n{whoami} \\- {troff_escape(app_data["description"])}\n')

        f.write(".SH SYNOPSIS\n")
        for line in app_data["synopsis"].splitlines():
            f.write(".br\n")
            f.write(format_line(f, line, bold_literals, is_prose=False) + "\n")

        f.write(
            ".SH DESCRIPTION\n.P\nA command-line tool for manipulating PDF files, inspired by pdftk.\n"
        )

        f.write(".SH OPERATIONS\n.P\nThe main commands available in pdftl.\n")
        for name, data in topics.items():
            print(name)
            if data["type"] == "operation":
                f.write(f'.TP\n.B {troff_escape(name)}\n{troff_escape(data["desc"])}\n')

        f.write(".SH OPTIONS\n.P\nOptions to control PDF output processing.\n")
        if app_data.get("options"):
            for name, data in app_data["options"].items():
                f.write(".TP\n")
                f.write(format_line(f, name, bold_literals, is_prose=False) + "\n")
                f.write(troff_escape(data["desc"]) + "\n")
                if data.get("long_desc"):
                    render_multiline_text(f, data["long_desc"], bold_literals)

        f.write('.SH "COMMANDS AND TOPICS"\n')
        for name, data in topics.items():
            section_type = "COMMAND" if data.get("type") == "operation" else "TOPIC"
            f.write(f'.SS "{section_type}: {name}"\n')
            if data.get("desc"):
                f.write(f".P\n{troff_escape(data['desc'])}\n")
            if data.get("usage"):
                f.write(".SS USAGE\n.EX\n")
                usage_line = f"{whoami} {data['usage']}"
                f.write(
                    format_line(f, usage_line, bold_literals, is_prose=False)
                    + "\n.EE\n"
                )
            if data.get("details"):
                f.write(".SS DETAILS\n")
                render_multiline_text(f, data["details"], bold_literals)
            if data.get("examples"):
                f.write(".SS EXAMPLES\n")
                for ex in data["examples"]:
                    f.write(".TP\n")
                    f.write(
                        format_example_cmd(f, ex["cmd"], whoami, bold_literals) + "\n"
                    )
                    if ex.get("desc"):
                        f.write(f"{troff_escape(ex['desc'])}\n")

    print(f"Successfully generated man page: {filepath}")


if __name__ == "__main__":
    app_info, all_topics = get_docs_data()
    generate_man_page(app_info, all_topics)
