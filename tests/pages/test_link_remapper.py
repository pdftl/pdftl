import logging

import pytest
from pikepdf import Array, Dictionary, Name, Pdf, String

try:
    from pikepdf.exceptions import ForeignObjectError
except ImportError:
    from pikepdf import ForeignObjectError

# --- Import the class to test ---
from pdftl.pages.link_remapper import LinkRemapper, RemapperContext

# Import the module itself so we can reload it


@pytest.fixture
def remapper_setup(mocker):
    """
    Sets up a complex test environment with real source and destination PDFs,
    mock context maps, and a fully initialized LinkRemapper instance.
    """

    # --- 1. Create Real PDF Objects ---
    pdf = Pdf.new()
    source_pdf = Pdf.new()

    # --- 2. Create Source Page and Destination ---
    source_pdf.add_blank_page(page_size=(600, 800))
    source_page = source_pdf.pages[0]
    source_page_objgen = source_page.obj.objgen

    # Create a real explicit destination array
    source_dest_array = Array([source_page.obj, Name.XYZ, 0, 800, 1])

    # Create a real named destination
    source_dests_dict = Dictionary(MyDest=source_dest_array)

    # --- 3. Create Target Page (to simulate output) ---
    pdf.add_blank_page(page_size=(1200, 1600))  # 2x scale
    target_page = pdf.pages[0]
    target_page_objgen = target_page.obj.objgen

    # --- 4. Mock Context Dictionaries ---
    instance_num = 1

    # Mock map: { (source_pdf_id, source_page_index, instance_num): target_page }
    page_map = {(id(source_pdf), 0, instance_num): target_page}

    # Mock reverse map: { source_pdf_id: { source_page_objgen: source_page_index } }
    rev_maps = {id(source_pdf): {source_page_objgen: 0}}

    # Mock destination cache: { source_pdf_id: { dest_name: dest_array } }
    dest_caches = {id(source_pdf): {"MyDest": source_dest_array}}

    # Mock PDF ID map: { source_pdf_id: input_index }
    pdf_to_input_index = {id(source_pdf): 0}

    # Mock transforms: { target_page_objgen: ((angle, rel), scale) }
    page_transforms = {target_page_objgen: ((90, False), 2.0)}

    # --- 5. Mock External Dependencies ---
    mock_transform_coords = mocker.patch(
        "pdftl.pages.link_remapper.transform_destination_coordinates"
    )
    # Simulate the 90-deg rotation and 2x scaling:
    # (x=0, y=800, z=1) -> (h-y, x) -> (1600-800, 0) -> (800, 0)
    # Scaled by 2.0 -> (1600, 0)
    mock_transform_coords.return_value = [1600.0, 0.0, 1]

    # --- 6. Initialize and Yield ---
    remapper_context = RemapperContext(
        page_map=page_map,
        rev_maps=rev_maps,
        dest_caches=dest_caches,
        pdf_to_input_index=pdf_to_input_index,
        page_transforms=page_transforms,
        include_instance=True,
        include_pdf_id=True,
    )
    remapper = LinkRemapper(remapper_context)
    remapper.set_call_context(pdf, source_pdf, instance_num)

    # Yield all the objects for tests to use
    yield {
        "remapper": remapper,
        "pdf": pdf,
        "source_pdf": source_pdf,
        "source_page": source_page,
        "target_page": target_page,
        "source_dest_array": source_dest_array,
        "mock_transform_coords": mock_transform_coords,
    }

    # --- 7. Teardown ---
    pdf.close()
    source_pdf.close()


# --- Test Cases ---


def test_copy_action_success(remapper_setup):
    """Tests that _copy_action successfully copies a dictionary."""
    remapper = remapper_setup["remapper"]
    # This dictionary does not belong to any PDF yet
    source_action = Dictionary(S=Name.URI, URI=String("http://example.com"))

    new_action = remapper._copy_action(source_action)

    assert new_action is not None
    assert new_action is not source_action
    assert new_action.S == Name.URI
    assert new_action.URI == String("http://example.com")
    assert new_action.is_owned_by(remapper.pdf)  # Now owned by the target PDF


def test_copy_action_fail(remapper_setup, caplog, mocker):
    """Tests that _copy_action logs a warning on ForeignObjectError."""
    caplog.set_level(logging.WARNING)

    remapper = remapper_setup["remapper"]

    mocker.patch.object(
        remapper.pdf,
        "copy_foreign",
        side_effect=ForeignObjectError("mocked copy error"),
    )

    source_action = Dictionary(S=Name.URI, URI=String("http://example.com"))

    new_action = remapper._copy_action(source_action)

    assert new_action is None
    assert "Failed to copy action object: mocked copy error" in caplog.text


