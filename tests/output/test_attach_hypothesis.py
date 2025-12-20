import pytest
from hypothesis import given
from hypothesis import strategies as st

# --- Import the exceptions it can raise ---
from pdftl.exceptions import MissingArgumentError

# --- Import the parser and its data class ---
from pdftl.output.attach import _parse_attach_specs_to_intent

# ---------------------------
# Reusable Strategies
# ---------------------------

# Strategy for filenames
st_filename = st.text(alphabet="abc._-", min_size=1, max_size=10).map(lambda s: f"{s}.pdf")

st_file_list = st.lists(st_filename, min_size=1, max_size=5)

# Strategy for page specs
st_page_spec = st.one_of(st.just("1"), st.just("even"), st.just("1-end"))

# Strategy for relations
st_relation = st.one_of(st.just("Source"), st.just("Data"), st.just("alternative"))

# ---------------------------
# "Positive" Tests
# ---------------------------


@given(files=st_file_list)
def test_parser_files_only(files):
    """Tests that a list of just files produces document-level attachments."""
    attachments = _parse_attach_specs_to_intent(files)

    assert len(attachments) == len(files)
    assert all(att.page_spec is None for att in attachments)
    assert all(att.relationship is None for att in attachments)
    assert [att.path for att in attachments] == files


@given(files=st_file_list, page_spec=st_page_spec)
def test_parser_to_page(files, page_spec):
    """Tests [files...] 'to_page' <spec>"""
    args = files + ["to_page", page_spec]
    attachments = _parse_attach_specs_to_intent(args)

    assert len(attachments) == len(files)
    assert all(att.page_spec == page_spec for att in attachments)
    assert all(att.relationship is None for att in attachments)
    assert [att.path for att in attachments] == files


@given(files=st_file_list, relation=st_relation)
def test_parser_relation(files, relation):
    """Tests [files...] 'relation' <type>"""
    args = files + ["relation", relation]
    attachments = _parse_attach_specs_to_intent(args)

    assert len(attachments) == len(files)

    # Assert that ALL attachments get the relationship
    assert all(att.page_spec is None for att in attachments)
    assert all(att.relationship == relation.capitalize() for att in attachments)
    assert [att.path for att in attachments] == files


@given(
    files1=st_file_list,
    page_spec=st_page_spec,
    files2=st_file_list,
    relation=st_relation,
)
def test_parser_mixed_chain(files1, page_spec, files2, relation):
    """
    Tests a complex chain:
    [files1] 'to_page' <spec> [files2] 'relation' <type>
    """
    args = files1 + ["to_page", page_spec] + files2 + ["relation", relation]
    attachments = _parse_attach_specs_to_intent(args)

    assert len(attachments) == len(files1) + len(files2)

    # Check first chunk (files1)
    atts1 = attachments[: len(files1)]
    assert all(att.page_spec == page_spec for att in atts1)

    # Check second chunk (files2)
    atts2 = attachments[len(files1) :]

    # Assert that ALL attachments in the second chunk get the relation
    assert all(att.page_spec is None for att in atts2)
    assert all(att.relationship == relation.capitalize() for att in atts2)


# ---------------------------
# "Negative" Tests (Asserting Errors)
# ---------------------------


@given(files=st_file_list)
def test_parser_raises_missing_arg_to_page(files):
    """Tests [files...] 'to_page' -> Error"""
    args = files + ["to_page"]
    with pytest.raises(MissingArgumentError, match="Missing argument after 'to_page'"):
        _parse_attach_specs_to_intent(args)


@given(files=st_file_list)
def test_parser_raises_missing_arg_relation(files):
    """Tests [files...] 'relation' -> Error"""
    args = files + ["relation"]
    with pytest.raises(MissingArgumentError, match="Missing argument after 'relation'"):
        _parse_attach_specs_to_intent(args)


@given(page_spec=st_page_spec)
def test_parser_raises_missing_file_to_page(page_spec):
    """Tests 'to_page' <spec> -> Error"""
    args = ["to_page", page_spec]
    with pytest.raises(MissingArgumentError, match="Missing filename before 'to_page'"):
        _parse_attach_specs_to_intent(args)


@given(relation=st_relation)
def test_parser_raises_missing_file_relation(relation):
    """Tests 'relation' <type> -> Error"""
    args = ["relation", relation]
    with pytest.raises(MissingArgumentError, match="Missing filename before 'relation'"):
        _parse_attach_specs_to_intent(args)


def test_parser_handles_empty_list():
    """Tests that an empty list returns an empty list."""
    assert _parse_attach_specs_to_intent([]) == []
