# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/dump_signatures.py

"""Dump and validate digital signatures from a PDF file using pyHanko"""

import io
import logging
import re

import pdftl.core.constants as c
from pdftl.core.registry import register_operation
from pdftl.core.types import OpResult
from pdftl.exceptions import OperationError
from pdftl.utils.io_helpers import smart_open

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
* `SignatureIntegrity`: VALID or INVALID (mathematical hash check).
* `SignerTrusted`: True or False (whether the certificate is trusted).
* `TrustProblem`: Detailed reason if the signer is untrusted.
* `SignatureCoverage`: ENTIRE_FILE, REVISION_ONLY, or PARTIAL.
* `SignatureModificationLevel`: NONE, FORM_FILLING, or SUSPICIOUS.
* `DocMDPOk`: True or False (whether document permissions allow the modifications).
* `SignatureTimestamp`: The verified cryptographic timestamp, or signer-reported time.
* `SignatureMechanism`: The signature algorithm mechanism (e.g., rsassa_pkcs1v15).
* `SignatureChainOfTrust`: The certificate chain of trust from end-entity to Root CA.

If `SignatureModificationLevel` is SUSPICIOUS, it will be followed by one or more blocks:
* `SuspiciousDetailsBegin`
* `SuspiciousDetailsType`: The category of the suspicious modification.
* `SuspiciousDetailsData`: The related objects or details.
"""


def _patch_pyhanko():
    """Workaround for a pyHanko bug: https://github.com/MatthiasValvekens/pyHanko/issues/671"""
    from pyhanko.pdf_utils import generic

    if getattr(generic.parse_pdf_date, "_pdftl_patched", False):
        return

    _orig = generic.parse_pdf_date

    def _patched(date_str, strict=False):
        if isinstance(date_str, bytes):
            date_str = date_str.decode("latin-1")
        # Strip trailing null bytes and whitespace from some PDF generators
        date_str = date_str.rstrip("\x00 \t\r\n")
        return _orig(date_str, strict=strict)

    _patched._pdftl_patched = True
    generic.parse_pdf_date = _patched


def _parse_suspicious_details(diff_result) -> list:
    """
    Parses pyHanko's text-based diff analysis into structured blocks.
    Returns a list of dicts with 'type' and 'data' keys.
    """
    text = str(diff_result)

    # Clean up outer Exception wrapper if present
    text = re.sub(r"^[A-Za-z]+\(\"(.*)\"\)$", r"\1", text, flags=re.DOTALL)

    # Compress object references: Reference(idnum=112, generation=0) -> 112:0
    text = re.sub(r"Reference\(idnum=(\d+),\s*generation=(\d+)\)", r"\1:\2", text)

    # Compress document paths: AbsoluteContext(path=PathInRevision('.Root.Pages')) -> .Root.Pages
    text = re.sub(r"AbsoluteContext\(path=PathInRevision\('([^']+)'\)\)", r"\1", text)

    blocks = []
    current_type = None
    current_data = []

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue

        if line.startswith("- "):
            # It's a bullet point belonging to the previous header
            val = line[2:].strip()
            if current_type:
                current_data.append(val)
            else:
                current_data.append(val)
        elif ":" in line:
            if line.endswith(":"):
                # It's a header line for bullet points
                if current_type and current_data:
                    blocks.append({"type": current_type, "data": " | ".join(current_data)})
                current_type = line[:-1].strip()
                current_data = []
            else:
                # It's an inline type/data split: "Type details here: 112:0, 113:0"
                if current_type and current_data:
                    blocks.append({"type": current_type, "data": " | ".join(current_data)})
                    current_type = None
                    current_data = []

                parts = line.split(":", 1)
                blocks.append({"type": parts[0].strip(), "data": parts[1].strip()})
        else:
            # Plain text fallback
            if current_type:
                current_data.append(line)
            else:
                blocks.append({"type": "General Modification", "data": line})

    # Flush any remaining data
    if current_type and current_data:
        blocks.append({"type": current_type, "data": " | ".join(current_data)})
    elif current_type and not current_data:
        blocks.append({"type": current_type, "data": ""})

    return blocks


def dump_signatures_cli_hook(result: OpResult, _stage, _pipeline):
    """
    CLI Hook for dump_signatures.
    Formats the list of signature dictionaries into the Stanza text format.
    """
    from pdftl.utils.hooks import from_result_meta

    output_file = from_result_meta(result, c.META_OUTPUT_FILE)
    signatures = result.data

    with smart_open(output_file) as out:
        if not signatures:
            print("No signatures found.", file=out)
            return

        for idx, sig_data in enumerate(signatures):
            print("SignatureBegin", file=out)
            print(f"SignatureFieldName: {sig_data.get('field_name')}", file=out)
            print(f"SignatureSigner: {sig_data.get('signer')}", file=out)
            print(f"SignatureHashAlgorithm: {sig_data.get('hash_algorithm')}", file=out)

            # Mathematical Integrity
            integrity = "VALID" if sig_data.get("is_intact") else "INVALID"
            print(f"SignatureIntegrity: {integrity}", file=out)

            # Trust Identity
            print(f"SignerTrusted: {sig_data.get('is_trusted')}", file=out)
            if sig_data.get("trust_problem") != "NO_PROBLEM":
                print(f"TrustProblem: {sig_data.get('trust_problem')}", file=out)

            print(f"SignatureCoverage: {sig_data.get('coverage')}", file=out)
            print(f"SignatureModificationLevel: {sig_data.get('modification_level')}", file=out)
            print(f"DocMDPOk: {sig_data.get('docmdp_ok')}", file=out)

            # Print parsed suspicious details blocks
            suspicious_details = sig_data.get("suspicious_details", [])
            for block in suspicious_details:
                print("SuspiciousDetailsBegin", file=out)
                print(f"SuspiciousDetailsType: {block.get('type', 'Unknown')}", file=out)
                print(f"SuspiciousDetailsData: {block.get('data', '')}", file=out)

            # Metadata and Identity details
            if sig_data.get("timestamp"):
                print(f"SignatureTimestamp: {sig_data.get('timestamp')}", file=out)

            if sig_data.get("signature_mechanism"):
                print(f"SignatureMechanism: {sig_data.get('signature_mechanism')}", file=out)

            chain = sig_data.get("chain_of_trust")
            if chain:
                print(f"SignatureChainOfTrust: {' -> '.join(chain)}", file=out)

            if idx + 1 < len(signatures):
                print("---", file=out)


