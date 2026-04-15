# tests/operations/test_dump_encryption.py

import io
import json
from contextlib import contextmanager

import pytest

import pdftl.core.constants as c
from pdftl.core.types import OpResult
from pdftl.operations.dump_encryption import (
    PERM_MAPPING,
    dump_encryption,
    dump_encryption_cli_hook,
)

# --- Mocks and Fixtures ---


class MockPermissions:
    """Mocks pikepdf.Permissions"""

    def __init__(self, **kwargs):
        # Default all to False, override with kwargs
        for val in PERM_MAPPING.values():
            setattr(self, val, False)
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockPdf:
    """Mocks pikepdf.Pdf"""

    def __init__(self, is_encrypted=False, encrypt_dict=None, allow=None):
        self.is_encrypted = is_encrypted
        self.trailer = {"/Encrypt": encrypt_dict} if encrypt_dict else {}
        self.allow = allow or MockPermissions()


@pytest.fixture
def mock_smart_open(monkeypatch):
    """Mocks smart_open_maybe_dash to capture output into a StringIO buffer"""
    buffer = io.StringIO()

    @contextmanager
    def _mock_open(filename):
        yield buffer

    monkeypatch.setattr("pdftl.operations.dump_encryption.smart_open_maybe_dash", _mock_open)
    return buffer


@pytest.fixture
def patch_pikepdf_dict(monkeypatch):
    """
    pikepdf.Dictionary checks are hard to mock without a real PDF context.
    This safely patches the isinstance check to allow standard Python dicts.
    """
    monkeypatch.setattr("pikepdf.Dictionary", dict)


@pytest.fixture
def mock_get_json_flag(monkeypatch):
    """Safely mocks the CLI flag parser for unit testing the core function"""
    monkeypatch.setattr(
        "pdftl.operations.dump_encryption.get_json_flag", lambda args, op: "json" in (args or [])
    )


# --- Core Logic Tests (dump_encryption) ---


def test_dump_encryption_unencrypted(mock_get_json_flag):
    """Test standard unencrypted file"""
    pdf = MockPdf(is_encrypted=False)
    result = dump_encryption(pdf)

    assert result.success is True
    assert result.data["IsEncrypted"] is False
    assert "Encryption" not in result.data

    # Unencrypted files inherently have all permissions allowed
    assert all(v is True for v in result.data["Permissions"].values())
    assert result.meta[c.META_JSON_OUTPUT] is False


def test_dump_encryption_missing_encrypt_dict(mock_get_json_flag):
    """Test edge case where is_encrypted is True but dictionary is missing"""
    pdf = MockPdf(is_encrypted=True, encrypt_dict=None)
    result = dump_encryption(pdf)

    assert result.data["IsEncrypted"] is True
    assert "Encryption" not in result.data


def test_dump_encryption_v2(mock_get_json_flag):
    """Test RC4 (128-bit) encryption extraction"""
    encrypt_dict = {"/Filter": "/Standard", "/V": 2, "/R": 3, "/P": -3904, "/Length": 128}
    allow = MockPermissions(print_highres=True, extract=False)
    pdf = MockPdf(is_encrypted=True, encrypt_dict=encrypt_dict, allow=allow)

    result = dump_encryption(pdf)
    enc = result.data["Encryption"]

    assert enc["EncryptionAlgorithm"] == "RC4 (128-bit)"
    assert enc["EncryptionFilter"] == "Standard"
    assert enc["EncryptionParameterV"] == 2
    assert enc["EncryptionParameterR"] == 3
    assert enc["EncryptionKeyLengthBits"] == 128
    assert enc["EncryptionRawPermissionsFlag"] == -3904

    # Check permissions mapping
    assert result.data["Permissions"]["Printing"] is True
    assert result.data["Permissions"]["CopyContents"] is False


def test_dump_encryption_v4_aes(mock_get_json_flag, patch_pikepdf_dict):
    """Test V=4 falling back to CryptFilters for AES extraction"""
    cf = {"/StdCF": {"/CFM": "/AESV2"}}
    encrypt_dict = {"/Filter": "/Standard", "/V": 4, "/Length": 128, "/CF": cf}
    pdf = MockPdf(is_encrypted=True, encrypt_dict=encrypt_dict)

    result = dump_encryption(pdf)
    assert result.data["Encryption"]["EncryptionAlgorithm"] == "AES (128-bit)"


