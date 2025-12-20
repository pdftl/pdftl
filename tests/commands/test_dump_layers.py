import json

import pikepdf

from pdftl.commands.dump_layers import dump_layers


def test_dump_layers_no_ocg(tmp_path):
    """Verify output when no layers exist."""
    pdf = pikepdf.new()
    output_file = tmp_path / "out.json"

    dump_layers(pdf, output_file=str(output_file))

    with open(output_file) as f:
        res = json.load(f)

    assert res["has_layers"] is False
    assert res["layers"] == []


def test_dump_layers_basic_hierarchy(tmp_path):
    """Verify a parent-child relationship is correctly parsed."""
    pdf = pikepdf.new()
    output_file = tmp_path / "hierarchy.json"

    # Create OCGs
    ocg1 = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.OCG, Name="Parent"))
    ocg2 = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.OCG, Name="Child"))

    # Setup OCProperties
    ocprops = pikepdf.Dictionary(
        OCGs=[ocg1, ocg2],
        D=pikepdf.Dictionary(
            Order=pikepdf.Array([ocg1, pikepdf.Array([ocg2])]),
            BaseState=pikepdf.Name.ON,
        ),
    )
    pdf.Root.OCProperties = ocprops

    dump_layers(pdf, output_file=str(output_file))

    with open(output_file) as f:
        res = json.load(f)

    assert res["has_layers"] is True
    parent_id = int(ocg1.objgen[0])
    child_id = int(ocg2.objgen[0])

    # Check tree: [ {parent}, [ {child} ] ]
    assert res["ui_hierarchy"][0]["obj_id"] == parent_id
    assert res["ui_hierarchy"][1][0]["obj_id"] == child_id
    assert res["ui_hierarchy"][1][0]["name"] == "Child"


def test_dump_layers_usage_cleaning(tmp_path):
    """Verify /Usage names like /PrintState become 'PrintState'."""
    pdf = pikepdf.new()
    output_file = tmp_path / "usage.json"

    ocg = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.OCG,
            Name="Layer",
            Usage=pikepdf.Dictionary(Print=pikepdf.Dictionary(PrintState=pikepdf.Name.OFF)),
        )
    )
    pdf.Root.OCProperties = pikepdf.Dictionary(OCGs=[ocg], D=pikepdf.Dictionary())

    dump_layers(pdf, output_file=str(output_file))

    with open(output_file) as f:
        res = json.load(f)

    usage = res["layers"][0]["usage"]
    assert "Print" in usage
    assert usage["Print"]["PrintState"] == "OFF"


def test_dump_layers_complex_features(tmp_path):
    """
    Targets:
    - Line 92-95: Labels and Strings in /Order
    - Line 116: Simple value in /Usage
    - Line 156: Alternate /Configs
    - Line 145 & 162: Top-level /Order and fallback logic
    """
    pdf = pikepdf.new()
    output_file = tmp_path / "complex.json"

    # 1. Create OCGs
    ocg1 = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.OCG,
            Name="Layer1",
            Usage=pikepdf.Dictionary(CreatorInfo="AppV1"),  # Line 116: Simple value
        )
    )

    # 2. Setup OCProperties with complex structure
    ocprops = pikepdf.Dictionary(
        OCGs=[ocg1],
        # Line 145: Top-level Order
        Order=pikepdf.Array([pikepdf.String("Section Label"), ocg1]),  # Line 92-93: Label
        D=pikepdf.Dictionary(BaseState=pikepdf.Name.ON),
        # Line 156: Alternate Configs
        Configs=pikepdf.Array([pikepdf.Dictionary(Name="AltView", BaseState=pikepdf.Name.OFF)]),
    )
    pdf.Root.OCProperties = ocprops

    dump_layers(pdf, output_file=str(output_file))

    with open(output_file) as f:
        res = json.load(f)

    # Verification
    assert res["ui_hierarchy"][0]["label"] == "Section Label"
    assert res["alternate_configs"][0]["name"] == "AltView"
    assert res["layers"][0]["usage"]["CreatorInfo"] == "AppV1"


def test_dump_layers_legacy_fallback(tmp_path):
    """Targets Line 162: Legacy top-level Order without a /D hierarchy."""
    pdf = pikepdf.new()
    output_file = tmp_path / "legacy.json"

    ocg1 = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.OCG, Name="Legacy"))

    # OCProperties with Order but NO Order inside D
    pdf.Root.OCProperties = pikepdf.Dictionary(
        OCGs=[ocg1],
        Order=pikepdf.Array([ocg1]),
        D=pikepdf.Dictionary(BaseState=pikepdf.Name.ON),  # No /Order here
    )

    dump_layers(pdf, output_file=str(output_file))

    with open(output_file) as f:
        res = json.load(f)

    assert "ui_hierarchy" in res
    assert res["ui_hierarchy"][0]["name"] == "Legacy"


def test_dump_layers_extreme_edge_cases(tmp_path):
    """
    Targets:
    - Line 95: Raw values (int) in /Order
    - Line 162: Legacy Order fallback when /D exists but is empty
    """
    pdf = pikepdf.new()
    output_file = tmp_path / "edge_cases.json"

    ocg1 = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.OCG, Name="Layer1"))

    # 1. Put an Integer in the Order array to hit Line 95
    # 2. Put Order in Root and an empty D to hit Line 162
    pdf.Root.OCProperties = pikepdf.Dictionary(
        OCGs=[ocg1],
        Order=pikepdf.Array([ocg1, 42]),  # 42 hits Line 95
        D=pikepdf.Dictionary(BaseState=pikepdf.Name.ON),  # No /Order here hits Line 162
    )

    dump_layers(pdf, output_file=str(output_file))

    with open(output_file) as f:
        res = json.load(f)

    # Verify Line 95: The integer 42 should be stringified
    assert "42" in res["ui_hierarchy"]
    # Verify Line 162: The hierarchy was still captured
    assert res["ui_hierarchy"][0]["name"] == "Layer1"
