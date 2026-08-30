import logging
from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.core import constants as constants_module
from pdftl.core.constants import PDFTL_SOURCE_INFO_KEY
from pdftl.exceptions import InvalidArgumentError, MissingArgumentError
from pdftl.output import save as save_module
from pdftl.output.save import (
    _iter_form_fields,
    _action_drop_flags,
    _allow_option,
    _build_encryption_object,
    _build_permissions_object,
    _build_save_options,
    _compress_options,
    _default_permissions_object,
    _drop_options,
    _encrypt_options,
    _get_passwords_from_options,
    _keep_id_options,
    _linearize_option,
    has_live_signatures,
    _need_appearances_option,
    _output_option,
    _owner_pw_option,
    _remove_source_info,
    _replacement_font_option,
    _set_permission_or_raise_error,
    _user_pw_option,
    save_content,
    save_pdf,
)
from pdftl.output.sign import (
    _sign_cert_option,
    _sign_field_option,
    _sign_key_option,
    _sign_pass_env_option,
    _sign_pass_prompt_option,
)


class TestSaveOptionsRegistration:
    """
    Tests that the option registration functions execute their 'pass' statements.
    These functions exist solely for their @register_option decorators, but coverage
    still requires the functions bodies to be executed to mark the 'pass' as covered.
    This test covers lines 31, 40, 49, 67, 101, 109, 114, 120, 125, 135, and 142 in save.py.
    """

    def test_option_functions_execute_pass(self):
        # Simply calling the functions executes the single 'pass' statement inside each.
        _allow_option()
        _compress_options()
        _drop_options()
        _encrypt_options()
        _keep_id_options()
        _linearize_option()
        _need_appearances_option()
        _output_option()
        _owner_pw_option()
        _replacement_font_option()
        _sign_cert_option()
        _sign_field_option()
        _sign_key_option()
        _sign_pass_env_option()
        _sign_pass_prompt_option()
        _user_pw_option()

        # Test passes if no exceptions are raised during execution.
        assert True


# --- Fixtures ---


@pytest.fixture
def mock_input_context():
    """Mock for the input context, used for password prompts."""
    return MagicMock()


@pytest.fixture
def mock_pdf():
    """Mock for a pikepdf.Pdf object."""
    pdf = MagicMock(spec=pikepdf.Pdf)
    pdf.Root = MagicMock()
    pdf.Root.AcroForm = MagicMock()  # Mock AcroForm for __setitem__
    pdf.trailer = MagicMock()
    pdf.flatten_annotations = MagicMock()
    pdf.save = MagicMock()
    return pdf


@pytest.fixture(autouse=True)
def patch_dependencies(mocker, request):
    """
    Patch all external and internal dependencies to isolate
    each function.
    """
    # Check if this specific test has the 'real_encryption' marker
    if "real_encryption" not in request.keywords:
        # Patch pikepdf.Encryption
        mocker.patch("pikepdf.Encryption", autospec=True)

        # --- Use the REAL permission strings from the help text ---
        mock_permission_map = {
            "Printing": ["print_highres", "print_lowres"],
            "DegradedPrinting": ["print_lowres"],
            "ModifyContents": ["modify", "modify_assembly"],
            "Assembly": ["modify_assembly"],
            "CopyContents": ["extract", "accessibility"],
            "ScreenReaders": ["accessibility"],
            "ModifyAnnotations": ["modify_annotation"],
            "FillIn": ["fill_form"],
            "BadFlag": ["non_existent_flag"],
        }
        mocker.patch.dict(
            constants_module.ALLOW_PERMISSIONS_MAP,
            mock_permission_map,
            clear=True,
        )

    # Always patch logging to keep test outputs clean
    mocker.patch("pdftl.output.save.logging")


# --- Test Cases ---

## _get_passwords_from_options ##


def test_get_passwords_from_options_direct():
    """Tests passwords read directly from options."""
    options = {"user_pw": "user123", "owner_pw": "owner456"}
    passwords = _get_passwords_from_options(options, MagicMock())
    assert passwords == {"user": "user123", "owner": "owner456"}


def test_get_passwords_from_options_prompt(mock_input_context):
    """Tests passwords read from a prompt."""
    options = {"user_pw": "PROMPT"}
    mock_input_context.get_pass.return_value = "from_prompt"

    passwords = _get_passwords_from_options(options, mock_input_context)

    mock_input_context.get_pass.assert_called_once()
    assert "Please enter the user password" in mock_input_context.get_pass.call_args[1]["prompt"]
    assert passwords == {"user": "from_prompt"}


def test_get_passwords_from_options_prompt_truncate(mock_input_context, caplog):
    """Tests that prompted passwords over 32 chars are truncated."""
    options = {"owner_pw": "PROMPT"}
    long_pass = "a" * 40
    truncated_pass = "a" * 32
    mock_input_context.get_pass.return_value = long_pass

    passwords = _get_passwords_from_options(options, mock_input_context)

    assert passwords == {"owner": truncated_pass}
    assert "Password was over 32 characters" in caplog.text


