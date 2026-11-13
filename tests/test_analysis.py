"""Tests for Prime-map indexing and the core ducat analysis."""

from warframe_profile.model.analysis import analyze, build_prime_map
from warframe_profile.model.inventory import EQUIPMENT_SECTIONS

_LATO = "/Lotus/Weapons/Tenno/LatoPrime"
_BARREL = "/Lotus/Weapons/Tenno/LatoPrimeBarrel"
_RECEIVER = "/Lotus/Weapons/Tenno/LatoPrimeReceiver"


def _items() -> list[dict]:
    return [
        {
            "uniqueName": _LATO,
            "name": "Lato Prime",
            "category": "Secondary",
            "masteryReq": 4,
            "masterable": True,
            "components": [
                {
                    "uniqueName": _BARREL,
                    "name": "Barrel",
                    "itemCount": 1,
                    "tradable": True,
                    "primeSellingPrice": 15,
                },
                {
                    "uniqueName": _RECEIVER,
                    "name": "Receiver",
                    "itemCount": 1,
                    "tradable": True,
                    "primeSellingPrice": 25,
                },
            ],
        }
    ]


def _prime_map() -> dict:
    return build_prime_map(_items())


def test_build_prime_map_indexes_tradable_parts():
    prime_map = _prime_map()

    assert _LATO in prime_map
    info = prime_map[_LATO]
    assert info["name"] == "Lato Prime"
    assert info["category"] == "Secondary"
    assert {part["name"] for part in info["parts"]} == {"Barrel", "Receiver"}
    assert next(p for p in info["parts"] if p["name"] == "Barrel")["ducats"] == 15


def test_analyze_classifies_buildable_item_and_surplus():
    inventory = {
        "MiscItems": [
            {"ItemType": _BARREL, "ItemCount": 3},
            {"ItemType": _RECEIVER, "ItemCount": 1},
        ]
    }

    result = analyze(inventory, _prime_map(), EQUIPMENT_SECTIONS)

    assert result.missing == []
    assert len(result.items) == 1
    item = result.items[0]
    assert item.status == "buildable"
    assert item.needed_builds == 1
    barrel = next(part for part in item.parts if part.name == "Barrel")
    assert barrel.owned == 3
    assert barrel.surplus == 2
    assert barrel.missing == 0
    assert result.total_ducats_safe == 2 * 15


def test_analyze_reports_missing_prime_when_no_parts_owned():
    result = analyze({}, _prime_map(), EQUIPMENT_SECTIONS)

    assert result.items == []
    assert [missing.name for missing in result.missing] == ["Lato Prime"]
    assert result.missing[0].mastery_req == 4


def test_analyze_classifies_owned_copy():
    inventory = {"Pistols": [{"ItemType": _LATO}]}

    result = analyze(inventory, _prime_map(), EQUIPMENT_SECTIONS)

    item = result.items[0]
    assert item.status == "have_copy"
    assert item.has_copy is True
    assert item.needed_builds == 0


def test_analyze_classifies_partial_item():
    inventory = {"MiscItems": [{"ItemType": _BARREL, "ItemCount": 1}]}

    result = analyze(inventory, _prime_map(), EQUIPMENT_SECTIONS)

    item = result.items[0]
    assert item.status == "partial"
    assert item.missing_parts == 1
