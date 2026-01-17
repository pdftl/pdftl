# pdftl

<img align="right" width="100" src="https://raw.githubusercontent.com/pdftl/pdftl/main/.github/assets/pdftl.svg">

[![PyPI](https://img.shields.io/pypi/v/pdftl)](https://pypi.org/project/pdftl/)
[![CI](https://github.com/pdftl/pdftl/actions/workflows/ci.yml/badge.svg)](https://github.com/pdftl/pdftl/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/pdftl/pdftl/graph/badge.svg)](https://codecov.io/gh/pdftl/pdftl)
[![Documentation Status](https://readthedocs.org/projects/pdftl/badge/?version=latest)](https://pdftl.readthedocs.io/en/latest/?badge=latest)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/pdftl)](https://pypi.org/project/pdftl/)
![Static Badge](https://img.shields.io/badge/platform-linux%20%7C%20windows%20%7C%20macos-lightgrey)

**pdftl** ("PDF tackle") is a CLI tool for PDF manipulation written in Python. It is intended to be a command-line compatible extension of the venerable `pdftk`.

Leveraging the power of [`pikepdf`](https://github.com/pikepdf/pikepdf) ([qpdf](https://github.com/qpdf/qpdf)) and other modern libraries, it offers advanced capabilities like cropping, chopping, regex text replacement, adding text and arbitrary content stream injection.

## Quick start
```bash
pipx install pdftl[full]

# merge, crop to letter paper, rotate last page and output with encryption with one command
pdftl A=a.pdf B=b.pdf cat A1-5 B2-end \
    --- crop '4-8,12(letter)' \
    --- rotate endright \
    output out.pdf owner_pw foo user_pw bar encrypt_aes256
```

## Key features and `pdftk` compatibility

* **Familiar syntax:** Command-line compatible with `pdftk`. Verified against [Mike Haertl's php-pdftk test suite][5], so `sed s/pdftk/pdftl/g` should result in working scripts.
* **Pipelining:** Chain multiple operations in a single command using `---`.
* **Probably performant:** `pdftl` seems faster than `pdftk` for many operations _(untested hunch; data needed)_. Reason: `pdftl` mostly drives `pikepdf` which drives `qpdf`, a fast C++ library.
* **Extra/enhanced operations and features** such as zooming pages, smart merging preserving links and outlines, cropping/chopping up pages, text extraction, optimizing images.
* **Modern security:** Supports AES-256 encryption and modern permission flags out of the box.
* **Content editing:** Find & replace text via regular expressions, inject raw PDF operators, or overlay dynamic text.

`pdftl` maintains command-line compatibility with `pdftk` while introducing features required for modern PDF workflows.

| Feature | `pdftk` (Legacy) | `pdftl` (Modern) |
| :--- | :--- | :--- |
| **Pipelining** | ❌ (Requires temp files) | ✅ **Native** (Chain ops with `---`) |
| **Encryption** | ⚠️ (Obsolete RC4) | ✅ **AES-256 Support** |
| **Syntax** | Standard | ✅ **Compatible Extension** |
| **Page Geometry** | ❌ | ✅ **Crop to fit, Zoom, & Chop** |
| **Pipelined Logic** | ❌ | ✅ **Rotate + Stamp in one command** |
| **Extensible** | ❌ | ✅ **Custom operations written in Python** |
| **Installation** | Often complex binary | ✅ **Simple `pipx install pdftl`** |
| **Performance** | Variable | ✅ **Powered by pikepdf/qpdf** |
| **Link Integrity**| ⚠️ Often breaks TOC/Links|✅ **Preserves internal cross-refs** |
| **Help** | ⚠️ Basic (manpage) | ✅ **Self-documenting: [`pdftl help <operation/option/topic/tag>`][7]** |

## Installation

Install [`pipx`](https://pipx.pypa.io), and then:

```bash
pipx install pdftl[full]
```

A simple `pip install pdftl[full]` install is also supported.

**Note:** The `[full]` install includes [`ocrmypdf`](https://pypi.org/project/ocrmypdf/) for image optimization, [`reportlab`](https://pypi.org/project/reportlab/) for text generation, [`pypdfium2`](https://pypi.org/project/pypdfium2/) for text extraction and robust flattening, and [`pyHanko`][6] for cryptographic signature functionality. Omit `[full]` to omit those features and dependencies.

## Key features

### 📄 Standard operations

* **Combine:** [`cat`](https://pdftl.readthedocs.io/en/latest/operations/cat.html), [`shuffle`](https://pdftl.readthedocs.io/en/latest/operations/shuffle.html) (interleave pages from multiple docs).
* **Split:** [`burst`](https://pdftl.readthedocs.io/en/latest/operations/burst.html) (split into single pages), [`delete`](https://pdftl.readthedocs.io/en/latest/operations/delete.html) pages.
* **Metadata:** [`dump_data`](https://pdftl.readthedocs.io/en/latest/operations/dump_data.html), [`update_info`](https://pdftl.readthedocs.io/en/latest/operations/update_info.html), [`attach_files`](https://pdftl.readthedocs.io/en/latest/operations/attach_files.html), [`unpack_files`](https://pdftl.readthedocs.io/en/latest/operations/unpack_files.html).
* **Watermarking:** [`stamp`](https://pdftl.readthedocs.io/en/latest/operations/stamp.html) / [`background`](https://pdftl.readthedocs.io/en/latest/operations/background.html) (single page), [`multistamp`](https://pdftl.readthedocs.io/en/latest/operations/multistamp.html) / [`multibackground`](https://pdftl.readthedocs.io/en/latest/operations/multibackground.html).

### ✂️ Geometry & splitting

* **Rotate:** [`rotate`](https://pdftl.readthedocs.io/en/latest/operations/rotate.html) pages (absolute or relative).
* **Crop:** [`crop`](https://pdftl.readthedocs.io/en/latest/operations/crop.html) to margins or standard paper sizes (e.g., "A4").
* **Chop:** [`chop`](https://pdftl.readthedocs.io/en/latest/operations/chop.html) pages into grids or rows (e.g., split a scanned spread into two pages).
* **Shift, scale and spin** page content *inside* the page boundaries using [`place`](https://pdftl.readthedocs.io/en/latest/operations/place.html).

### 📝 Forms & annotations

* **Forms:** [`fill_form`](https://pdftl.readthedocs.io/en/latest/operations/fill_form.html), [`generate_fdf`](https://pdftl.readthedocs.io/en/latest/operations/generate_fdf.html), [`dump_data_fields`](https://pdftl.readthedocs.io/en/latest/operations/dump_data_fields.html).
* **Annotations:** [`modify_annots`](https://pdftl.readthedocs.io/en/latest/operations/modify_annots.html) (surgical edits to link properties, colors, borders), [`delete_annots`](https://pdftl.readthedocs.io/en/latest/operations/delete_annots.html), [`dump_annots`](https://pdftl.readthedocs.io/en/latest/operations/dump_annots.html).

### 🔐 Security
* **Decryption:** using [`input_pw`](https://pdftl.readthedocs.io/en/latest/general/input.html).
* **Encryption:** using [`owner_pw`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#owner-pw-pw), [`user_pw`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#user-pw-pw) and [`encrypt_aes256`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#encrypt-aes256), optionally setting permissions with [`allow`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#allow-perm).
* **Signatures:** add secure signatures using [`sign_key`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#sign-key-file) and [`sign_cert`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#sign-cert-file). List and verify signatures using [`dump_signatures`](https://pdftl.readthedocs.io/en/latest/operations/dump_signatures.html) (powered by [`pyHanko`][6]).

### 🛠️ Advanced

* **Text replacement:** [`replace`](https://pdftl.readthedocs.io/en/latest/operations/replace.html) text in content streams using regular expressions (experimental).
* **Code injection:** [`inject`](https://pdftl.readthedocs.io/en/latest/operations/inject.html) raw PDF operators at the head/tail of content streams.
* **Optimization:** [`optimize_images`](https://pdftl.readthedocs.io/en/latest/operations/optimize_images.html) (smart compression via OCRmyPDF).
* **Dynamic text:** [`add_text`](https://pdftl.readthedocs.io/en/latest/operations/add_text.html) supports Bates stamping and can add page numbers, filenames, timestamps, etc.
* **Cleanup:** [`normalize`](https://pdftl.readthedocs.io/en/latest/operations/normalize.html) content streams, [`linearize`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#linearize) for web viewing.
* **Extensibility:** write your own custom operation in Python, save to `~/.config/pdftl/operations` (*nix) or
  `%APPDATA%\pdftl\config` (windows)` and you can use it in pdftl, just like the built-in operations.

## Examples

For more than 100 other examples: `pdftl help examples`.

### Concatenation

```bash
# Merge two files
pdftl in1.pdf in2.pdf cat output combined.pdf

# Now with in2.pdf zoomed in
pdftl A=in1.pdf B=in2.pdf cat A Bz1 output combined2.pdf
```

### Geometry

```bash
# Take pages 1-5, rotate them 90 degrees East, and crop to A4
pdftl in.pdf cat 1-5east --- crop "(a4)" output out.pdf
```

### Pipelining

You can chain operations without intermediate files using `---`:

```bash
# Burst a file, but rotate and stamp every page first
pdftl in.pdf rotate south \
  --- stamp watermark.pdf \
  --- burst output page_%04d.pdf
```

### Forms and metadata

```bash
# Fill a form and flatten it (make it non-editable)
pdftl form.pdf fill_form data.fdf flatten output signed.pdf
```

### Modify annotations

```bash
# Change all Highlight annotations on odd pages to Red
pdftl docs.pdf modify_annots "odd/Highlight(C=[1 0 0])" output red_notes.pdf
```

### Modify content

```bash
# Add a watermark, the pdftk way
pdftl in.pdf stamp watermark.pdf output marked1.pdf
```

```
# Add an obnoxious semi-transparent red watermark on odd pages only
pdftl in.pdf add_text 'odd/YOUR AD HERE/(position=mid-center, font=Helvetica-Bold, size=72, rotate=45, color=1 0 0 0.5)' output with_ads.pdf
```

```
# Add Bates numbering starting at 000121
# Result: DEF-000121, DEF-000122, ...
pdftl in.pdf \
  add_text "/DEF-{page+120:06d}/(position=bottom-center, offset-y=10)" \
  output bates.pdf
```

```
# Content stream replacment with regular expressions (YMMV)
# Change black to red
pdftl in.pdf replace '/0 0 0 (RG|rg)/1 0 0 \1/' output redder.pdf
```


## Python API

While `pdftl` is primarily a CLI tool, it also exposes a robust Python API for integrating PDF workflows into your scripts.
 It supports both a Functional interface (similar to the CLI) and a Fluent interface (for method chaining).

```python
from pdftl import pipeline

# Chain operations fluently without saving intermediate files
(
    pipeline("input.pdf")
    .rotate("right")
    .stamp("watermark.pdf")
    .save("output.pdf")
)
```

See the **[API Tutorial][4]** for more details.

## Operations and options

| Operation                                                                                               | Description                                          |
|---------------------------------------------------------------------------------------------------------|------------------------------------------------------|
| [`add_text`](https://pdftl.readthedocs.io/en/latest/operations/add_text.html)                           | Add user-specified text strings to PDF pages         |
| [`attach_files`](https://pdftl.readthedocs.io/en/latest/operations/attach_files.html)                   | Attach files to the output PDF                       |
| [`background`](https://pdftl.readthedocs.io/en/latest/operations/background.html)                       | Use a 1-page PDF as the background for each page     |
| [`burst`](https://pdftl.readthedocs.io/en/latest/operations/burst.html)                                 | Split a single PDF into individual page files        |
| [`cat`](https://pdftl.readthedocs.io/en/latest/operations/cat.html)                                     | Concatenate pages from input PDFs into a new PDF     |
| [`chop`](https://pdftl.readthedocs.io/en/latest/operations/chop.html)                                   | Chop pages into multiple smaller pieces              |
| [`crop`](https://pdftl.readthedocs.io/en/latest/operations/crop.html)                                   | Crop pages                                           |
| [`delete`](https://pdftl.readthedocs.io/en/latest/operations/delete.html)                               | Delete pages from an input PDF                       |
| [`delete_annots`](https://pdftl.readthedocs.io/en/latest/operations/delete_annots.html)                 | Delete annotation info                               |
| [`dump_annots`](https://pdftl.readthedocs.io/en/latest/operations/dump_annots.html)                     | Dump annotation info                                 |
| [`dump_data`](https://pdftl.readthedocs.io/en/latest/operations/dump_data.html)                         | Metadata, page and bookmark info (XML-escaped)       |
| [`dump_data_annots`](https://pdftl.readthedocs.io/en/latest/operations/dump_data_annots.html)           | Dump annotation info in pdftk style                  |
| [`dump_data_fields`](https://pdftl.readthedocs.io/en/latest/operations/dump_data_fields.html)           | Print PDF form field data with XML-style escaping    |
| [`dump_data_fields_utf8`](https://pdftl.readthedocs.io/en/latest/operations/dump_data_fields_utf8.html) | Print PDF form field data in UTF-8                   |
| [`dump_data_utf8`](https://pdftl.readthedocs.io/en/latest/operations/dump_data_utf8.html)               | Metadata, page and bookmark info (in UTF-8)          |
| [`dump_dests`](https://pdftl.readthedocs.io/en/latest/operations/dump_dests.html)                       | Print PDF named destinations data to the console     |
| [`dump_files`](https://pdftl.readthedocs.io/en/latest/operations/dump_files.html)                       | List file attachments                                |
| [`dump_layers`](https://pdftl.readthedocs.io/en/latest/operations/dump_layers.html)                     | Dump layer info (JSON)                               |
| [`dump_signatures`](https://pdftl.readthedocs.io/en/latest/operations/dump_signatures.html)             | List and validate digital signatures                 |
| [`dump_text`](https://pdftl.readthedocs.io/en/latest/operations/dump_text.html)                         | Print PDF text data to the console or a file         |
| [`fill_form`](https://pdftl.readthedocs.io/en/latest/operations/fill_form.html)                         | Fill a PDF form                                      |
| [`filter`](https://pdftl.readthedocs.io/en/latest/operations/filter.html)                               | Do nothing (the default if `<operation>` is absent)  |
| [`generate_fdf`](https://pdftl.readthedocs.io/en/latest/operations/generate_fdf.html)                   | Generate an FDF file containing PDF form data        |
| [`inject`](https://pdftl.readthedocs.io/en/latest/operations/inject.html)                               | Inject code at start or end of page content streams  |
| [`insert`](https://pdftl.readthedocs.io/en/latest/operations/insert.html)                               | Insert blank pages                                   |
| [`modify_annots`](https://pdftl.readthedocs.io/en/latest/operations/modify_annots.html)                 | Modify properties of existing annotations            |
| [`move`](https://pdftl.readthedocs.io/en/latest/operations/move.html)                                   | Move pages to a new location                         |
| [`multibackground`](https://pdftl.readthedocs.io/en/latest/operations/multibackground.html)             | Use multiple pages as backgrounds                    |
| [`multistamp`](https://pdftl.readthedocs.io/en/latest/operations/multistamp.html)                       | Stamp multiple pages onto an input PDF               |
| [`normalize`](https://pdftl.readthedocs.io/en/latest/operations/normalize.html)                         | Reformat page content streams                        |
| [`optimize_images`](https://pdftl.readthedocs.io/en/latest/operations/optimize_images.html)             | Optimize images                                      |
| [`place`](https://pdftl.readthedocs.io/en/latest/operations/place.html)                                 | Shift, scale, and spin page content                  |
| [`replace`](https://pdftl.readthedocs.io/en/latest/operations/replace.html)                             | Regex replacement on page content streams            |
| [`render`](https://pdftl.readthedocs.io/en/latest/operations/render.html)                               | Render PDF pages as images                           |
| [`rotate`](https://pdftl.readthedocs.io/en/latest/operations/rotate.html)                               | Rotate pages in a PDF                                |
| [`shuffle`](https://pdftl.readthedocs.io/en/latest/operations/shuffle.html)                             | Interleave pages from multiple input PDFs            |
| [`stamp`](https://pdftl.readthedocs.io/en/latest/operations/stamp.html)                                 | Stamp a 1-page PDF onto each page of an input PDF    |
| [`unpack_files`](https://pdftl.readthedocs.io/en/latest/operations/unpack_files.html)                   | Unpack file attachments                              |
| [`update_info`](https://pdftl.readthedocs.io/en/latest/operations/update_info.html)                     | Update PDF metadata from dump_data instructions      |
| [`update_info_utf8`](https://pdftl.readthedocs.io/en/latest/operations/update_info_utf8.html)           | Update PDF metadata from dump_data_utf8 instructions |

| Option                                                                                                       | Description                                       |
|--------------------------------------------------------------------------------------------------------------|---------------------------------------------------|
| [`allow <perm>`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#allow-perm)                 | Specify permissions for encrypted files           |
| [`compress`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#compress)                       | Compress output file streams (default)            |
| [`drop_info`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#drop-info)                     | Discard document-level info metadata              |
| [`drop_xfa`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#drop-xfa)                       | Discard form XFA data if present                  |
| [`drop_xmp`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#drop-xmp)                       | Discard document-level XMP metadata               |
| [`encrypt_128bit`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#encrypt-128bit)           | Use 128 bit encryption (obsolete, maybe insecure) |
| [`encrypt_40bit`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#encrypt-40bit)             | Use 40 bit encryption (obsolete, highly insecure) |
| [`encrypt_aes128`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#encrypt-aes128)           | Use 128 bit AES encryption (maybe obsolete)       |
| [`encrypt_aes256`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#encrypt-aes256)           | Use 256 bit AES encryption                        |
| [`flatten`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#flatten)                         | Flatten all annotations                           |
| [`keep_final_id`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#keep-final-id)             | Copy final input PDF's ID metadata to output      |
| [`keep_first_id`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#keep-first-id)             | Copy first input PDF's ID metadata to output      |
| [`linearize`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#linearize)                     | Linearize output file(s)                          |
| [`no_encrypt_metadata`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#no-encrypt-metadata) | Leave metadata unencrypted                        |
| [`need_appearances`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#need-appearances)       | Set a form rendering flag in the output PDF       |
| [`output <file>`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#output-file)               | The output file path, or a template for burst     |
| [`owner_pw <pw>`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#owner-pw-pw)               | Set owner password and encrypt output             |
| [`sign_cert <file>`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#sign-cert-file)         | Path to certificate PEM                           |
| [`sign_field <name>`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#sign-field-name)       | Signature field name (default: Signature1)        |
| [`sign_key <file>`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#sign-key-file)           | Path to private key PEM                           |
| [`sign_pass_env <var>`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#sign-pass-env-var)   | Environment variable with sign_cert passphrase    |
| [`sign_pass_prompt`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#sign-pass-prompt)       | Prompt for sign_cert passphrase                   |
| [`uncompress`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#uncompress)                   | Disable compression of output file streams        |
| [`user_pw <pw>`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#user-pw-pw)                 | Set user password and encrypt output              |
| [`verbose`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#verbose)                         | Turn on verbose output                            |


## Links

* **License:** This project is licensed under the [Mozilla Public License 2.0][1].
* **Changelog:** [CHANGELOG.md][2].
* **Documentation:** [pdftl.readthedocs.io][3].

[1]: https://raw.githubusercontent.com/pdftl/pdftl/main/LICENSE
[2]: https://github.com/pdftl/pdftl/blob/main/CHANGELOG.md
[3]: https://pdftl.readthedocs.io
[4]: https://pdftl.readthedocs.io/en/latest/api_tutorial.html
[5]: https://github.com/mikehaertl/php-pdftk
[6]: https://github.com/MatthiasValvekens/pyHanko
[7]: https://pdftl.readthedocs.io/en/latest/general/help.html
