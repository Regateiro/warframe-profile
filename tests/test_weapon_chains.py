"""Tests for weapon upgrade-chain discovery and rendering."""

from warframe_profile.model.craft_model import build_weapon_chains
from warframe_profile.model.utils import normalize_path
from warframe_profile.view.web.templates import render_weapon_chains

_BASE = "/Lotus/Weapons/Tenno/Braton"
_UPGRADE = "/Lotus/Weapons/Tenno/BratonVandal"
_BLUEPRINT = "/Lotus/Recipes/BratonVandalBlueprint"


def _items_by_un() -> dict:
    return {
        _BASE.lower(): {
            "uniqueName": _BASE,
            "name": "Braton",
            "productCategory": "LongGuns",
        },
        _UPGRADE.lower(): {
            "uniqueName": _UPGRADE,
            "name": "Braton Vandal",
            "productCategory": "LongGuns",
        },
    }


def _recipes_by_result() -> dict:
    return {
        _UPGRADE.lower(): {
            "_recipeKey": _BLUEPRINT,
            "ingredients": [{"ItemType": _BASE, "ItemCount": 1}],
            "num": 1,
            "consumeOnUse": True,
        }
    }


def _build(owned=None, mastered=None):
    return build_weapon_chains(
        _items_by_un(),
        _recipes_by_result(),
        owned or {},
        {},
        mastered=mastered or set(),
    )


def test_chain_is_returned_when_final_is_unowned():
    chains, _req, _craft, shown = _build()

    assert shown == 1
    assert len(chains) == 1
    assert chains[0] == [_BASE.lower(), _UPGRADE.lower()]


def test_chain_is_hidden_when_final_is_owned():
    chains, _req, _craft, shown = _build(owned={normalize_path(_UPGRADE): 1})

    assert shown == 0
    assert chains == []


def test_chain_is_hidden_when_final_is_mastered_but_sold():
    # Regression: mastered-and-sold weapons must not reappear as suggestions.
    chains, _req, _craft, shown = _build(mastered={normalize_path(_UPGRADE)})

    assert shown == 0
    assert chains == []


def test_chain_is_kept_when_only_the_intermediate_is_owned():
    chains, _req, _craft, shown = _build(owned={normalize_path(_BASE): 1})

    assert shown == 1
    assert chains[0][-1] == _UPGRADE.lower()


def test_web_view_renders_the_full_chain_path():
    chains, req, craft, shown = _build()

    page = render_weapon_chains(
        chains,
        req,
        craft,
        shown,
        _items_by_un(),
        _recipes_by_result(),
        {},
        {},
    )

    assert "Braton → Braton Vandal" in page
