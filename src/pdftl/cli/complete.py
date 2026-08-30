# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/cli/complete.py

import marshal  # built in, no import cost
import os
import sys
import pickle

# CONFIGURATION
PICKLER = "cloudpickle"

# if grammar.py output changes: update this!
GRAMMAR_VERSION = "9"

HARDCODED_KEYWORDS = {
    "EACH",
    "JOB",
    "DONE",
    "add_bookmarks",
    "add_images",
    "add_marks",
    "add_text",
    "attach_files",
    "background",
    "barcode",
    "booklet",
    "burst",
    "cat",
    "chop",
    "clip",
    "create",
    "crop",
    "deduplicate_fonts",
    "deduplicate_icc_profiles",
    "deduplicate_images",
    "delete",
    "delete_actions",
    "delete_annots",
    "delete_attachments",
    "delete_bookmarks",
    "delete_blank",
    "delete_images",
    "deskew",
    "diff_text",
    "dump_actions",
    "dump_annots",
    "dump_bookmarks",
    "dump_colorspaces",
    "dump_data",
    "dump_data_annots",
    "dump_data_fields",
    "dump_data_fields_utf8",
    "dump_data_utf8",
    "dump_dests",
    "dump_encryption",
    "dump_fonts",
    "dump_files",
    "dump_images",
    "dump_layers",
    "dump_signatures",
    "dump_streams",
    "dump_tables",
    "dump_tags",
    "dump_text",
    "embed_fonts",
    "excise",
    "export_fonts",
    "export_images",
    "fill_form",
    "filter",
    "generate_fdf",
    "grep",
    "highlight",
    "import_fonts",
    "import_images",
    "import_streams",
    "inject",
    "insert",
    "link_urls",
    "modify_annots",
    "modify_images",
    "modify_layers",
    "montage",
    "move",
    "multibackground",
    "multistamp",
    "mutate_content",
    "normalize",
    "optimize_images",
    "place",
    "recolor_images",
    "recolor_vectors",
    "redact",
    "render",
    "replace",
    "resample_images",
    "rotate",
    "server",
    "set",
    "shuffle",
    "simplify_vectors",
    "stamp",
    "stamp_fields",
    "style_text",
    "subset_fonts",
    "tag",
    "unpack_files",
    "unpause",
    "update_bookmarks",
    "update_info",
    "update_info_utf8",
    "usage",
    "zoom",
    "allow",
    "compress",
    "drop_info",
    "drop_xfa",
    "drop_xmp",
    "encrypt_128bit",
    "encrypt_40bit",
    "encrypt_aes128",
    "encrypt_aes256",
    "fast",
    "flatten",
    "keep_final_id",
    "keep_first_id",
    "linearize",
    "need_appearances",
    "no_encrypt_metadata",
    "output",
    "owner_pw",
    "replacement_font",
    "sign_cert",
    "sign_field",
    "sign_key",
    "sign_pass_env",
    "sign_pass_prompt",
    "uncompress",
    "user_pw",
    "verbose",
}


def get_cache_dir():
    # Windows
    completions_subdir_name = "completions"
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return os.path.join(base, "pdftl", "Cache", completions_subdir_name)
    # Mac / Linux (XDG)
    # XDG_CACHE_HOME defaults to ~/.cache if not set
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "pdftl", completions_subdir_name)


def get_cache_path(cache_name="grammar", cache_dir=None, cache_format=PICKLER):
    if cache_dir is None:
        cache_dir = get_cache_dir()
    return os.path.join(cache_dir, f"completion_{cache_name}_v{GRAMMAR_VERSION}.{cache_format}")


def get_grammar_cache_path(cache_dir=None):
    return get_cache_path(cache_name="grammar", cache_dir=cache_dir, cache_format=PICKLER)


def get_simple_cache_path(cache_dir=None):
    py_tag = f"{sys.version_info.major}.{sys.version_info.minor}"
    return get_cache_path(
        cache_name="simple", cache_dir=cache_dir, cache_format=f"marshal_{py_tag}"
    )