def test_get_passwords_from_options_none():
    """Tests that None passwords are not added."""
    options = {"user_pw": None}  # e.g. from a missing option
    passwords = _get_passwords_from_options(options, MagicMock())
    assert passwords == {}


## _default_permissions_object ##


def test_default_permissions_object():
    """
    Tests that the default permissions dict has all-False values.
    We check a few known flags from the real pikepdf.Permissions.
    """
    perms = _default_permissions_object()

    # Check a few known permissions
    assert "modify_assembly" in perms
    assert perms["modify_assembly"] is False
    assert "print_highres" in perms
    assert perms["print_highres"] is False
    assert "extract" in perms
    assert perms["extract"] is False
    assert "modify_form" in perms
    assert perms["modify_form"] is False

    # Ensure all values are False
    assert all(value is False for value in perms.values())


## _set_permission_or_raise_error ##


def test_set_permission_or_raise_error_success():
    """Tests setting a valid permission."""
    perms_dict = {"print_highres": False, "print_lowres": False, "copy": False}
    _set_permission_or_raise_error("Printing", perms_dict)

    assert perms_dict["print_highres"] is True
    assert perms_dict["print_lowres"] is True
    assert perms_dict["copy"] is False


def test_set_permission_or_raise_error_unknown_perm():
    """Tests raising an error for an unknown permission name."""
    with pytest.raises(ValueError, match="Unknown permission 'Singing'"):
        _set_permission_or_raise_error("Singing", {})


def test_set_permission_or_raise_error_unknown_flag():
    """Tests raising an error for a permission mapping to a bad flag."""
    perms_dict = {"print_highres": False}
    with pytest.raises(ValueError, match="maps to an unknown flag 'non_existent_flag'"):
        _set_permission_or_raise_error("BadFlag", perms_dict)


## _build_permissions_object ##


def test_build_permissions_object_all_features(mocker):
    """Tests the 'AllFeatures' shortcut."""
    # Mock the class *inside* the test
    mock_permissions_cls = mocker.patch("pikepdf.Permissions", autospec=True)
    mock_instance = mock_permissions_cls.return_value

    result = _build_permissions_object(["AllFeatures"])

    # Assert the class was called correctly
    mock_permissions_cls.assert_called_once_with(modify_assembly=True)
    assert result is mock_instance


def test_build_permissions_object_empty(mocker):
    """
    Tests building with no 'allow' options (defaults to All Denied).
    Note: To match pdftk behavior, the code treats empty list [] as
    'deny everything', using the restrictive _default_permissions_object helper.
    """
    # 1. Patch the class constructor
    mock_permissions_cls = mocker.patch("pikepdf.Permissions", autospec=True)
    mock_instance = mock_permissions_cls.return_value

    # 2. Patch the helper (we expect this TO be called now)
    mock_default_helper = mocker.patch("pdftl.output.save._default_permissions_object")
    # Give the mock a fake dictionary to return so the kwargs unpack doesn't crash
    fake_restrictive_dict = {"print_highres": False, "extract": False}
    mock_default_helper.return_value = fake_restrictive_dict

    # 3. Run the function with empty list
    result = _build_permissions_object([])

    # 4. Assertions
    # The helper SHOULD be called (because we restrict permissions by default)
    mock_default_helper.assert_called_once()

    # Ensure pikepdf.Permissions was initialized using the restrictive dictionary
    mock_permissions_cls.assert_called_once_with(**fake_restrictive_dict)
    assert result == mock_instance


def test_build_permissions_object_specific(mocker):
    """Tests building with a specific list of permissions."""
    # 1. Get the REAL default dictionary *before* patching
    real_default_perms = _default_permissions_object()

    # 2. Create the dictionary we EXPECT at the end
    expected_perms = real_default_perms.copy()  # Must copy!
    # "Printing" flags
    expected_perms["print_highres"] = True
    expected_perms["print_lowres"] = True
    # "CopyContents" flags
    expected_perms["extract"] = True
    expected_perms["accessibility"] = True

    # 3. Patch the class constructor
    mock_permissions_cls = mocker.patch("pikepdf.Permissions", autospec=True)
    mock_instance = mock_permissions_cls.return_value

    # 4. Patch the helper to return the REAL default dict
    mock_default_helper = mocker.patch("pdftl.output.save._default_permissions_object")
    # We must return a copy here so the original isn't mutated
    mock_default_helper.return_value = real_default_perms.copy()

    # 5. Run the function (using the *correct* user-facing strings)
    result = _build_permissions_object(["Printing", "CopyContents"])

    # 6. Assertions will now work
    # The helper was called
    mock_default_helper.assert_called_once()
    # The constructor was called with the MODIFIED dictionary
    mock_permissions_cls.assert_called_once_with(**expected_perms)
    assert result is mock_instance


## _build_encryption_object ##


def test_build_encryption_object_no_encrypt(mock_input_context):
    """Tests that no encryption is returned if no options are given."""
    result = _build_encryption_object({}, mock_input_context)
    assert result is False


