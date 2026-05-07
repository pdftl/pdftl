#!/usr/bin/env bash
# Usage: ./tools/update_portfile.sh <new_version>
# Requires: curl, openssl, rhash, python3
#   sudo apt install rhash

set -euo pipefail

VERSION="${1:?Usage: $0 <new_version>}"
PORTFILE="macports/Portfile"
TARBALL_URL="https://files.pythonhosted.org/packages/source/p/pdftl/pdftl-${VERSION}.tar.gz"
TARBALL="/tmp/pdftl-${VERSION}.tar.gz"

echo "Downloading $TARBALL_URL ..."
curl -fsSL "$TARBALL_URL" -o "$TARBALL"

echo "Computing checksums..."
SHA256=$(openssl dgst -sha256 "$TARBALL" | awk '{print $2}')
RMD160=$(rhash --ripemd160 --printf="%r\n" "$TARBALL")
SIZE=$(wc -c < "$TARBALL" | tr -d ' ')

echo "  sha256:  $SHA256"
echo "  rmd160:  $RMD160"
echo "  size:    $SIZE"

# Update version
sed -i "s/^version\s\+.*/version             ${VERSION}/" "$PORTFILE"

# Update checksums block
python3 - "$PORTFILE" "$RMD160" "$SHA256" "$SIZE" <<'EOF'
import re, sys

portfile, rmd160, sha256, size = sys.argv[1:]

with open(portfile) as f:
    text = f.read()

new_checksums = (
    f"checksums           rmd160  {rmd160} \\\n"
    f"                    sha256  {sha256} \\\n"
    f"                    size    {size}"
)

text = re.sub(
    r"checksums\s+rmd160\s+\S+\s*\\\s*\n\s*sha256\s+\S+\s*\\\s*\n\s*size\s+\S+",
    new_checksums,
    text,
)

with open(portfile, "w") as f:
    f.write(text)

print("Portfile updated.")
EOF

rm "$TARBALL"
echo "Done. Review with: git diff $PORTFILE"
