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


# --- Akimbo Prime weapons (finished Prime consumed as an ingredient) -------

_MAGNUS = "/Lotus/Weapons/Tenno/Pistols/PrimeMagnus/PrimeMagnusWeapon"
_MAG_BARREL = "/Lotus/Types/Recipes/Weapons/WeaponParts/MagnusPrimeBarrel"
_MAG_BP = "/Lotus/Types/Recipes/Weapons/MagnusPrimeBlueprint"
_MAG_RECEIVER = "/Lotus/Types/Recipes/Weapons/WeaponParts/MagnusPrimeReceiver"
_AKMAGNUS = "/Lotus/Weapons/Tenno/Akimbo/DualMagnusPrime"
_AK_BP = "/Lotus/Types/Recipes/Weapons/AkmagnusPrimeBlueprint"
_AK_LINK = "/Lotus/Types/Recipes/Weapons/WeaponParts/AkmagnusPrimeLink"


def _akimbo_prime_map() -> dict:
    items = [
        {
            "uniqueName": _MAGNUS,
            "name": "Magnus Prime",
            "category": "Secondary",
            "masterable": True,
            "components": [
                {
                    "uniqueName": _MAG_BARREL,
                    "name": "Magnus Prime Barrel",
                    "itemCount": 1,
                    "tradable": True,
                    "primeSellingPrice": 100,
                },
                {
                    "uniqueName": _MAG_BP,
                    "name": "Magnus Prime Blueprint",
                    "itemCount": 1,
                    "tradable": True,
                    "primeSellingPrice": 15,
                },
                {
                    "uniqueName": _MAG_RECEIVER,
                    "name": "Magnus Prime Receiver",
                    "itemCount": 1,
                    "tradable": True,
                    "primeSellingPrice": 45,
                },
            ],
        },
        {
            "uniqueName": _AKMAGNUS,
            "name": "Akmagnus Prime",
            "category": "Secondary",
            "masterable": True,
            "components": [
                {
                    "uniqueName": _AK_BP,
                    "name": "Akmagnus Prime Blueprint",
                    "itemCount": 1,
                    "tradable": True,
                    "primeSellingPrice": 15,
                },
                {
                    "uniqueName": _AK_LINK,
                    "name": "Akmagnus Prime Link",
                    "itemCount": 1,
                    "tradable": True,
                    "primeSellingPrice": 45,
                },
                {
                    "uniqueName": _MAGNUS,
                    "name": "Magnus Prime",
                    "itemCount": 2,
                    "tradable": False,
                },
            ],
        },
    ]
    return build_prime_map(items)


def _akimbo_inventory(extra_misc: list[dict] | None = None, pistols: int = 1) -> dict:
    return {
        "MiscItems": [
            {"ItemType": _AK_BP, "ItemCount": 1},
            {"ItemType": _AK_LINK, "ItemCount": 1},
            *(extra_misc or []),
        ],
        "Pistols": [{"ItemType": _MAGNUS} for _ in range(pistols)],
    }


def test_akimbo_is_partial_when_second_base_weapon_cannot_be_built():
    # One built Magnus Prime + a spare blueprint, but no Barrel or Receiver.
    inventory = _akimbo_inventory([{"ItemType": _MAG_BP, "ItemCount": 1}])

    result = analyze(inventory, _akimbo_prime_map(), EQUIPMENT_SECTIONS)
    akmagnus = next(i for i in result.items if i.name == "Akmagnus Prime")

    assert akmagnus.status == "partial"
    assert akmagnus.can_build is False
    ingredient = next(p for p in akmagnus.parts if p.name == "Magnus Prime")
    assert (ingredient.owned, ingredient.needed, ingredient.missing) == (1, 2, 1)


def test_akimbo_is_buildable_when_spare_base_weapon_parts_are_owned():
    inventory = _akimbo_inventory(
        [
            {"ItemType": _MAG_BP, "ItemCount": 1},
            {"ItemType": _MAG_BARREL, "ItemCount": 1},
            {"ItemType": _MAG_RECEIVER, "ItemCount": 1},
        ]
    )

    result = analyze(inventory, _akimbo_prime_map(), EQUIPMENT_SECTIONS)
    akmagnus = next(i for i in result.items if i.name == "Akmagnus Prime")

    assert akmagnus.status == "buildable"
    assert akmagnus.missing_parts == 0


def test_akimbo_is_buildable_with_two_built_base_weapons():
    inventory = _akimbo_inventory(pistols=2)

    result = analyze(inventory, _akimbo_prime_map(), EQUIPMENT_SECTIONS)
    akmagnus = next(i for i in result.items if i.name == "Akmagnus Prime")

    assert akmagnus.status == "buildable"
    assert akmagnus.missing_parts == 0
