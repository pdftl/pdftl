# tests/output/test_sign.py

from unittest.mock import MagicMock, mock_open, patch

import pytest

from pdftl.exceptions import UserCommandLineError
from pdftl.output.sign import parse_sign_options, save_and_sign

from pathlib import Path

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

    # NOTE: this test used to reload pdftl.cli.parser, pdftl.cli.main, and
    # pdftl.output.sign here, in the belief that the sign_key/sign_cert
    # options needed a module reload to be "seen" by the parser. That's not
    # how this codebase works: _get_value_keywords()/_get_flag_keywords() in
    # parser.py read registry.options live, on every call, with no caching -
    # there is no module-level VALUE_KEYWORDS constant anywhere to go stale.
    # The sign_key/sign_cert options are registered once at process startup
    # via initialize_registry() and are already visible here with no reload.

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


# --- Tests for parse_sign_options ---


def test_parse_sign_options_missing_args():
    with pytest.raises(UserCommandLineError, match="requires both 'sign_key' and 'sign_cert'"):
        parse_sign_options({"sign_key": "key.pem"}, None)


def test_parse_sign_options_env_passphrase(monkeypatch):
    monkeypatch.setenv("MY_PASS_VAR", "secret123")
    options = {
        "sign_key": "k.pem",
        "sign_cert": "c.pem",
        "sign_pass_env": "MY_PASS_VAR",
    }
    cfg = parse_sign_options(options, None)
    assert cfg["passphrase"] == "secret123"


def test_parse_sign_options_env_missing(monkeypatch):
    options = {
        "sign_key": "k.pem",
        "sign_cert": "c.pem",
        "sign_pass_env": "MISSING_VAR",
    }
    monkeypatch.delenv("MISSING_VAR", raising=False)
    with pytest.raises(UserCommandLineError, match="Environment variable MISSING_VAR not found"):
        parse_sign_options(options, None)


def test_parse_sign_options_prompt():
    mock_context = MagicMock()
    mock_context.get_pass.return_value = "prompt_pass"
    options = {"sign_key": "k.pem", "sign_cert": "c.pem", "sign_pass_prompt": True}
    cfg = parse_sign_options(options, mock_context)
    assert cfg["passphrase"] == "prompt_pass"


# --- Tests for save_and_sign ---


@patch("pyhanko.sign.signers.PdfSigner")
@patch("pyhanko.sign.signers.SimpleSigner.load")
@patch("pyhanko.pdf_utils.incremental_writer.IncrementalPdfFileWriter")
def test_save_and_sign_with_encryption(mock_writer_cls, mock_signer_load, mock_pdf_signer_cls):
    # Use a Mock object that simulates pikepdf.Pdf
    mock_pdf = MagicMock()
    mock_enc = MagicMock()
    mock_enc.user = "userpw"
    mock_enc.owner = "ownerpw"

    save_opts = {"encryption": mock_enc}
    sign_cfg = {
        "key": "key.pem",
        "cert": "cert.pem",
        "passphrase": "pass",
        "field": "CustomSignature",
    }

    mock_writer = MagicMock()
    mock_writer_cls.return_value = mock_writer
    mock_signer = MagicMock()
    mock_signer_load.return_value = mock_signer

    with patch("pdftl.output.sign.open", mock_open()):
        save_and_sign(mock_pdf, sign_cfg, save_opts, "out.pdf")

    # Verify pikepdf save used encryption
    mock_pdf.save.assert_called_once()

    # Verify pyHanko writer encryption
    mock_writer.encrypt.assert_called_with(user_pwd=b"userpw")

    # Verify field name in metadata
    args, kwargs = mock_pdf_signer_cls.call_args
    assert args[0].field_name == "CustomSignature"

    # Verify stamp_style was NoOpStampStyle (appearance suppression)
    from pyhanko.stamp import NoOpStampStyle

    assert isinstance(kwargs["stamp_style"], NoOpStampStyle)

    # Verify signing was invoked exactly once (guards against the
    # duplicate sign_pdf() regression)
    mock_pdf_signer_cls.return_value.sign_pdf.assert_called_once()


