"""Layer operations on /OCProperties entries of the wrong type."""

import pikepdf
import pytest

from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.dump_layers import dump_layers
from pdftl.operations.modify_layer_configs import modify_layer_configs
from pdftl.operations.modify_layers import modify_layers


def _pdf():
    pdf = pikepdf.new()
    pdf.add_blank_page()
    a, b = (
        pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.OCG, Name=pikepdf.String(n)))
        for n in ("A", "B")
    )
    pdf.Root.OCProperties = pikepdf.Dictionary(
        OCGs=pikepdf.Array([a, b]),
        D=pikepdf.Dictionary(Order=pikepdf.Array([a, b])),
    )
    return pdf, a, b


def _config(name, **entries):
    return pikepdf.Dictionary(Name=pikepdf.String(name), **entries)


def _dump(pdf):
    return dump_layers(pdf).data


def _ids(*ocgs):
    return [o.objgen[0] for o in ocgs]


def test_lone_config_dictionary():
    pdf, a, b = _pdf()
    pdf.Root.OCProperties.Configs = _config("Solo", Locked=pikepdf.Array([a]))

    assert [c["name"] for c in _dump(pdf)["alternate_configs"]] == ["Solo"]

    modify_layer_configs(pdf, ["update", "Solo", "hide", "B"])
    modify_layer_configs(pdf, ["add", "Second"])
    configs = pdf.Root.OCProperties.Configs
    assert isinstance(configs, pikepdf.Array)
    assert [str(c.Name) for c in configs] == ["Solo", "Second"]
    assert _ids(*configs[0].OFF) == _ids(b)


def test_lone_config_dictionary_cleaned_by_strip():
    pdf, a, b = _pdf()
    pdf.Root.OCProperties.Configs = _config("Solo", Locked=pikepdf.Array([a, b]))
    modify_layers(pdf, ["strip", "A"])
    assert _ids(*pdf.Root.OCProperties.Configs.Locked) == _ids(b)


def test_junk_in_configs_array_is_skipped():
    pdf, a, _ = _pdf()
    pdf.Root.OCProperties.Configs = pikepdf.Array(
        [None, pikepdf.String("junk"), 3, _config("Real", OFF=pikepdf.Array([a]))]
    )

    assert [c["name"] for c in _dump(pdf)["alternate_configs"]] == ["Real"]
    modify_layers(pdf, ["strip", "A"])
    assert len(pdf.Root.OCProperties.Configs[3].OFF) == 0


@pytest.mark.parametrize("key", ["/ON", "/OFF", "/Locked"])
def test_lone_ocg_instead_of_array(key):
    pdf, a, _ = _pdf()
    pdf.Root.OCProperties.D[key] = a
    pdf.Root.OCProperties.Configs = pikepdf.Array([_config("C", **{key[1:]: a})])

    dump = _dump(pdf)
    list_key = {"/ON": "on_list_ids", "/OFF": "off_list_ids", "/Locked": "locked_list_ids"}[key]
    assert dump["default_config"][list_key] == _ids(a)
    assert dump["alternate_configs"][0][list_key] == _ids(a)

    modify_layer_configs(pdf, ["update", "C", "show", "B"])
    modify_layer_configs(pdf, ["add", "Copy"])
    modify_layers(pdf, ["hide", "B", "lock", "B"])
    assert isinstance(pdf.Root.OCProperties.D[key], pikepdf.Array)
    assert a.objgen[0] in _ids(*pdf.Root.OCProperties.D[key])


def test_scalars_in_state_arrays_are_skipped():
    pdf, a, _ = _pdf()
    pdf.Root.OCProperties.D.OFF = pikepdf.Array([None, 7, pikepdf.Name.X, a])
    pdf.Root.OCProperties.D.Locked = pikepdf.Array([None, a])

    dump = _dump(pdf)
    assert dump["default_config"]["off_list_ids"] == _ids(a)
    assert dump["default_config"]["locked_list_ids"] == _ids(a)
    assert dump["layers"][0]["default_state"] == "OFF"
    modify_layer_configs(pdf, ["add", "Snap"])
    assert _ids(*pdf.Root.OCProperties.Configs[0].OFF) == _ids(a)


def test_intent_as_single_name():
    pdf, a, _ = _pdf()
    a.Intent = pikepdf.Name.Design
    assert _dump(pdf)["layers"][0]["intent"] == ["Design"]


def test_auto_state_with_single_values():
    pdf, a, b = _pdf()
    a.Usage = pikepdf.Dictionary(Print=pikepdf.Dictionary(PrintState=pikepdf.Name.OFF))
    pdf.Root.OCProperties.D.AS = pikepdf.Array(
        [None, pikepdf.Dictionary(Event=pikepdf.Name.Print, Category=pikepdf.Name.Print, OCGs=a)]
    )

    assert _dump(pdf)["layers"][0]["usage"]["Print"]["active"] is True
    modify_layers(pdf, ["noprint", "B"])
    modify_layers(pdf, ["strip", "A"])
    assert _ids(*pdf.Root.OCProperties.D.AS[1].OCGs) == _ids(b)


