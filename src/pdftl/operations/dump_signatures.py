# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/dump_signatures.py

"""Dump and validate digital signatures from a PDF file using pyHanko"""

import concurrent.futures
import io
import logging
import re
from pathlib import Path

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError, OperationError
from pdftl.utils.dependencies import ensure_dependencies
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
* `SignatureIntegrity`: VALID or INVALID.
  Requires both that the document bytes are untouched since signing (digest match), and that
  the cryptographic signature verifies against the embedded certificate's public key.
* `SignerTrusted`: True or False (whether the certificate is trusted against provided trust roots).
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


def _process_suspicious_line(line, blocks, current_type, current_data):
    """Helper to process a single line of the diff analysis diff."""
    if line.startswith("- "):
        current_data.append(line[2:].strip())
        return current_type, current_data

    if ":" in line:
        # Flush the existing block before starting a new key
        if current_type is not None and current_data:
            blocks.append({"type": current_type, "data": " | ".join(current_data)})
            # We flushed data, so we reset the context
            current_type = None
            current_data = []

        if line.endswith(":"):
            return line[:-1].strip(), []

        # Handle inline split (e.g., 112:0)
        parts = line.split(":", 1)
        blocks.append({"type": parts[0].strip(), "data": parts[1].strip()})
        # Preserve current_type and current_data context if we didn't flush
        return current_type, current_data

    # Handle plain text blocks
    if current_type is not None:
        current_data.append(line)
    else:
        blocks.append({"type": "General Modification", "data": line})

    return current_type, current_data


def _flush_suspicious_block(blocks, current_type, current_data):
    """Helper to flush any remaining block data at the end of parsing."""
    if current_type is not None:
        data_str = " | ".join(current_data) if current_data else ""
        blocks.append({"type": current_type, "data": data_str})


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

    blocks: list[dict[str, str]] = []
    current_type = None
    current_data: list[str] = []

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        current_type, current_data = _process_suspicious_line(
            line, blocks, current_type, current_data
        )

    _flush_suspicious_block(blocks, current_type, current_data)
    return blocks


def _print_signature_stanza(sig_data, out):
    """Helper to print a single signature stanza, lowering CLI hook complexity."""
    print("SignatureBegin", file=out)
    print(f"SignatureFieldName: {sig_data.get('field_name')}", file=out)
    print(f"SignatureSigner: {sig_data.get('signer')}", file=out)
    print(f"SignatureHashAlgorithm: {sig_data.get('hash_algorithm')}", file=out)

    # Mathematical Integrity: both the byte-range digest must match (intact)
    # AND the PKCS#1 signature must cryptographically verify against the
    # embedded certificate's public key (valid).
    integrity = (
        "VALID" if sig_data.get("is_intact") and sig_data.get("is_signature_valid") else "INVALID"
    )
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


def dump_signatures_cli_hook(result: OpResult, stage, _pipeline):
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
            _print_signature_stanza(sig_data, out)
            if idx + 1 < len(signatures):
                print("---", file=out)


def _parse_operation_args(operation_args) -> list[str]:
    """Parse trust_roots from CLI positional operation_args."""
    if not operation_args:
        return []

    trust_roots = []
    i = 0
    while i < len(operation_args):
        arg = operation_args[i]
        if arg == "trust_roots":
            i += 1
            found = False
            while i < len(operation_args):
                trust_roots.append(operation_args[i])
                found = True
                i += 1
            if not found:
                raise InvalidArgumentError(
                    "`trust_roots` option requires at least one certificate file path"
                )
        else:
            raise InvalidArgumentError(f"Unrecognized argument '{arg}' for dump_signatures")
        i += 1
    return trust_roots


@register_operation(
    "dump_signatures",
    tags=["info", "security", "signatures"],
    cli_hook=dump_signatures_cli_hook,
    type="single input operation",
    desc="List and validate digital signatures",
    long_desc=_DUMP_SIGNATURES_LONG_DESC,
    usage="<input> dump_signatures [output <output>] [trust_roots <file...>]",
    args=(
        [c.INPUT_FILENAME, c.INPUT_PDF, c.INPUT_PASSWORD, c.OPERATION_ARGS],
        {"output_file": c.OUTPUT},
    ),
)
def dump_signatures(
    pdf_filename,
    pdf,
    pdf_password,
    operation_args=None,
    output_file=None,
    trust_roots=None,
) -> OpResult:
    """
    Validate PDF signatures and returns a list of validation results.
    """
    if trust_roots is None and operation_args:
        trust_roots = _parse_operation_args(operation_args)

    _patch_pyhanko()
    ph_logger = logging.getLogger("pyhanko")
    cv_logger = logging.getLogger("pyhanko_certvalidator")
    prev_ph, prev_cv = ph_logger.level, cv_logger.level
    ph_logger.setLevel(logging.CRITICAL)
    cv_logger.setLevel(logging.CRITICAL)

    try:
        signatures_data = _validate_signatures_worker(
            pdf_filename, pdf, pdf_password, trust_roots=trust_roots
        )
        return OpResult(success=True, data=signatures_data, meta={c.META_OUTPUT_FILE: output_file})
    finally:
        ph_logger.setLevel(prev_ph)
        cv_logger.setLevel(prev_cv)