@register_operation(
    "dump_signatures",
    tags=["info", "security", "signatures"],
    cli_hook=dump_signatures_cli_hook,
    type="single input operation",
    desc="List and validate digital signatures",
    long_desc=_DUMP_SIGNATURES_LONG_DESC,
    usage="<input> dump_signatures [output <output>]",
    # Pass filename and password to bypass pikepdf object modifications
    args=([c.INPUT_FILENAME, c.INPUT_PDF, c.INPUT_PASSWORD], {"output_file": c.OUTPUT}),
)
def dump_signatures(pdf_filename, pdf, pdf_password, output_file=None) -> OpResult:
    """
    Validate PDF signatures and returns a list of validation results.
    """
    _patch_pyhanko()
    ph_logger = logging.getLogger("pyhanko")
    cv_logger = logging.getLogger("pyhanko_certvalidator")
    prev_ph, prev_cv = ph_logger.level, cv_logger.level
    ph_logger.setLevel(logging.CRITICAL)
    cv_logger.setLevel(logging.CRITICAL)

    try:
        signatures_data = _validate_signatures_worker(pdf_filename, pdf, pdf_password)
        return OpResult(success=True, data=signatures_data, meta={c.META_OUTPUT_FILE: output_file})
    finally:
        ph_logger.setLevel(prev_ph)
        cv_logger.setLevel(prev_cv)


def _validate_signatures_worker(pdf_filename, pdf, pdf_password):
    try:
        from pyhanko.pdf_utils.reader import PdfFileReader
        from pyhanko.sign.diff_analysis import DiffResult
        from pyhanko.sign.validation import validate_pdf_signature
        from pyhanko.sign.validation.errors import SignatureValidationError
    except ImportError:
        raise RuntimeError("The 'pyhanko' library is required for dump_signatures.")

    if pdf_filename != "_":
        with open(pdf_filename, "rb") as f:
            source_bytes = f.read()
    else:
        buf = io.BytesIO()
        pdf.save(buf)
        source_bytes = buf.getvalue()

    reader = PdfFileReader(io.BytesIO(source_bytes))

    if reader.encrypted:
        password = pdf_password or ""
        reader.decrypt(password.encode("utf-8"))

    results = []

    for sig in reader.embedded_signatures:
        try:
            status = validate_pdf_signature(sig)
        except (SignatureValidationError, ValueError) as e:
            raise OperationError(f"[dump_signatures] {e}") from e

        signer_name = "Unknown"
        if getattr(status, "signing_cert", None):
            signer_name = status.signing_cert.subject.native.get("common_name", "Unknown")

        # Handle Document Modifications
        suspicious_blocks = []
        if isinstance(status.diff_result, DiffResult):
            mod_level = status.diff_result.modification_level.name
            if mod_level == "SUSPICIOUS":
                suspicious_blocks = _parse_suspicious_details(status.diff_result)
        else:
            mod_level = f"SUSPICIOUS ({type(status.diff_result).__name__})"
            suspicious_blocks = _parse_suspicious_details(status.diff_result)

        timestamp = None
        if getattr(status, "timestamp_validity", None) and status.timestamp_validity.timestamp:
            timestamp = (
                f"{status.timestamp_validity.timestamp.isoformat()} (Cryptographic TSA Token)"
            )
        elif getattr(status, "signer_reported_dt", None) and status.signer_reported_dt:
            timestamp = f"{status.signer_reported_dt.isoformat()} (Signer-reported)"

        trust_problem = "NO_PROBLEM"
        if getattr(status, "trust_problem_indic", None):
            trust_problem = status.trust_problem_indic.name

        chain = []
        if getattr(status, "validation_path", None):
            for cert in status.validation_path:
                subject_name = cert.subject.native.get("common_name", cert.subject.human_friendly)
                chain.append(subject_name)
        elif getattr(status, "signing_cert", None):
            cert = status.signing_cert
            subject_name = cert.subject.native.get("common_name", cert.subject.human_friendly)
            chain.append(f"{subject_name} (Untrusted/Isolated)")

        sig_data = {
            "field_name": sig.field_name,
            "signer": signer_name,
            "hash_algorithm": getattr(status, "md_algorithm", "Unknown"),
            "is_intact": getattr(status, "intact", False),
            "is_trusted": getattr(status, "valid", False) and trust_problem == "NO_PROBLEM",
            "trust_problem": trust_problem,
            "coverage": status.coverage.name
            if getattr(status, "coverage", None)
            else "UNKNOWN_NONE",
            "modification_level": mod_level,
            "suspicious_details": suspicious_blocks,
            "docmdp_ok": getattr(status, "docmdp_ok", None),
            "timestamp": timestamp,
            "signature_mechanism": getattr(status, "pkcs7_signature_mechanism", None),
            "chain_of_trust": chain,
        }
        results.append(sig_data)

    return results
