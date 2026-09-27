import pikepdf
import pytest

from pdftl.core.registry import registry
from pdftl.exceptions import InvalidArgumentError, MissingArgumentError
from pdftl.operations.dump_layers import dump_layers
from pdftl.operations.modify_layer_configs import modify_layer_configs


def _layered_pdf(names=("A", "B", "C"), **d_entries):
    pdf = pikepdf.new()
    pdf.add_blank_page()
    ocgs = [
        pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.OCG, Name=pikepdf.String(n)))
        for n in names
    ]
    d_dict = pikepdf.Dictionary(Order=pikepdf.Array(ocgs), **d_entries)
    pdf.Root.OCProperties = pikepdf.Dictionary(OCGs=pikepdf.Array(ocgs), D=d_dict)
    return pdf, ocgs


def _ids(arr):
    return [o.objgen[0] for o in arr]


def _names(arr):
    assert all(o.is_indirect for o in arr)
    return [str(o.Name) for o in arr]


def _config(pdf, name):
    matches = [c for c in pdf.Root.OCProperties.Configs if str(c.get("/Name", "")) == name]
    assert len(matches) == 1
    return matches[0]


def _reopen(pdf, tmp_path):
    path = tmp_path / "out.pdf"
    pdf.save(path)
    return pikepdf.open(path)


def test_registered():
    assert "modify_layer_configs" in registry.operations


def test_add_hide_all_show_some(tmp_path):
    pdf, _ = _layered_pdf()
    modify_layer_configs(pdf, ["add", "M&E View", "hide", "all", "show", "A", "show", "B"])

    reopened = _reopen(pdf, tmp_path)
    cfg = _config(reopened, "M&E View")
    assert cfg.BaseState == pikepdf.Name.OFF
    assert _names(cfg.ON) == ["A", "B"]
    assert "/OFF" not in cfg
    assert str(cfg.Creator) == "pdftl"
    assert "/Order" not in cfg


def test_add_without_actions_snapshots_default():
    pdf, (a, b, c) = _layered_pdf(OFF=pikepdf.Array())
    pdf.Root.OCProperties.D.OFF.append(b)
    pdf.Root.OCProperties.D.Locked = pikepdf.Array([c])

    modify_layer_configs(pdf, ["add", "Snap"])

    cfg = _config(pdf, "Snap")
    assert cfg.BaseState == pikepdf.Name.ON
    assert _ids(cfg.OFF) == [b.objgen[0]]
    assert _ids(cfg.Locked) == [c.objgen[0]]


def test_add_starts_from_default_state():
    pdf, (a, b, c) = _layered_pdf(OFF=pikepdf.Array())
    pdf.Root.OCProperties.D.OFF.append(b)

    modify_layer_configs(pdf, ["add", "X", "hide", "C"])

    assert _ids(_config(pdf, "X").OFF) == [b.objgen[0], c.objgen[0]]


def test_add_copies_auto_state_independently():
    pdf, (a, b, c) = _layered_pdf()
    as_entry = pikepdf.Dictionary(
        Event=pikepdf.Name.Print,
        Category=pikepdf.Array([pikepdf.Name.Print]),
        OCGs=pikepdf.Array([a]),
    )
    pdf.Root.OCProperties.D.AS = pikepdf.Array([as_entry])

    modify_layer_configs(pdf, ["add", "X"])
    pdf.Root.OCProperties.D.AS[0].OCGs.append(b)

    assert _ids(_config(pdf, "X").AS[0].OCGs) == [a.objgen[0]]


def test_add_lock():
    pdf, (a, b, c) = _layered_pdf()
    modify_layer_configs(pdf, ["add", "X", "lock", "all", "unlock", "B"])
    assert _ids(_config(pdf, "X").Locked) == [a.objgen[0], c.objgen[0]]


def test_add_by_id():
    pdf, (a, b, c) = _layered_pdf()
    modify_layer_configs(pdf, ["add", "X", "hide", f"id={b.objgen[0]}"])
    assert _ids(_config(pdf, "X").OFF) == [b.objgen[0]]


def test_add_duplicate_name_rejected():
    pdf, _ = _layered_pdf()
    modify_layer_configs(pdf, ["add", "X"])
    with pytest.raises(InvalidArgumentError, match="already exists"):
        modify_layer_configs(pdf, ["add", "X"])


