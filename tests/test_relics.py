"""Tests for relic reward-name matching and needed drops."""

from types import SimpleNamespace

from warframe_profile.model.analysis import (
    build_needed_drops,
    build_relics_map,
)
from warframe_profile.model.data import Inventory, ItemData
from warframe_profile.model.relics import _reward_names_for_part

_VALKYR = "/Lotus/Powersuits/Berserker/ValkyrPrime"
_CHASSIS = "/Lotus/Types/Recipes/WarframeRecipes/ValkyrPrimeChassisComponent"
_RELIC = "/Lotus/Types/Game/Projections/T4VoidProjectionValkyrPrimeKey"

_NAUTILUS = "/Lotus/Types/Sentinels/SentinelPowersuits/NautilusPrimeSentinelPowerSuit"
_NAUTILUS_BP = "/Lotus/Types/Recipes/SentinelRecipes/NautilusPrimeSentinelBlueprint"
_NAUTILUS_CHASSIS = "/Lotus/Types/Recipes/SentinelRecipes/NautilusPrimeChassisComponent"
_NAUTILUS_CHASSIS_BP = "/Lotus/Types/Recipes/SentinelRecipes/NautilusPrimeChassisBlueprint"
_NAUTILUS_RELIC = "/Lotus/Types/Game/Projections/T4VoidProjectionNautilusPrimeKey"


def test_frame_component_reward_appends_blueprint():
    assert _reward_names_for_part("Valkyr Prime", "Valkyr Prime Neuroptics", "Warframes") == [
        "Valkyr Prime Neuroptics Blueprint"
    ]


def test_bare_frame_part_gets_parent_prefix_and_blueprint():
    assert _reward_names_for_part("Valkyr Prime", "Chassis", "Warframes") == [
        "Valkyr Prime Chassis Blueprint"
    ]


def test_frame_blueprint_is_not_double_suffixed():
    assert _reward_names_for_part("Valkyr Prime", "Valkyr Prime Blueprint", "Warframes") == [
        "Valkyr Prime Blueprint"
    ]


def test_archwing_prefix_is_stripped():
    assert _reward_names_for_part(
        "<ARCHWING> Odonata Prime",
        "<ARCHWING> Odonata Prime Blueprint",
        "Archwing",
    ) == ["Odonata Prime Blueprint"]


def test_weapon_part_reward_has_no_blueprint_suffix():
    assert _reward_names_for_part("Magnus Prime", "Magnus Prime Barrel", "Secondary") == [
        "Magnus Prime Barrel"
    ]


def _state(items: list[dict], inventory: dict, recipes: dict | None = None):
    raw = {"items": items, "recipes": recipes or {}, "dict": {}}
    db = SimpleNamespace(items=items, recipes=raw["recipes"], locale={}, raw=raw)
    item_data = ItemData(db)
    return item_data, Inventory(inventory, item_data)


def _items() -> list[dict]:
    return [
        {
            "uniqueName": _VALKYR,
            "name": "Valkyr Prime",
            "category": "Warframes",
            "masterable": True,
            "components": [
                {
                    "uniqueName": _CHASSIS,
                    "name": "Valkyr Prime Chassis",
                    "itemCount": 1,
                    "tradable": True,
                    "primeSellingPrice": 100,
                }
            ],
        },
        {
            "uniqueName": _RELIC,
            "name": "Axi V10 Intact",
            "category": "Relics",
            "rewards": [{"item": {"name": "Valkyr Prime Chassis Blueprint"}, "rarity": "Rare"}],
        },
    ]


def test_needed_drops_match_warframe_relic_rewards():
    items = _items()
    inventory = {"MiscItems": [{"ItemType": _RELIC, "ItemCount": 1}]}
    item_data, state = _state(items, inventory)

    needed = build_needed_drops(item_data, state)

    assert len(needed) == 1
    drop = needed[0]
    assert drop.part_name == "Valkyr Prime Chassis Blueprint"
    assert drop.drops == [("Axi V10", 1, "Axi", "Rare")]


def _nautilus_items() -> list[dict]:
    return [
        {
            "uniqueName": _NAUTILUS,
            "name": "Nautilus Prime",
            "category": "Sentinels",
            "masterable": True,
            "components": [
                {
                    "uniqueName": _NAUTILUS_CHASSIS,
                    "name": "Nautilus Prime Chassis",
                    "itemCount": 1,
                    "tradable": True,
                    "primeSellingPrice": 100,
                }
            ],
        },
        {
            "uniqueName": _NAUTILUS_RELIC,
            "name": "Axi N1 Intact",
            "category": "Relics",
            "rewards": [{"item": {"name": "Nautilus Prime Chassis"}, "rarity": "Rare"}],
        },
    ]


def test_pending_parent_item_covers_its_missing_parts():
    items = _nautilus_items()
    inventory = {
        "MiscItems": [{"ItemType": _NAUTILUS_RELIC, "ItemCount": 1}],
        "PendingRecipes": [{"ItemType": _NAUTILUS_BP}],
    }
    recipes = {_NAUTILUS_BP: {"resultType": _NAUTILUS}}
    item_data, state = _state(items, inventory, recipes)

    needed = build_needed_drops(item_data, state)
    relics = build_relics_map(item_data, state)

    assert needed == []
    assert relics[0].safe is True


def test_pending_component_covers_its_own_missing_part():
    items = _nautilus_items()
    inventory = {
        "MiscItems": [{"ItemType": _NAUTILUS_RELIC, "ItemCount": 1}],
        "PendingRecipes": [{"ItemType": _NAUTILUS_CHASSIS_BP}],
    }
    recipes = {_NAUTILUS_CHASSIS_BP: {"resultType": _NAUTILUS_CHASSIS}}
    item_data, state = _state(items, inventory, recipes)

    needed = build_needed_drops(item_data, state)
    relics = build_relics_map(item_data, state)

    assert needed == []
    assert relics[0].safe is True


def test_relic_rarity_is_derived_from_chance_grouping():
    items = [
        _items()[0],
        {
            "uniqueName": "/Lotus/Types/Game/Projections/T4VoidProjectionRarityTest",
            "name": "Axi R1 Intact",
            "category": "Relics",
            "rewards": [
                {
                    "item": {"name": "Valkyr Prime Chassis Blueprint"},
                    "chance": 25.33,
                    "rarity": "Uncommon",
                },
                {"item": {"name": "Filler A"}, "chance": 25.33, "rarity": "Uncommon"},
                {"item": {"name": "Filler B"}, "chance": 25.33, "rarity": "Uncommon"},
                {"item": {"name": "Filler C"}, "chance": 11, "rarity": "Uncommon"},
                {"item": {"name": "Filler D"}, "chance": 11, "rarity": "Uncommon"},
                {"item": {"name": "Filler E"}, "chance": 2, "rarity": "Rare"},
            ],
        },
    ]
    inventory = {
        "MiscItems": [
            {"ItemType": "/Lotus/Types/Game/Projections/T4VoidProjectionRarityTest", "ItemCount": 1}
        ]
    }
    item_data, state = _state(items, inventory)

    needed = build_needed_drops(item_data, state)

    assert needed[0].drops == [("Axi R1", 1, "Axi", "Common")]