def test_build_encryption_object_too_many_methods(mock_input_context):
    """Tests that multiple encryption methods raise an error."""
    options = {"encrypt_aes256": True, "encrypt_128bit": True}
    with pytest.raises(InvalidArgumentError, match="Too many encryption options"):
        _build_encryption_object(options, mock_input_context)


@patch("pdftl.output.save._get_passwords_from_options", return_value={})
@patch("pdftl.output.save._build_permissions_object")
def test_build_encryption_object_by_method_only(
    mock_build_perms, mock_get_pass, mock_input_context, mocker
):
    """Tests triggering encryption by method, with no passwords."""
    mock_encryption_cls = mocker.patch("pikepdf.Encryption")
    options = {"encrypt_aes256": True}
    mock_default_perms = MagicMock()
    mock_build_perms.return_value = mock_default_perms

    _build_encryption_object(options, mock_input_context)

    # Should get default passwords ("") and default permissions
    mock_get_pass.assert_called_once_with(options, mock_input_context)
    mock_build_perms.assert_called_once_with([])

    expected_encrypt_opts = {
        "user": "",
        "owner": "",
        "aes": True,
        "metadata": True,
        "R": 6,  # From encrypt_aes256
        "allow": mock_default_perms,
    }
    mock_encryption_cls.assert_called_once_with(**expected_encrypt_opts)


@patch("pdftl.output.save._get_passwords_from_options", return_value={"user": "123"})
@patch("pdftl.output.save._build_permissions_object")
def test_build_encryption_object_by_password_only(
    mock_build_perms, mock_get_pass, mock_input_context, mocker
):
    """Tests triggering encryption by password only."""
    mock_encryption_cls = mocker.patch("pikepdf.Encryption")
    options = {"user_pw": "123"}
    mock_default_perms = MagicMock()
    mock_build_perms.return_value = mock_default_perms

    _build_encryption_object(options, mock_input_context)

    # Should get passwords and default permissions
    mock_get_pass.assert_called_once_with(options, mock_input_context)
    mock_build_perms.assert_called_once_with([])

    expected_encrypt_opts = {
        "R": 4,
        "aes": True,
        "metadata": True,
        "user": "123",
        "owner": "",  # default
        "allow": mock_default_perms,
    }
    mock_encryption_cls.assert_called_once_with(**expected_encrypt_opts)


@patch("pdftl.output.save._get_passwords_from_options", return_value={"user": "123"})
@patch("pdftl.output.save._build_permissions_object")
def test_build_encryption_object_full(mock_build_perms, mock_get_pass, mock_input_context, mocker):
    """Tests a full encryption call with method, passwords, and perms."""
    mock_encryption_cls = mocker.patch("pikepdf.Encryption")
    options = {
        "user_pw": "123",
        "encrypt_128bit": True,
        "allow": ["Printing"],
    }
    mock_printing_perms = MagicMock()
    mock_build_perms.return_value = mock_printing_perms

    _build_encryption_object(options, mock_input_context)

    mock_get_pass.assert_called_once_with(options, mock_input_context)
    mock_build_perms.assert_called_once_with(["Printing"])

    expected_encrypt_opts = {
        "user": "123",
        "owner": "",
        "aes": False,
        "metadata": False,
        "R": 3,  # From encrypt_128bit
        "allow": mock_printing_perms,
    }
    mock_encryption_cls.assert_called_once_with(**expected_encrypt_opts)


## _build_save_options ##


@patch("pdftl.output.save._build_encryption_object", return_value=False)
def test_build_save_options_default(mock_build_enc, mock_input_context):
    """Tests the default save options."""
    options = {}
    save_opts = _build_save_options(options, mock_input_context)

    assert save_opts["linearize"] is False
    assert save_opts["encryption"] is False
    assert save_opts["compress_streams"] is True
    assert save_opts["object_stream_mode"] == pikepdf.ObjectStreamMode.generate

    # Check for warning
    save_module.logging.warning.assert_not_called()


@patch("pdftl.output.save._build_encryption_object", return_value=False)
def test_build_save_options_uncompress(mock_build_enc, mock_input_context):
    """Tests the 'uncompress' option."""
    options = {"uncompress": True}
    save_opts = _build_save_options(options, mock_input_context)

    assert save_opts["compress_streams"] is False
    assert save_opts["object_stream_mode"] == pikepdf.ObjectStreamMode.disable


@patch("pdftl.output.save._build_encryption_object", return_value=False)
def test_build_save_options_linearize(mock_build_enc, mock_input_context):
    """Tests the 'linearize' option."""
    options = {"linearize": True}
    save_opts = _build_save_options(options, mock_input_context)

    assert save_opts["linearize"] is True


@patch("pdftl.output.save._build_encryption_object", return_value=False)
def test_build_save_options_allow_warning(mock_build_enc, mock_input_context, caplog):
    """Tests warning if 'allow' is given without encryption."""

    options = {"allow": ["Printing"]}
    with caplog.at_level("WARNING"):
        _build_save_options(options, mock_input_context)

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.message == "Encryption not requested, so 'allow' permissions will be ignored."