@pytest.mark.parametrize("action", ["strip", "merge", "print", "noprint", "screen", "noscreen"])
def test_non_config_actions_rejected(action):
    pdf, _ = _layered_pdf()
    with pytest.raises(InvalidArgumentError, match="do not apply to configurations"):
        modify_layer_configs(pdf, ["add", "X", action, "A"])
    assert "/Configs" not in pdf.Root.OCProperties


def test_no_layers_rejected():
    pdf = pikepdf.new()
    with pytest.raises(InvalidArgumentError, match="no layers"):
        modify_layer_configs(pdf, ["add", "X"])
    assert "/OCProperties" not in pdf.Root


def test_missing_name_rejected():
    pdf, _ = _layered_pdf()
    with pytest.raises(MissingArgumentError):
        modify_layer_configs(pdf, ["add"])


def test_unknown_subcommand_rejected():
    pdf, _ = _layered_pdf()
    with pytest.raises(InvalidArgumentError, match="unknown subcommand"):
        modify_layer_configs(pdf, ["frobnicate", "X"])


def test_update_edits_existing_config():
    pdf, (a, b, c) = _layered_pdf()
    modify_layer_configs(pdf, ["add", "X", "hide", "all", "show", "A"])
    modify_layer_configs(pdf, ["update", "X", "show", "C", "hide", "A"])

    cfg = _config(pdf, "X")
    assert cfg.BaseState == pikepdf.Name.OFF
    assert _ids(cfg.ON) == [c.objgen[0]]


def test_update_preserves_unchanged_base_state():
    pdf, (a, b, c) = _layered_pdf()
    pdf.Root.OCProperties.Configs = pikepdf.Array(
        [
            pikepdf.Dictionary(
                Name=pikepdf.String("U"),
                BaseState=pikepdf.Name.Unchanged,
                ON=pikepdf.Array([a]),
            )
        ]
    )
    modify_layer_configs(pdf, ["update", "U", "hide", "B"])

    cfg = _config(pdf, "U")
    assert cfg.BaseState == pikepdf.Name.Unchanged
    assert _ids(cfg.ON) == [a.objgen[0]]
    assert _ids(cfg.OFF) == [b.objgen[0]]


def test_delete():
    pdf, _ = _layered_pdf()
    modify_layer_configs(pdf, ["add", "X"])
    modify_layer_configs(pdf, ["add", "Y"])
    modify_layer_configs(pdf, ["delete", "X"])
    assert [str(c.Name) for c in pdf.Root.OCProperties.Configs] == ["Y"]
    modify_layer_configs(pdf, ["delete", "Y"])
    assert "/Configs" not in pdf.Root.OCProperties


def test_delete_missing_rejected():
    pdf, _ = _layered_pdf()
    with pytest.raises(InvalidArgumentError, match="no configuration named"):
        modify_layer_configs(pdf, ["delete", "X"])


def test_delete_extra_args_rejected():
    pdf, _ = _layered_pdf()
    modify_layer_configs(pdf, ["add", "X"])
    with pytest.raises(InvalidArgumentError, match="extra arguments"):
        modify_layer_configs(pdf, ["delete", "X", "Y"])


def test_ambiguous_name_rejected():
    pdf, _ = _layered_pdf()
    dup = pikepdf.Dictionary(Name=pikepdf.String("Dup"))
    pdf.Root.OCProperties.Configs = pikepdf.Array([dup, pikepdf.Dictionary(Name="Dup")])
    with pytest.raises(InvalidArgumentError, match="2 configurations are named"):
        modify_layer_configs(pdf, ["delete", "Dup"])


def test_rename():
    pdf, _ = _layered_pdf()
    modify_layer_configs(pdf, ["add", "X"])
    modify_layer_configs(pdf, ["rename", "X", "Y"])
    assert [str(c.Name) for c in pdf.Root.OCProperties.Configs] == ["Y"]


def test_rename_onto_existing_rejected():
    pdf, _ = _layered_pdf()
    modify_layer_configs(pdf, ["add", "X"])
    modify_layer_configs(pdf, ["add", "Y"])
    with pytest.raises(InvalidArgumentError, match="already exists"):
        modify_layer_configs(pdf, ["rename", "X", "Y"])


