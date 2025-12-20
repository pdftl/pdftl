# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/dump_signatures.py

"""Dump and validate digital signatures from a PDF file using pyHanko"""

import io
import logging

from pdftl.core.registry import register_operation
from pdftl.utils.io_helpers import smart_open_output

logger = logging.getLogger(__name__)

_DUMP_SIGNATURES_LONG_DESC = """ Lists all digital signatures embedded
in the PDF and performs cryptographic validation on each.

If possible, this reads the PDF file directly from disk to ensure that
the cryptographic byte ranges are not disturbed by PDF parsing
engines.

### Signature Stanza Format

* `SignatureBegin`
* `SignatureFieldName`: The name of the signature field.
* `SignatureSigner`: Common Name (CN) of the signer.
* `SignatureHashAlgorithm`: e.g., sha256.
* `SignatureIntegrity`: VALID or INVALID (mathematical check).
* `SignatureCoverage`: ENTIRE_FILE, REVISION_ONLY, or PARTIAL.
* `SignatureModificationLevel`: NONE, FORM_FILLING, or SUSPICIOUS.
"""


@register_operation(
    "dump_signatures",
    tags=["info", "security", "signatures"],
    type="single input operation",
    desc="List and validate digital signatures",
    long_desc=_DUMP_SIGNATURES_LONG_DESC,
    usage="<input> dump_signatures [output <output>]",
    # Pass filename and password to bypass pikepdf object modifications
    args=(["input_filename", "input_pdf", "input_password"], {"output_file": "output"}),
)
def dump_signatures(pdf_filename, pdf, pdf_password, output_file=None):
    """
    Validate PDF signatures and dump status in a stanza-based format.
    """
    try:
        from pyhanko.pdf_utils.reader import PdfFileReader
        from pyhanko.sign.diff_analysis import DiffResult
        from pyhanko.sign.validation import validate_pdf_signature
    except ImportError:
        # Standard error for pdftl when a dependency is missing
        raise RuntimeError("The 'pyhanko' library is required for dump_signatures.")

    # Mute pyHanko's internal validation log noise (tracebacks for self-signed certs)
    ph_logger = logging.getLogger("pyhanko")
    cv_logger = logging.getLogger("pyhanko_certvalidator")
    prev_ph, prev_cv = ph_logger.level, cv_logger.level
    ph_logger.setLevel(logging.CRITICAL)
    cv_logger.setLevel(logging.CRITICAL)

    try:
        if pdf_filename != "_":
            with open(pdf_filename, "rb") as f:
                source_bytes = f.read()
        else:
            buf = io.BytesIO()
            pdf.save(buf)
            source_bytes = buf.getvalue()

        # 1. Initialize without the password argument
        reader = PdfFileReader(io.BytesIO(source_bytes))

        # 2. If a password was provided, decrypt the reader instance
        if reader.encrypted:
            password = pdf_password or ""
            # pyHanko's decrypt usually takes the password as bytes
            reader.decrypt(password.encode("utf-8"))

        # 3. Now you can access signatures safely
        signatures = list(reader.embedded_signatures)

        with smart_open_output(output_file) as out:
            if not signatures:
                print("No signatures found.", file=out)
                return

            for idx, sig in enumerate(signatures):
                # Perform cryptographic validation
                status = validate_pdf_signature(sig)

                print("SignatureBegin", file=out)
                print(f"SignatureFieldName: {sig.field_name}", file=out)

                # Safe extraction of signer name
                signer_name = status.signing_cert.subject.native.get("common_name", "Unknown")
                print(f"SignatureSigner: {signer_name}", file=out)
                print(f"SignatureHashAlgorithm: {status.md_algorithm}", file=out)

                # 1. Cryptographic Integrity (The Math)
                print(
                    f"SignatureIntegrity: {'VALID' if status.intact else 'INVALID'}",
                    file=out,
                )

                # 2. File Coverage
                print(f"SignatureCoverage: {status.coverage.name}", file=out)

                # 3. Difference Analysis (The Modifications)
                if isinstance(status.diff_result, DiffResult):
                    mod_level = status.diff_result.modification_level.name
                else:
                    # Handles SuspiciousModification or other exception objects
                    mod_level = f"SUSPICIOUS ({type(status.diff_result).__name__})"

                print(f"SignatureModificationLevel: {mod_level}", file=out)

                if idx + 1 < len(signatures):
                    print("---", file=out)
    finally:
        # Restore logging levels
        ph_logger.setLevel(prev_ph)
        cv_logger.setLevel(prev_cv)