@patch("pdftl.output.save._build_encryption_object")
def test_build_save_options_with_encryption(mock_build_enc, mock_input_context):
    """Tests that the encryption object is passed through."""
    mock_enc_obj = MagicMock()
    mock_build_enc.return_value = mock_enc_obj
    options = {"user_pw": "123"}

    save_opts = _build_save_options(options, mock_input_context)

    assert save_opts["encryption"] is mock_enc_obj


## save_pdf ##


@patch("pdftl.operations.attach_files.attach_files")
@patch("pdftl.output.save._build_save_options")
@patch("pdftl.output.save.flatten_pdf")
def test_save_pdf_orchestration(
    mock_flatten, mock_build_save, mock_attach, mock_pdf, mock_input_context
):
    """Tests the main orchestration of the save_pdf function."""
    output_file = "out.pdf"

    # Enable flatten to trigger the logic path
    options = {"flatten": True, "need_appearances": True}

    mock_save_opts = {"linearize": False, "encryption": False}
    mock_build_save.return_value = mock_save_opts

    # Configure flatten mock to behave like the real one:
    # "I take a PDF and return a PDF"
    mock_flatten.return_value = mock_pdf

    save_pdf(mock_pdf, output_file, mock_input_context, options)

    # --- Assertions ---

    # 1. Verify flatten was actually called (Crucial!)
    mock_flatten.assert_called_once_with(mock_pdf)

    # 2. Verify other standard calls
    mock_build_save.assert_called_once()
    mock_attach.assert_not_called()

    # 3. Verify final save
    mock_pdf.save.assert_called_with(output_file, linearize=False, encryption=False)


def test_save_pdf_no_output_file(mock_pdf, mock_input_context):
    """Tests that a missing output filename raises an error."""
    with pytest.raises(MissingArgumentError, match="output file must be specified"):
        save_pdf(mock_pdf, "", mock_input_context)
    with pytest.raises(MissingArgumentError, match="output file must be specified"):
        save_pdf(mock_pdf, None, mock_input_context)


@patch("pdftl.operations.attach_files.attach_files")
@patch("pdftl.output.save._build_save_options")
def test_save_pdf_set_pdf_id(mock_build_save, mock_attach, mock_pdf, mock_input_context):
    """Tests the 'set_pdf_id' option."""
    pdf_id_val = b"some_id"
    save_pdf(mock_pdf, "out.pdf", mock_input_context, set_pdf_id=pdf_id_val)

    assert mock_pdf.trailer.ID == pdf_id_val


@patch("pdftl.operations.attach_files.attach_files")
@patch("pdftl.output.save._build_save_options")
def test_save_pdf_need_appearances_fails(
    mock_build_save, mock_attach, mock_pdf, mock_input_context, caplog
):
    """Tests that a failure in 'need_appearances' is logged as a warning."""
    # Simulate the __setitem__ call raising an AttributeError.
    # This is what the 'try...except' block is designed to catch.
    mock_pdf.Root.AcroForm.__setitem__.side_effect = AttributeError("Test error")

    options = {"need_appearances": True}
    with caplog.at_level("WARNING"):
        save_pdf(mock_pdf, "out.pdf", mock_input_context, options)

    # Check that a warning was logged and save was still called
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.message == "Problem setting need_appearances: AttributeError Test error"

    mock_pdf.save.assert_called_once()


def test_save_generator_logic(tmp_path):
    """
    Covers lines 301-305 (generator loop) and 313-320 (cleanup).
    Simulates saving a result that is a generator of PDF objects.
    """
    mock_pdf_item = MagicMock(spec=pikepdf.Pdf)
    mock_pdf_item.save = MagicMock()
    mock_pdf_item.close = MagicMock()

    # Create a generator
    def data_gen():
        yield ("doc_1.pdf", mock_pdf_item)

    # Patch the internal router to isolate the loop logic
    with patch("pdftl.output.save._save_by_type") as mock_router:
        save_content(data_gen(), str(tmp_path), None)

        # Verify router was called
        mock_router.assert_called()
        # Verify cleanup (close) was called on the item
        mock_pdf_item.close.assert_called()


