from unittest.mock import patch

import pikepdf
import pytest

from pdftl.operations.mutate_content import mutate_content

# A simple script that inserts a '0 w' op (set line width 0) at start of stream
SCRIPT_CONTENT = """
import pikepdf

def mutate(instructions, context):
    # Insert '0 w' at the beginning
    instructions.insert(0, ([0], pikepdf.Operator("w")))
    
    # Check for test args
    if "trigger_error" in context["args"]:
        raise ValueError("Triggered Error")
        
    return instructions

def custom_entry(instructions, context):
    # Different mutation for custom entry point test
    # Insert '1 J' (round cap)
    instructions.insert(0, ([1], pikepdf.Operator("J")))
    return instructions
"""


@pytest.fixture
def mutation_script(tmp_path):
    """Creates a temporary python script for mutation."""
    p = tmp_path / "mutator.py"
    p.write_text(SCRIPT_CONTENT, encoding="utf-8")
    return p


@pytest.fixture
def simple_pdf():
    """Creates a minimal in-memory PDF with one page and content."""
    pdf = pikepdf.new()
    pdf.add_blank_page()
    # Add dummy content: 1 0 0 1 0 0 cm
    stream = b"1 0 0 1 0 0 cm"
    pdf.pages[0].Contents = pdf.make_stream(stream)
    return pdf


def test_mutate_content_basic(simple_pdf, mutation_script):
    """Covers lines 48-110: Basic execution flow."""
    args = [str(mutation_script)]

    mutate_content(simple_pdf, args)

    # Verify mutation: content stream should now start with '0 w'
    cmds = pikepdf.parse_content_stream(simple_pdf.pages[0])
    assert cmds[0][1] == pikepdf.Operator("w")


def test_mutate_content_custom_entry_point(simple_pdf, mutation_script):
    """Covers line 56-59: parsing script.py::func syntax."""
    args = [f"{mutation_script}::custom_entry"]

    mutate_content(simple_pdf, args)

    # Verify custom mutation: content stream should start with '1 J'
    cmds = pikepdf.parse_content_stream(simple_pdf.pages[0])
    assert cmds[0][1] == pikepdf.Operator("J")


def test_mutate_content_colon_regression(simple_pdf, mutation_script):
    """
    Regression test for Windows path handling (e.g. C:\\path vs script:func).

    Verifies that the operation strictly uses '::' as the separator.
    If the code regresses to splitting on ':', passing 'script.py:custom_entry'
    would mistakenly succeed (loading the script and calling the function).

    Correct behavior: treat 'script.py:custom_entry' as a literal filename,
    which does not exist, raising FileNotFoundError.
    """
    # Construct an argument using the old syntax "file:func"
    # mutation_script is a valid file, custom_entry is a valid function inside it.
    ambiguous_arg = f"{mutation_script}:custom_entry"

    # 1. If logic splits on ':' (Bug/Regression), it runs custom_entry -> Success.
    # 2. If logic requires '::' (Fix), it looks for file "path/mutator.py:custom_entry" -> fails.
    with pytest.raises(FileNotFoundError):
        mutate_content(simple_pdf, [ambiguous_arg])


def test_mutate_content_user_args(simple_pdf, mutation_script):
    """Covers passing extra args to context."""
    # Pass an arg that the script uses to raise an error
    args = [str(mutation_script), "trigger_error"]

    with pytest.raises(ValueError, match="Triggered Error"):
        mutate_content(simple_pdf, args)


def test_mutate_content_missing_file(simple_pdf):
    """Covers line 63-64: Script not found."""
    with pytest.raises(FileNotFoundError):
        mutate_content(simple_pdf, ["/path/to/nothing.py"])


def test_mutate_content_no_args(simple_pdf):
    """Covers line 49-50: Missing arguments."""
    with pytest.raises(ValueError, match="requires a script path"):
        mutate_content(simple_pdf, [])