def test_rename_needs_new_name():
    pdf, _ = _layered_pdf()
    modify_layer_configs(pdf, ["add", "X"])
    with pytest.raises(InvalidArgumentError, match="rename expects"):
        modify_layer_configs(pdf, ["rename", "X"])


def test_set_default_writes_base_state_on(tmp_path):
    pdf, _ = _layered_pdf()
    modify_layer_configs(pdf, ["add", "V", "hide", "all", "show", "B"])
    modify_layer_configs(pdf, ["set_default", "V"])

    reopened = _reopen(pdf, tmp_path)
    d_dict = reopened.Root.OCProperties.D
    assert d_dict.BaseState == pikepdf.Name.ON
    assert _names(d_dict.OFF) == ["A", "C"]
    assert "/ON" not in d_dict
    assert str(d_dict.Name) == "V"


def test_set_default_keeps_order_and_auto_state():
    pdf, (a, b, c) = _layered_pdf()
    as_array = pikepdf.Array(
        [pikepdf.Dictionary(Event=pikepdf.Name.View, OCGs=pikepdf.Array([a, b, c]))]
    )
    pdf.Root.OCProperties.D.AS = as_array
    modify_layer_configs(pdf, ["add", "V", "hide", "A"])
    del _config(pdf, "V")["/AS"]

    modify_layer_configs(pdf, ["set_default", "V"])

    d_dict = pdf.Root.OCProperties.D
    assert _ids(d_dict.Order) == [a.objgen[0], b.objgen[0], c.objgen[0]]
    assert _ids(d_dict.AS[0].OCGs) == [a.objgen[0], b.objgen[0], c.objgen[0]]


def test_set_default_uses_config_order_when_present():
    pdf, (a, b, c) = _layered_pdf()
    pdf.Root.OCProperties.Configs = pikepdf.Array(
        [pikepdf.Dictionary(Name=pikepdf.String("V"), Order=pikepdf.Array([c, a]))]
    )
    modify_layer_configs(pdf, ["set_default", "V"])
    assert _ids(pdf.Root.OCProperties.D.Order) == [c.objgen[0], a.objgen[0]]


def test_set_default_resolves_unchanged_against_old_default():
    pdf, (a, b, c) = _layered_pdf(OFF=pikepdf.Array())
    pdf.Root.OCProperties.D.OFF.append(a)
    pdf.Root.OCProperties.Configs = pikepdf.Array(
        [
            pikepdf.Dictionary(
                Name=pikepdf.String("U"),
                BaseState=pikepdf.Name.Unchanged,
                OFF=pikepdf.Array([b]),
            )
        ]
    )
    modify_layer_configs(pdf, ["set_default", "U"])

    d_dict = pdf.Root.OCProperties.D
    assert d_dict.BaseState == pikepdf.Name.ON
    assert _ids(d_dict.OFF) == [a.objgen[0], b.objgen[0]]


def test_set_default_takes_config_locks_only():
    pdf, (a, b, c) = _layered_pdf(Locked=pikepdf.Array())
    pdf.Root.OCProperties.D.Locked.append(a)
    pdf.Root.OCProperties.Configs = pikepdf.Array(
        [pikepdf.Dictionary(Name=pikepdf.String("V"), Locked=pikepdf.Array([c]))]
    )
    modify_layer_configs(pdf, ["set_default", "V"])
    assert _ids(pdf.Root.OCProperties.D.Locked) == [c.objgen[0]]


def test_set_default_saves_previous_default():
    pdf, (a, b, c) = _layered_pdf(OFF=pikepdf.Array())
    pdf.Root.OCProperties.D.OFF.append(c)
    modify_layer_configs(pdf, ["add", "V", "hide", "all"])
    modify_layer_configs(pdf, ["set_default", "V"])

    prev = _config(pdf, "Previous default")
    assert _ids(prev.OFF) == [c.objgen[0]]
    assert _ids(prev.Order) == [a.objgen[0], b.objgen[0], c.objgen[0]]
    assert list(prev.RBGroups) == []
    assert "/Name" not in pdf.Root.OCProperties.D or str(pdf.Root.OCProperties.D.Name) == "V"


