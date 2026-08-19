import pytest
from pdftl.core.registry import registry
from pdftl.exceptions import OperationError
from unittest.mock import MagicMock, patch

# Assuming the package is installed or in PYTHONPATH as pdftl
from pdftl.operations.modify_images import (
    LazyImageModifierHelpProxy,
    modify_images_operation,
    _parse_operational_args,
    _compile_pipeline_steps,
    _discover_target_images,
    _build_callbacks,
)
from pdftl.exceptions import InvalidArgumentError


# --- FIXTURES ---


@pytest.fixture
def mock_registry():
    with patch("pdftl.operations.modify_images.registry") as mock_reg:
        yield mock_reg


@pytest.fixture
def mock_pdf():
    pdf = MagicMock()
    pdf.pages = [MagicMock(), MagicMock(), MagicMock()]
    return pdf


# --- TESTS FOR LazyImageModifierHelpProxy ---


def test_lazy_modify_help_proxy_no_image_modifiers(mock_registry):
    mock_registry.image_modifiers = {}
    proxy = LazyImageModifierHelpProxy()
    result = str(proxy)
    assert "(No image modifiers discovered.)" in result
    assert "Processes bitmap page image assets" in result


def test_lazy_modify_help_proxy_with_image_modifiers(mock_registry):
    mock_plugin = MagicMock()
    mock_plugin.arg_type = "float"
    mock_plugin.title = "Contrast"
    mock_plugin.desc = "Adjusts contrast."

    mock_registry.image_modifiers = {"contrast": mock_plugin}
    proxy = LazyImageModifierHelpProxy()
    result = str(proxy)

    assert "| Contrast | `contrast=<float>` | Adjusts contrast. |" in result
    assert "(No image modifiers discovered.)" not in result


# --- TESTS FOR _parse_operational_args ---


def test_parse_operational_args_valid():
    args = ["threads=4", "quality=80", "1-5(contrast=1.5)"]
    threads, quality, clean = _parse_operational_args(args)
    assert threads == 4
    assert quality == 80
    assert clean == ["1-5(contrast=1.5)"]


def test_parse_operational_args_invalid_values_ignored():
    args = ["threads=abc", "quality=def", "1-5(contrast=1.5)"]
    threads, quality, clean = _parse_operational_args(args)
    assert threads is None
    assert quality == 75  # Default fallback
    assert clean == ["1-5(contrast=1.5)"]


# --- TESTS FOR _compile_pipeline_steps ---


def test_compile_pipeline_steps_valid(mock_registry):
    mock_plugin = MagicMock()
    mock_plugin.validator.return_value = 1.5
    mock_registry.image_modifiers = {"contrast": mock_plugin}

    mock_op = MagicMock()
    mock_op.name = "contrast"
    mock_op.params = {"value": "1.5"}

    mock_cmd = MagicMock()
    mock_cmd.operations = [mock_op]

    steps, have_image_modifiers, forced_codec = _compile_pipeline_steps(mock_cmd)

    assert have_image_modifiers is True
    assert steps == [("contrast", 1.5)]
    assert forced_codec is None
    mock_plugin.validator.assert_called_with("1.5")


def test_compile_pipeline_steps_unknown_image_modifier(mock_registry):
    mock_registry.image_modifiers = {}
    mock_op = MagicMock()
    mock_op.name = "unknown_image_modifier"
    mock_cmd = MagicMock()
    mock_cmd.operations = [mock_op]

    with pytest.raises(InvalidArgumentError, match="Unknown image modifier"):
        _compile_pipeline_steps(mock_cmd)


def test_compile_pipeline_steps_empty_val_to_true(mock_registry):
    mock_plugin = MagicMock()
    mock_plugin.validator.return_value = True
    mock_registry.image_modifiers = {"sharpen": mock_plugin}

    mock_op = MagicMock()
    mock_op.name = "sharpen"
    mock_op.params = {"value": "  "}  # Empty/whitespace value

    mock_cmd = MagicMock()
    mock_cmd.operations = [mock_op]

    steps, have_image_modifiers, forced_codec = _compile_pipeline_steps(mock_cmd)
    mock_plugin.validator.assert_called_with("true")
    assert have_image_modifiers is True
    assert forced_codec is None