def rebuild_cache():
    """
    The Heavy Function.
    Only runs if cache is missing. Imports the world.
    """
    # 1. Lazy imports (Heavy!)
    try:
        import cloudpickle as pickler
        from lark import Lark

        from pdftl.completion.grammar import GrammarBuilder
    except ImportError:
        return None

    # 2. Build the Grammar
    builder = GrammarBuilder()
    grammar_str = builder.build()

    # 3. Create the Parser (Earley is required for completion)
    parser = Lark(grammar_str, parser="earley")

    cache_dir = get_cache_dir()
    # 4. Save to Disk
    if not os.path.exists(cache_dir):
        os.makedirs(cache_dir, exist_ok=True)

    cache_file = get_grammar_cache_path(cache_dir=cache_dir)
    with open(cache_file, "wb") as f:
        pickler.dump(parser, f)

    return parser


def load_simple_cache():
    """
    Attempts to load the lightweight simple cache.
    Returns a dict { "context_key": ["candidate1", "candidate2"] } or {} on failure.
    """
    cache_path = get_simple_cache_path()
    if not is_package_newer_than_cache(cache_path):
        try:
            if os.path.exists(cache_path):
                with open(cache_path, "rb") as f:
                    return marshal.load(f)
        except (OSError, ValueError, EOFError):
            return {}
    return {}


def update_simple_cache(context_key, candidates, cache=None):
    """
    Updates the simple cache with new results found by the heavy parser.
    """
    # Only cache purely static text results.
    # If the parser returned dynamic instructions like __FILE__, we cache that instruction,
    # but we don't try to cache the result of the file listing itself.

    if cache is None:
        cache = load_simple_cache()
    cache[context_key] = list(candidates)  # Ensure it's a list for serialization

    path = get_simple_cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, "wb") as f:
        marshal.dump(cache, f)


_cache_check_results: dict[str, bool] = {}


def _any_path_newer_than(paths, mtime):
    return any(os.path.exists(p) and os.path.getmtime(p) > mtime for p in paths)


def is_package_newer_than_cache(cache_path):
    if cache_path in _cache_check_results:
        return _cache_check_results[cache_path]
    try:
        cache_mtime = os.path.getmtime(cache_path)
        cli_dir = os.path.dirname(os.path.abspath(__file__))
        package_root = os.path.dirname(cli_dir)
        if os.name == "nt":
            config_home = os.environ.get("APPDATA", os.path.expanduser("~\\AppData\\Roaming"))
        else:
            config_home = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
        custom_op_dir = os.path.join(config_home, "pdftl", "operations")
        critical_paths = [
            package_root,
            os.path.join(package_root, "operations"),
            os.path.join(package_root, "completion"),
            custom_op_dir,
            __file__,
        ]
        result = _any_path_newer_than(critical_paths, cache_mtime)
    except OSError:
        result = True
    _cache_check_results[cache_path] = result
    return result


def get_parser():
    """
    The Fast Function.
    Tries to load from disk to avoid importing pdftl.
    """
    cache_file = get_grammar_cache_path()

    if not is_package_newer_than_cache(cache_file):
        try:
            import cloudpickle as pickler

            with open(cache_file, "rb") as f:
                return pickler.load(f)
        except (ImportError, pickle.UnpicklingError, OSError, AttributeError, EOFError):
            # Fall back to rebuild (slow)
            pass

    return rebuild_cache()


def _resolve_dynamic_token(
    name: str, parser, terminal_lookup: dict | None
) -> tuple[list[str], dict | None]:
    """Resolves dynamic grammar terminal node names,
    lazily building the lookup dict only when required."""
    # Fast path for static magic strings
    if name == "PDF_PATH":
        return ["__PDF_FILE__"], terminal_lookup
    if name == "FILE_PATH":
        return ["__FILE__"], terminal_lookup
    if name == "CHAIN_SEP":
        return ["---"], terminal_lookup

    # Lazy dictionary evaluation
    if terminal_lookup is None and parser:
        terminal_lookup = {t.name: t for t in parser.terminals}

    if not terminal_lookup:
        return [], terminal_lookup

    if name == "HELP_SUB_KW":
        t = terminal_lookup.get("HELP_SUB_KW")
        if t:
            # This extracts "help", "sign", etc. from the Lark terminal pattern
            options = t.pattern.value[3:-1].split("|")
            return [opt.strip('" ') for opt in options], terminal_lookup
    elif name.startswith("KW_"):
        # This handles dynamic registry operations/options
        t = terminal_lookup.get(name)
        if t:
            # Clean up the literal (remove quotes)
            val = t.pattern.value.strip('"').strip("'")
            return [val], terminal_lookup

    return [], terminal_lookup


