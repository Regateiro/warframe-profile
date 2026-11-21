"""Inventory-cleanup analysis — excess items and market-rebuyable gear."""

from collections import defaultdict
from collections.abc import Set
from dataclasses import dataclass

from warframe_profile.model.data import Inventory, ItemData
from warframe_profile.model.utils import normalize_path

#: Weapon categories that can be primed.
WEAPON_CATEGORIES = {"Primary", "Secondary", "Melee"}

#: Subjective rarity tiers used to determine the "rarest" component.
_RESOURCE_RARITY: dict[str, int] = {
    "Nitain Extract": 4,
    "Tellurium": 3,
    "Argon Crystal": 3,
    "Forma": 3,
    "Oxium": 2,
    "Cryotic": 2,
    "Kuva": 2,
    "Hexenon": 2,
    "Neurodes": 1,
    "Neural Sensors": 1,
    "Orokin Cell": 1,
    "Gallium": 1,
    "Morphics": 1,
    "Plastids": 1,
    "Polymer Bundle": 1,
    "Control Module": 1,
}


@dataclass
class SellableEquipment:
    """A non-Prime weapon that can be sold because it can be re-bought for credits.

    Attributes:
        name:           Display name.
        category:       Equipment category.
        buy_type:       ``"direct"`` (buy outright) or ``"blueprint"``.
        rare_component: Name of the rarest required material, if any.
    """

    name: str
    category: str
    buy_type: str
    rare_component: str | None


@dataclass
class ExcessItem:
    """A blueprint or component the player owns more copies of than needed.

    Attributes:
        name:     Display name.
        item_type: ``"Blueprint"`` or ``"Component"``.
        owned:    Total copies owned.
        sell:     Number that can be safely sold.
        builds:   What finished item(s) this crafts into.
    """

    name: str
    item_type: str
    owned: int
    sell: int
    builds: str


def _get_rarest_component(item_data: dict) -> str | None:
    """Return the name of the rarest required resource for *item_data*.

    Rarity is defined by :data:`_RESOURCE_RARITY`.  Blueprint components
    are skipped.
    """
    comps = item_data.get("components", [])
    if not comps:
        return None
    rarest: str | None = None
    rarest_tier = -1
    for comp in comps:
        cname = comp.get("name", "")
        if not cname or "Blueprint" in cname:
            continue
        tier = _RESOURCE_RARITY.get(cname, 0)
        if tier > rarest_tier:
            rarest_tier = tier
            rarest = cname
    return rarest if rarest_tier > 0 else None


def build_ingredient_to_results(recipes: dict) -> dict[str, set[str]]:
    """Build a reverse index: ingredient ``ItemType`` → set of result types."""
    index: dict[str, set[str]] = defaultdict(set)
    for recipe_data in recipes.values():
        result_type = recipe_data.get("resultType", "")
        if not result_type:
            continue
        for ingredient in recipe_data.get("ingredients", []):
            item_type = ingredient.get("ItemType", "")
            if item_type:
                index[item_type].add(result_type)
    return index


def build_ingredient_index_craftable_to_owned(
    item_data: ItemData,
    inventory: Inventory,
) -> set[str]:
    """Build a set of item uniqueNames that should *not* be sold.

    Only ingredients needed for items the player does not already own (or is
    not currently crafting) are excluded.  Ingredients whose every crafting
    path leads to an owned/mastered/foundry item are safe to sell.
    """
    weapon_uns: set[str] = {
        item["uniqueName"]
        for item in item_data.items
        if item.get("uniqueName") and item.get("category") in WEAPON_CATEGORIES
    }

    result_to_recipe: dict[str, dict] = {}
    for recipe_data in item_data.recipes.values():
        result_type = recipe_data.get("resultType", "")
        if result_type:
            result_to_recipe[result_type] = recipe_data

    ingredient_to_results = item_data.ingredient_to_results

    exclude: set[str] = set()

    # Case 1: ingredients that craft into at least one unowned item.
    for recipe_data in item_data.recipes.values():
        for ingredient in recipe_data.get("ingredients", []):
            item_type = ingredient.get("ItemType", "")
            if not item_type:
                continue

            results = ingredient_to_results.get(item_type, set())
            # If the ingredient has no known results or any result is not
            # owned/crafting, keep it — it is still needed.
            if not results or not all(inventory.is_owned(result) for result in results):
                exclude.add(item_type)

    # Case 2: items whose recipe requires a weapon as an ingredient and the
    # result is not already owned or being crafted.
    for result_type, recipe_data in result_to_recipe.items():
        if inventory.is_owned(result_type):
            continue
        for ingredient in recipe_data.get("ingredients", []):
            if ingredient.get("ItemType", "") in weapon_uns:
                exclude.add(result_type)
                break

    return exclude