def test_set_default_round_trip_restores_visibility():
    pdf, (a, b, c) = _layered_pdf(OFF=pikepdf.Array())
    pdf.Root.OCProperties.D.OFF.append(b)
    modify_layer_configs(pdf, ["add", "V", "hide", "all", "show", "C"])
    modify_layer_configs(pdf, ["set_default", "V"])
    modify_layer_configs(pdf, ["set_default", "Previous default"])

    d_dict = pdf.Root.OCProperties.D
    assert d_dict.BaseState == pikepdf.Name.ON
    assert _ids(d_dict.OFF) == [b.objgen[0]]
    names = [str(cfg.Name) for cfg in pdf.Root.OCProperties.Configs]
    assert names == ["V", "Previous default", "V (2)"]


def test_set_default_previous_name_avoids_clash():
    pdf, _ = _layered_pdf()
    modify_layer_configs(pdf, ["add", "Previous default"])
    modify_layer_configs(pdf, ["add", "V"])
    modify_layer_configs(pdf, ["set_default", "V"])
    _config(pdf, "Previous default (2)")


def test_set_default_with_indirect_default_config():
    pdf, (a, b, c) = _layered_pdf()
    ocprops = pdf.Root.OCProperties
    ocprops.D = pdf.make_indirect(ocprops.D)
    modify_layer_configs(pdf, ["add", "V", "hide", "B"])
    modify_layer_configs(pdf, ["set_default", "V"])
    assert _ids(ocprops.D.OFF) == [b.objgen[0]]
    assert "/OFF" not in _config(pdf, "Previous default")


def test_dump_layers_reports_locked_and_creator():
    pdf, (a, b, c) = _layered_pdf()
    modify_layer_configs(pdf, ["add", "X", "lock", "B"])
    (cfg,) = dump_layers(pdf).data["alternate_configs"]
    assert cfg["locked_list_ids"] == [b.objgen[0]]
    assert cfg["creator"] == "pdftl"


def test_set_default_pins_inherited_order_in_other_configs():
    pdf, (a, b, c) = _layered_pdf(RBGroups=pikepdf.Array())
    pdf.Root.OCProperties.D.RBGroups.append(pikepdf.Array([a, b]))
    pdf.Root.OCProperties.Configs = pikepdf.Array(
        [
            pikepdf.Dictionary(
                Name=pikepdf.String("V"), Order=pikepdf.Array([c]), RBGroups=pikepdf.Array()
            ),
            pikepdf.Dictionary(Name=pikepdf.String("Other")),
        ]
    )
    modify_layer_configs(pdf, ["set_default", "V"])

    other = _config(pdf, "Other")
    assert _ids(other.Order) == [a.objgen[0], b.objgen[0], c.objgen[0]]
    assert [_ids(g) for g in other.RBGroups] == [[a.objgen[0], b.objgen[0]]]


def test_set_default_leaves_inheritance_when_unchanged():
    pdf, _ = _layered_pdf()
    modify_layer_configs(pdf, ["add", "V", "hide", "A"])
    modify_layer_configs(pdf, ["add", "Other"])
    modify_layer_configs(pdf, ["set_default", "V"])
    assert "/Order" not in _config(pdf, "Other")


def test_non_dictionary_config_entries_skipped():
    pdf, (a, b, c) = _layered_pdf()
    pdf.Root.OCProperties.Configs = pikepdf.Array(
        [None, pikepdf.Dictionary(Name=pikepdf.String("A"))]
    )
    modify_layer_configs(pdf, ["update", "A", "hide", "B"])
    assert _ids(_config_at(pdf, 1).OFF) == [b.objgen[0]]
    modify_layer_configs(pdf, ["set_default", "A"])
    assert _ids(pdf.Root.OCProperties.D.OFF) == [b.objgen[0]]


def _config_at(pdf, index):
    return pdf.Root.OCProperties.Configs[index]


def test_update_config_shared_with_default_leaves_default_alone():
    pdf, _ = _layered_pdf()
    ocprops = pdf.Root.OCProperties
    ocprops.D = pdf.make_indirect(ocprops.D)
    ocprops.D.Name = pikepdf.String("Main")
    ocprops.Configs = pikepdf.Array([ocprops.D])

    modify_layer_configs(pdf, ["update", "Main", "hide", "all"])
    modify_layer_configs(pdf, ["rename", "Main", "Renamed"])

    assert "/BaseState" not in ocprops.D
    assert str(ocprops.D.Name) == "Main"
    assert _config(pdf, "Renamed").BaseState == pikepdf.Name.OFF


