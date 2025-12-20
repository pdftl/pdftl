# Changelog

This changelog format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!-- Posible headings: Added, Changed, Deprecated, Fixed, Removed, Security -->

## [0.3.0] - 2025-12-20

### Added

- Get `help` by tag with `pdftl help tag:<tagname>`

- `dump_signatures`: view and validate PDF signatures

- PDF signature output options:

    - `sign_cert <file>` Path to certificate PEM

	- `sign_field <name>` Signature field name (default: Signature1)

    - `sign_key <file>` Path to private key PEM

    - `sign_pass_env <var>` Environment variable with sign_cert passphrase

    - `sign_pass_prompt` Prompt for sign_cert passphrase

- `dump_layers`: dump PDF optional content groups (OCGs), a.k.a. "layers"

### Fixed

- performance improvements for `cat`

## [0.2.1] - 2025-12-17

### Added

- `crop`: added `fit` and `fit-group`

- artwork

- extended NOTICE.md: acknowledge `pikepdf`/`qpdf` and `pypdfium2`

- Windows testing

### Fixed

- performance improvements (lazy-loaded imports)

- help tweaks: add sources for non-operations; more help topic aliases

## [0.2.0] - 2025-12-13

### Added

- readthedocs integration and docs generation

### Fixed

- Improved help text

## [0.1.1] - 2025-12-12

### Added

- codecov integration
- PyPI publish integration

## [0.1.0] - 2025-12-11

### Added

- Initial public release of `pdftl`.
- Operations:
   - ``add_text``               Add user-specified text strings to PDF pages
   - ``background``             Use a 1-page PDF as the background for each page
   - ``burst``                  Split a single PDF into individual page files
   - ``cat``                    Concatenate pages from input PDFs into a new PDF
   - ``chop``                   Chop pages into multiple smaller pieces
   - ``crop``                   Crop pages
   - ``delete``                 Delete pages from an input PDF
   - ``delete_annots``          Delete annotation info
   - ``dump_annots``            Dump annotation info
   - ``dump_data``              Metadata, page and bookmark info (XML-escaped)
   - ``dump_data_annots``       Dump annotation info in pdftk style
   - ``dump_data_fields``       Print PDF form field data with XML-style escaping
   - ``dump_data_fields_utf8``  Print PDF form field data in UTF-8
   - ``dump_data_utf8``         Metadata, page and bookmark info (in UTF-8)
   - ``dump_dests``             Print PDF named destinations data to the console
   - ``dump_text``              Print PDF text data to the console or a file
   - ``fill_form``              Fill a PDF form
   - ``filter``                 Do nothing. (The default if ``<operation>`` omitted.)
   - ``generate_fdf``           Generate an FDF file containing PDF form data
   - ``inject``                 Inject code at start or end of page content streams
   - ``list_files``             List file attachments
   - ``modify_annots``          Modify properties of existing annotations
   - ``multibackground``        Use multiple pages as backgrounds
   - ``multistamp``             Stamp multiple pages onto an input PDF
   - ``normalize``              Reformat page content streams
   - ``optimize_images``        Optimize images
   - ``replace``                Regex replacement on page content streams
   - ``rotate``                 Rotate pages in a PDF
   - ``shuffle``                Interleave pages from multiple input PDFs
   - ``spin``                   Spin page content in a PDF
   - ``stamp``                  Stamp a 1-page PDF onto each page of an input PDF
   - ``unpack_files``           Unpack file attachments
   - ``update_info``            Update PDF metadata
   - ``update_info_utf8``       Update PDF metadata from dump_data_utf8 instructions
- Output options:
   - ``allow <perm>...``        Specify permissions for encrypted files
   - ``attach_files <file>``... Attach files to the output PDF
   - ``compress``               (default) Compress output file streams
   - ``drop_info``              Discard document-level info metadata
   - ``drop_xmp``               Discard document-level XMP metadata
   - ``encrypt_128bit``         Use 128 bit encryption (obsolete, maybe insecure)
   - ``encrypt_40bit``          Use 40 bit encryption (obsolete, highly insecure)
   - ``encrypt_aes128``         Use 128 bit AES encryption (maybe obsolete)
   - ``encrypt_aes256``         Use 256 bit AES encryption
   - ``flatten``                Flatten all annotations
   - ``keep_final_id``          Copy final input PDF's ID metadata to output
   - ``keep_first_id``          Copy first input PDF's ID metadata to output
   - ``linearize``              Linearize output file(s)
   - ``need_appearances``       Set a form rendering flag in the output PDF
   - ``output <file>``          The output file path, or a template for 'burst'
   - ``owner_pw <pw>``          Set owner password and encrypt output
   - ``uncompress``             Disables compression of output file streams
   - ``user_pw <pw>``           Set user password and encrypt output
   - ``verbose``                Turn on verbose output
