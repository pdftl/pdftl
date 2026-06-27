# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/test_actions_filters.py

from unittest.mock import patch
import pikepdf
import pytest

import pdftl.core.constants as c
from pdftl.operations.dump_actions import (
    dump_actions,
    dump_actions_cli_hook,
    _values_equal,
    _annot_matches_filters,
    _has_page_specifier,
)
from pdftl.operations.delete_actions import delete_actions


@pytest.fixture
def actions_pdf():
    """Build an ISO-compliant comprehensive multi-page PDF with nested chains and AcroForm triggers."""
    pdf = pikepdf.new()
    page1 = pdf.add_blank_page()
    page2 = pdf.add_blank_page()

    # 1. Document OpenAction (with single /Next chain link)
    chain_link_open = pdf.make_indirect(
        pikepdf.Dictionary(
            S=pikepdf.Name.JavaScript, JS=pikepdf.String("console.log('chained_open');")
        )
    )
    pdf.Root.OpenAction = pdf.make_indirect(
        pikepdf.Dictionary(
            S=pikepdf.Name.JavaScript,
            JS=pikepdf.String("console.log('open');"),
            Next=chain_link_open,
        )
    )

    # 2. Document AA (with nested array of /Next chain actions)
    sub_chain1 = pdf.make_indirect(
        pikepdf.Dictionary(
            S=pikepdf.Name.JavaScript, JS=pikepdf.String("console.log('sub_chain1');")
        )
    )
    sub_chain2 = pdf.make_indirect(
        pikepdf.Dictionary(
            S=pikepdf.Name.JavaScript, JS=pikepdf.String("console.log('sub_chain2');")
        )
    )
    pdf.Root.AA = pdf.make_indirect(
        pikepdf.Dictionary(
            WC=pdf.make_indirect(
                pikepdf.Dictionary(
                    S=pikepdf.Name.JavaScript,
                    JS=pikepdf.String("console.log('wc');"),
                    Next=pdf.make_indirect(pikepdf.Array([sub_chain1, sub_chain2])),
                )
            )
        )
    )

    # 3. Page AA (Page 1)
    page1.AA = pdf.make_indirect(
        pikepdf.Dictionary(
            O=pdf.make_indirect(
                pikepdf.Dictionary(
                    S=pikepdf.Name.JavaScript, JS=pikepdf.String("console.log('page_open');")
                )
            )
        )
    )

    # 4. Page AA (Page 2)
    page2.AA = pdf.make_indirect(
        pikepdf.Dictionary(
            O=pdf.make_indirect(
                pikepdf.Dictionary(
                    S=pikepdf.Name.JavaScript, JS=pikepdf.String("console.log('page2_open');")
                )
            )
        )
    )

    # 5. Annotation Action & AA on Page 1
    annot = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Link,
        Rect=[0, 0, 10, 10],
        A=pdf.make_indirect(
            pikepdf.Dictionary(
                S=pikepdf.Name.JavaScript, JS=pikepdf.String("console.log('annot_a');")
            )
        ),
        AA=pdf.make_indirect(
            pikepdf.Dictionary(
                D=pdf.make_indirect(
                    pikepdf.Dictionary(
                        S=pikepdf.Name.JavaScript, JS=pikepdf.String("console.log('annot_aa');")
                    )
                )
            )
        ),
    )
    page1.Annots = pdf.make_indirect(pikepdf.Array([annot]))

    # 6. JavaScript Name Tree and Dests
    js_tree = pikepdf.NameTree.new(pdf)
    js_tree["func1"] = pdf.make_indirect(pikepdf.Dictionary(JS=pikepdf.String("function f() {}")))

    dests_tree = pikepdf.NameTree.new(pdf)
    dests_tree["mydest"] = pdf.make_indirect(pikepdf.Array([page1.obj, pikepdf.Name.Fit]))

    pdf.Root.Names = pdf.make_indirect(
        pikepdf.Dictionary(JavaScript=js_tree.obj, Dests=dests_tree.obj)
    )

    # 7. Outline Tree Actions with nested children
    outline_child = pdf.make_indirect(
        pikepdf.Dictionary(
            Title=pikepdf.String("Child Bookmark"),
            A=pdf.make_indirect(
                pikepdf.Dictionary(
                    S=pikepdf.Name.JavaScript, JS=pikepdf.String("console.log('child');")
                )
            ),
        )
    )
    outline_item2 = pdf.make_indirect(
        pikepdf.Dictionary(
            Title=pikepdf.String("Bookmark 2"),
            A=pdf.make_indirect(
                pikepdf.Dictionary(
                    S=pikepdf.Name.JavaScript, JS=pikepdf.String("console.log('outline2');")
                )
            ),
        )
    )
    outline_item = pdf.make_indirect(
        pikepdf.Dictionary(
            Title=pikepdf.String("Bookmark 1"),
            A=pdf.make_indirect(
                pikepdf.Dictionary(
                    S=pikepdf.Name.JavaScript, JS=pikepdf.String("console.log('outline1');")
                )
            ),
            First=outline_child,
            Next=outline_item2,
        )
    )
    pdf.Root.Outlines = pdf.make_indirect(
        pikepdf.Dictionary(First=outline_item, Last=outline_item2)
    )

    # 8. Interactive AcroForm fields (PDF 1.3+) trigger action triggers
    kid_field = pdf.make_indirect(
        pikepdf.Dictionary(
            T=pikepdf.String("KidField"),
            AA=pdf.make_indirect(
                pikepdf.Dictionary(
                    V=pdf.make_indirect(
                        pikepdf.Dictionary(
                            S=pikepdf.Name.JavaScript, JS=pikepdf.String("console.log('val_kid');")
                        )
                    )
                )
            ),
        )
    )
    form_field = pdf.make_indirect(
        pikepdf.Dictionary(
            T=pikepdf.String("MyTextField"),
            AA=pdf.make_indirect(
                pikepdf.Dictionary(
                    F=pdf.make_indirect(
                        pikepdf.Dictionary(
                            S=pikepdf.Name.JavaScript,
                            JS=pikepdf.String("console.log('fmt_field');"),
                        )
                    )
                )
            ),
            Kids=pdf.make_indirect(pikepdf.Array([kid_field])),
        )
    )
    pdf.Root.AcroForm = pdf.make_indirect(
        pikepdf.Dictionary(Fields=pdf.make_indirect(pikepdf.Array([form_field])))
    )

    return pdf