def test_compile_pipeline_steps_format_png(mock_registry):
    mock_registry.image_modifiers = {}
    mock_op = MagicMock()
    mock_op.name = "format"
    mock_op.params = {"value": "png"}

    mock_cmd = MagicMock()
    mock_cmd.operations = [mock_op]

    steps, have_image_modifiers, forced_codec = _compile_pipeline_steps(mock_cmd)

    assert steps == []
    assert have_image_modifiers is True
    assert forced_codec == "png"


def test_compile_pipeline_steps_format_jpg_normalizes_to_jpeg(mock_registry):
    mock_registry.image_modifiers = {}
    mock_op = MagicMock()
    mock_op.name = "format"
    mock_op.params = {"value": "JPG"}

    mock_cmd = MagicMock()
    mock_cmd.operations = [mock_op]

    steps, have_image_modifiers, forced_codec = _compile_pipeline_steps(mock_cmd)

    assert forced_codec == "jpeg"
    assert have_image_modifiers is True


def test_compile_pipeline_steps_format_missing_value(mock_registry):
    mock_registry.image_modifiers = {}
    mock_op = MagicMock()
    mock_op.name = "format"
    mock_op.params = {"value": "  "}

    mock_cmd = MagicMock()
    mock_cmd.operations = [mock_op]

    with pytest.raises(InvalidArgumentError, match="Image modifier 'format': missing value"):
        _compile_pipeline_steps(mock_cmd)


def test_compile_pipeline_steps_format_invalid_value(mock_registry):
    mock_registry.image_modifiers = {}
    mock_op = MagicMock()
    mock_op.name = "format"
    mock_op.params = {"value": "gif"}

    mock_cmd = MagicMock()
    mock_cmd.operations = [mock_op]

    with pytest.raises(InvalidArgumentError, match="Image modifier 'format':.*must be one of"):
        _compile_pipeline_steps(mock_cmd)


def test_compile_pipeline_steps_format_plus_modifier(mock_registry):
    mock_plugin = MagicMock()
    mock_plugin.validator.return_value = 2
    mock_registry.image_modifiers = {"posterize": mock_plugin}

    format_op = MagicMock()
    format_op.name = "format"
    format_op.params = {"value": "png"}

    posterize_op = MagicMock()
    posterize_op.name = "posterize"
    posterize_op.params = {"value": "2"}

    mock_cmd = MagicMock()
    mock_cmd.operations = [posterize_op, format_op]

    steps, have_image_modifiers, forced_codec = _compile_pipeline_steps(mock_cmd)

    assert steps == [("posterize", 2)]
    assert forced_codec == "png"
    assert have_image_modifiers is True


def test_compile_pipeline_steps_validator_error(mock_registry):
    mock_plugin = MagicMock()
    mock_plugin.validator.side_effect = InvalidArgumentError("Bad value")
    mock_registry.image_modifiers = {"contrast": mock_plugin}

    mock_op = MagicMock()
    mock_op.name = "contrast"
    mock_op.params = {"value": "bad"}
    mock_cmd = MagicMock()
    mock_cmd.operations = [mock_op]

    with pytest.raises(InvalidArgumentError, match="Image modifier 'contrast': Bad value"):
        _compile_pipeline_steps(mock_cmd)


def test_compile_pipeline_steps_all_false_image_modifiers(mock_registry):
    # Tests the loop where if a boolean is false, it doesn't trigger have_image_modifiers
    mock_plugin = MagicMock()
    mock_plugin.validator.return_value = False
    mock_registry.image_modifiers = {"sharpen": mock_plugin}

    mock_op = MagicMock()
    mock_op.name = "sharpen"
    mock_op.params = {"value": "false"}
    mock_cmd = MagicMock()
    mock_cmd.operations = [mock_op]

    steps, have_image_modifiers, forced_codec = _compile_pipeline_steps(mock_cmd)
    assert have_image_modifiers is False
    assert forced_codec is None


# --- TESTS FOR _discover_target_images ---


def test_discover_target_images_success():
    pdf = MagicMock()
    # Create a page with an image
    page1 = {"/Resources": {"/XObject": {"/Im1": {"/Subtype": "/Image"}}}}
    # Create a page missing /XObject
    page2 = {"/Resources": {}}
    # Create a page missing /Resources entirely
    page3 = {}

    pdf.pages = [page1, page2, page3]

    images = _discover_target_images(pdf, target_pages=[1, 2, 3, 99], total_pages=3)

    assert len(images) == 1
    assert images[0]["name"] == "/Im1"
    assert images[0]["page_num"] == 1


