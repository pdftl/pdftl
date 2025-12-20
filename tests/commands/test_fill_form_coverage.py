from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.commands.fill_form import fill_form
from pdftl.exceptions import UserCommandLineError


@pytest.fixture
def pdf():
    """Create a basic PDF with an initialized AcroForm."""
    p = pikepdf.new()
    p.add_blank_page()
    p.Root.AcroForm = pikepdf.Dictionary(
        Fields=pikepdf.Array(),
        DA=pikepdf.String("/Helv 0 Tf 0 g"),
        NeedAppearances=True,
    )
    return p


def test_fill_form_xfdf_fallback(pdf):
    """
    Test that if FDF parsing fails, it tries XFDF (which isn't implemented),
    and eventually raises UserCommandLineError.
    """
    with patch("pdftl.commands.fill_form._fill_form_from_fdf_data") as mock_fdf:
        mock_fdf.side_effect = ValueError("Not FDF")

        with patch("builtins.open", new_callable=MagicMock) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = b"JUNK"

            with pytest.raises(UserCommandLineError, match="Error encountered"):
                fill_form(pdf, ["dummy.xfdf"], lambda x: x)


def test_fill_form_recursion_kids(pdf, tmp_path):
    """Test FDF with nested 'Kids' (recursion logic)."""
    child = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Widget,
        FT=pikepdf.Name.Tx,
        T=pikepdf.String("Child"),
        V=pikepdf.String("OldValue"),
        Rect=[0, 0, 50, 50],
    )
    indirect_child = pdf.make_indirect(child)

    parent = pikepdf.Dictionary(
        T=pikepdf.String("Parent"), Kids=[indirect_child], FT=pikepdf.Name.Tx
    )
    indirect_parent = pdf.make_indirect(parent)
    indirect_child.Parent = indirect_parent

    pdf.Root.AcroForm.Fields.append(indirect_parent)
    pdf.pages[0].Annots = pdf.make_indirect([indirect_child])

    fdf = pikepdf.new()
    fdf_parent = pikepdf.Dictionary(
        T=pikepdf.String("Parent"),
        Kids=[pikepdf.Dictionary(T=pikepdf.String("Child"), V=pikepdf.String("NewValue"))],
    )
    fdf.Root.FDF = pikepdf.Dictionary(Fields=[fdf_parent])
    fdf_path = tmp_path / "kids.fdf"
    fdf.save(fdf_path)

    fill_form(pdf, [str(fdf_path)], None)

    updated_child = pdf.Root.AcroForm.Fields[0].Kids[0]
    assert str(updated_child.V) == "NewValue"


def test_fill_form_radio_button_index(pdf, tmp_path):
    """Test setting RadioButton value by index using a valid Group structure."""

    # Use pdf.make_stream() instead of pikepdf.Stream()
    opt1 = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Widget,
        AS=pikepdf.Name.Off,
        Rect=[10, 10, 20, 20],
        AP=pikepdf.Dictionary(
            N=pikepdf.Dictionary(ChoiceA=pdf.make_stream(b""), Off=pdf.make_stream(b""))
        ),
    )
    opt2 = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Widget,
        AS=pikepdf.Name.Off,
        Rect=[30, 10, 40, 20],
        AP=pikepdf.Dictionary(
            N=pikepdf.Dictionary(ChoiceB=pdf.make_stream(b""), Off=pdf.make_stream(b""))
        ),
    )
    ind_opt1 = pdf.make_indirect(opt1)
    ind_opt2 = pdf.make_indirect(opt2)

    radio_group = pikepdf.Dictionary(
        FT=pikepdf.Name.Btn,
        Ff=32768,
        T=pikepdf.String("MyRadio"),
        V=pikepdf.Name.Off,
        Opt=[pikepdf.String("ChoiceA"), pikepdf.String("ChoiceB")],
        Kids=[ind_opt1, ind_opt2],
    )
    ind_group = pdf.make_indirect(radio_group)

    ind_opt1.Parent = ind_group
    ind_opt2.Parent = ind_group

    pdf.Root.AcroForm.Fields.append(ind_group)
    pdf.pages[0].Annots = pdf.make_indirect([ind_opt1, ind_opt2])

    fdf = pikepdf.new()
    fdf.Root.FDF = pikepdf.Dictionary(
        Fields=[pikepdf.Dictionary(T=pikepdf.String("MyRadio"), V=pikepdf.String("ChoiceB"))]
    )
    fdf_path = tmp_path / "radio.fdf"
    fdf.save(fdf_path)

    fill_form(pdf, [str(fdf_path)], None)

    assert str(pdf.Root.AcroForm.Fields[0].V) == "/1"
