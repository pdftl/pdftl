from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.x509.oid import NameOID
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.validation import validate_pdf_signature

from pdftl.cli.main import main


@pytest.fixture
def test_pki(tmp_path):
    from datetime import datetime, timedelta, timezone  # 3.10 compatible

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PDTFL Test")])

    # Use timezone.utc for compatibility with Python 3.10
    now = datetime.now(timezone.utc)

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.ExtendedKeyUsage([x509.ObjectIdentifier("1.2.840.113583.1.1.5")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    key_path = tmp_path / "test_key.pem"
    cert_path = tmp_path / "test_cert.pem"

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    return key_path, cert_path


def test_sign_pipeline_integrity(tmp_path, test_pki):
    key_path, cert_path = test_pki
    input_pdf = Path("tests/assets/2_page.pdf")
    output_pdf = tmp_path / "signed.pdf"

    # Mock sys.argv so main() thinks it was called from the CLI
    test_args = [
        "pdftl",
        str(input_pdf),
        "output",
        str(output_pdf),
        "sign_key",
        str(key_path),
        "sign_cert",
        str(cert_path),
    ]

    with patch("sys.argv", test_args):
        # main() usually returns None or 0 on success
        main()

    # Verify Cryptographic Integrity
    with open(output_pdf, "rb") as f:
        reader = PdfFileReader(f)
        # Typically, we want to validate the last signature added
        # If there's only one, it's at index 0
        if not reader.embedded_signatures:
            pytest.fail("No signatures found in the output PDF")

        sig = reader.embedded_signatures[0]
        status = validate_pdf_signature(sig)

        assert status.intact, "Signature digest mismatch - file likely corrupted"
        assert status.valid, "Signature failed cryptographic validation"

        # Verify the algorithm used
        # This ensures pyHanko didn't default to an older algorithm like SHA1
        assert status.md_algorithm == "sha256", f"Expected SHA256, got {status.md_algorithm}"

        # Verify the signer identity using asn1crypto's native dictionary access
        signer_info = status.signing_cert.subject.native
        # signer_info will look like: {'common_name': 'PDTFL Test'}
        assert signer_info["common_name"] == "PDTFL Test"

        # Verify the file coverage
        # ENTIRE_FILE means the signature covers everything except the signature itself
        from pyhanko.sign.validation import SignatureCoverageLevel

        assert status.coverage == SignatureCoverageLevel.ENTIRE_FILE

        # Verify it was an incremental update
        # ModificationLevel.NONE confirms no changes were made to the document
        # structure after the signature was applied.
        from pyhanko.sign.diff_analysis import ModificationLevel

        assert status.diff_result.modification_level == ModificationLevel.NONE
