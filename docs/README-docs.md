# Documentation Generation

These scripts use the application's live code as a single source of truth to generate documentation in multiple formats.

## Prerequisites

If not already installed, you need to install Sphinx to generate HTML documentation.

```shell
pip install sphinx sphinx_rtd_theme
```

## How to Use

All scripts should be run from the project's root directory (the one containing the `docs/` and `src/` folders).

### 1. Generate Man Page

This will create a `pdftl.1` file in the project root, which can be installed or viewed directly.

```shell
python docs/generate_man_page.py
```

To view the generated man page:

```shell
man ./pdftl.1
```

To install it system-wide (on Linux/macOS):

```shell
sudo cp pdftl.1 /usr/local/share/man/man1/
sudo mandb
```

### 2. Generate HTML Documentation

This process has two steps: generating the source `.rst` files and then building the HTML site with Sphinx.

#### Step 1: Generate the `.rst` source files

This will create a `docs/source/` directory containing `index.rst` and a subdirectory with a file for each command.

```shell
python docs/generate_sphinx_docs.py
```

#### Step 2: Initialize and Build the Sphinx Site

If this is your first time, you need to initialize a Sphinx project. You only need to do this once.

```shell
# Run this from the docs/ directory
cd docs
sphinx-quickstart
```

> **Note:** During `sphinx-quickstart`, accept the defaults, but specify `source` as the source directory and `build` as the build directory.

After the one-time setup, you can build the HTML site anytime you update the docs:

```shell
# Run this from the docs/ directory
sphinx-build -b html source build
```

The final, viewable website will be located at `docs/build/index.html`.
