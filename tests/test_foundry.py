"""Tests for the Warframe Foundry export builder."""

import json

from warframe_profile.model.foundry_export import (
    build_foundry_export,
    foundry_key,
    mastery_xp_threshold,
    steel_settings_from_xp,
)
from warframe_profile.model.inventory import ExportDB

_EXCALIBUR = "/Lotus/Powersuits/Excalibur/Excalibur"
_LATO = "/Lotus/Weapons/Tenno/LatoPrime"
_BARREL = "/Lotus/Weapons/Tenno/LatoPrimeBarrel"


def _db(tmp_path) -> ExportDB:
    data = {
        "items": [
            {
                "uniqueName": _EXCALIBUR,
                "name": "Excalibur",
                "category": "Warframes",
                "components": [],
            },
            {
                "uniqueName": _LATO,
                "name": "Lato Prime",
                "category": "Secondary",
                "components": [
                    {"uniqueName": _BARREL, "name": "Barrel", "itemCount": 1},
                ],
            },
        ],
        "recipes": {},
        "dict": {},
    }
    path = tmp_path / "export_db.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    db = ExportDB(str(path))
    db.load()
    return db


def test_foundry_key_uses_final_path_segment():
    assert foundry_key("/Lotus/Powersuits/Excalibur/Excalibur") == "Excalibur"
    assert foundry_key("/Lotus\\Weapons\\Tenno\\LatoPrime") == "LatoPrime"


def test_mastery_xp_threshold_by_category():
    assert mastery_xp_threshold(_EXCALIBUR, {"category": "Warframes"}) == 900_000
    assert mastery_xp_threshold(_LATO, {"category": "Secondary"}) == 450_000
    assert mastery_xp_threshold("/Lotus/Powersuits/EntratiMech/EntratiMech", None) == 1_600_000


def test_steel_settings_from_xp_splits_missions_and_junctions():
    assert steel_settings_from_xp(0) == {"steelMissionCount": 0, "steelJunctionCount": 0}
    assert steel_settings_from_xp(63 * 3 + 1000) == {
        "steelMissionCount": 3,
        "steelJunctionCount": 1,
    }


def test_build_foundry_export_marks_built_and_mastered(tmp_path):
    db = _db(tmp_path)
    inventory = {
        "Suits": [{"ItemType": _EXCALIBUR, "XP": 900_000}],
        "MiscItems": [{"ItemType": "/Lotus/Types/Items/Resources/OrokinCell", "ItemCount": 7}],
    }

    export = build_foundry_export(db, inventory)

    assert export["version"] == 2
    excalibur = export["items"]["data"]["Excalibur"]
    assert excalibur["built"] is True
    assert excalibur["mastered"] is True
    assert export["resources"]["OrokinCell"] == 7


def test_build_foundry_export_does_not_master_partially_ranked_items(tmp_path):
    db = _db(tmp_path)
    inventory = {"Suits": [{"ItemType": _EXCALIBUR, "XP": 400_000}]}

    export = build_foundry_export(db, inventory)

    excalibur = export["items"]["data"]["Excalibur"]
    assert excalibur["built"] is True
    assert "mastered" not in excalibur


def test_build_foundry_export_records_owned_components(tmp_path):
    db = _db(tmp_path)
    inventory = {"MiscItems": [{"ItemType": _BARREL, "ItemCount": 2}]}

    export = build_foundry_export(db, inventory)

    lato = export["items"]["data"]["LatoPrime"]
    # Counts are capped at the component's per-build quantity.
    assert lato["LatoPrimeBarrel"] == 1


def test_build_foundry_export_applies_and_clamps_settings(tmp_path):
    db = _db(tmp_path)

    export = build_foundry_export(db, {}, settings={"missionCount": 5, "junctionCount": 10**9})

    assert export["settings"]["missionCount"] == 5
    assert export["settings"]["junctionCount"] <= 15  # clamped to the Foundry limit