def test_discover_target_images_no_image_subtype():
    pdf = MagicMock()
    page1 = {"/Resources": {"/XObject": {"/Font1": {"/Subtype": "/Font"}}}}
    pdf.pages = [page1]
    images = _discover_target_images(pdf, target_pages=[1], total_pages=1)
    assert len(images) == 0


# --- TESTS FOR _build_callbacks ---


# --- FIX FOR test_build_callbacks ---
@patch("pikepdf.PdfImage", create=True)  # <-- Patching direct at the source module
@patch("pdftl.operations.modify_images.ensure_thread_safe")
@patch("pdftl.operations.modify_images.get_orig_stream_size")
@patch("pdftl.operations.modify_images.encode_and_update_pdf_image")
@patch("pdftl.operations.modify_images.pikepdf", create=True)
def test_build_callbacks(
    mock_pikepdf, mock_encode, mock_get_size, mock_ensure, mock_pdfimage_class, mock_registry
):
    prepare_cb, worker_cb, commit_cb = _build_callbacks(True, [("contrast", 1.5)], 80)

    # 1. Test prepare_cb
    mock_xobj = MagicMock()
    mock_xobj.objgen = (1, 0)
    mock_xobj.get.return_value = None
    img_item = {"xobj": mock_xobj, "page_num": 1, "name": "Im1"}

    # Configure the instantiated PdfImage mock to return a fake PIL image safely
    mock_pdf_img_instance = MagicMock()
    mock_pdf_img_instance.as_pil_image.return_value = "fake_pil_image"
    mock_pdfimage_class.return_value = mock_pdf_img_instance

    # Successful prepare
    seen = set()
    payload, ctx = prepare_cb(img_item, seen)
    assert payload["pil_image"] == "fake_pil_image"
    assert payload["steps"] == [("contrast", 1.5)]
    assert "1_0" in seen

    # Already seen
    assert prepare_cb(img_item, seen) is None

    # Have image_modifiers is false short-circuit
    prep_no_image_modifiers, _, _ = _build_callbacks(False, [], 80)
    assert prep_no_image_modifiers(img_item, set()) is None

    # Extraction raises ValueError or pikepdf.PdfError
    mock_pdfimage_class.side_effect = ValueError("Bad image")
    unseen = set()
    assert prepare_cb(img_item, unseen) is None

    # Reset side effect for subsequent test steps
    mock_pdfimage_class.side_effect = None

    # 2. Test worker_cb
    mock_pil = MagicMock()
    mock_plugin = MagicMock()
    mock_plugin.function.return_value = "processed_pil"
    mock_registry.image_modifiers = {"contrast": mock_plugin}

    worker_payload = {"pil_image": mock_pil, "steps": [("contrast", 1.5)]}
    result_pil = worker_cb(worker_payload)
    assert result_pil == "processed_pil"
    mock_plugin.function.assert_called_with(mock_pil, 1.5)

    # 3. Test commit_cb
    mock_ctx = MagicMock()
    success = commit_cb(mock_ctx, "processed_pil", {})
    assert success is True
    mock_encode.assert_called_with(mock_ctx, "processed_pil", 80, None)


# --- TESTS FOR modify_images_operation (Orchestrator) ---


@patch("pdftl.operations.modify_images._parse_operational_args")
@patch("pdftl.operations.modify_images.parse_modify_images_args")
@patch("pdftl.operations.modify_images._compile_pipeline_steps")
@patch("pdftl.operations.modify_images.page_numbers_matching_page_spec")
@patch("pdftl.operations.modify_images._discover_target_images")
@patch("pdftl.operations.modify_images._build_callbacks")
@patch("pdftl.operations.modify_images.run_parallel_image_job")
def test_modify_images_operation_full_success(
    mock_run_job,
    mock_build_cbs,
    mock_discover,
    mock_page_match,
    mock_compile,
    mock_parse_args,
    mock_parse_op_args,
    mock_pdf,
):
    # Setup mocks
    mock_parse_op_args.return_value = (4, 80, ["1(contrast=1)"])

    mock_cmd = MagicMock()
    mock_cmd.page_spec = "1"
    mock_parse_args.return_value = [mock_cmd]

    mock_compile.return_value = ([("contrast", 1)], True, None)
    mock_page_match.return_value = [1]
    mock_discover.return_value = [{"xobj": MagicMock()}]
    mock_build_cbs.return_value = ("prep", "work", "commit")

    # Execute
    res = modify_images_operation(mock_pdf, ["1(contrast=1)"])

    # Asserts
    assert res.success is True
    assert res.pdf == mock_pdf
    mock_run_job.assert_called_once_with(
        images=[{"xobj": mock_discover.return_value[0]["xobj"]}],
        threads=4,
        prepare_func="prep",
        worker_func="work",
        commit_func="commit",
    )