def test_dump_actions_all(actions_pdf):
    res = dump_actions(actions_pdf)
    assert res.success
    # Base Actions:
    # 1 open (+ 1 chain link) -> 2
    # 1 doc_aa WC (+ 2 sub chain links in array) -> 3
    # 2 page_aa -> 2
    # 2 annot_actions -> 2
    # 1 js_name -> 1
    # 3 outlines -> 3
    # 2 form fields triggers (MyTextField, KidField) -> 2
    # Total expected actions = 15 total items
    assert len(res.data) == 15


def test_dump_actions_filter_page(actions_pdf):
    res = dump_actions(actions_pdf, specs=["1/JavaScript"])
    assert res.success
    # 1 page_aa on page 1 + 2 annot_actions on page 1 = 3 items.
    assert len(res.data) == 3
    for entry in res.data:
        assert entry["Page"] == 1


def test_dump_actions_type_mismatch(actions_pdf):
    res = dump_actions(actions_pdf, specs=["/GoTo"])
    assert res.success
    assert len(res.data) == 0


def test_dump_actions_filter_value_selector(actions_pdf):
    res = dump_actions(actions_pdf, specs=["/JavaScript(JS=(console.log('wc');))"])
    assert res.success
    assert len(res.data) == 1
    assert "Document Additional Action" in res.data[0]["Location"]


def test_dump_actions_invalid_value_selector(actions_pdf):
    res = dump_actions(actions_pdf, specs=["/JavaScript(JS=[invalid)"])
    assert res.success
    assert len(res.data) == 0


def test_dump_actions_cli_hook(actions_pdf, tmp_path):
    res = dump_actions(actions_pdf)
    out_file = tmp_path / "dump.json"
    res.meta[c.META_OUTPUT_FILE] = str(out_file)
    dump_actions_cli_hook(res, None, None)
    assert out_file.exists()
    assert "actions" in out_file.read_text()


def test_delete_actions_all(actions_pdf):
    res = delete_actions(actions_pdf, specs=None)
    assert res.success
    dump_res = dump_actions(actions_pdf)
    assert len(dump_res.data) == 0


def test_delete_actions_selective(actions_pdf):
    res = delete_actions(actions_pdf, specs=["1/JavaScript"])
    assert res.success
    dump_res = dump_actions(actions_pdf)
    # 15 total - 3 page-1 actions deleted = 12 remaining
    assert len(dump_res.data) == 12


def test_delete_actions_chain_single(actions_pdf):
    # Selectively wipe only the chained OpenAction node ("chained_open")
    res = delete_actions(actions_pdf, specs=["/JavaScript(JS=(console.log('chained_open');))"])
    assert res.success
    dump_res = dump_actions(actions_pdf)
    # 15 total - 1 chain link deleted = 14 remaining
    assert len(dump_res.data) == 14
    # Ensure the parent OpenAction dictionary no longer has a /Next field
    assert "/Next" not in actions_pdf.Root.OpenAction