@pytest.mark.parametrize(
    "include_instance, include_pdf_id, expected_name",
    [
        (False, False, "TestName"),
        (True, False, "1-TestName"),
        (True, True, "0-1-TestName"),
        (False, True, "0-1-TestName"),
    ],
)
def test_get_new_destination_name(remapper_setup, include_instance, include_pdf_id, expected_name):
    """Tests the logic for generating new unique destination names."""
    remapper = remapper_setup["remapper"]
    remapper.context.include_instance = include_instance
    remapper.context.include_pdf_id = include_pdf_id

    new_name = remapper._get_new_destination_name("TestName")
    assert new_name == expected_name


def test_find_remapped_page_success(remapper_setup):
    """Tests finding a valid, mapped page."""
    remapper = remapper_setup["remapper"]
    source_dest_array = remapper_setup["source_dest_array"]
    target_page = remapper_setup["target_page"]

    page = remapper._find_remapped_page(source_dest_array)
    assert page is target_page


@pytest.mark.parametrize(
    "bad_array",
    [
        None,
        Array([]),
    ],
)
def test_find_remapped_page_fail_bad_data(remapper_setup, bad_array):
    """Tests that invalid destination arrays return None."""
    remapper = remapper_setup["remapper"]
    assert remapper._find_remapped_page(bad_array) is None


def test_find_remapped_page_fail_not_in_map(remapper_setup):
    """Tests finding a page that is not in the rev_map."""
    remapper = remapper_setup["remapper"]

    # Create a new page in the source PDF that isn't in the maps
    remapper.source_pdf.add_blank_page()
    unmapped_page = remapper.source_pdf.pages[1]
    unmapped_dest_array = Array([unmapped_page.obj, Name.XYZ, 0, 0, 1])

    page = remapper._find_remapped_page(unmapped_dest_array)
    assert page is None


def test_transform_destination_array_xyz(remapper_setup):
    """Tests that an /XYZ destination is correctly transformed."""
    remapper = remapper_setup["remapper"]
    source_dest_array = remapper_setup["source_dest_array"]
    target_page = remapper_setup["target_page"]
    mock_transform_coords = remapper_setup["mock_transform_coords"]

    new_array = remapper._transform_destination_array(source_dest_array, target_page)

    expected_page_box = target_page.get(Name.CropBox, target_page.MediaBox)
    mock_transform_coords.assert_called_once_with([0, 800, 1], expected_page_box, 90, 2.0)

    assert new_array[0] == target_page.obj
    assert new_array[1] == Name.XYZ
    assert list(new_array)[2:] == [1600.0, 0.0, 1]


def test_transform_destination_array_fit(remapper_setup):
    """Tests that non-/XYZ destinations are not transformed."""
    remapper = remapper_setup["remapper"]
    target_page = remapper_setup["target_page"]
    mock_transform_coords = remapper_setup["mock_transform_coords"]

    fit_dest_array = Array([remapper_setup["source_page"].obj, Name.Fit])

    new_array = remapper._transform_destination_array(fit_dest_array, target_page)

    mock_transform_coords.assert_not_called()
    assert new_array[0] == target_page.obj
    assert new_array[1] == Name.Fit


def test_remap_explicit_destination_data(remapper_setup):
    """Tests the orchestrator for explicit destinations."""
    remapper = remapper_setup["remapper"]
    source_dest_array = remapper_setup["source_dest_array"]

    new_action_dest, new_named_dest = remapper._remap_explicit_destination_data(source_dest_array)

    assert new_named_dest is None
    assert isinstance(new_action_dest, Array)
    assert list(new_action_dest)[2:] == [1600.0, 0.0, 1]


def test_remap_named_destination_data_success(remapper_setup):
    """Tests the orchestrator for named destinations."""
    remapper = remapper_setup["remapper"]

    new_action_dest, new_named_dest = remapper._remap_named_destination_data(String("MyDest"))

    assert new_action_dest == String("0-1-MyDest")
    assert new_named_dest is not None

    new_name, dest_dict = new_named_dest
    assert new_name == String("0-1-MyDest")
    assert isinstance(dest_dict, Dictionary)
    assert "/D" in dest_dict

    transformed_array = dest_dict.D
    assert list(transformed_array)[2:] == [1600.0, 0.0, 1]


def test_remap_named_destination_data_fail_not_found(remapper_setup, caplog):
    """Tests a named destination that doesn't exist in the cache."""
    # Explicitly set the logging level for caplog to ensure
    # WARNING messages are captured.
    caplog.set_level(logging.WARNING)
    remapper = remapper_setup["remapper"]

    new_action_dest, new_named_dest = remapper._remap_named_destination_data(String("BadDest"))

    assert new_action_dest is None
    assert new_named_dest is None
    assert "Named destination 'BadDest' not found" in caplog.text


def test_remap_goto_action_named(remapper_setup):
    """Tests the top-level remap for a GoTo (Named) action."""
    remapper = remapper_setup["remapper"]
    action = Dictionary(S=Name.GoTo, D=String("MyDest"))

    new_action, new_named_dest = remapper.remap_goto_action(action)

    assert new_action.D == String("0-1-MyDest")
    assert new_named_dest is not None
    assert new_named_dest[0] == String("0-1-MyDest")


