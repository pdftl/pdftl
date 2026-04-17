# tests/operations/test_set_props.py

import pikepdf
import pytest

from pdftl.exceptions import OperationError
from pdftl.operations.set_props import set_props


@pytest.fixture
def blank_pdf():
    """Provides a 10-page blank PDF for testing page labels and open actions."""
    pdf = pikepdf.Pdf.new()
    for _ in range(10):
        pdf.add_blank_page()
    return pdf


def test_set_props_missing_args(blank_pdf):
    with pytest.raises(OperationError, match="requires at least one key=value pair"):
        set_props(blank_pdf, [])


def test_set_props_invalid_arg_format(blank_pdf):
    with pytest.raises(OperationError, match="Expected format: key=value"):
        set_props(blank_pdf, ["lang_en-US"])


def test_set_props_standard_properties(blank_pdf):
    op_args = [" LANG = fr-FR ", "layout=TwoColumnRight", "mode=UseOutlines"]
    res = set_props(blank_pdf, op_args)
    assert res.success is True
    assert str(blank_pdf.Root.Lang) == "fr-FR"
    assert str(blank_pdf.Root.PageLayout) == "/TwoColumnRight"
    assert str(blank_pdf.Root.PageMode) == "/UseOutlines"


def test_set_props_viewer_prefs_valid(blank_pdf):
    op_args = [
        "hide_toolbar=true",
        "hide_menubar=1",
        "display_title=yes",
        "fit_window=false",
        "center_window=0",
        "hide_windowui=no",
    ]
    set_props(blank_pdf, op_args)
    prefs = blank_pdf.Root.ViewerPreferences
    assert bool(prefs.HideToolbar) is True
    assert bool(prefs.HideMenubar) is True
    assert bool(prefs.DisplayDocTitle) is True
    assert bool(prefs.FitWindow) is False
    assert bool(prefs.CenterWindow) is False
    assert bool(prefs.HideWindowUI) is False


def test_set_props_viewer_prefs_existing(blank_pdf):
    blank_pdf.Root.ViewerPreferences = pikepdf.Dictionary(FitWindow=False)
    set_props(blank_pdf, ["center_window=true"])
    prefs = blank_pdf.Root.ViewerPreferences
    assert bool(prefs.FitWindow) is False
    assert bool(prefs.CenterWindow) is True


def test_set_props_viewer_prefs_invalid(blank_pdf):
    with pytest.raises(OperationError, match="Invalid boolean value for hide_toolbar: 'banana'"):
        set_props(blank_pdf, ["hide_toolbar=banana"])


def test_set_props_open_action_simple(blank_pdf):
    """Tests the simplified open_action pointing to just a page number."""
    set_props(blank_pdf, ["open_action=5"])

    action = blank_pdf.Root.OpenAction
    # Ensure it resolves to the 5th page object (index 4) and defaults to /XYZ with nulls
    assert action[0] == blank_pdf.pages[4].obj
    assert str(action[1]) == "/XYZ"
    assert action[2] is None
    assert action[3] is None
    assert action[4] is None


def test_set_props_open_action_complex(blank_pdf):
    """Tests an open_action with a complex view mapping, handling nulls and floats."""
    set_props(blank_pdf, ["open_action=2,XYZ,null,15.5,2"])

    action = blank_pdf.Root.OpenAction
    assert action[0] == blank_pdf.pages[1].obj
    assert str(action[1]) == "/XYZ"
    assert action[2] is None
    assert action[3] == 15.5
    assert action[4] == 2


def test_set_props_open_action_auto_pad(blank_pdf):
    """Tests that view types with missing arguments are automatically padded with nulls."""
    # FitR expects 4 coordinates (left, bottom, right, top). We'll only provide 1.
    set_props(blank_pdf, ["open_action=5,FitR,10"])

    action = blank_pdf.Root.OpenAction
    assert action[0] == blank_pdf.pages[4].obj
    assert str(action[1]) == "/FitR"
    assert action[2] == 10
    # The remaining 3 should be padded with nulls
    assert action[3] is None
    assert action[4] is None
    assert action[5] is None


def test_set_props_open_action_clear(blank_pdf):
    """Tests safely removing the open_action."""
    set_props(blank_pdf, ["open_action=1"])
    assert "/OpenAction" in blank_pdf.Root

    set_props(blank_pdf, ["open_action="])
    assert "/OpenAction" not in blank_pdf.Root


def test_set_props_open_action_errors(blank_pdf):
    """Tests various OpenAction failure modes."""
    # Out of bounds
    with pytest.raises(OperationError, match="out of bounds"):
        set_props(blank_pdf, ["open_action=99"])

    # Bad page number
    with pytest.raises(OperationError, match="must start with a valid page number"):
        set_props(blank_pdf, ["open_action=banana,XYZ"])

    # Bad argument cast
    with pytest.raises(OperationError, match="Invalid open_action argument: bad_zoom"):
        set_props(blank_pdf, ["open_action=1,XYZ,null,null,bad_zoom"])


def test_set_props_pagelabels_parsing(blank_pdf):
    op_args = ["pagelabels=1r,, 5(Chap )D, 8(A-)D8, 10(Cover) ,"]
    set_props(blank_pdf, op_args)

    nt = pikepdf.NumberTree(blank_pdf.Root.PageLabels)
    assert str(nt[0].S) == "/r"
    assert nt[0].St == 1
    assert "/P" not in nt[0]

    assert str(nt[4].P) == "Chap "
    assert str(nt[4].S) == "/D"
    assert nt[4].St == 1

    assert str(nt[7].P) == "A-"
    assert str(nt[7].S) == "/D"
    assert nt[7].St == 8

    assert str(nt[9].P) == "Cover"
    assert "/S" not in nt[9]
    assert "/St" not in nt[9]


def test_set_props_pagelabels_invalid_syntax(blank_pdf):
    with pytest.raises(OperationError, match="Invalid page label syntax: 'invalid_stuff'"):
        set_props(blank_pdf, ["pagelabels=1r,invalid_stuff,5D"])


def test_set_props_pagelabels_clear(blank_pdf):
    assert "/PageLabels" not in blank_pdf.Root
    set_props(blank_pdf, ["pagelabels="])
    assert "/PageLabels" not in blank_pdf.Root

    set_props(blank_pdf, ["pagelabels=1r"])
    assert "/PageLabels" in blank_pdf.Root

    set_props(blank_pdf, ["pagelabels=   "])
    assert "/PageLabels" not in blank_pdf.Root