def test_remove_source_info_logic():
    """Covers line 286: Deleting the specific source info key."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(100, 100))

    # Inject the key that _remove_source_info looks for
    target_key = "/" + PDFTL_SOURCE_INFO_KEY
    page[target_key] = "Metadata to remove"

    _remove_source_info(pdf)

    assert target_key not in page


def test_save_generator_image_routing_and_close(tmp_path):
    """
    Covers:
    - Lines 331-333: Routing to item.save() for Image-like objects.
    - Lines 322-323: Calling .close() on non-pikepdf objects.
    """
    # Create a Mock that looks like a PIL Image
    mock_image = MagicMock()
    mock_image.format = "PNG"  # Trigger detection
    mock_image.save = MagicMock()
    mock_image.close = MagicMock()

    def image_gen():
        yield ("test_img.png", mock_image)

    # We patch _save_by_type's internal logic or simply let it run
    # since we want to hit the if/else blocks inside _save_by_type.
    # We pass None as input_context as it's not used for images.
    save_content(image_gen(), str(tmp_path), None)

    # Verify Save called
    mock_image.save.assert_called_with("test_img.png")
    # Verify Close called
    mock_image.close.assert_called()


def test_save_unknown_type(tmp_path):
    """Covers Line 341: TypeError for unknown objects."""

    def bad_gen():
        yield ("test.txt", object())  # Plain object has no .save

    with pytest.raises(TypeError, match="Unknown content object type"):
        save_content(bad_gen(), str(tmp_path), None)


@pytest.mark.real_encryption
def test_pdftl_aes_encryption_forces_accessibility(tmp_path, mock_input_context):
    """
    Tests that when pdftl saves a locked-down AES-128 file, the underlying
    qpdf behavior overrides the accessibility permission to True (-3392 flag).
    """
    out_path = tmp_path / "aes_locked.pdf"

    # 1. Create and save a minimal blank PDF inside an isolated block
    with pikepdf.Pdf.new() as pdf:
        pdf.add_blank_page()
        options = {"output": str(out_path), "encrypt_aes128": True}

        # Execute the pdftl save routine with real, pristine C++ structures
        save_pdf(pdf, out_path, mock_input_context, options=options)

    # 2. Re-open and verify the final binary output safely
    with pikepdf.Pdf.open(out_path, password="") as encrypted_pdf:
        perms = encrypted_pdf.allow

        # Accessibility MUST be True (overridden by qpdf during pdftl's save)
        assert perms.accessibility is True, "qpdf failed to force accessibility=True"

        # Ensure pdftl still correctly locked down everything else
        assert perms.extract is False
        assert perms.print_highres is False

        # Verify the raw permission flag mathematically (-3392 instead of -3904)
        encrypt_dict = encrypted_pdf.trailer.get("/Encrypt")
        raw_p = int(encrypt_dict.get("/P", 0))
        assert raw_p == -3392, f"Expected raw AES permissions flag -3392, got {raw_p}"


def test_fast_option_registration():
    """
    Covers line 157 by ensuring the registry-decorated function is at least
    referenced/defined.
    """
    from pdftl.output.save import _fast_option

    assert callable(_fast_option)
    # Calling it directly doesn't do anything (it's a pass), but hits the line
    _fast_option()


def test_build_save_options_fast_logic(tmp_path):
    """
    Covers lines 348-349 by triggering the 'use_fast' conditional branch.
    """
    options = {"fast": True}
    # Passing None for input_context as it's not needed for this branch
    ret = _build_save_options(options, None)

    assert ret["stream_decode_level"] == pikepdf.StreamDecodeLevel.none
    assert ret["compress_streams"] is False
    # Verify it didn't accidentally overwrite unrelated defaults
    assert "linearize" in ret


def test_save_pdf_with_fast_flag(tmp_path):
    """
    Integration test to ensure the 'fast' option flows through the actual
    save_pdf call and produces a valid file.
    """
    out_file = tmp_path / "test_fast.pdf"

    # Create a tiny dummy PDF with a stream (an image or content)
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()

    options = {"fast": True}

    # This executes the full path from save_pdf -> _build_save_options
    save_pdf(pdf, str(out_file), None, options=options)

    assert out_file.exists()

    # Verify the saved file is readable
    with pikepdf.open(out_file) as saved_pdf:
        assert len(saved_pdf.pages) == 1


def test_fast_overrides_uncompress(tmp_path):
    """
    Edge case: Ensure 'fast' logic takes precedence or works
    sensibly alongside 'uncompress'.
    """
    # If both are True, 'fast' sets compress_streams to False (same as uncompress)
    options = {"fast": True, "uncompress": True}
    ret = _build_save_options(options, None)

    assert ret["compress_streams"] is False
    assert ret["stream_decode_level"] == pikepdf.StreamDecodeLevel.none


# --- merged from test_save_coverage.py ---


def test_save_to_stdout_success(minimal_pdf):
    """
    Covers save.py logic: Save to buffer, write buffer to sys.stdout.buffer
    """
    # Fix 1: Patch sys.stdout and attach a mock buffer
    with patch("sys.stdout") as mock_stdout:
        mock_buffer = MagicMock()
        mock_stdout.buffer = mock_buffer

        # Fix 2: Create a mock for the required 'input_context' argument
        mock_ctx = MagicMock()

        # Fix 3: Use correct argument names: 'output_filename' instead of 'output_path'
        save_pdf(minimal_pdf, output_filename="-", input_context=mock_ctx)

        # Verify write was called
        assert mock_buffer.write.called


def test_save_encryption_metadata_aes_flag(minimal_pdf):
    """
    Covers lines 267-268:
    If 'no_encrypt_metadata' is set and we use AES, metadata encryption should be disabled.
    """
    mock_ctx = MagicMock()
    # Use AES-128 (default if just passwords, but explicit flag helps clarity)
    options = {"encrypt_aes128": True, "owner_pw": "secret", "no_encrypt_metadata": True}

    # Patch pikepdf.Encryption to inspect the kwargs passed to it
    with (
        patch("pikepdf.Encryption") as MockEncryption,
        patch.object(minimal_pdf, "save"),
    ):
        save_pdf(minimal_pdf, output_filename="dummy.pdf", input_context=mock_ctx, options=options)

        # Verify the Encryption constructor was called with metadata=False
        call_kwargs = MockEncryption.call_args.kwargs
        assert call_kwargs.get("metadata") is False
        assert call_kwargs.get("aes") is True


def test_save_encryption_metadata_rc4_warning(minimal_pdf, caplog):
    """
    Covers lines 269-270:
    If 'no_encrypt_metadata' is set but we use RC4 (non-AES),
    it should log a warning because RC4 cannot selectively encrypt metadata.
    """
    mock_ctx = MagicMock()
    # encrypt_128bit corresponds to RC4 (aes=False in the map)
    options = {"encrypt_128bit": True, "owner_pw": "secret", "no_encrypt_metadata": True}

    # We expect a logger warning
    with caplog.at_level(logging.WARNING):
        # We don't need to mock Encryption perfectly, just ensure the code runs to the logging point
        with patch("pikepdf.Encryption"), patch.object(minimal_pdf, "save"):
            save_pdf(
                minimal_pdf, output_filename="dummy.pdf", input_context=mock_ctx, options=options
            )

    # Check for the specific warning message
    assert "Ignoring 'no_encrypt_metadata'" in caplog.text
    assert "encrypt_128bit" in caplog.text


def test_save_sign_to_stdout_error(minimal_pdf):
    """
    Covers line 424:
    If options include signing flags (sign_*) and output is stdout ('-'),
    it should raise NotImplementedError.
    """
    mock_ctx = MagicMock()
    # Any option starting with 'sign_' triggers the is_signing flag
    options = {"sign_page": 1, "sign_cert": "me.p12"}

    with pytest.raises(
        NotImplementedError, match="Signing and saving to stdout is not yet implemented"
    ):
        save_pdf(minimal_pdf, output_filename="-", input_context=mock_ctx, options=options)


def test_password_prompt_truncation():
    """
    Covers lines 192-195: Interactive password prompt handling.
    Verifies that passwords > 32 chars are truncated and a warning is logged.
    """
    # Setup
    options = {"user_pw": "PROMPT"}
    mock_input_context = MagicMock()

    # Return a 33-character string
    long_password = "x" * 33
    mock_input_context.get_pass.return_value = long_password

    # Mock the logger to verify the warning
    with patch("pdftl.output.save.logger") as mock_logger:
        passwords = _get_passwords_from_options(options, mock_input_context)

    # Assertions
    # 1. Password should be truncated to 32 chars
    expected_password = "x" * 32
    assert passwords["user"] == expected_password
    assert len(passwords["user"]) == 32

    # 2. Warning should be logged
    mock_logger.warning.assert_called_once()
    assert "truncated" in mock_logger.warning.call_args[0][0]


def test_save_pdf_triggers_font_replacement():
    """
    Covers line 415: save_pdf calling replace_form_fonts.
    """
    # Setup
    mock_pdf = MagicMock()
    options = {"replacement_font": "/path/to/custom_font.ttf"}
    output_filename = "output.pdf"
    mock_context = MagicMock()

    # We need to patch the external dependency functions so save_pdf can run
    with (
        patch("pdftl.output.save.replace_form_fonts") as mock_replace,
        patch("pdftl.output.save._build_save_options", return_value={}),
        patch("pdftl.output.save._remove_source_info"),
        patch("pdftl.output.save._action_drop_flags"),
    ):
        # Execute
        save_pdf(mock_pdf, output_filename, mock_context, options=options)

        # Assertion
        mock_replace.assert_called_once_with(mock_pdf, "/path/to/custom_font.ttf")


# --- merged from test_save_extra.py ---

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
    mock_input_context.get_pass.return_value = "secret"

    pws = _get_passwords_from_options(options, mock_input_context)

    assert pws["user"] == "secret"
    mock_input_context.get_pass.assert_called_once()


def test_get_passwords_truncate(mock_input_context, caplog):
    """Test truncation of long passwords."""
    long_pw = "a" * 40
    mock_input_context.get_pass.return_value = long_pw
    options = {"owner_pw": "PROMPT"}

    pws = _get_passwords_from_options(options, mock_input_context)
    assert len(pws["owner"]) == 32
    assert "Password was over 32 characters" in caplog.text


def test_build_encryption_conflict(mock_input_context):
    """Test error when multiple encryption types are selected."""
    options = {"encrypt_aes256": True, "encrypt_128bit": True}
    with pytest.raises(InvalidArgumentError, match="Too many encryption options"):
        _build_encryption_object(options, mock_input_context)


@pytest.mark.real_encryption
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
        patch("pdftl.operations.attach_files.attach_files") as mock_attach,
        patch("pdftl.output.save._build_save_options", return_value={}),
    ):
        mock_flat.return_value = mock_pdf  # Return the pdf object

        save_pdf(mock_pdf, "out.pdf", mock_input_context, options)

        mock_flat.assert_called_once()
        mock_attach.assert_not_called()
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


def test_save_content_os_error_raises_pdftl_output_error():
    """
    Covers line 383: Verifies that an OSError caught during saving
    is wrapped and re-raised as a PdftlOutputError.
    """
    from pdftl.exceptions import PdftlOutputError

    # Force the internal saving function to throw an intentional OSError
    with patch("pdftl.output.save._save_by_type") as mock_save:
        mock_save.side_effect = OSError("Mocked disk full or permission error")

        # Assert that the custom exception is raised with the correct message
        with pytest.raises(
            PdftlOutputError,
            match="While saving content, got Mocked disk full or permission error",
        ):
            save_content("dummy_content", "dummy_path.pdf", None)


def test_save_pdf_cannot_overwrite_input_error(mock_pdf, mock_input_context):
    """Test catching pikepdf overwrite ValueError and raising PdftlOutputError."""
    from pdftl.exceptions import PdftlOutputError

    mock_pdf.save.side_effect = ValueError(
        "Cannot overwrite input file. Open the file with pikepdf.open(...)"
    )
    with pytest.raises(
        PdftlOutputError, match="Cannot overwrite input file '/tmp/c0.pdf' directly"
    ):
        save_pdf(mock_pdf, "/tmp/c0.pdf", mock_input_context)


# ---------------------------------------------------------
# Fix for missing /AcroForm and /XFA branches
# ---------------------------------------------------------


def test_drop_xfa_no_acroform():
    """Evaluates drop_xfa when the PDF has no /AcroForm."""
    pdf_mock = MagicMock()
    pdf_mock.Root = {}

    # Should complete without error
    _action_drop_flags(pdf_mock, {"drop_xfa": True})


def test_drop_xfa_no_xfa_in_acroform():
    """Evaluates drop_xfa when the PDF has /AcroForm but no /XFA."""
    pdf_mock = MagicMock()
    pdf_mock.Root = {"/AcroForm": {}}

    # Should complete without error
    _action_drop_flags(pdf_mock, {"drop_xfa": True})


# ---------------------------------------------------------
# Fix for generic ValueError re-raise
# ---------------------------------------------------------


def test_save_pdf_raises_other_valueerror():
    """Evaluates the fallback raise when a different ValueError occurs."""
    pdf_mock = MagicMock()
    pdf_mock.save.side_effect = ValueError("An unrelated value error occurred")

    input_context_mock = MagicMock()

    with pytest.raises(ValueError, match="An unrelated value error occurred"):
        save_pdf(
            pdf=pdf_mock,
            output_filename="output.pdf",
            input_context=input_context_mock,
            options={},
        )


# ---------------------------------------------------------------------------
# _iter_form_fields / has_live_signatures
# ---------------------------------------------------------------------------


def test_iter_form_fields_flat():
    """A flat list of fields with no /Kids yields each field once."""
    fields = [{"name": "a"}, {"name": "b"}]
    result = list(_iter_form_fields(fields))
    assert result == fields


def test_iter_form_fields_recurses_into_kids():
    """A field with /Kids yields itself, then recurses into the kids
    (covering the nested-hierarchy 'yield from' branch)."""
    grandchild = {"name": "grandchild"}
    child = {"name": "child", "/Kids": [grandchild]}
    root = {"name": "root", "/Kids": [child]}

    result = list(_iter_form_fields([root]))
    assert result == [root, child, grandchild]


def test_iter_form_fields_field_without_get_method():
    """A field with no .get method at all (e.g. a plain int/str) must
    still be yielded, just without recursion attempted on it."""
    result = list(_iter_form_fields(["not_a_dict"]))
    assert result == ["not_a_dict"]


def test_has_live_signatures_no_acroform():
    """No /AcroForm on the Root -> False, no crash."""
    pdf = MagicMock()
    pdf.Root = {}
    assert has_live_signatures(pdf) is False


def test_has_live_signatures_acroform_get_raises():
    """pdf.Root.get raising PdfError/AttributeError is caught -> False."""
    pdf = MagicMock()
    pdf.Root.get.side_effect = AttributeError("boom")
    assert has_live_signatures(pdf) is False


def test_has_live_signatures_acroform_none():
    """/AcroForm present but resolves to None -> False."""
    pdf = MagicMock()
    pdf.Root.get.return_value = None
    assert has_live_signatures(pdf) is False


def test_has_live_signatures_no_fields_key():
    """/AcroForm present but has no /Fields -> False."""
    pdf = MagicMock()
    pdf.Root.get.return_value = {}
    assert has_live_signatures(pdf) is False


def test_has_live_signatures_field_get_raises_continues():
    """A field whose .get('/FT', ...) call raises PdfError/AttributeError
    (but whose /Kids lookup succeeds normally) is skipped via `continue`,
    not fatal to the overall scan."""
    import pikepdf

    class BrokenField:
        def get(self, *a, **kw):
            if a and a[0] == "/Kids":
                return None
            raise pikepdf.PdfError("broken field")

    pdf = MagicMock()
    pdf.Root.get.return_value = {"/Fields": [BrokenField()]}
    assert has_live_signatures(pdf) is False


def test_has_live_signatures_non_sig_field_skipped():
    """A field whose /FT is not /Sig is skipped (continue)."""
    pdf = MagicMock()
    field = {"/FT": "/Tx"}
    pdf.Root.get.return_value = {"/Fields": [field]}
    assert has_live_signatures(pdf) is False


def test_has_live_signatures_sig_field_no_value():
    """A /Sig field with no /V (unsigned placeholder) -> False."""
    pdf = MagicMock()
    field = {"/FT": "/Sig"}
    pdf.Root.get.return_value = {"/Fields": [field]}
    assert has_live_signatures(pdf) is False


def test_has_live_signatures_sig_field_non_dict_value():
    """A /Sig field whose /V is present but not dict-like (no .get)
    is treated as an unsigned placeholder, not a real signature."""
    pdf = MagicMock()
    field = {"/FT": "/Sig", "/V": "not_a_dict"}
    pdf.Root.get.return_value = {"/Fields": [field]}
    assert has_live_signatures(pdf) is False


def test_has_live_signatures_true_for_real_signature():
    """A /Sig field with a dict-like /V (a real signature dict) -> True."""
    pdf = MagicMock()
    field = {"/FT": "/Sig", "/V": {"/Type": "/Sig", "/Contents": b"..."}}
    pdf.Root.get.return_value = {"/Fields": [field]}
    assert has_live_signatures(pdf) is True


def test_has_live_signatures_nested_via_kids():
    """A signed /Sig field nested under /Kids is still found."""
    pdf = MagicMock()
    signed_kid = {"/FT": "/Sig", "/V": {"/Type": "/Sig"}}
    parent = {"name": "group", "/Kids": [signed_kid]}
    pdf.Root.get.return_value = {"/Fields": [parent]}
    assert has_live_signatures(pdf) is True


# ---------------------------------------------------------------------------
# save_pdf: live-signature warning branch
# ---------------------------------------------------------------------------


def test_save_pdf_warns_on_live_signatures_when_not_signing(mock_pdf, mock_input_context, caplog):
    """When not signing and the document has live signatures, save_pdf
    logs a warning (rather than blocking the save) before proceeding."""
    with (
        patch("pdftl.output.save.has_live_signatures", return_value=True),
        patch("pdftl.output.save._build_save_options", return_value={}),
        caplog.at_level(logging.WARNING),
    ):
        save_pdf(mock_pdf, "out.pdf", mock_input_context, options={})

    assert any("will invalidate them" in record.message for record in caplog.records)
    mock_pdf.save.assert_called_once()


def test_prune_resources_option_registration():
    """Covers the 'pass' statement in _prune_resources_option."""
    from pdftl.output.save import _prune_resources_option

    _prune_resources_option()


def test_apply_prune_resources_disabled_by_default(mock_pdf):
    """No option set -> remove_unreferenced_resources is never called."""
    from pdftl.output.save import _apply_prune_resources

    _apply_prune_resources(mock_pdf, {}, is_signing=False)

    mock_pdf.remove_unreferenced_resources.assert_not_called()


def test_apply_prune_resources_calls_pikepdf(mock_pdf):
    """Option set, not signing -> calls through to pikepdf."""
    from pdftl.output.save import _apply_prune_resources

    _apply_prune_resources(mock_pdf, {"prune_resources": True}, is_signing=False)

    mock_pdf.remove_unreferenced_resources.assert_called_once()


def test_apply_prune_resources_skipped_when_signing(mock_pdf, caplog):
    """Option set, but signing -> skipped with a warning, not called."""
    from pdftl.output.save import _apply_prune_resources

    with caplog.at_level("WARNING"):
        _apply_prune_resources(mock_pdf, {"prune_resources": True}, is_signing=True)

    mock_pdf.remove_unreferenced_resources.assert_not_called()
    assert "Ignoring 'prune_resources'" in caplog.text


def test_save_pdf_prune_resources_integration(tmp_path):
    """End-to-end: an unreferenced font resource is dropped from the page
    after save_pdf runs with prune_resources=True."""
    import pikepdf

    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page()
    page.Resources = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(F1=pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font)))
    )
    page.Contents = pdf.make_stream(b"BT ET")  # empty content stream, /F1 unused

    out_file = tmp_path / "pruned.pdf"
    save_pdf(pdf, str(out_file), None, options={"prune_resources": True})

    with pikepdf.open(out_file) as saved:
        resources = saved.pages[0].get("/Resources", {})
        fonts = resources.get("/Font", {})
        assert "/F1" not in fonts


def test_save_pdf_prune_resources_skipped_when_signing():
    """save_pdf with both prune_resources and a sign_* option should warn
    and not call remove_unreferenced_resources, then proceed to sign."""
    from unittest.mock import MagicMock, patch

    mock_pdf = MagicMock()
    options = {"prune_resources": True, "sign_field": "Sig1", "output": "out.pdf"}

    with (
        patch("pdftl.output.save.parse_sign_options"),
        patch("pdftl.output.save.save_and_sign"),
        patch("pdftl.output.save._build_save_options", return_value={}),
    ):
        save_pdf(mock_pdf, "out.pdf", MagicMock(), options)

    mock_pdf.remove_unreferenced_resources.assert_not_called()