def test_mutate_xobjects(simple_pdf, mutation_script):
    """
    Covers lines 113-137: Recursive processing of Form XObjects.
    """
    # 1. Create a Form XObject
    xobj_stream = b"0 g"  # Set gray color
    xobj = simple_pdf.make_stream(xobj_stream)
    xobj.Subtype = "/Form"
    xobj.Type = "/XObject"
    xobj.BBox = [0, 0, 100, 100]

    # 2. Attach it to the page resources
    page = simple_pdf.pages[0]
    page.Resources = pikepdf.Dictionary()
    page.Resources.XObject = pikepdf.Dictionary()
    page.Resources.XObject["/MyForm"] = xobj

    # 3. Run mutation
    mutate_content(simple_pdf, [str(mutation_script)])

    # 4. Verify XObject was mutated
    # The script adds '0 w' to the start
    cmds = pikepdf.parse_content_stream(xobj)
    assert cmds[0][1] == pikepdf.Operator("w")


def test_mutate_content_complex_xobjects_recursion_and_deduplication(simple_pdf, mutation_script):
    """
    Covers:
    - Line 118: Skipping already processed XObjects (deduplication).
    - Line 137: Recursively processing nested XObjects (recursion).
    """
    # 1. Create an Inner XObject
    # Original content: "1 w"
    xobj_inner = simple_pdf.make_stream(b"1 w")
    xobj_inner.Subtype = "/Form"
    xobj_inner.Type = "/XObject"
    xobj_inner.BBox = [0, 0, 10, 10]

    # 2. Create an Outer XObject that references the Inner one
    # Original content: "0 g"
    xobj_outer = simple_pdf.make_stream(b"0 g")
    xobj_outer.Subtype = "/Form"
    xobj_outer.Type = "/XObject"
    xobj_outer.BBox = [0, 0, 100, 100]

    # This triggers Line 137 (recursion) when xobj_outer is processed
    xobj_outer.Resources = pikepdf.Dictionary(
        {"/XObject": pikepdf.Dictionary({"/Inner": xobj_inner})}
    )

    # 3. Attach Outer to the Page, referencing it TWICE
    page = simple_pdf.pages[0]
    page.Resources = pikepdf.Dictionary(
        {
            "/XObject": pikepdf.Dictionary(
                {
                    "/Outer": xobj_outer,
                    # This triggers Line 118 (continue): referencing the same object again
                    "/OuterCopy": xobj_outer,
                }
            )
        }
    )

    # 4. Execute
    # The script inserts '0 w' at the start of every stream it processes
    mutate_content(simple_pdf, [str(mutation_script)])

    # 5. Verify Recursion (Line 137)
    # The inner XObject should have been found and mutated
    cmds_inner = pikepdf.parse_content_stream(xobj_inner)
    assert cmds_inner[0][1] == pikepdf.Operator("w"), "Inner XObject should be mutated"
    assert cmds_inner[0][0][0] == 0, "Should be '0 w' (inserted)"

    # 6. Verify Deduplication (Line 118)
    # If deduplication failed, xobj_outer would be processed twice.
    # Since the script inserts '0 w' at index 0, processing twice would result in '0 w 0 w ...'
    cmds_outer = pikepdf.parse_content_stream(xobj_outer)

    # We expect exactly one insertion of '0 w'
    # Index 0: '0 w' (Inserted)
    # Index 1: '0 g' (Original)
    assert len(cmds_outer) == 2

    assert list(cmds_outer[0].operands) == [0]
    assert cmds_outer[0].operator == pikepdf.Operator("w")
    assert list(cmds_outer[1].operands) == [0]
    assert cmds_outer[1].operator == pikepdf.Operator("g")


def test_mutate_content_load_errors(tmp_path):
    from unittest.mock import MagicMock

    import pytest

    from pdftl.operations.mutate_content import mutate_content

    script = tmp_path / "broken.py"
    script.write_text("def valid_func(instr, ctx): return instr")

    pdf = MagicMock()

    # Hit line 91: ImportError (Simulated by mocking spec_from_file_location to None)
    with patch("importlib.util.spec_from_file_location", return_value=None):
        with pytest.raises(ImportError, match="Could not load script module"):
            mutate_content(pdf, [str(script)])

    # Hit line 98: AttributeError (Function doesn't exist in script)
    # We specify a function name that isn't in the script ('missing_func')
    with pytest.raises(AttributeError, match="Function 'missing_func' not found"):
        mutate_content(pdf, [f"{script}::missing_func"])