def resolve_candidates(allowed_tokens, parser):
    candidates = set()

    # Map for explicit terminals in GrammarBuilder
    # This must match the names in GrammarBuilder
    # We hard-code these for speed rather than importing
    literal_map = {
        "KW_EACH": "EACH",
        "KW_SUB": "JOB",
        "KW_END": "DONE",
        "HELP_KW": "help",
        "HELP_FLAG": "--help",
        "COMP_FLAG": "--completion",
        "COMP_BASH": "bash",
        "COMP_ZSH": "zsh",
        "COMP_PWSH": "powershell",
        "VERSION_FLAG": "--version",
        "DEBUG_FLAG": "--debug",
        "ARGS_FLAG": "--args",
    }

    terminal_lookup = None

    for name in allowed_tokens:
        if name in literal_map:
            candidates.add(literal_map[name])
        else:
            resolved, terminal_lookup = _resolve_dynamic_token(name, parser, terminal_lookup)
            candidates.update(resolved)

    return candidates


def simple_cache_key(context):
    if len(context) == 0:
        return "__zero"
    lastword = context[-1]
    if lastword in ("help", "--help"):
        return "__help"
    if lastword.startswith("--") and not lastword.startswith("---"):
        return f"__option:{lastword}"
    if len(context) == 1:
        return "__one"
    if lastword == "---":
        return "__start_pipeline"
    if lastword in HARDCODED_KEYWORDS:
        return f"__hardcoded:{lastword}"
    return None


def output_candidates(candidates, current_partial=""):
    # Always output the magic tokens, regardless of partial match
    for c in candidates:
        if c in ("__FILE__", "__PDF_FILE__"):
            print(c)
        elif c.startswith(current_partial):
            print(c)


def main():
    # 1. Grab arguments
    raw_args = sys.argv[1:]

    # 2. Determine the "Partial Word" being typed
    if raw_args:
        current_partial = raw_args[-1]
        context = raw_args[:-1]
    else:
        current_partial = ""
        context = []

    simple_context_key = simple_cache_key(context)

    # Initialize simple_cache to be in scope for the update call later
    simple_cache = {}
    if simple_context_key is not None:
        simple_cache = load_simple_cache()
        if simple_context_key in simple_cache:
            # HIT! We found the list of allowed tokens (e.g. ["merge", "--help", "__PDF_FILE__"])
            # We didn't import Lark. We are fast.
            candidates = simple_cache[simple_context_key]
            output_candidates(candidates, current_partial)
            return

    # 3. Load Parser (Fast if cached)
    parser = get_parser()
    if not parser:
        return

    # 4. Prepare input for the parser
    # Lark interactive parsing expects a full string.
    # We add a special character \x01 at the cursor position to trigger the error.
    context_str = " ".join(context)
    if context_str:
        context_str += " "

    # The full string we attempt to parse
    full_str = context_str + "\x01"

    # 5. Run Parser & Catch Expected Error
    from lark.exceptions import UnexpectedCharacters, UnexpectedEOF, UnexpectedToken

    try:
        parser.parse(full_str)
    except (UnexpectedCharacters, UnexpectedToken, UnexpectedEOF) as e:
        # Lark tells us exactly what tokens were allowed at this position
        allowed = getattr(e, "allowed", getattr(e, "expected", set()))

        candidates = sorted(resolve_candidates(allowed, parser))
        if simple_context_key is not None:
            # Pass the already-loaded cache to avoid a re-read
            update_simple_cache(simple_context_key, candidates, cache=simple_cache)

        output_candidates(candidates, current_partial)


if __name__ == "__main__":
    main()
