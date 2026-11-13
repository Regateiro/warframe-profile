"""Tests for inventory aggregation helpers."""

from warframe_profile.model.inventory import (
    build_mastered_set,
    build_owned,
    inventory_summary,
    parse_pending_recipes,
)
from warframe_profile.model.utils import normalize_path


def test_build_owned_aggregates_every_section():
    inventory = {
        "MiscItems": [{"ItemType": "/Lotus/Types/Items/Resources/Ferrite", "ItemCount": 5}],
        "Recipes": [{"ItemType": "/Lotus/Weapons/Tenno/LatoPrimeBlueprint", "ItemCount": 2}],
        "Suits": [{"ItemType": "/Lotus/Powersuits/Excalibur/Excalibur"}],
        "LongGuns": [{"ItemType": "/Lotus/Weapons/Tenno/Braton"}],
    }

    owned = build_owned(inventory)

    assert owned[normalize_path("/Lotus/Types/Items/Resources/Ferrite")] == 5
    assert owned[normalize_path("/Lotus/Weapons/Tenno/LatoPrimeBlueprint")] == 2
    assert owned[normalize_path("/Lotus/Powersuits/Excalibur/Excalibur")] == 1
    assert owned[normalize_path("/Lotus/Weapons/Tenno/Braton")] == 1


def test_build_owned_includes_pending_recipes():
    inventory = {"PendingRecipes": [{"ItemType": "/Lotus/Weapons/Tenno/KeratinosBladeBlueprint"}]}

    owned = build_owned(inventory)

    assert owned[normalize_path("/Lotus/Weapons/Tenno/KeratinosBlade")] == 1


def test_build_mastered_set_normalises_windows_paths():
    inventory = {
        "XPInfo": [{"ItemType": "/Lotus\\Weapons\\Tenno\\LatoPrime", "XP": 0}],
        "LongGuns": [{"ItemType": "/Lotus/Weapons/Tenno/Braton", "XP": 100}],
    }

    mastered = build_mastered_set(inventory)

    assert normalize_path("/Lotus/Weapons/Tenno/LatoPrime") in mastered
    assert normalize_path("/Lotus/Weapons/Tenno/Braton") in mastered


def test_build_mastered_set_ignores_zero_xp_equipment():
    inventory = {"LongGuns": [{"ItemType": "/Lotus/Weapons/Tenno/Braton", "XP": 0}]}

    assert build_mastered_set(inventory) == set()


def test_parse_pending_recipes_uses_recipe_result_type():
    inventory = {"PendingRecipes": [{"ItemType": "/Lotus/Recipes/Weapons/XBlueprint"}]}
    recipes = {"/Lotus/Recipes/Weapons/XBlueprint": {"resultType": "/Lotus/Weapons/Tenno/X"}}

    pending = parse_pending_recipes(inventory, recipes)

    assert pending[normalize_path("/Lotus/Weapons/Tenno/X")] == 1


def test_parse_pending_recipes_falls_back_to_suffix_stripping():
    inventory = {"PendingRecipes": [{"ItemType": "/Lotus/Weapons/Tenno/BladeBlueprint"}]}

    pending = parse_pending_recipes(inventory)

    assert pending[normalize_path("/Lotus/Weapons/Tenno/Blade")] == 1


def test_inventory_summary_counts_sections():
    inventory = {
        "MiscItems": [{}, {}],
        "Recipes": [{}],
        "Suits": [{}],
        "Pistols": [{}, {}],
    }

    assert inventory_summary(inventory) == (2, 1, 3)