def test_ocg_in_both_on_and_off_counts_as_on_everywhere():
    pdf, (a, b, c) = _layered_pdf()
    pdf.Root.OCProperties.Configs = pikepdf.Array(
        [
            pikepdf.Dictionary(
                Name=pikepdf.String("V"), ON=pikepdf.Array([a]), OFF=pikepdf.Array([a])
            )
        ]
    )
    modify_layer_configs(pdf, ["update", "V", "lock", "C"])
    assert "/OFF" not in _config(pdf, "V")
    modify_layer_configs(pdf, ["set_default", "V"])
    assert "/OFF" not in pdf.Root.OCProperties.D


def test_direct_ocgs_ignored():
    pdf, (a, b, c) = _layered_pdf()
    direct = pikepdf.Dictionary(Type=pikepdf.Name.OCG, Name=pikepdf.String("D1"))
    pdf.Root.OCProperties.OCGs.append(direct)
    pdf.Root.OCProperties.OCGs.append(
        pikepdf.Dictionary(Type=pikepdf.Name.OCG, Name=pikepdf.String("D2"))
    )

    modify_layer_configs(pdf, ["add", "X", "hide", "D1", "lock", "all"])
    modify_layer_configs(pdf, ["add", "Y", "hide", "all", "show", "A"])
    modify_layer_configs(pdf, ["set_default", "Y"])

    cfg = _config(pdf, "X")
    assert "/OFF" not in cfg
    assert _ids(cfg.Locked) == [a.objgen[0], b.objgen[0], c.objgen[0]]
    assert _ids(pdf.Root.OCProperties.D.OFF) == [b.objgen[0], c.objgen[0]]


def test_errors_name_the_operation():
    pdf, _ = _layered_pdf()
    with pytest.raises(InvalidArgumentError, match="^modify_layer_configs: conflicting actions"):
        modify_layer_configs(pdf, ["add", "X", "show", "A", "hide", "A"])


@pytest.mark.parametrize("args", [["keep", "A"], ["keep"], ["hide", "KEEP"]])
def test_keep_rejected(args):
    pdf, _ = _layered_pdf()
    with pytest.raises(InvalidArgumentError, match="do not apply to configurations"):
        modify_layer_configs(pdf, ["add", "X", *args])
    assert "/Configs" not in pdf.Root.OCProperties


def test_set_default_drops_non_view_intent():
    pdf, (a, _, _) = _layered_pdf()
    pdf.Root.OCProperties.Configs = pikepdf.Array(
        [pikepdf.Dictionary(Name=pikepdf.String("V"), Intent=pikepdf.Name.Design)]
    )
    modify_layer_configs(pdf, ["set_default", "V"])
    assert "/Intent" not in pdf.Root.OCProperties.D
    assert _config(pdf, "V").Intent == pikepdf.Name.Design


def test_set_default_equal_indirect_order_is_not_pinned():
    pdf, (a, b, c) = _layered_pdf()
    ocprops = pdf.Root.OCProperties
    ocprops.D.Order = pdf.make_indirect(pikepdf.Array([a, b, c]))
    ocprops.Configs = pikepdf.Array(
        [
            pikepdf.Dictionary(Name=pikepdf.String("V"), Order=pikepdf.Array([a, b, c])),
            pikepdf.Dictionary(Name=pikepdf.String("Other")),
        ]
    )
    modify_layer_configs(pdf, ["set_default", "V"])
    assert "/Order" not in _config(pdf, "Other")


def test_set_default_pins_order_of_same_named_other_layer():
    pdf, (a, b) = _layered_pdf(names=("X", "X"))
    ocprops = pdf.Root.OCProperties
    ocprops.D.Order = pikepdf.Array([a])
    ocprops.Configs = pikepdf.Array(
        [
            pikepdf.Dictionary(Name=pikepdf.String("V"), Order=pikepdf.Array([b])),
            pikepdf.Dictionary(Name=pikepdf.String("Other")),
        ]
    )
    modify_layer_configs(pdf, ["set_default", "V"])
    assert _ids(_config(pdf, "Other").Order) == [a.objgen[0]]


def test_update_unlock_all():
    pdf, _ = _layered_pdf()
    modify_layer_configs(pdf, ["add", "X", "lock"])
    modify_layer_configs(pdf, ["update", "X", "unlock"])
    assert not list(_config(pdf, "X").get("/Locked", []))