def test_dump_encryption_v4_rc4(mock_get_json_flag, patch_pikepdf_dict):
    """Test V=4 falling back to CryptFilters for RC4 extraction"""
    cf = {"/StdCF": {"/CFM": "/V2"}}
    encrypt_dict = {"/Filter": "/Standard", "/V": 4, "/Length": 128, "/CF": cf}
    pdf = MockPdf(is_encrypted=True, encrypt_dict=encrypt_dict)

    result = dump_encryption(pdf)
    assert result.data["Encryption"]["EncryptionAlgorithm"] == "RC4 (128-bit)"

@pytest.mark.parametrize(
    "cf", [{}, "invalid", {"/StdCF": {}}, {"/StdCF": {"/CFM": []}}]
)
def test_dump_encryption_v4_rc4_missing_keys(mock_get_json_flag, cf, patch_pikepdf_dict):
    """Test V=4 falling back to CryptFilters for RC4 extraction"""
    encrypt_dict = {"/Filter": "/Standard", "/V": 4, "/Length": 128, "/CF": cf}
    pdf = MockPdf(is_encrypted=True, encrypt_dict=encrypt_dict)
    result = dump_encryption(pdf)
    assert result.data["Encryption"]["EncryptionAlgorithm"] == "AES / RC4 (128-bit)"


@pytest.mark.parametrize(
    "v_param, expected_algo",
    [
        (1, "RC4 (40-bit)"),
        (5, "AES (256-bit)"),
        (6, "AES (256-bit, PDF 2.0)"),
        (99, "Unknown (V=99)"),
    ],
)
def test_dump_encryption_algorithms(mock_get_json_flag, v_param, expected_algo):
    """Test extraction of various standard PDF encryption algorithms"""
    encrypt_dict = {"/V": v_param, "/Length": 256}
    pdf = MockPdf(is_encrypted=True, encrypt_dict=encrypt_dict)

    result = dump_encryption(pdf)
    assert result.data["Encryption"]["EncryptionAlgorithm"] == expected_algo


# --- CLI Hook Formatting Tests (dump_encryption_cli_hook) ---


def test_cli_hook_empty_data(mock_smart_open):
    """Ensure hook bails out gracefully if no data is present"""
    result = OpResult(success=True, data={}, pdf=None)
    dump_encryption_cli_hook(result, None, None)
    assert mock_smart_open.getvalue() == ""


def test_cli_hook_none_data(mock_smart_open):
    result = OpResult(success=True, data=None, pdf=None)
    dump_encryption_cli_hook(result, None, None)
    assert mock_smart_open.getvalue() == ""


def test_cli_hook_json_output(mock_smart_open):
    """Test structured JSON output format"""
    data = {"IsEncrypted": False, "Permissions": {"Printing": True, "FillIn": False}}
    result = OpResult(success=True, data=data, pdf=None, meta={c.META_JSON_OUTPUT: True})

    dump_encryption_cli_hook(result, None, None)

    output = mock_smart_open.getvalue()
    parsed_json = json.loads(output)

    assert parsed_json["IsEncrypted"] is False
    assert parsed_json["Permissions"]["Printing"] is True
    assert parsed_json["Permissions"]["FillIn"] is False


def test_cli_hook_text_unencrypted(mock_smart_open):
    """Test pdftk stanza text output for unencrypted files"""
    data = {"IsEncrypted": False, "Permissions": {"Printing": True, "FillIn": False}}
    result = OpResult(success=True, data=data, pdf=None, meta={c.META_JSON_OUTPUT: False})

    dump_encryption_cli_hook(result, None, None)

    output = mock_smart_open.getvalue()
    assert "IsEncrypted: false\n" in output
    assert "EncryptionBegin" not in output
    assert "PermissionBegin\n" in output
    assert "PermissionPrinting: true\n" in output
    assert "PermissionFillIn: false\n" in output


def test_cli_hook_text_encrypted(mock_smart_open):
    """Test pdftk stanza text output for encrypted files"""
    data = {
        "IsEncrypted": True,
        "Encryption": {
            "EncryptionAlgorithm": "AES (256-bit)",
            "EncryptionFilter": "Standard",
            "EncryptionParameterV": 5,
        },
        "Permissions": {"CopyContents": False},
    }
    result = OpResult(success=True, data=data, pdf=None, meta={c.META_JSON_OUTPUT: False})

    dump_encryption_cli_hook(result, None, None)

    output = mock_smart_open.getvalue()
    assert "IsEncrypted: true\n" in output
    assert "EncryptionBegin\n" in output
    assert "EncryptionAlgorithm: AES (256-bit)\n" in output
    assert "EncryptionFilter: Standard\n" in output
    assert "EncryptionParameterV: 5\n" in output
    assert "PermissionBegin\n" in output
    assert "PermissionCopyContents: false\n" in output
