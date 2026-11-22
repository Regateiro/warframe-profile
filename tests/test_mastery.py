"""Tests for mastery classification and report sections."""

from types import SimpleNamespace

from warframe_profile.model.data import Inventory, ItemData
from warframe_profile.model.mastery import analyze_mastery, build_mastery_report

_ITEM = {
    "uniqueName": "/Lotus/Weapons/Tenno/LongGuns/PrimeCedo/PrimeCedoWeapon",
    "name": "Cedo Prime",
    "category": "Primary",
    "masterable": True,
    "components": [],
    "tags": [],
}
_BLUEPRINT = "/Lotus/Types/Recipes/Weapons/CedoPrimeBlueprint"
_MATERIAL = "/Lotus/Types/Items/MiscItems/OrokinCell"
_MATERIAL_ITEM = {
    "uniqueName": _MATERIAL,
    "name": "Orokin Cell",
    "category": "Resources",
    "masterable": False,
}

_KDRIVE_ITEM = {
    "uniqueName": (
        "/Lotus/Types/Vehicles/Hoverboard/HoverboardParts/PartComponents/"
        "HoverboardSolarisA/HoverboardSolarisADeck"
    ),
    "name": "Bad Baby",
    "category": "Misc",
    "type": "K-Drive Component",
    "masterable": True,
    "components": [],
    "tags": [],
}


def _db(recipes: dict | None = None, items: list[dict] | None = None) -> ItemData:
    recipes = recipes or {}
    items = items or [_ITEM]
    raw = {"items": items, "recipes": recipes, "dict": {}}
    return ItemData(SimpleNamespace(items=items, raw=raw, recipes=recipes, locale={}))


def _analyze(inventory: dict, recipes: dict | None = None, items: list[dict] | None = None):
    item_data = _db(recipes, items)
    return analyze_mastery(item_data, Inventory(inventory, item_data))


def _report(inventory: dict, recipes: dict | None = None, items: list[dict] | None = None):
    item_data = _db(recipes, items)
    return build_mastery_report(item_data, Inventory(inventory, item_data))


def _recipe() -> dict:
    return {
        _BLUEPRINT: {
            "resultType": _ITEM["uniqueName"],
            "ingredients": [{"ItemType": _MATERIAL, "ItemCount": 3}],
            "num": 1,
            "consumeOnUse": True,
        }
    }


def test_owned_zero_xp_item_is_unmastered_owned():
    inventory = {"LongGuns": [{"ItemType": _ITEM["uniqueName"], "XP": 0}]}

    total, mastered, ready, owned_blueprints, missing, in_progress = _analyze(inventory)

    assert total == 1
    assert mastered == 0
    assert ready == []
    assert owned_blueprints == []
    assert missing == []
    assert [item["name"] for item, _ in in_progress] == ["Cedo Prime"]


def test_unowned_item_without_blueprint_is_missing_blueprints():
    total, mastered, ready, owned_blueprints, missing, in_progress = _analyze({})

    assert total == 1
    assert mastered == 0
    assert ready == []
    assert in_progress == []
    assert owned_blueprints == []
    assert [item["name"] for item, _ in missing] == ["Cedo Prime"]


def test_unowned_item_with_blueprint_only_is_owned_blueprints():
    inventory = {"Recipes": [{"ItemType": _BLUEPRINT, "ItemCount": 1}]}

    _, _, ready, owned_blueprints, missing, _ = _analyze(
        inventory, _recipe(), [_ITEM, _MATERIAL_ITEM]
    )

    assert ready == []
    assert missing == []
    assert [item["name"] for item, _ in owned_blueprints] == ["Cedo Prime"]


def test_unowned_item_with_all_blueprints_and_materials_is_ready():
    inventory = {
        "Recipes": [{"ItemType": _BLUEPRINT, "ItemCount": 1}],
        "MiscItems": [{"ItemType": _MATERIAL, "ItemCount": 3}],
    }

    _, _, ready, owned_blueprints, missing, _ = _analyze(
        inventory, _recipe(), [_ITEM, _MATERIAL_ITEM]
    )

    assert [item["name"] for item, _ in ready] == ["Cedo Prime"]
    assert owned_blueprints == []
    assert missing == []


def test_report_sections_use_new_names_and_order():
    inventory = {"LongGuns": [{"ItemType": _ITEM["uniqueName"], "XP": 0}]}

    report = _report(inventory)

    assert [section.header for section in report.sections] == [
        "UNMASTERED OWNED ITEMS",
        "READY TO BUILD",
        "MISSING PARTS",
    ]
    assert [row.name for row in report.sections[0].rows] == ["Cedo Prime"]


def test_loose_modular_component_is_not_owned():
    inventory = {"MiscItems": [{"ItemType": _KDRIVE_ITEM["uniqueName"], "ItemCount": 1}]}

    _, _, ready, owned_blueprints, missing, in_progress = _analyze(inventory, items=[_KDRIVE_ITEM])

    assert ready == []
    assert in_progress == []
    assert owned_blueprints == []
    assert [item["name"] for item, _ in missing] == ["Bad Baby"]


def test_modular_component_with_blueprint_is_owned_blueprints():
    blueprint = f"{_KDRIVE_ITEM['uniqueName']}Blueprint"
    recipes = {
        blueprint: {
            "resultType": _KDRIVE_ITEM["uniqueName"],
            "ingredients": [],
            "num": 1,
            "consumeOnUse": True,
        }
    }
    inventory = {
        "MiscItems": [{"ItemType": _KDRIVE_ITEM["uniqueName"], "ItemCount": 1}],
        "Recipes": [{"ItemType": blueprint, "ItemCount": 1}],
    }

    _, _, ready, owned_blueprints, missing, in_progress = _analyze(
        inventory, recipes, [_KDRIVE_ITEM]
    )

    assert ready == []
    assert in_progress == []
    assert missing == []
    assert [item["name"] for item, _ in owned_blueprints] == ["Bad Baby"]


def test_assembled_modular_parts_count_as_owned():
    inventory = {
        "Hoverboards": [
            {
                "ItemType": "/Lotus/Types/Vehicles/Hoverboard/HoverboardSuit",
                "ModularParts": [_KDRIVE_ITEM["uniqueName"]],
            }
        ]
    }

    _, _, ready, owned_blueprints, missing, in_progress = _analyze(inventory, items=[_KDRIVE_ITEM])

    assert ready == []
    assert missing == []
    assert owned_blueprints == []
    assert [item["name"] for item, _ in in_progress] == ["Bad Baby"]
