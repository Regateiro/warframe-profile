"""Tests for weapon upgrade-chain discovery and rendering."""

from warframe_profile.model.craft_model import build_weapon_chains
from warframe_profile.model.data import Inventory, ItemData
from warframe_profile.view.web.templates import render_weapon_chains

_BASE = "/Lotus/Weapons/Tenno/Braton"
_UPGRADE = "/Lotus/Weapons/Tenno/BratonVandal"
_BLUEPRINT = "/Lotus/Recipes/BratonVandalBlueprint"


def _state(inventory: dict | None = None):
    items = [
        {
            "uniqueName": _BASE,
            "name": "Braton",
            "productCategory": "LongGuns",
            "_source": "weapons",
        },
        {
            "uniqueName": _UPGRADE,
            "name": "Braton Vandal",
            "productCategory": "LongGuns",
            "_source": "weapons",
        },
    ]
    recipes = {
        _BLUEPRINT: {
            "resultType": _UPGRADE,
            "ingredients": [{"ItemType": _BASE, "ItemCount": 1}],
            "num": 1,
            "consumeOnUse": True,
        }
    }
    raw = {"items": items, "recipes": recipes, "dict": {}}
    item_data = ItemData.from_raw(raw)
    return item_data, Inventory(inventory or {}, item_data)


def _build(inventory: dict | None = None):
    item_data, state = _state(inventory)
    return build_weapon_chains(item_data, state)


def test_chain_is_returned_when_final_is_unowned():
    chains, _req, _craft, shown = _build()

    assert shown == 1
    assert len(chains) == 1
    assert chains[0] == [_BASE.lower(), _UPGRADE.lower()]


def test_chain_is_hidden_when_final_is_owned():
    chains, _req, _craft, shown = _build({"LongGuns": [{"ItemType": _UPGRADE}]})

    assert shown == 0
    assert chains == []


def test_chain_is_hidden_when_final_is_mastered_but_sold():
    # Regression: mastered-and-sold weapons must not reappear as suggestions.
    chains, _req, _craft, shown = _build({"XPInfo": [{"ItemType": _UPGRADE, "XP": 450000}]})

    assert shown == 0
    assert chains == []


def test_chain_is_kept_when_only_the_intermediate_is_owned():
    chains, _req, _craft, shown = _build({"LongGuns": [{"ItemType": _BASE}]})

    assert shown == 1
    assert chains[0][-1] == _UPGRADE.lower()


def test_web_view_renders_the_full_chain_path():
    item_data, inventory = _state()
    chains, req, craft, shown = build_weapon_chains(item_data, inventory)

    chain_views = [
        (
            [item_data.weapon_name(un) for un in chain],
            item_data.craft_plan(chain[-1], inventory, max_depth=5),
        )
        for chain in chains
    ]
    page = render_weapon_chains(chain_views, req, craft, shown)

    assert "Braton → Braton Vandal" in page
