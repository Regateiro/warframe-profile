"""Tests for the central Inventory model and credential extraction."""

from types import SimpleNamespace

import pytest

from warframe_profile.model.data import Inventory, ItemData
from warframe_profile.model.errors import InventoryFetchError
from warframe_profile.model.inventory import (
    _extract_credentials,
    _find_credentials_in_memory,
    inventory_summary,
)
from warframe_profile.model.utils import normalize_path

#: Fake 24-character account ID used by the credential tests.
_ACCOUNT = "0123456789abcdef01234567"


def _inventory(raw: dict | None = None, recipes: dict | None = None) -> Inventory:
    recipes = recipes or {}
    payload = {"items": [], "recipes": recipes, "dict": {}}
    db = SimpleNamespace(items=[], recipes=recipes, locale={}, raw=payload)
    return Inventory(raw or {}, ItemData(db))


def test_inventory_counts_every_section():
    inventory = {
        "MiscItems": [{"ItemType": "/Lotus/Types/Items/Resources/Ferrite", "ItemCount": 5}],
        "Recipes": [{"ItemType": "/Lotus/Weapons/Tenno/LatoPrimeBlueprint", "ItemCount": 2}],
        "Suits": [{"ItemType": "/Lotus/Powersuits/Excalibur/Excalibur"}],
        "LongGuns": [{"ItemType": "/Lotus/Weapons/Tenno/Braton"}],
    }

    state = _inventory(inventory)

    assert state.count("/Lotus/Types/Items/Resources/Ferrite") == 5
    assert state.count("/Lotus/Weapons/Tenno/LatoPrimeBlueprint") == 2
    assert state.count("/Lotus/Powersuits/Excalibur/Excalibur") == 1
    assert state.count("/Lotus/Weapons/Tenno/Braton") == 1


def test_inventory_includes_pending_recipes_as_owned():
    inventory = {"PendingRecipes": [{"ItemType": "/Lotus/Weapons/Tenno/KeratinosBladeBlueprint"}]}

    state = _inventory(inventory)

    assert state.count("/Lotus/Weapons/Tenno/KeratinosBlade") == 1
    assert state.is_crafting("/Lotus/Weapons/Tenno/KeratinosBlade") is True
    assert state.is_owned("/Lotus/Weapons/Tenno/KeratinosBlade") is True


def test_inventory_subtracts_pending_recipe_blueprint():
    blueprint = "/Lotus/Types/Recipes/Weapons/CedoPrimeBlueprint"
    result = "/Lotus/Weapons/Tenno/LongGuns/PrimeCedo/PrimeCedoWeapon"
    inventory = {
        "Recipes": [{"ItemType": blueprint, "ItemCount": 2}],
        "PendingRecipes": [{"ItemType": blueprint}],
    }
    recipes = {blueprint: {"resultType": result}}

    state = _inventory(inventory, recipes)

    assert state.count(blueprint) == 1
    assert state.count(result) == 1


def test_inventory_never_goes_negative_for_pending_blueprints():
    blueprint = "/Lotus/Types/Recipes/Weapons/CedoPrimeBlueprint"

    state = _inventory({"PendingRecipes": [{"ItemType": blueprint}]})

    assert state.count(blueprint) == 0


def test_inventory_mastered_normalises_windows_paths():
    inventory = {
        "XPInfo": [{"ItemType": "/Lotus\\Weapons\\Tenno\\LatoPrime", "XP": 0}],
        "LongGuns": [{"ItemType": "/Lotus/Weapons/Tenno/Braton", "XP": 100}],
    }

    state = _inventory(inventory)

    assert normalize_path("/Lotus/Weapons/Tenno/LatoPrime") in state.mastered
    assert normalize_path("/Lotus/Weapons/Tenno/Braton") in state.mastered


def test_inventory_mastered_ignores_zero_xp_equipment():
    inventory = {"LongGuns": [{"ItemType": "/Lotus/Weapons/Tenno/Braton", "XP": 0}]}

    assert _inventory(inventory).mastered == set()