@patch("pyhanko.sign.signers.PdfSigner")
@patch("pyhanko.sign.signers.SimpleSigner.load")
@patch("pyhanko.pdf_utils.incremental_writer.IncrementalPdfFileWriter")
def test_save_and_sign_default_field(mock_writer_cls, mock_signer_load, mock_pdf_signer_cls):
    """Tests default signature field name fallback."""
    mock_pdf = MagicMock()

    sign_cfg = {"key": "k.pem", "cert": "c.pem", "passphrase": None, "field": None}

    with patch("pdftl.output.sign.open", mock_open()):
        save_and_sign(mock_pdf, sign_cfg, {}, "out.pdf")

    args, kwargs = mock_pdf_signer_cls.call_args
    # Verify fallback to "Signature1"
    assert args[0].field_name == "Signature1"

    from pyhanko.stamp import NoOpStampStyle

    assert isinstance(kwargs["stamp_style"], NoOpStampStyle)
    mock_pdf_signer_cls.return_value.sign_pdf.assert_called_once()


@patch("pyhanko.sign.signers.SimpleSigner.load")
@patch("pyhanko.sign.signers.PdfSigner")
@patch("pyhanko.pdf_utils.incremental_writer.IncrementalPdfFileWriter")
def test_save_and_sign_full_flow(mock_writer, mock_pdf_signer_cls, mock_load):
    mock_pdf = MagicMock()
    mock_save_opts = {"encryption": None}
    sign_cfg = {"key": "key.pem", "cert": "cert.pem", "passphrase": "password", "field": "MySig"}

    with patch("pdftl.output.sign.open", mock_open()):
        save_and_sign(mock_pdf, sign_cfg, mock_save_opts, "out.pdf")

    mock_load.assert_called_once_with("key.pem", "cert.pem", key_passphrase=b"password")
    mock_pdf_signer_cls.return_value.sign_pdf.assert_called_once()


def test_save_and_sign_encryption_success_path():
    """Hits the /Encrypt-detection debug branch by simulating a buffer that contains /Encrypt."""
    mock_pdf = MagicMock()

    mock_enc = MagicMock()
    mock_enc.user = "user_pw"
    mock_enc.owner = "owner_pw"

    save_opts = {"encryption": mock_enc}
    sign_cfg = {"key": "dummy.pem", "cert": "dummy.pem", "passphrase": "pass", "field": "Sig1"}

    def side_effect_save(buffer, **kwargs):
        buffer.write(b"some pdf data /Encrypt more data")
        return None

    mock_pdf.save.side_effect = side_effect_save

    with (
        patch("pyhanko.pdf_utils.incremental_writer.IncrementalPdfFileWriter"),
        patch("pyhanko.sign.signers.SimpleSigner.load"),
        patch("pyhanko.sign.signers.PdfSigner"),
        patch("pdftl.output.sign.logger") as mock_logger,
        patch("pdftl.output.sign.open", mock_open()),
    ):
        save_and_sign(mock_pdf, sign_cfg, save_opts, "out.pdf")

        mock_logger.debug.assert_any_call("SUCCESS: Buffer is now encrypted.")


def test_sign_load_failure_coverage():
    """Hit sign.py:67 - Failed to load signing key and certificate."""
    from unittest.mock import MagicMock, patch

    from pdftl.exceptions import UserCommandLineError
    from pdftl.output.sign import save_and_sign

    mock_pdf = MagicMock()
    sign_cfg = {"key": "k", "cert": "c", "passphrase": "p", "field": "f"}
    save_opts = {"encryption": None}

    # Intercept pyhanko's load method to return None
    with (
        patch("pyhanko.sign.signers.SimpleSigner.load", return_value=None),
        patch("pyhanko.pdf_utils.incremental_writer.IncrementalPdfFileWriter"),
        patch("pdftl.output.sign.io.BytesIO"),
    ):
        with pytest.raises(UserCommandLineError, match="Failed to load signing key"):
            save_and_sign(mock_pdf, sign_cfg, save_opts, "out.pdf")