def test_modify_images_operation_empty_args(mock_pdf):
    with pytest.raises(InvalidArgumentError, match="Missing execution payload"):
        modify_images_operation(mock_pdf, [])


@patch("pdftl.operations.modify_images.parse_modify_images_args")
def test_modify_images_operation_empty_commands(mock_parse_args, mock_pdf):
    mock_parse_args.return_value = []
    with pytest.raises(InvalidArgumentError, match="Missing execution payload"):
        modify_images_operation(mock_pdf, ["bad_arg"])


@patch("pdftl.operations.modify_images.parse_modify_images_args")
@patch("pdftl.operations.modify_images._compile_pipeline_steps")
@patch("pdftl.operations.modify_images.page_numbers_matching_page_spec")
def test_modify_images_operation_no_target_pages(
    mock_page_match, mock_compile, mock_parse_args, mock_pdf
):
    mock_cmd = MagicMock()
    mock_cmd.page_spec = "99"
    mock_parse_args.return_value = [mock_cmd]
    mock_compile.return_value = ([], True, None)
    mock_page_match.return_value = []  # No pages match

    res = modify_images_operation(mock_pdf, ["99(contrast=1)"])
    assert res.success is True


@patch("pdftl.operations.modify_images.parse_modify_images_args")
@patch("pdftl.operations.modify_images._compile_pipeline_steps")
@patch("pdftl.operations.modify_images.page_numbers_matching_page_spec")
@patch("pdftl.operations.modify_images._discover_target_images")
def test_modify_images_operation_no_images_discovered(
    mock_discover, mock_page_match, mock_compile, mock_parse_args, mock_pdf
):
    mock_cmd = MagicMock()
    mock_parse_args.return_value = [mock_cmd]
    mock_compile.return_value = ([], True, None)
    mock_page_match.return_value = [1]
    mock_discover.return_value = []  # No images found

    res = modify_images_operation(mock_pdf, ["1(contrast=1)"])
    assert res.success is True


def test_worker_callback_converts_value_error_to_operation_error():
    """Ensure worker_callback catches a plugin ValueError and converts it to an OperationError."""

    # 1. Setup a dummy plugin that intentionally raises a ValueError
    class MockFailingPlugin:
        def function(self, img, val):
            raise ValueError("image has wrong mode")

    # Inject the dummy plugin context into the registry
    plugin_name = "test_fail_modifier"
    registry.image_modifiers[plugin_name] = MockFailingPlugin()

    try:
        # 2. Generate the worker callback using the private helper
        steps = [(plugin_name, "dummy_value")]
        _, worker_cb, _ = _build_callbacks(have_image_modifiers=True, steps=steps, quality=75)

        # 3. Construct a synthetic payload simulating what prepare_callback forwards
        mock_pil_image = MagicMock()
        payload = {
            "pil_image": mock_pil_image,
            "steps": steps,
            "page_num": 42,
            "name": "/Im0_FailingAsset",
        }

        # 4. Assert that the ValueError is caught and wrapped into an OperationError
        expected_msg = (
            "Modifier 'test_fail_modifier' failed processing asset '/Im0_FailingAsset' "
            "on page 42: image has wrong mode"
        )

        with pytest.raises(OperationError) as exc_info:
            worker_cb(payload)

        assert str(exc_info.value) == expected_msg
        assert isinstance(exc_info.value.__cause__, ValueError)

    finally:
        # Clean up the global registry so it doesn't pollute other tests
        if plugin_name in registry.image_modifiers:
            del registry.image_modifiers[plugin_name]
