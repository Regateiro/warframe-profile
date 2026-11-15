"""Tests for mastery classification and report sections."""

from types import SimpleNamespace

from warframe_profile.presenter.mastery import analyze_mastery, build_mastery_report

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
    "masterable": True,
    "components": [],
    "tags": [],
}


def _db(recipes: dict | None = None, items: list[dict] | None = None):
    recipes = recipes or {}
    items = items or [_ITEM]
    return SimpleNamespace(
        items=items,
        raw={"items": items, "recipes": recipes},
        recipes=recipes,
    )


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

    total, mastered, ready, owned_blueprints, missing, in_progress = analyze_mastery(
        _db(), inventory
    )

    assert total == 1
    assert mastered == 0
    assert ready == []
    assert owned_blueprints == []
    assert missing == []
    assert [item["name"] for item, _ in in_progress] == ["Cedo Prime"]


def test_unowned_item_without_blueprint_is_missing_blueprints():
    total, mastered, ready, owned_blueprints, missing, in_progress = analyze_mastery(_db(), {})

    assert total == 1
    assert mastered == 0
    assert ready == []
    assert in_progress == []
    assert owned_blueprints == []
    assert [item["name"] for item, _ in missing] == ["Cedo Prime"]


def test_unowned_item_with_blueprint_only_is_owned_blueprints():
    inventory = {"Recipes": [{"ItemType": _BLUEPRINT, "ItemCount": 1}]}

    _, _, ready, owned_blueprints, missing, _ = analyze_mastery(
        _db(_recipe(), [_ITEM, _MATERIAL_ITEM]), inventory
    )

    assert ready == []
    assert missing == []
    assert [item["name"] for item, _ in owned_blueprints] == ["Cedo Prime"]


def test_unowned_item_with_all_blueprints_and_materials_is_ready():
    inventory = {
        "Recipes": [{"ItemType": _BLUEPRINT, "ItemCount": 1}],
        "MiscItems": [{"ItemType": _MATERIAL, "ItemCount": 3}],
    }

    _, _, ready, owned_blueprints, missing, _ = analyze_mastery(
        _db(_recipe(), [_ITEM, _MATERIAL_ITEM]), inventory
    )

    assert [item["name"] for item, _ in ready] == ["Cedo Prime"]
    assert owned_blueprints == []
    assert missing == []


def test_report_sections_use_new_names_and_order():
    inventory = {"LongGuns": [{"ItemType": _ITEM["uniqueName"], "XP": 0}]}

    report = build_mastery_report(_db(), inventory)

    assert [section.header for section in report.sections] == [
        "UNMASTERED OWNED ITEMS",
        "READY TO BUILD",
        "OWNED BLUEPRINTS",
        "MISSING BLUEPRINTS",
    ]
    assert [row.name for row in report.sections[0].rows] == ["Cedo Prime"]


def test_loose_modular_component_counts_as_owned():
    db = SimpleNamespace(
        items=[_KDRIVE_ITEM],
        raw={"items": [_KDRIVE_ITEM], "recipes": {}},
        recipes={},
    )
    inventory = {"MiscItems": [{"ItemType": _KDRIVE_ITEM["uniqueName"], "ItemCount": 1}]}

    _, _, ready, owned_blueprints, missing, in_progress = analyze_mastery(db, inventory)

    assert ready == []
    assert missing == []
    assert owned_blueprints == []
    assert [item["name"] for item, _ in in_progress] == ["Bad Baby"]


def test_assembled_modular_parts_count_as_owned():
    db = SimpleNamespace(
        items=[_KDRIVE_ITEM],
        raw={"items": [_KDRIVE_ITEM], "recipes": {}},
        recipes={},
    )
    inventory = {
        "Hoverboards": [
            {
                "ItemType": "/Lotus/Types/Vehicles/Hoverboard/HoverboardSuit",
                "ModularParts": [_KDRIVE_ITEM["uniqueName"]],
            }
        ]
    }

    _, _, ready, owned_blueprints, missing, in_progress = analyze_mastery(db, inventory)

    assert ready == []
    assert missing == []
    assert owned_blueprints == []
    assert [item["name"] for item, _ in in_progress] == ["Bad Baby"]
