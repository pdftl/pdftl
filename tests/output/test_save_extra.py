from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.exceptions import InvalidArgumentError, MissingArgumentError
from pdftl.output.save import (
    _action_drop_flags,
    _build_encryption_object,
    _build_permissions_object,
    _build_save_options,
    _get_passwords_from_options,
    save_content,
    save_pdf,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_input_context():
    ctx = MagicMock()
    ctx.get_pass.return_value = "secret"
    return ctx


@pytest.fixture
def mock_pdf():
    pdf = MagicMock(spec=pikepdf.Pdf)
    pdf.pages = []
    # Note: We don't set pdf.Root here; individual tests will configure it
    # to be a Mock or a real Dictionary as needed.
    pdf.docinfo = MagicMock()
    return pdf


# ---------------------------------------------------------------------------
# Test: Encryption & Permissions
# ---------------------------------------------------------------------------


def test_get_passwords_simple(mock_input_context):
    options = {"user_pw": "u123", "owner_pw": "o123"}
    pws = _get_passwords_from_options(options, mock_input_context)
    assert pws == {"user": "u123", "owner": "o123"}


def test_get_passwords_prompt(mock_input_context):
    """Test interactive password prompt."""
    options = {"user_pw": "PROMPT"}
    pws = _get_passwords_from_options(options, mock_input_context)

    assert pws["user"] == "secret"
    mock_input_context.get_pass.assert_called_once()


def test_get_passwords_truncate(mock_input_context, capsys):
    """Test truncation of long passwords."""
    long_pw = "a" * 40
    mock_input_context.get_pass.return_value = long_pw
    options = {"owner_pw": "PROMPT"}

    pws = _get_passwords_from_options(options, mock_input_context)
    assert len(pws["owner"]) == 32
    assert "Warning: Password was over 32 characters" in capsys.readouterr().out


def test_build_encryption_conflict(mock_input_context):
    """Test error when multiple encryption types are selected."""
    options = {"encrypt_aes256": True, "encrypt_128bit": True}
    with pytest.raises(InvalidArgumentError, match="Too many encryption options"):
        _build_encryption_object(options, mock_input_context)


def test_build_encryption_aes256(mock_input_context):
    options = {"encrypt_aes256": True, "user_pw": "foo"}
    enc = _build_encryption_object(options, mock_input_context)
    assert isinstance(enc, pikepdf.Encryption)
    # R=6 implies AES256 in PDF spec/pikepdf
    assert enc.R == 6


def test_build_permissions_invalid():
    with pytest.raises(ValueError, match="Unknown permission"):
        _build_permissions_object(["MakeCoffee"])


def test_build_permissions_default():
    """Default should be 'all denied' except assembly if empty list passed explicitly to helper."""
    # Case 1: No restrictions requested (None or empty) -> All Allowed
    perms = _build_permissions_object([])
    # Note: We rely on pikepdf behavior not raising exceptions here.
    assert perms is not None


def test_build_save_options_structure(mock_input_context):
    """Ensure dictionary for pikepdf.save is correct."""
    options = {"linearize": True, "uncompress": True}
    save_opts = _build_save_options(options, mock_input_context)

    assert save_opts["linearize"] is True
    assert save_opts["compress_streams"] is False
    assert save_opts["encryption"] is False


# ---------------------------------------------------------------------------
# Test: Save Content Routing
# ---------------------------------------------------------------------------


def test_save_content_generator(mock_input_context):
    """Test saving a generator of items (e.g., from burst)."""

    # Mock items
    pdf_item = MagicMock(spec=pikepdf.Pdf)

    # Generator yielding (filename, item)
    def content_gen():
        yield "page1.pdf", pdf_item

    with patch("pdftl.output.save.save_pdf") as mock_save_pdf:
        save_content(content_gen(), "ignored_template", mock_input_context)

        mock_save_pdf.assert_called_once()
        # Verify cleanup called (close)
        pdf_item.close.assert_called_once()


def test_save_content_pil_image(mock_input_context):
    """
    Covers lines 348-350.
    Test routing to PIL image save.
    """

    class PILImageFake:
        def save(self, path):
            pass

    fake_img = PILImageFake()
    fake_img.save = MagicMock()

    save_content(fake_img, "out.jpg", mock_input_context)

    fake_img.save.assert_called_with("out.jpg")


def test_save_content_unknown_type(mock_input_context):
    """
    Covers lines 352-356.
    Test TypeError for unsupported objects.
    """
    bad_obj = {"i am": "a dict"}  # Not a PDF, not an Image

    with pytest.raises(TypeError, match="Unknown content object type"):
        save_content(bad_obj, "out.txt", mock_input_context)


# ---------------------------------------------------------------------------
# Test: PDF Logic & Flags
# ---------------------------------------------------------------------------


def test_action_drop_flags(mock_pdf):
    """Test stripping metadata."""
    # Setup XFA structure in a real Dictionary to ensure 'in' operators work
    acro_form = pikepdf.Dictionary()
    acro_form["/XFA"] = "some xfa data"

    # Use a real pikepdf.Dictionary for Root
    # This ensures `"/AcroForm" in pdf.Root` returns True
    root_dict = pikepdf.Dictionary({"/AcroForm": acro_form, "/Metadata": pikepdf.Dictionary()})

    mock_pdf.Root = root_dict

    options = {"drop_info": True, "drop_xmp": True, "drop_xfa": True}

    _action_drop_flags(mock_pdf, options)

    # Verify deletions
    # 1. XFA: Should be removed from the dictionary
    assert "/XFA" not in acro_form

    # 2. Metadata: Should be removed (via del pdf.Root.Metadata)
    assert "/Metadata" not in root_dict


def test_save_pdf_flatten_and_attach(mock_pdf, mock_input_context):
    """Test high-level save logic calls flatten and attach."""
    options = {"flatten": True, "output": "out.pdf"}
    mock_pdf.Root = MagicMock()  # Ensure Root exists

    with (
        patch("pdftl.output.save.flatten_pdf") as mock_flat,
        patch("pdftl.output.save.attach_files") as mock_attach,
        patch("pdftl.output.save._build_save_options", return_value={}),
    ):

        mock_flat.return_value = mock_pdf  # Return the pdf object

        save_pdf(mock_pdf, "out.pdf", mock_input_context, options)

        mock_flat.assert_called_once()
        mock_attach.assert_called_once()
        mock_pdf.save.assert_called_with("out.pdf")


def test_save_pdf_need_appearances(mock_pdf, mock_input_context, caplog):
    """Test setting NeedAppearances flag."""
    options = {"need_appearances": True, "output": "out.pdf"}

    # Setup AcroForm so it can be accessed
    mock_pdf.Root = MagicMock()
    mock_pdf.Root.AcroForm = MagicMock()

    save_pdf(mock_pdf, "out.pdf", mock_input_context, options)

    # Check if we set the item
    # Since AcroForm is a Mock, __setitem__ is called
    mock_pdf.Root.AcroForm.__setitem__.assert_called()


def test_save_pdf_missing_arg(mock_pdf, mock_input_context):
    with pytest.raises(MissingArgumentError):
        save_pdf(mock_pdf, None, mock_input_context)


def test_save_pdf_signing(mock_pdf, mock_input_context):
    """Test routing to save_and_sign."""
    options = {"sign_field": "Sig1", "output": "out.pdf"}
    mock_pdf.Root = MagicMock()

    with (
        patch("pdftl.output.save.parse_sign_options") as mock_parse,
        patch("pdftl.output.save.save_and_sign") as mock_sign,
    ):

        save_pdf(mock_pdf, "out.pdf", mock_input_context, options)

        mock_parse.assert_called_once()
        mock_sign.assert_called_once()
        # Should NOT call pdf.save directly
        mock_pdf.save.assert_not_called()


def test_cleanup_item_pikepdf():
    """Test _cleanup_item calls close on Pdf objects."""
    pdf = MagicMock(spec=pikepdf.Pdf)

    from pdftl.output.save import _cleanup_item

    _cleanup_item(pdf)
    pdf.close.assert_called_once()
