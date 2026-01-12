# pdftl Notices

This software (**pdftl**) is distributed under the Mozilla Public License 2.0.

## PDFtk Compatibility

### CLI design
The pdftl command-line interface is a clean-room implementation intended to be compatible with **PDFtk Server**.

* **Original Concept & Design:** Sid Steward
* **Implementation:** This project is a clean-room implementation in Python. It does not use source code from the original PDFtk (GPL) or any other implementation.
* **Status:** This software is not affiliated with or endorsed by the PDFtk project.

_PDFtk is a trademark of Sid Steward and PDF Labs. This project is not affiliated with, endorsed by, or connected to PDF Labs._

### Compatibility test suites

#### pdftk-java tests
The tests located in `vendor_tests/pdftk-java` are ported from the [**pdftk-java**](https://gitlab.com/pdftk-java/pdftk-java) project.

* **Original Copyright:** Marc Vinyals and the pdftk-java contributors.
* **License:** These specific test files are licensed under the **GNU General Public License (GPL) v2 or later**.
* **Note:** These tests are used for verification during development and are excluded from the binary distribution (wheel) of pdftl to preserve the permissive license of
  the core library.


#### php-pdftk tests
Tests from the
[**php-pdftk**](https://github.com/mikehaertl/php-pdftk) project
may be downloaded to `vendor_tests/php-pdftk` using scripts in `tools/`, to verify
compatibility with php-pdftk. No files from the php-pdftk
project are included in pdftl.
* **Original Author:** Mike Haertl
* **License:** MIT License

## Artwork

### The pdftl icon

The [pdftl icon](https://raw.githubusercontent.com/pdftl/pdftl/main/.github/assets/pdftl.svg) is a derivative work. It incorporates elements from the [**Google Noto Emoji**](https://github.com/googlefonts/noto-emoji) library.
* **Original copyright:** 2013 Google Inc.
* **Original license:** Apache License, Version 2.0
* **Modifications:** The artwork has been modified (recolored, reshaped, and combined) for the pdftl project.
* **Icon license:** These modifications and the resulting composite icon are licensed under the **Mozilla Public License 2.0** to match the pdftl project's primary license.


## Third-party components

### OCRmyPDF

Portions of the `optimize_images` operation are adapted from [**OCRmyPDF**](https://github.com/ocrmypdf/OCRmyPDF).

* **Original author:** James R. Barlow
* **License:** Mozilla Public License 2.0
* **Copyright:** © 2022 James R. Barlow

The original source code can be found at <https://github.com/ocrmypdf/OCRmyPDF>.

### pikepdf/qpdf

This project relies heavily on **pikepdf** (© 2022-2024 James R. Barlow, MPL 2.0) and **qpdf** (MPL 2.0). While not directly "adapted" code, they are core dependencies that make this tool possible.

### pypdfium2 / PDFium
The `dump_text` operation and the `flatten` option use **pypdfium2**.
* **pypdfium2 Copyright:** Copyright © 2021-2026 geisserml and contributors.
* **PDFium Copyright:** Copyright © 2014 PDFium Authors (Google Inc.).
* **License:** BSD-3-Clause / Apache-2.0.
* **Source:* https://github.com/pypdfium2/pypdfium2
