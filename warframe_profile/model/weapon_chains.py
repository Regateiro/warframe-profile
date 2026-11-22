"""Weapon upgrade-chain discovery and aggregation."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from warframe_profile.model.craft_index import categorize, compute_crafting_plan, merge_dicts

if TYPE_CHECKING:
    from warframe_profile.model.data import Inventory, ItemData


def _build_weapon_graph(
    recipes_by_result: dict,
    weapon_uns: set[str],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Build weapon dependency graph: ``builds_into`` and ``built_from``."""
    builds_into: dict[str, list[str]] = defaultdict(list)
    built_from: dict[str, list[str]] = defaultdict(list)

    for result_un_lower, recipe in recipes_by_result.items():
        if result_un_lower not in weapon_uns:
            continue
        for ing in recipe.get("ingredients", []):
            ing_un_lower = ing["ItemType"].lower()
            if ing_un_lower in weapon_uns:
                builds_into[ing_un_lower].append(result_un_lower)
                built_from[result_un_lower].append(ing_un_lower)

    return builds_into, built_from


def _find_chains(
    weapon_uns: set[str],
    builds_into: dict,
    built_from: dict,
) -> list[list[str]]:
    """Walk dependency graph to find linear upgrade chains."""
    roots = sorted(
        un
        for un in weapon_uns
        if un in builds_into and (un not in built_from or not built_from[un])
    )
    chains: list[list[str]] = []
    for root in roots:
        chain = [root]
        current = root
        while current in builds_into and builds_into[current]:
            nxt = builds_into[current][0]
            chain.append(nxt)
            current = nxt
            if len(chain) > 20:
                break
        if len(chain) >= 2:
            chains.append(chain)
    return chains


def _aggregate_chain_requirements(
    chain: list[str],
    item_data: ItemData,
    inventory: Inventory,
    all_requirements: dict,
    all_craftables: dict,
    max_depth: int,
) -> None:
    """Resolve crafting tree for one chain and merge into accumulators."""
    final_un = chain[-1]
    if inventory.is_owned(final_un):
        return

    req, craft = item_data.resolve_tree(final_un, 1, inventory, max_depth=max_depth)
    merge_dicts(all_requirements, req, "quantity")
    merge_dicts(all_craftables, craft, "quantity")

    recipe = item_data.recipe_for(final_un)
    if recipe:
        bp_key = recipe.get("_recipeKey", "")
        if bp_key:
            bp_owned = inventory.count(bp_key)
            names = [item_data.weapon_name(un) for un in chain]
            bp_name = f"{names[-1]} Blueprint"
            key = bp_key.lower()
            if key in all_requirements:
                all_requirements[key]["quantity"] = max(all_requirements[key]["quantity"], 1)
            else:
                all_requirements[key] = {"name": bp_name, "quantity": 1, "owned": bp_owned}


def build_weapon_chains(
    item_data: ItemData,
    inventory: Inventory,
    max_depth: int = 5,
) -> tuple[list[list[str]], dict, dict, int]:
    """Build weapon upgrade chains whose final weapon is not owned or crafting.

    The returned chain list is already filtered, so both the CLI and the web
    view can render it directly; the final integer mirrors its length.
    """
    from warframe_profile.model.cleanup import WEAPON_CATEGORIES

    weapon_uns = {
        un_lower
        for un_lower, item in item_data.items_by_un.items()
        if categorize(item) in WEAPON_CATEGORIES
    }

    builds_into, built_from = _build_weapon_graph(item_data.recipes_by_result, weapon_uns)
    chains = _find_chains(weapon_uns, builds_into, built_from)

    all_requirements: dict[str, dict] = {}
    all_craftables: dict[str, dict] = {}
    shown_chains: list[list[str]] = []

    for chain in chains:
        if inventory.is_owned(chain[-1]):
            continue
        _aggregate_chain_requirements(
            chain,
            item_data,
            inventory,
            all_requirements,
            all_craftables,
            max_depth,
        )
        shown_chains.append(chain)

    compute_crafting_plan(all_craftables)

    return shown_chains, all_requirements, all_craftables, len(shown_chains)
