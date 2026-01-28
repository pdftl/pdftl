# src/pdftl/cli/complete.py

import os
import sys

# CONFIGURATION
PICKLER = "cloudpickle"
# if grammar.py output changes: update!
GRAMMAR_VERSION = "1"


def get_cache_dir_and_file():
    # Windows
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        cache_dir = os.path.join(base, "pdftl", "Cache")
    else:
        # Mac / Linux (XDG)
        # XDG_CACHE_HOME defaults to ~/.cache if not set
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
        cache_dir = os.path.join(base, "pdftl")

    cache_file = os.path.join(cache_dir, f"completion_grammar_v{GRAMMAR_VERSION}.{PICKLER}")
    return cache_dir, cache_file


def rebuild_cache():
    """
    The Heavy Function.
    Only runs if cache is missing. Imports the world.
    """
    # 1. Lazy imports (Heavy!)
    try:
        import cloudpickle as pickler
        from lark import Lark

        # This is likely the slow import that scans your folders
        from pdftl.completion.grammar import GrammarBuilder
    except ImportError:
        return None

    # 2. Build the Grammar
    builder = GrammarBuilder()
    grammar_str = builder.build()

    # 3. Create the Parser (Earley is required for completion)
    parser = Lark(grammar_str, parser="earley")

    cache_dir, cache_file = get_cache_dir_and_file()
    # 4. Save to Disk
    if not os.path.exists(cache_dir):
        os.makedirs(cache_dir, exist_ok=True)

    with open(cache_file, "wb") as f:
        pickler.dump(parser, f)

    return parser


def get_parser():
    """
    The Fast Function.
    Tries to load from disk to avoid importing pdftl.
    """
    _, cache_file = get_cache_dir_and_file()
    try:
        import cloudpickle as pickler

        # We must verify we can load it.
        # If the pickle is stale/corrupt, we fall back to rebuild.
        with open(cache_file, "rb") as f:
            return pickler.load(f)

    except Exception:  # noqa: BLE001 (performance)
        pass  # Fall through to rebuild

    return rebuild_cache()


def resolve_candidates(allowed_tokens, parser):
    candidates = set()

    # NEW: Map for the explicit terminals you added to GrammarBuilder
    # This matches the names in your latest GrammarBuilder
    literal_map = {
        "HELP_KW": "help",
        "HELP_FLAG": "--help",
        "COMP_FLAG": "--completion",
        "COMP_BASH": "bash",
        "COMP_ZSH": "zsh",
        "COMP_PWSH": "powershell",
        "VERSION_FLAG": "--version",
        "DEBUG_FLAG": "--debug",
    }

    for name in allowed_tokens:
        if name in literal_map:
            candidates.add(literal_map[name])
        elif name == "HELP_SUB_KW":
            # Ask the parser what the literal values for this terminal are
            for t in parser.terminals:
                if t.name == "HELP_SUB_KW":
                    # This extracts "help", "sign", etc. from the Lark terminal pattern
                    options = t.pattern.value[3:-1].split("|")
                    candidates.update(opt.strip('" ') for opt in options)
        elif name == "PDF_PATH":
            candidates.add("__PDF_FILE__")
        elif name == "FILE_PATH":
            candidates.add("__FILE__")
        elif name == "CHAIN_SEP":
            candidates.add("---")
        elif name.startswith("KW_"):
            # This handles your dynamic registry operations/options
            # It tries to find the literal string Lark is looking for
            for t in parser.terminals:
                if t.name == name:
                    # Clean up the literal (remove quotes)
                    val = t.pattern.value.strip('"').strip("'")
                    candidates.add(val)
                    break
    return candidates


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

        candidates = resolve_candidates(allowed, parser)

        for c in sorted(candidates):
            # --- FIX 2: Always output the magic tokens, regardless of partial match ---
            if c in ("__FILE__", "__PDF_FILE__"):
                print(c)
            elif c.startswith(current_partial):
                print(c)


if __name__ == "__main__":
    main()