def test_remap_goto_action_explicit(remapper_setup):
    """Tests the top-level remap for a GoTo (Explicit) action."""
    remapper = remapper_setup["remapper"]
    action = Dictionary(S=Name.GoTo, D=remapper_setup["source_dest_array"])

    new_action, new_named_dest = remapper.remap_goto_action(action)

    assert new_named_dest is None
    assert isinstance(new_action.D, Array)
    assert list(new_action.D)[2:] == [1600.0, 0.0, 1]


def test_copy_self_contained_action(remapper_setup):
    """Tests that a URI action is copied correctly."""
    remapper = remapper_setup["remapper"]
    action = Dictionary(S=Name.URI, URI=String("http://example.com"))

    new_action, new_named_dest = remapper.copy_self_contained_action(action)

    assert new_named_dest is None
    assert new_action is not None
    assert new_action.URI == String("http://example.com")
    assert new_action.is_owned_by(remapper.pdf)


def test_copy_unsupported_action(remapper_setup, caplog):
    """Tests that a JavaScript action is copied, but with a warning."""
    caplog.set_level(logging.WARNING)
    remapper = remapper_setup["remapper"]
    action = Dictionary(S=Name.JavaScript, JS=String("alert('hi')"))

    new_action, new_named_dest = remapper.copy_unsupported_action(action)

    assert new_named_dest is None
    assert new_action is not None
    assert new_action.JS == String("alert('hi')")
    assert "Unsupported action type '/JavaScript' copied" in caplog.text


def test_transform_destination_array_no_transform(remapper_setup):
    """Tests that an /XYZ destination remains unchanged when no transform applies."""
    remapper = remapper_setup["remapper"]
    target_page = remapper_setup["target_page"]

    # Override transforms: no rotation, no scale
    remapper.context.page_transforms[target_page.obj.objgen] = ((0, False), 1.0)

    source_dest_array = remapper_setup["source_dest_array"]
    mock_transform_coords = remapper_setup["mock_transform_coords"]

    # Run transform
    new_array = remapper._transform_destination_array(source_dest_array, target_page)

    # Should not invoke transformation
    mock_transform_coords.assert_not_called()
    # The array should reference the new target page but coordinates should remain same
    assert list(new_array) == [target_page.obj, Name.XYZ, 0, 800, 1]


def test_remap_goto_action_named_fail(remapper_setup):
    """Tests that remap_goto_action returns None when destination not found."""
    remapper = remapper_setup["remapper"]
    bad_action = Dictionary(S=Name.GoTo, D=String("Nonexistent"))

    new_action, new_named_dest = remapper.remap_goto_action(bad_action)

    assert new_action is None
    assert new_named_dest is None


def test_remap_goto_action_invalid_destination_type(remapper_setup):
    """Tests that remap_goto_action safely ignores unsupported destination types."""
    remapper = remapper_setup["remapper"]
    action = Dictionary(S=Name.GoTo, D=42)  # Invalid type

    new_action, new_named_dest = remapper.remap_goto_action(action)

    assert new_action is None
    assert new_named_dest is None


def test_remap_explicit_destination_data_page_not_found(remapper_setup):
    """Tests that explicit destination remap returns None if page is unmapped."""
    remapper = remapper_setup["remapper"]

    # Create unmapped source array
    new_page = remapper.source_pdf.add_blank_page()
    unmapped_array = Array([new_page.obj, Name.XYZ, 10, 20, 1])

    new_action_dest, new_named_dest = remapper._remap_explicit_destination_data(unmapped_array)

    assert new_action_dest is None
    assert new_named_dest is None


def test_remap_named_destination_data_page_not_found(remapper_setup):
    """Tests named destination remap when target page is not found in page map."""
    remapper = remapper_setup["remapper"]

    # Temporarily remove the mapping so the lookup fails
    remapper.context.page_map.clear()

    new_action_dest, new_named_dest = remapper._remap_named_destination_data(String("MyDest"))

    assert new_action_dest is None
    assert new_named_dest is None


def test_copy_action_with_indirect_dict(remapper_setup):
    """Tests copying an already indirect action dictionary."""
    remapper = remapper_setup["remapper"]
    src_pdf = remapper.source_pdf

    # Make a dictionary indirect
    indirect_action = src_pdf.make_indirect(
        Dictionary(S=Name.URI, URI=String("http://example.com"))
    )

    new_action = remapper._copy_action(indirect_action)

    assert new_action is not None
    assert new_action.is_owned_by(remapper.pdf)


def test_copy_unsupported_action_no_S_key(remapper_setup, caplog):
    """Tests copy_unsupported_action when the action lacks an /S key."""
    caplog.set_level(logging.WARNING)
    remapper = remapper_setup["remapper"]
    action = Dictionary(JS=String("alert('no type')"))

    new_action, new_named_dest = remapper.copy_unsupported_action(action)

    assert new_action is not None
    assert "Unsupported action type 'Unknown' copied" in caplog.text
