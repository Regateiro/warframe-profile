"""Tests for relic reward-name matching and needed drops."""

from warframe_profile.model.analysis import (
    _reward_names_for_part,
    build_needed_drops,
    build_owned,
    build_prime_map,
)
from warframe_profile.model.inventory import EQUIPMENT_SECTIONS

_VALKYR = "/Lotus/Powersuits/Berserker/ValkyrPrime"
_CHASSIS = "/Lotus/Types/Recipes/WarframeRecipes/ValkyrPrimeChassisComponent"
_RELIC = "/Lotus/Types/Game/Projections/T4VoidProjectionValkyrPrimeKey"


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
            "rewards": [{"item": {"name": "Valkyr Prime Chassis Blueprint"}}],
        },
    ]


def test_needed_drops_match_warframe_relic_rewards():
    items = _items()
    inventory = {"MiscItems": [{"ItemType": _RELIC, "ItemCount": 1}]}
    prime_map = build_prime_map(items)
    owned = build_owned(inventory)

    needed = build_needed_drops(items, inventory, prime_map, owned, EQUIPMENT_SECTIONS)

    assert len(needed) == 1
    drop = needed[0]
    assert drop.part_name == "Valkyr Prime Chassis Blueprint"
    assert drop.drops == [("Axi V10", 1, "Axi")]
