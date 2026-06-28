#!/bin/bash

CHANGELOG="CHANGELOG.md"

# 0. Branch Safeguard
CURRENT_BRANCH=$(git branch --show-current)
if [ "$CURRENT_BRANCH" != "main" ]; then
    echo "❌ Error: You are currently on branch '$CURRENT_BRANCH'."
    echo "Releases can only be published from the 'main' branch. Please switch to 'main' and try again."
    exit 1
fi

# 1. Parse arguments (Version vs. GitHub Flags)
VERSION=""
GH_FLAGS=()

for arg in "$@"; do
    if [[ "$arg" == -* ]]; then
        GH_FLAGS+=("$arg") # It's a flag (e.g., --draft)
    elif [[ -z "$VERSION" ]]; then
        VERSION="$arg"     # It's the first non-flag argument (the version)
    else
        GH_FLAGS+=("$arg") # Catch-all for extra stray arguments
    fi
done

# Determine the target version (either via argument or auto-bump)
if [ -z "$VERSION" ]; then
    echo "No version argument provided. Detecting current version from git tags..."
    CURRENT_TAG=$(git describe --tags --abbrev=0 2>/dev/null)

    if [ -z "$CURRENT_TAG" ]; then
        echo "❌ Error: Could not detect any git tags to bump. Please provide a version manually."
        echo "Usage: $0 [version] [flags...]"
        exit 1
    fi

    # Strip the 'v' prefix
    CURRENT_VERSION="${CURRENT_TAG#v}"

    # Parse into x, y, z
    IFS='.' read -r major minor patch <<< "$CURRENT_VERSION"

    if [[ -z "$major" || -z "$minor" || -z "$patch" ]]; then
        echo "❌ Error: Current tag ($CURRENT_TAG) does not follow x.y.z semantic versioning."
        exit 1
    fi

    # Bump minor version and reset patch to 0
    minor=$((minor + 1))
    patch=0
    RAW_VERSION="${major}.${minor}.${patch}"

    echo "Detected previous tag: ${CURRENT_TAG}"
    echo "Auto-bumping version to: ${RAW_VERSION}"
else
    # Normalize user input (strip 'v' if they provided it)
    RAW_VERSION="${VERSION#v}"
fi

TAG_VERSION="v$RAW_VERSION"

# 2. Extract Release Notes
TMP_NOTES=$(mktemp /tmp/release_notes_${RAW_VERSION}_XXXXXX.md)
echo "Looking for notes for [${RAW_VERSION}] in ${CHANGELOG}..."

# Extract the block of text specifically for this version using awk.
awk -v ver="^## \\\[${RAW_VERSION}\\\]" '
    $0 ~ ver {flag=1; next}
    /^## \[/ {if(flag) exit}
    flag {print}
' "$CHANGELOG" > "$TMP_NOTES"

# Strip leading/trailing blank lines from the temporary file to keep it clean
sed -i.bak -e '/./,$!d' "$TMP_NOTES" && rm -f "${TMP_NOTES}.bak"

# Abort if the extracted file is empty or only contains whitespace
if ! grep -q "[^[:space:]]" "$TMP_NOTES"; then
    echo "❌ Error: Could not find release notes for [${RAW_VERSION}] in ${CHANGELOG}."
    echo "Aborting release. Please make sure you have added '## [${RAW_VERSION}]' to the changelog."
    rm -f "$TMP_NOTES"
    exit 1
fi

echo "✅ Notes extracted successfully."

# 3. Review Notes
# Open the notes in the user's preferred editor (fallback to nano)
EDITOR="${EDITOR:-nano}"
$EDITOR "$TMP_NOTES"

# 4. Confirmation & Publish
echo "--------------------------------------------------"
cat "$TMP_NOTES"
echo "--------------------------------------------------"

# If we are doing a draft, let's make that clear in the prompt
PROMPT_TEXT="Create tag ${TAG_VERSION} and publish GitHub release with these notes? (y/N) "
if [[ " ${GH_FLAGS[*]} " =~ " --draft " ]]; then
    PROMPT_TEXT="Create DRAFT GitHub release ${TAG_VERSION} with these notes? (y/N) "
fi

read -p "$PROMPT_TEXT" confirm

if [[ "$confirm" =~ ^[Yy]$ ]]; then
    echo "🚀 Pushing release ${TAG_VERSION} to GitHub..."

    # 'gh release create' handles pushing the new tag and creating the GitHub release.
    gh release create "$TAG_VERSION" \
       --title "Release $TAG_VERSION" \
       -F "$TMP_NOTES" \
       --generate-notes \
       "${GH_FLAGS[@]}"

    echo "🎉 Release command completed!"

    # Only fetch tags if it's not a draft, since draft releases don't push the tag
    if [[ ! " ${GH_FLAGS[*]} " =~ " --draft " ]]; then
        echo "📥 Pulling the new tag from upstream..."
        git fetch --tags
        echo "✅ Local repository is up to date."
    else
        echo "📝 Draft release created. You can review and publish it using:"
        echo "   gh release edit $TAG_VERSION --draft=false"
    fi
else
    echo "🛑 Release aborted by user."
fi

# Clean up
rm -f "$TMP_NOTES"
