#!/bin/bash
set -e

# -------------------------------------------------------------------------
# Dynamic Sudo: Detect if running as root (Docker) or user (Local/GitHub VM)
# -------------------------------------------------------------------------
if [ "$EUID" -eq 0 ]; then
  SUDO=""
else
  SUDO="sudo"
fi

# -------------------------------------------------------------------------
# 1. System Dependencies (Requires Root/Sudo)
# -------------------------------------------------------------------------
echo ">>> Updating apt repositories..."
$SUDO apt-get update -qq

echo ">>> Installing System Dependencies..."
# DEBIAN_FRONTEND=noninteractive prevents asking for timezone/keyboard input
$SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y \
    git \
    unzip \
    php-cli \
    php-curl \
    php-xml \
    php-mbstring

# -------------------------------------------------------------------------
# 2. Composer Installation (Requires Root/Sudo for global install)
# -------------------------------------------------------------------------
echo ">>> Checking/Installing Composer..."
if ! command -v composer &> /dev/null; then
    # We download to a temp file first to be safe
    EXPECTED_CSUM=$(curl -s https://composer.github.io/installer.sig)
    php -r "copy('https://getcomposer.org/installer', 'composer-setup.php');"
    ACTUAL_CSUM=$(php -r "echo hash_file('sha384', 'composer-setup.php');")

    if [ "$EXPECTED_CSUM" != "$ACTUAL_CSUM" ]; then
        >&2 echo 'ERROR: Invalid installer checksum'
        rm composer-setup.php
        exit 1
    fi

    # Install globally to /usr/local/bin
    $SUDO php composer-setup.php --install-dir=/usr/local/bin --filename=composer
    rm composer-setup.php
    echo "Composer installed successfully."
else
    echo "Composer is already installed."
fi

# -------------------------------------------------------------------------
# 3. Project Dependencies (Runs as CURRENT USER - No Sudo)
# -------------------------------------------------------------------------
TARGET_DIR="vendor_tests/php-pdftk"

echo ">>> Setting up PHP Test Suite in $TARGET_DIR..."
mkdir -p vendor_tests

if [ ! -d "$TARGET_DIR" ]; then
    echo "Cloning mikehaertl/php-pdftk..."
    git clone https://github.com/mikehaertl/php-pdftk.git "$TARGET_DIR"
else
    echo "Repo already exists. Pulling latest changes..."
    # Suppress output unless there is an error
    git -C "$TARGET_DIR" pull origin master -q
fi

echo ">>> Installing PHP Project Dependencies..."
# Run as the normal user so you don't mess up file permissions in your home dir
cd "$TARGET_DIR"
composer install --no-interaction --prefer-dist --no-progress --quiet

echo ""
echo ">>> DONE. Ready to run: pytest"