def _make_pdf_with_sig_field(tmp_path, *, existing_ap=False, existing_v=False):
    """
    Build a minimal single-page PDF with one /Sig form field named
    'signature', optionally pre-populated with /AP and/or /V, to exercise
    prepare_sig_field()'s real branching logic.
    """
    import pikepdf

    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))

    widget = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Annot"),
                "/Subtype": pikepdf.Name("/Widget"),
                "/FT": pikepdf.Name("/Sig"),
                "/T": pikepdf.String("signature"),
                "/F": 4,  # Print flag
                "/Rect": pikepdf.Array([10, 10, 190, 40]),
                "/P": page.obj,
            }
        )
    )

    if existing_ap:
        ap_stream = pdf.make_stream(b"q 1 0 0 RG 0 0 180 30 re S Q")
        ap_stream.update(
            {
                "/Type": pikepdf.Name("/XObject"),
                "/Subtype": pikepdf.Name("/Form"),
                "/BBox": pikepdf.Array([0, 0, 180, 30]),
            }
        )
        widget.AP = pikepdf.Dictionary({"/N": ap_stream})

    if existing_v:
        # A dummy non-empty /V is enough to make pyhanko's
        # enumerate_fields_in() report the field as already filled -
        # it only checks bool(field.get('/V')), not that /V is a
        # well-formed signature dictionary.
        widget.V = pikepdf.String("already-signed-placeholder")

    page.Annots = pikepdf.Array([widget])
    pdf.Root.AcroForm = pikepdf.Dictionary(
        {
            "/Fields": pikepdf.Array([widget]),
            "/SigFlags": 3,
        }
    )

    pdf_path = tmp_path / "sig_field_fixture.pdf"
    pdf.save(pdf_path)
    return pdf_path


def test_sign_preserves_existing_ap(tmp_path, test_pki):
    """
    Case: field already has a custom /AP (e.g. from stamp_fields) and no /V.
    Signing must succeed, must not overwrite the existing /AP, and must
    populate /V with a valid signature.
    """
    import pikepdf

    key_path, cert_path = test_pki
    input_pdf = _make_pdf_with_sig_field(tmp_path, existing_ap=True, existing_v=False)
    output_pdf = tmp_path / "signed_preserved_ap.pdf"

    test_args = [
        "pdftl",
        str(input_pdf),
        "output",
        str(output_pdf),
        "sign_key",
        str(key_path),
        "sign_cert",
        str(cert_path),
        "sign_field",
        "signature",
    ]

    with patch("sys.argv", test_args):
        main()

    # Confirm /AP content stream is byte-for-byte unchanged
    with pikepdf.open(input_pdf) as before, pikepdf.open(output_pdf) as after:
        before_ap = before.Root.AcroForm.Fields[0].AP.N.read_bytes()
        after_ap = after.Root.AcroForm.Fields[0].AP.N.read_bytes()
        assert before_ap == after_ap, "NoOpStampStyle must not alter an existing /AP"

    # Confirm the signature itself is valid
    with open(output_pdf, "rb") as f:
        reader = PdfFileReader(f)
        assert reader.embedded_signatures, "Expected a signature to be present"
        status = validate_pdf_signature(reader.embedded_signatures[0])
        assert status.intact
        assert status.valid


def test_sign_rejects_already_filled_field(tmp_path, test_pki, capsys):
    """
    Case: field already has a non-empty /V (already signed / filled).
    pyhanko must refuse to re-sign it, and pdftl must surface a clear
    PdftlOutputError via a clean CLI error message and nonzero return code
    (not a raw traceback), and must not leave a partial/truncated file at
    the requested output path.
    """
    key_path, cert_path = test_pki
    input_pdf = _make_pdf_with_sig_field(tmp_path, existing_ap=False, existing_v=True)
    output_pdf = tmp_path / "signed_should_fail.pdf"

    test_args = [
        "pdftl",
        str(input_pdf),
        "output",
        str(output_pdf),
        "sign_key",
        str(key_path),
        "sign_cert",
        str(cert_path),
        "sign_field",
        "signature",
    ]

    with patch("sys.argv", test_args):
        result = main()

    assert result == 1

    captured = capsys.readouterr()
    assert "already" in captured.err.lower() or "filled" in captured.err.lower()

    assert not output_pdf.exists(), (
        "No output file should be left behind on a failed signing attempt"
    )


# --- Additional fixture builders ---


def _make_split_form_pdf(tmp_path, filename="split_form.pdf"):
    """
    Build a PDF where the /Sig field dict and its widget annotation are
    separate objects joined via /Kids and /Parent (the "split form"
    structure LaTeX form generators commonly produce), rather than the
    combined single-dictionary shorthand.
    """
    import pikepdf

    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))

    # Non-terminal field dictionary: no /Rect, no /Subtype - just field
    # properties plus a /Kids array pointing at the widget.
    field = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/FT": pikepdf.Name("/Sig"),
                "/T": pikepdf.String("signature"),
            }
        )
    )

    widget = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Annot"),
                "/Subtype": pikepdf.Name("/Widget"),
                "/F": 4,
                "/Rect": pikepdf.Array([10, 10, 190, 40]),
                "/P": page.obj,
                "/Parent": field,
            }
        )
    )
    field.Kids = pikepdf.Array([widget])

    page.Annots = pikepdf.Array([widget])
    pdf.Root.AcroForm = pikepdf.Dictionary(
        {
            "/Fields": pikepdf.Array([field]),
            "/SigFlags": 3,
        }
    )

    pdf_path = tmp_path / filename
    pdf.save(pdf_path)
    return pdf_path


