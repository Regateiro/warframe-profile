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


def _db(recipes: dict | None = None):
    recipes = recipes or {}
    return SimpleNamespace(items=[_ITEM], raw={"recipes": recipes}, recipes=recipes)


def test_owned_zero_xp_item_is_unmastered_owned():
    inventory = {"LongGuns": [{"ItemType": _ITEM["uniqueName"], "XP": 0}]}

    total, mastered, missing, in_progress, owned_blueprints = analyze_mastery(_db(), inventory)

    assert total == 1
    assert mastered == 0
    assert missing == []
    assert owned_blueprints == []
    assert [item["name"] for item, _ in in_progress] == ["Cedo Prime"]


def test_unowned_item_without_blueprint_is_missing_blueprints():
    total, mastered, missing, in_progress, owned_blueprints = analyze_mastery(_db(), {})

    assert total == 1
    assert mastered == 0
    assert in_progress == []
    assert owned_blueprints == []
    assert [item["name"] for item, _ in missing] == ["Cedo Prime"]


def test_unowned_item_with_blueprint_is_owned_blueprints():
    recipes = {_BLUEPRINT: {"resultType": _ITEM["uniqueName"], "ingredients": []}}
    inventory = {"Recipes": [{"ItemType": _BLUEPRINT, "ItemCount": 1}]}

    _, _, missing, in_progress, owned_blueprints = analyze_mastery(_db(recipes), inventory)

    assert missing == []
    assert in_progress == []
    assert [item["name"] for item, _ in owned_blueprints] == ["Cedo Prime"]


def test_report_sections_use_new_names_and_order():
    inventory = {"LongGuns": [{"ItemType": _ITEM["uniqueName"], "XP": 0}]}

    report = build_mastery_report(_db(), inventory)

    assert [section.header for section in report.sections] == [
        "UNMASTERED OWNED ITEMS",
        "OWNED BLUEPRINTS",
        "MISSING BLUEPRINTS",
    ]
    assert [row.name for row in report.sections[0].rows] == ["Cedo Prime"]


def test_loose_modular_component_is_not_an_owned_item():
    db = SimpleNamespace(items=[_KDRIVE_ITEM], raw={"recipes": {}}, recipes={})
    inventory = {"MiscItems": [{"ItemType": _KDRIVE_ITEM["uniqueName"], "ItemCount": 1}]}

    _, _, missing, in_progress, owned_blueprints = analyze_mastery(db, inventory)

    assert in_progress == []
    assert owned_blueprints == []
    assert [item["name"] for item, _ in missing] == ["Bad Baby"]


def test_assembled_modular_parts_count_as_owned():
    db = SimpleNamespace(items=[_KDRIVE_ITEM], raw={"recipes": {}}, recipes={})
    inventory = {
        "Hoverboards": [
            {
                "ItemType": "/Lotus/Types/Vehicles/Hoverboard/HoverboardSuit",
                "ModularParts": [_KDRIVE_ITEM["uniqueName"]],
            }
        ]
    }

    _, _, missing, in_progress, owned_blueprints = analyze_mastery(db, inventory)

    assert missing == []
    assert owned_blueprints == []
    assert [item["name"] for item, _ in in_progress] == ["Bad Baby"]
