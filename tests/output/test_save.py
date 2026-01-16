from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.core import constants as constants_module
from pdftl.exceptions import InvalidArgumentError, MissingArgumentError
from pdftl.output import save as save_module
from pdftl.output.save import (
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
    _need_appearances_option,
    _output_option,
    _owner_pw_option,
    _set_permission_or_raise_error,
    _user_pw_option,
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
def patch_dependencies(mocker):
    """
    Patch all external and internal dependencies to isolate
    each function.
    """
    # Patch pikepdf.Encryption
    mocker.patch("pikepdf.Encryption", autospec=True)

    # ---
    # DO NOT MOCK pikepdf.Permissions here.
    # _default_permissions_object relies on inspecting the REAL class.
    # ---

    # --- Use the REAL permission strings from the help text ---
    mock_permission_map = {
        # Map user-facing strings (from help) to real pikepdf flags
        "Printing": ["print_highres", "print_lowres"],
        "DegradedPrinting": ["print_lowres"],
        "ModifyContents": ["modify", "modify_assembly"],
        "Assembly": ["modify_assembly"],
        "CopyContents": ["extract", "accessibility"],  # Corrected from "Copying"
        "ScreenReaders": ["accessibility"],
        "ModifyAnnotations": ["modify_annotation"],
        "FillIn": ["fill_form"],
        "BadFlag": ["non_existent_flag"],  # For testing error
    }
    mocker.patch.dict(
        constants_module.ALLOW_PERMISSIONS_MAP,
        mock_permission_map,
        clear=True,
    )

    # Patch logging
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
    Tests building with no 'allow' options (defaults to All Permitted).
    Note: The code treats empty list [] as 'allow everything', avoiding the
    restrictive _default_permissions_object helper.
    """
    # 1. Patch the class constructor
    mock_permissions_cls = mocker.patch("pikepdf.Permissions", autospec=True)
    mock_instance = mock_permissions_cls.return_value

    # 2. Patch the helper (we expect this NOT to be called)
    mock_default_helper = mocker.patch("pdftl.output.save._default_permissions_object")

    # 3. Run the function with empty list
    result = _build_permissions_object([])

    # 4. Assertions
    # The helper should NOT be called (because we are not restricting permissions)
    mock_default_helper.assert_not_called()

    # The constructor should be called with the default "allow all" setting
    # (checking implementation detail from save.py line 209)
    mock_permissions_cls.assert_called_once_with(modify_assembly=True)

    assert result is mock_instance


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


from unittest.mock import patch

from pdftl.core.constants import PDFTL_SOURCE_INFO_KEY
from pdftl.output.save import _remove_source_info, save_content


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


from unittest.mock import patch

import pytest


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