def test_lone_auto_state_dictionary():
    pdf, a, _ = _pdf()
    a.Usage = pikepdf.Dictionary(Print=pikepdf.Dictionary(PrintState=pikepdf.Name.OFF))
    pdf.Root.OCProperties.D.AS = pikepdf.Dictionary(
        Event=pikepdf.Name.Print, Category=pikepdf.Array([pikepdf.Name.Print]), OCGs=[a]
    )

    assert _dump(pdf)["layers"][0]["usage"]["Print"]["active"] is True
    modify_layers(pdf, ["noprint", "B"])
    events = [str(d.Event) for d in pdf.Root.OCProperties.D.AS]
    assert sorted(events) == ["/Print", "/View"]


def test_junk_in_ocgs_array_is_skipped():
    pdf, a, b = _pdf()
    pdf.Root.OCProperties.OCGs = pikepdf.Array([None, a, 5, b])

    assert [layer["name"] for layer in _dump(pdf)["layers"]] == ["A", "B"]
    modify_layers(pdf, ["hide", "all", "noprint", "all"])
    assert _ids(*pdf.Root.OCProperties.D.OFF) == _ids(a, b)
    modify_layer_configs(pdf, ["add", "X", "hide", "A"])


def test_lone_ocg_dictionary_as_ocgs():
    pdf, a, _ = _pdf()
    pdf.Root.OCProperties.OCGs = a

    assert [layer["name"] for layer in _dump(pdf)["layers"]] == ["A"]
    modify_layers(pdf, ["hide", "A", "noprint", "A"])
    modify_layer_configs(pdf, ["add", "X", "show", "A"])
    assert isinstance(pdf.Root.OCProperties.OCGs, pikepdf.Array)


def test_default_config_not_a_dictionary():
    pdf, _, _ = _pdf()
    pdf.Root.OCProperties.D = pikepdf.String("junk")

    assert _dump(pdf)["default_config"] == {}
    modify_layers(pdf, ["hide", "A", "noprint", "A"])
    modify_layer_configs(pdf, ["add", "X", "hide", "A"])
    assert isinstance(pdf.Root.OCProperties.D, pikepdf.Dictionary)


def test_ocproperties_not_a_dictionary():
    pdf, _, _ = _pdf()
    pdf.Root.OCProperties = pikepdf.String("junk")

    assert _dump(pdf)["has_layers"] is False
    assert "No matching" in modify_layers(pdf, ["hide", "all"]).data
    with pytest.raises(InvalidArgumentError, match="no layers"):
        modify_layer_configs(pdf, ["add", "X"])


def test_usage_entries_not_dictionaries():
    pdf, a, b = _pdf()
    a.Usage = pikepdf.String("junk")
    b.Usage = pikepdf.Dictionary(Print=5, View=pikepdf.Name.X)

    assert _dump(pdf)["layers"][0]["usage"] is None
    modify_layers(pdf, ["noprint", "all", "noscreen", "all"])
    assert a.Usage.Print.PrintState == pikepdf.Name.OFF
    assert b.Usage.Print.PrintState == pikepdf.Name.OFF
    assert b.Usage.View.ViewState == pikepdf.Name.OFF


def test_create_layer_repairs_malformed_ocproperties():
    from pdftl.utils.ocg import create_layer

    pdf, a, _ = _pdf()
    pdf.Root.OCProperties.OCGs = a
    pdf.Root.OCProperties.D.Order = pikepdf.String("junk")
    ocg = create_layer(pdf, "New")
    ocprops = pdf.Root.OCProperties
    assert _ids(*ocprops.OCGs) == _ids(a, ocg)
    assert _ids(*ocprops.D.Order) == _ids(ocg)
    assert _ids(*ocprops.D.ON) == _ids(ocg)


def test_strip_cleans_locked_rbgroups_and_auto_state():
    pdf, a, b = _pdf()
    d_dict = pdf.Root.OCProperties.D
    d_dict.Locked = pikepdf.Array([a, b])
    d_dict.RBGroups = pikepdf.Array([pikepdf.Array([a, b]), pikepdf.Array([a])])
    d_dict.AS = pikepdf.Array(
        [pikepdf.Dictionary(Event=pikepdf.Name.View, OCGs=pikepdf.Array([a, b]))]
    )

    modify_layers(pdf, ["strip", "A"])
    assert _ids(*d_dict.Locked) == _ids(b)
    assert [_ids(*group) for group in d_dict.RBGroups] == [_ids(b)]
    assert _ids(*d_dict.AS[0].OCGs) == _ids(b)


def test_direct_ocg_is_skipped_by_modify_layers():
    pdf, a, b = _pdf()
    direct = pikepdf.Dictionary(Type=pikepdf.Name.OCG, Name=pikepdf.String("A"))
    pdf.Root.OCProperties.OCGs.append(direct)

    modify_layers(pdf, ["hide", "A"])
    assert _ids(*pdf.Root.OCProperties.D.OFF) == _ids(a)