def test_delete_actions_chain_array(actions_pdf):
    # Selectively delete one of the nested chain array actions ("sub_chain1")
    res = delete_actions(actions_pdf, specs=["/JavaScript(JS=(console.log('sub_chain1');))"])
    assert res.success
    dump_res = dump_actions(actions_pdf)
    # 15 total - 1 link deleted = 14 remaining
    assert len(dump_res.data) == 14
    # Parent /Next array should now only contain 1 item ("sub_chain2")
    wc_act = actions_pdf.Root.AA["/WC"]
    assert len(wc_act["/Next"]) == 1


def test_delete_actions_wipes_names_completely():
    pdf = pikepdf.new()
    pdf.add_blank_page()
    js_tree = pikepdf.NameTree.new(pdf)
    js_tree["func"] = pdf.make_indirect(pikepdf.Dictionary(JS=pikepdf.String("function g() {}")))
    pdf.Root.Names = pdf.make_indirect(pikepdf.Dictionary(JavaScript=js_tree.obj))

    assert "/Names" in pdf.Root

    res = delete_actions(pdf, specs=None)
    assert res.success
    assert "/Names" not in pdf.Root


def test_delete_actions_no_names():
    """Trigger the 'no Root.Names' check on delete_actions execution."""
    pdf = pikepdf.new()
    pdf.add_blank_page()

    assert "/Names" not in pdf.Root
    res = delete_actions(pdf, specs=None)
    assert res.success
    assert "/Names" not in pdf.Root


def test_delete_chain_link_unsupported_parents():
    """Trigger early-return paths in _delete_chain_link when parent action lacks /Next or is invalid."""
    from pdftl.operations.delete_actions import _delete_chain_link

    pdf = pikepdf.new()
    pdf.add_blank_page()

    # 1. Parent Dictionary lacks "/Next"
    pdf.Root.OpenAction = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name.JavaScript))
    item_no_next = {
        "parent": {"type": "document_open", "parent": pdf.Root, "key": "/OpenAction"},
        "obj": pikepdf.Dictionary(S=pikepdf.Name.JavaScript),
    }
    _delete_chain_link(item_no_next)
    assert "/OpenAction" in pdf.Root

    # 2. Parent Action is not a Dictionary (e.g. String destination)
    pdf.Root.OpenAction = pikepdf.String("my_destination_bookmark")
    item_not_dict = {
        "parent": {"type": "document_open", "parent": pdf.Root, "key": "/OpenAction"},
        "obj": pikepdf.Dictionary(S=pikepdf.Name.JavaScript),
    }
    _delete_chain_link(item_not_dict)
    assert "/OpenAction" in pdf.Root


def test_values_equal_edge_cases():
    assert _values_equal([1, 2], [1, 2, 3]) is False
    assert _values_equal([1, 2], [1, 3]) is False
    assert _values_equal([1, 2], "not_a_list") is False
    assert _values_equal("not_a_list", [1, 2]) is False
    assert _values_equal("foo", "/bar") is False
    assert _values_equal("/foo", "foo") is True
    assert _values_equal(True, False) is False
    assert _values_equal("abc", 123) is False
    assert _values_equal("/JavaScript", "JavaScript") is True


def test_has_page_specifier():
    assert _has_page_specifier(None) is False
    assert _has_page_specifier("") is False
    assert _has_page_specifier("/JavaScript") is False
    assert _has_page_specifier("1/JavaScript") is True
    assert _has_page_specifier("1") is True
    assert _has_page_specifier("(JS=open)") is False
    assert _has_page_specifier("odd/JavaScript") is True


def test_annot_matches_filters_exceptions_and_missing():
    with patch("pdftl.operations.modify_annots._parse_value_to_python", side_effect=TypeError):
        assert _annot_matches_filters({}, [("JS", "val")]) is False
    with patch("pdftl.operations.modify_annots._parse_value_to_python", side_effect=KeyError):
        assert _annot_matches_filters({}, [("JS", "val")]) is False

    assert _annot_matches_filters({"/S": "/JavaScript"}, [("Nonexistent", "val")]) is False


def test_document_actions_edge_cases():
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.Root.OpenAction = pikepdf.String("destination_name")
    pdf.Root.AA = pikepdf.Dictionary()

    res = dump_actions(pdf)
    assert res.success
    assert len(res.data) == 0
