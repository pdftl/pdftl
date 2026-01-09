#!/bin/bash

# ==============================================================================
# DROPBOX IGNORE SCRIPT (Cross-Platform)
# Marks Python build artifacts and caches so Dropbox ignores them.
# Works on: Linux (via attr) and macOS (via xattr).
# ==============================================================================

# 1. Detect the platform and set the command
if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS
    PLATFORM="mac"
    echo "Detected macOS. Using 'xattr'."
elif command -v attr &> /dev/null; then
    # Linux with attr installed
    PLATFORM="linux"
    echo "Detected Linux. Using 'attr'."
else
    echo "Error: Linux detected but 'attr' command not found."
    echo "Please run: sudo apt-get install attr (or equivalent)"
    exit 1
fi

# Function to apply the ignore attribute
ignore_path() {
    local target="$1"
    
    # Only ignore if the file/folder actually exists
    if [ ! -e "$target" ]; then
        return
    fi

    echo "  -> Ignoring: $target"

    if [ "$PLATFORM" == "mac" ]; then
        xattr -w com.dropbox.ignored 1 "$target"
    else
        attr -s com.dropbox.ignored -V 1 "$target" > /dev/null
    fi
}

# ==============================================================================
# TARGETS TO IGNORE
# ==============================================================================

# List of root-level directories or files to ignore
# Change: removed ".git"

CRUFT_LIST=(
    ".pytest_cache"
    ".mypy_cache"
    ".ruff_cache"
    ".tox"
    ".coverage"
    "htmlcov"
    "build"
    "dist"
    "*.egg-info"
)

echo "--- Processing Root Level Artifacts ---"
for item in "${CRUFT_LIST[@]}"; do
    # Handle wildcards like *.egg-info
    for match in $item; do
        ignore_path "$match"
    done
done

echo ""
echo "--- Recursively Processing __pycache__ ---"
# Find all __pycache__ folders recursively and ignore them
find . -name "__pycache__" -type d | while read -r dir; do
    ignore_path "$dir"
done

echo ""
echo "✅ Done. Dropbox will now ignore these artifacts on this machine."