def test_inventory_pending_uses_recipe_result_type():
    inventory = {"PendingRecipes": [{"ItemType": "/Lotus/Recipes/Weapons/XBlueprint"}]}
    recipes = {"/Lotus/Recipes/Weapons/XBlueprint": {"resultType": "/Lotus/Weapons/Tenno/X"}}

    state = _inventory(inventory, recipes)

    assert state.pending_count("/Lotus/Weapons/Tenno/X") == 1


def test_inventory_pending_falls_back_to_suffix_stripping():
    inventory = {"PendingRecipes": [{"ItemType": "/Lotus/Weapons/Tenno/BladeBlueprint"}]}

    state = _inventory(inventory)

    assert state.pending_count("/Lotus/Weapons/Tenno/Blade") == 1


def test_inventory_summary_counts_sections():
    inventory = {
        "MiscItems": [{}, {}],
        "Recipes": [{}],
        "Suits": [{}],
        "Pistols": [{}, {}],
    }

    assert inventory_summary(inventory) == (2, 1, 3)


def test_extract_credentials_handles_account_first():
    chunk = f"?accountId={_ACCOUNT}&nonce=12ab34cd&ct=STM".encode()

    assert _extract_credentials(chunk) == (_ACCOUNT, "12ab34cd")


def test_extract_credentials_handles_extra_query_parameters():
    chunk = f"?accountId={_ACCOUNT}&platform=PC&nonce=abcdef123".encode()

    assert _extract_credentials(chunk) == (_ACCOUNT, "abcdef123")


def test_extract_credentials_handles_nonce_first():
    chunk = f"?nonce=deadbeef&accountId={_ACCOUNT}".encode()

    assert _extract_credentials(chunk) == (_ACCOUNT, "deadbeef")


def test_extract_credentials_rejects_incomplete_values():
    assert _extract_credentials(b"?accountId=0123456789abcdef") is None
    assert _extract_credentials(b"?accountId=short&nonce=123") is None


def test_find_credentials_trusts_a_repeated_pair(monkeypatch):
    stale = f"accountId={'f' * 24}&nonce=stale".encode()
    valid = f"?accountId={_ACCOUNT}&nonce=12345&ct=STM".encode()

    def fake_scan(_pid, pattern, context=256, max_per_region=None):
        assert pattern == "accountId="
        assert context == 512
        yield 0x1000, stale
        yield 0x2000, valid
        yield 0x3000, valid
        yield 0x4000, valid

    monkeypatch.setattr("warframe_profile.model.inventory.iter_in_memory", fake_scan)

    assert _find_credentials_in_memory(1234) == (_ACCOUNT, "12345")


def test_find_credentials_uses_most_common_when_not_repeated(monkeypatch):
    first = f"?accountId={'1' * 24}&nonce=one".encode()
    second = f"?accountId={'2' * 24}&nonce=two".encode()

    def fake_scan(_pid, pattern, context=256, max_per_region=None):
        assert pattern == "accountId="
        yield 0x1000, first
        yield 0x2000, second
        yield 0x3000, second

    monkeypatch.setattr("warframe_profile.model.inventory.iter_in_memory", fake_scan)

    assert _find_credentials_in_memory(1234) == ("2" * 24, "two")


def test_find_credentials_falls_back_to_nonce_first_search(monkeypatch):
    valid = f"?nonce=cafebabe&accountId={_ACCOUNT}".encode()

    def fake_scan(_pid, pattern, context=256, max_per_region=None):
        if pattern == "nonce=":
            yield 0x1000, valid
            yield 0x2000, valid
            yield 0x3000, valid

    monkeypatch.setattr("warframe_profile.model.inventory.iter_in_memory", fake_scan)

    assert _find_credentials_in_memory(1234) == (_ACCOUNT, "cafebabe")


def test_find_credentials_raises_when_nothing_matches(monkeypatch):
    def fake_scan(_pid, pattern, context=256, max_per_region=None):
        return
        yield  # pragma: no cover - keeps this a generator

    monkeypatch.setattr("warframe_profile.model.inventory.iter_in_memory", fake_scan)

    with pytest.raises(InventoryFetchError, match="accountId/nonce"):
        _find_credentials_in_memory(1234)