def _make_pdf_no_acroform(tmp_path, filename="no_acroform.pdf"):
    """A plain PDF with no /AcroForm at all, to exercise the
    "form doesn't exist yet, create it" branch of prepare_sig_field."""
    import pikepdf

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    pdf_path = tmp_path / filename
    pdf.save(pdf_path)
    return pdf_path


def _make_pdf_with_two_sig_fields(tmp_path, filename="two_sig_fields.pdf"):
    """Two distinct, empty /Sig fields on the same page, to confirm
    sign_field=<name> targets the correct one and leaves the other alone."""
    import pikepdf

    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))

    def _make_widget(name, rect):
        return pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Annot"),
                    "/Subtype": pikepdf.Name("/Widget"),
                    "/FT": pikepdf.Name("/Sig"),
                    "/T": pikepdf.String(name),
                    "/F": 4,
                    "/Rect": pikepdf.Array(rect),
                    "/P": page.obj,
                }
            )
        )

    widget_a = _make_widget("signature_a", [10, 10, 90, 40])
    widget_b = _make_widget("signature_b", [110, 10, 190, 40])

    page.Annots = pikepdf.Array([widget_a, widget_b])
    pdf.Root.AcroForm = pikepdf.Dictionary(
        {
            "/Fields": pikepdf.Array([widget_a, widget_b]),
            "/SigFlags": 3,
        }
    )

    pdf_path = tmp_path / filename
    pdf.save(pdf_path)
    return pdf_path


# --- 1. Split-form vs combined-form field structure ---


def test_sign_split_form_field(tmp_path, test_pki):
    """
    A /Sig field whose field dict and widget annotation are split across
    two objects (/Kids + /Parent), as commonly produced by LaTeX form
    generators, must sign successfully just like the combined form.
    """
    key_path, cert_path = test_pki
    input_pdf = _make_split_form_pdf(tmp_path)
    output_pdf = tmp_path / "signed_split_form.pdf"

    test_args = [
        "pdftl",
        str(input_pdf),
        "output",
        str(output_pdf),
        "sign_key",
        str(key_path),
        "sign_cert",
        str(cert_path),
        "sign_field",
        "signature",
    ]

    with patch("sys.argv", test_args):
        result = main()

    assert result == 0

    with open(output_pdf, "rb") as f:
        reader = PdfFileReader(f)
        assert reader.embedded_signatures
        status = validate_pdf_signature(reader.embedded_signatures[0])
        assert status.intact
        assert status.valid


# --- 2. No /AcroForm at all: auto-creation path ---


def test_sign_creates_field_when_no_acroform_present(tmp_path, test_pki):
    """
    Signing a PDF with no /AcroForm and no existing signature field must
    auto-create both the AcroForm and the field, per prepare_sig_field's
    creation branch, and still produce a valid signature.
    """
    key_path, cert_path = test_pki
    input_pdf = _make_pdf_no_acroform(tmp_path)
    output_pdf = tmp_path / "signed_new_field.pdf"

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
        result = main()

    assert result == 0

    with open(output_pdf, "rb") as f:
        reader = PdfFileReader(f)
        assert reader.embedded_signatures
        status = validate_pdf_signature(reader.embedded_signatures[0])
        assert status.intact
        assert status.valid


# --- 3. Multiple signature fields: target the right one by name ---