def _get_timestamp_string(status) -> str | None:
    """Helper to safely extract the timestamp string from status."""
    if getattr(status, "timestamp_validity", None) and status.timestamp_validity.timestamp:
        return f"{status.timestamp_validity.timestamp.isoformat()} (Cryptographic TSA Token)"
    if getattr(status, "signer_reported_dt", None) and status.signer_reported_dt:
        return f"{status.signer_reported_dt.isoformat()} (Signer-reported)"
    return None


def _get_cert_chain(status) -> list:
    """Helper to extract the certificate chain array from status."""
    chain = []
    if getattr(status, "validation_path", None):
        for cert in status.validation_path:
            subject_name = cert.subject.native.get("common_name", cert.subject.human_friendly)
            chain.append(subject_name)
    elif getattr(status, "signing_cert", None):
        cert = status.signing_cert
        subject_name = cert.subject.native.get("common_name", cert.subject.human_friendly)
        chain.append(f"{subject_name} (Untrusted/Isolated)")
    return chain


def _extract_signature_info(sig, status) -> dict:
    """Helper to extract and format a single pyHanko signature validation result."""
    from pyhanko.sign.diff_analysis import DiffResult

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

    trust_problem = getattr(status, "trust_problem_indic", None)
    trust_problem_name = trust_problem.name if trust_problem else "NO_PROBLEM"
    is_trusted = getattr(status, "valid", False) and trust_problem_name == "NO_PROBLEM"

    return {
        "field_name": sig.field_name,
        "signer": signer_name,
        "hash_algorithm": getattr(status, "md_algorithm", "Unknown"),
        "is_intact": getattr(status, "intact", False),
        "is_signature_valid": getattr(status, "valid", False),
        "is_trusted": is_trusted,
        "trust_problem": trust_problem_name,
        "coverage": status.coverage.name if getattr(status, "coverage", None) else "UNKNOWN_NONE",
        "modification_level": mod_level,
        "suspicious_details": suspicious_blocks,
        "docmdp_ok": getattr(status, "docmdp_ok", None),
        "timestamp": _get_timestamp_string(status),
        "signature_mechanism": getattr(status, "pkcs7_signature_mechanism", None),
        "chain_of_trust": _get_cert_chain(status),
    }


def _load_cert_from_path(cert_path):
    from pyhanko.keys import load_certs_from_pemder

    try:
        certs = list(load_certs_from_pemder([cert_path]))
        if not certs:
            raise InvalidArgumentError(
                f"No valid certificates found in trust root file '{cert_path}'"
            )
        return certs
    except InvalidArgumentError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        raise InvalidArgumentError(
            f"Failed to load trust root certificate '{cert_path}': {e}"
        ) from e


def _load_vc_trust_roots(trust_roots):
    if not trust_roots:
        return []

    if isinstance(trust_roots, (str, Path)):
        trust_roots = [trust_roots]

    vc_trust_roots = []
    for item in trust_roots:
        if isinstance(item, (str, Path)):
            vc_trust_roots.extend(_load_cert_from_path(item))
        else:
            vc_trust_roots.append(item)

    return vc_trust_roots


def _get_pdf_source_bytes(pdf_filename, pdf):
    if pdf_filename != "_":
        with open(pdf_filename, "rb") as f:
            return f.read()

    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def _safe_validate_pdf_signature(sig, vc):
    """Executes pyHanko's validate_pdf_signature safely in active event loop environments."""
    from pyhanko.sign.validation import validate_pdf_signature
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(
                validate_pdf_signature, sig, signer_validation_context=vc
            ).result()

    return validate_pdf_signature(sig, signer_validation_context=vc)


def _validate_signatures_worker(pdf_filename, pdf, pdf_password, trust_roots=None):
    ensure_dependencies(
        feature_name="validate_signatures",
        dependencies={"pyhanko": "pyhanko"},
        extra_tag="signing",
    )
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation.errors import SignatureValidationError
    from pyhanko_certvalidator import ValidationContext

    vc_trust_roots = _load_vc_trust_roots(trust_roots)
    vc = ValidationContext(trust_roots=vc_trust_roots, allow_fetching=False)

    source_bytes = _get_pdf_source_bytes(pdf_filename, pdf)
    reader = PdfFileReader(io.BytesIO(source_bytes))

    if reader.encrypted:
        password = pdf_password or ""
        reader.decrypt(password.encode("utf-8"))

    results = []

    for sig in reader.embedded_signatures:
        try:
            status = _safe_validate_pdf_signature(sig, vc)
        except (SignatureValidationError, ValueError) as e:
            raise OperationError(f"[dump_signatures] {e}") from e

        results.append(_extract_signature_info(sig, status))

    return results
