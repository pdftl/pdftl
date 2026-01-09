#!/bin/bash
# setup_php_tests.sh

# Create a folder for external vendor tests
mkdir -p vendor_tests

# Clone the PHP library if it doesn't exist
if [ ! -d "vendor_tests/php-pdftk" ]; then
    echo "Cloning php-pdftk..."
    git clone https://github.com/mikehaertl/php-pdftk.git vendor_tests/php-pdftk
else
    echo "php-pdftk already cloned."
fi

# Install PHP dependencies
if command -v composer &> /dev/null; then
    echo "Installing Composer dependencies..."
    cd vendor_tests/php-pdftk
    composer install --prefer-dist --no-progress
else
    echo "ERROR: Composer is not installed. Please install 'composer' to run PHP tests."
    exit 1
fi

echo "Setup complete. You can now run pytest."