def test_sign_targets_correct_field_among_multiple(tmp_path, test_pki):
    """
    With two empty /Sig fields present, signing with sign_field=<name>
    must only fill the named field and leave the other one empty.
    """
    import pikepdf

    key_path, cert_path = test_pki
    input_pdf = _make_pdf_with_two_sig_fields(tmp_path)
    output_pdf = tmp_path / "signed_targeted.pdf"

    test_args = [
        "pdftl",
        str(input_pdf),
        "output",
        str(output_pdf),
        "sign_key",
        str(key_path),
        "sign_cert",
        str(cert_path),
        "sign_field",
        "signature_b",
    ]

    with patch("sys.argv", test_args):
        result = main()

    assert result == 0

    with pikepdf.open(output_pdf) as pdf:
        fields = {str(f.get("/T")): f for f in pdf.Root.AcroForm.Fields}
        assert fields["signature_b"].get("/V") is not None, "Targeted field should be filled"
        assert fields["signature_a"].get("/V") is None, "Untargeted field must remain empty"

    with open(output_pdf, "rb") as f:
        reader = PdfFileReader(f)
        assert len(reader.embedded_signatures) == 1
        status = validate_pdf_signature(reader.embedded_signatures[0])
        assert status.intact
        assert status.valid


# --- 4. Bad passphrase for an encrypted signing key ---


@pytest.fixture
def test_pki_encrypted_key(tmp_path):
    """Same as test_pki, but the private key is password-protected."""
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PDTFL Test")])
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

    key_path = tmp_path / "encrypted_key.pem"
    cert_path = tmp_path / "encrypted_cert.pem"

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.BestAvailableEncryption(b"correct-horse"),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    return key_path, cert_path


def test_sign_wrong_passphrase_raises_clean_error(tmp_path, test_pki_encrypted_key, capsys):
    """
    Supplying the wrong passphrase for an encrypted private key must
    surface a clean, user-facing error rather than an unhandled
    cryptography-library exception or a silent empty output file.
    """
    key_path, cert_path = test_pki_encrypted_key
    input_pdf = _make_pdf_with_sig_field(tmp_path, existing_ap=False, existing_v=False)
    output_pdf = tmp_path / "signed_wrong_pass.pdf"

    test_args = [
        "pdftl",
        str(input_pdf),
        "output",
        str(output_pdf),
        "sign_key",
        str(key_path),
        "sign_cert",
        str(cert_path),
        "sign_pass_env",
        "PDFTL_TEST_WRONG_PASS",
    ]

    with (
        patch.dict("os.environ", {"PDFTL_TEST_WRONG_PASS": "definitely-not-it"}),
        patch("sys.argv", test_args),
    ):
        result = main()

    assert result != 0
    assert not output_pdf.exists()

    captured = capsys.readouterr()
    assert captured.err.strip(), "Expected a user-facing error message on stderr"
    # NOTE: at time of writing this asserts only that *some* clean error
    # surfaced, not a specific message, since the exact exception type
    # raised by cryptography/pyhanko for a bad passphrase was not
    # independently confirmed. Tighten this once that's verified - see
    # inline comment on save_and_sign's cms_signer loading step.


# --- 5. Encrypted output + pre-existing /AP ---


def test_sign_encrypted_output_with_existing_ap(tmp_path, test_pki):
    """
    Signing a field that already has a custom /AP, while also requesting
    AES encryption on the output, must succeed: the appearance must survive
    unchanged, and the resulting encrypted+signed file must still validate.
    """
    import pikepdf

    key_path, cert_path = test_pki
    input_pdf = _make_pdf_with_sig_field(tmp_path, existing_ap=True, existing_v=False)
    output_pdf = tmp_path / "signed_encrypted_preserved_ap.pdf"

    test_args = [
        "pdftl",
        str(input_pdf),
        "output",
        str(output_pdf),
        "sign_key",
        str(key_path),
        "sign_cert",
        str(cert_path),
        "sign_field",
        "signature",
        "encrypt_aes256",
        "owner_pw",
        "ownersecret",
        "user_pw",
        "usersecret",
    ]

    with patch("sys.argv", test_args):
        result = main()

    assert result == 0

    # Confirm /AP content stream survived, reading through the user password
    with (
        pikepdf.open(input_pdf) as before,
        pikepdf.open(output_pdf, password="usersecret") as after,
    ):
        before_ap = before.Root.AcroForm.Fields[0].AP.N.read_bytes()
        after_ap = after.Root.AcroForm.Fields[0].AP.N.read_bytes()
        assert before_ap == after_ap, "Encryption + signing must not alter existing /AP"
        assert after.Root.AcroForm.Fields[0].get("/V") is not None

    # Confirm the signature validates once decrypted
    with open(output_pdf, "rb") as f:
        reader = PdfFileReader(f)
        reader.decrypt("usersecret")
        assert reader.embedded_signatures, "Expected a signature to be present"
        status = validate_pdf_signature(reader.embedded_signatures[0])
        assert status.intact
        assert status.valid