def _all_crafting_paths_lead_to_owned(
    item_un: str,
    inventory: Inventory,
    ingredient_to_results: dict[str, set[str]],
    visited: set[str],
) -> bool:
    """Return whether every item *item_un* crafts into is already covered."""
    if inventory.is_owned(item_un):
        return True

    if item_un in visited:
        return True
    visited.add(item_un)

    results = ingredient_to_results.get(item_un, set())
    if not results:
        return inventory.is_owned(item_un)

    return all(
        _all_crafting_paths_lead_to_owned(result, inventory, ingredient_to_results, visited)
        for result in results
    )


def _excess_item(
    item_un: str,
    count: int,
    item_type: str,
    item_data: ItemData,
    inventory: Inventory,
    ingredient_to_results: dict,
) -> ExcessItem | None:
    """Return an ExcessItem if *item_un* only crafts into covered items."""
    if count == 0:
        return None
    if not _all_crafting_paths_lead_to_owned(item_un, inventory, ingredient_to_results, set()):
        return None
    item = item_data.item(item_un)
    name = item_data.name(item.get("name", "")) if item else item_un.split("/")[-1]
    results = ingredient_to_results.get(item_un, set())
    builds = ", ".join(item_data.item_name(result) for result in results) if results else "?"
    return ExcessItem(name=name, item_type=item_type, owned=count, sell=count, builds=builds)


def find_excess_blueprints_and_components(
    item_data: ItemData,
    inventory: Inventory,
) -> list[ExcessItem]:
    """Identify blueprints and components the player can safely sell.

    A blueprint or component is *excess* when every finished item it can
    ultimately be used to craft is already owned, mastered, or being crafted.
    """
    ingredient_to_results = item_data.ingredient_to_results

    excess: list[ExcessItem] = []

    for blueprint in inventory.blueprints:
        bp_un = blueprint.get("ItemType", "")
        if not bp_un:
            continue
        item = _excess_item(
            bp_un,
            blueprint.get("ItemCount", 1),
            "Blueprint",
            item_data,
            inventory,
            ingredient_to_results,
        )
        if item:
            excess.append(item)

    for entry in inventory.components:
        item_un = entry.get("ItemType", "")
        if not item_un or not ingredient_to_results.get(item_un):
            continue
        item = _excess_item(
            item_un,
            entry.get("ItemCount", 1),
            "Component",
            item_data,
            inventory,
            ingredient_to_results,
        )
        if item:
            excess.append(item)

    excess.sort(key=lambda x: (x.item_type, x.name))
    return excess


def build_market_credit_map(items: list[dict]) -> dict[str, dict]:
    """Build a map of weapon ``uniqueName`` → purchase info for items
    that can be re-bought from the in-game market for credits.

    Two buy types are recognised:
    * ``"direct"`` — MK1 variants bought outright for credits.
    * ``"blueprint"`` — weapons bought as blueprints (credit cost).
    """
    market: dict[str, dict] = {}
    for item in items:
        un = item.get("uniqueName", "")
        name = item.get("name", "")
        if not un or not name:
            continue
        cat = item.get("category", "")
        if cat not in WEAPON_CATEGORIES:
            continue
        if name.lower().startswith("mk1-"):
            market[un] = {"name": name, "category": cat, "buy_type": "direct"}
            continue
        if item.get("marketCost") is not None and item.get("bpCost") is not None:
            info: dict = {"name": name, "category": cat, "buy_type": "blueprint"}
            rarest = _get_rarest_component(item)
            if rarest:
                info["rare_component"] = rarest
            market[un] = info
    return market


def find_sellable_equipment(
    inventory: Inventory,
    market_map: dict[str, dict],
    exclude_ingredients: Set[str] = frozenset(),
) -> list[SellableEquipment]:
    """Find owned equipment that can be safely sold (re-buyable for credits).

    Items whose ``uniqueName`` appears in *exclude_ingredients* are kept
    because they are needed for other crafts.
    """
    owned: list[SellableEquipment] = []
    seen: set[str] = set()
    for equipment in inventory.equipment_entries():
        un = equipment.get("ItemType", "")
        npath = normalize_path(un)
        if npath in seen:
            continue
        seen.add(npath)
        if un in market_map and un not in exclude_ingredients:
            info = market_map[un]
            owned.append(
                SellableEquipment(
                    name=info["name"],
                    category=info["category"],
                    buy_type=info["buy_type"],
                    rare_component=info.get("rare_component"),
                )
            )
    owned.sort(key=lambda x: (x.category, x.name))
    return owned


def compute_sellable_equipment(
    item_data: ItemData, inventory: Inventory
) -> list[SellableEquipment]:
    """Compute the market-re-buyable equipment list for an inventory snapshot."""
    ingredient_index = build_ingredient_index_craftable_to_owned(item_data, inventory)
    market_map = build_market_credit_map(item_data.items)
    return find_sellable_equipment(inventory, market_map, ingredient_index)