# --- 6. Full stamp_fields -> sign pipeline (regression test for the
#          original /V-vs-/AP bug and the duplicate sign_pdf() bug) ---


def _make_stamp_source_pdf(tmp_path, filename="stamp_source.pdf"):
    """A trivial single-page PDF to use as the stamp_fields source."""
    import pikepdf

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(100, 50))
    pdf_path = tmp_path / filename
    pdf.save(pdf_path)
    return pdf_path


def test_stamp_then_sign_pipeline(tmp_path, test_pki):
    """
    End-to-end regression test for the original bug report: stamp_fields
    writes a custom /AP into the 'signature' field, and sign_field then
    signs that same field in one pdftl invocation. Must succeed, must
    preserve the stamped appearance, and must produce a valid signature.
    """
    import pikepdf

    key_path, cert_path = test_pki
    input_pdf = _make_pdf_with_sig_field(tmp_path, existing_ap=False, existing_v=False)
    stamp_source = _make_stamp_source_pdf(tmp_path)
    output_pdf = tmp_path / "stamped_and_signed.pdf"

    test_args = [
        "pdftl",
        str(input_pdf),
        "stamp_fields",
        str(stamp_source),
        "signature",
        "output",
        str(output_pdf),
        "sign_key",
        str(key_path),
        "sign_cert",
        str(cert_path),
        "sign_field",
        "signature",
    ]

    with patch("sys.argv", test_args):
        result = main()

    assert result == 0

    with pikepdf.open(output_pdf) as pdf:
        field = pdf.Root.AcroForm.Fields[0]
        assert field.get("/V") is not None, "Field must be signed"
        assert "/AP" in field, "Stamped appearance must survive signing"
        assert "/N" in field.AP, "Stamped /AP must have an /N appearance stream"

    with open(output_pdf, "rb") as f:
        reader = PdfFileReader(f)
        assert reader.embedded_signatures
        status = validate_pdf_signature(reader.embedded_signatures[0])
        assert status.intact
        assert status.valid


@pytest.mark.xfail
def test_sign_second_field_preserves_first_signature(tmp_path, test_pki):
    """
    Signing a second, distinct field in a document that already has one
    valid signature must succeed, and the first signature must remain
    valid afterward (i.e. the second incremental update doesn't corrupt
    or invalidate the first signature's coverage).
    """
    key_path, cert_path = test_pki
    input_pdf = _make_pdf_with_two_sig_fields(tmp_path)
    once_signed_pdf = tmp_path / "signed_once.pdf"
    twice_signed_pdf = tmp_path / "signed_twice.pdf"

    # First signature: sign signature_a
    first_args = [
        "pdftl",
        str(input_pdf),
        "output",
        str(once_signed_pdf),
        "sign_key",
        str(key_path),
        "sign_cert",
        str(cert_path),
        "sign_field",
        "signature_a",
    ]
    with patch("sys.argv", first_args):
        assert main() == 0

    # Second signature: sign signature_b on the already-once-signed file
    second_args = [
        "pdftl",
        str(once_signed_pdf),
        "output",
        str(twice_signed_pdf),
        "sign_key",
        str(key_path),
        "sign_cert",
        str(cert_path),
        "sign_field",
        "signature_b",
    ]
    with patch("sys.argv", second_args):
        assert main() == 0

    with open(twice_signed_pdf, "rb") as f:
        reader = PdfFileReader(f)
        assert len(reader.embedded_signatures) == 2, "Expected both signatures to be present"

        for sig in reader.embedded_signatures:
            status = validate_pdf_signature(sig)
            assert status.intact, f"Signature on field {sig.field_name} failed integrity check"
            assert status.valid, f"Signature on field {sig.field_name} failed validation"


def test_safe_sign_pdf_active_event_loop():
    """Test invoking _safe_sign_pdf inside an active asyncio event loop."""
    import asyncio
    from unittest.mock import MagicMock
    from pdftl.output.sign import _safe_sign_pdf

    mock_signer = MagicMock()
    mock_signer.sign_pdf.return_value = "signed_result"
    mock_writer = MagicMock()
    mock_output = MagicMock()

    async def _async_runner():
        return _safe_sign_pdf(mock_signer, mock_writer, mock_output)

    result = asyncio.run(_async_runner())

    assert result == "signed_result"
    mock_signer.sign_pdf.assert_called_once_with(mock_writer, output=mock_output)
