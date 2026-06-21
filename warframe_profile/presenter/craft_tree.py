#!/usr/bin/env python3
"""Crafting tree browser (``--craft``) — presenter.

Orchestrates data loading, delegates domain logic to
:mod:`warframe_profile.craft_model`, and displays output via
:mod:`warframe_profile.report`.
"""

import os
import sys

from warframe_profile import DATA_DIR
from warframe_profile.model.craft_model import (
    build_items_by_un,
    build_lookup,
    build_recipes_by_result,
    build_weapon_chains,
    categorize,
    compute_crafting_plan,
    decompose_raw_materials,
    find_items,
    merge_dicts,
    preserve_blueprints,
    resolve_tree,
    weapon_name,
)
from warframe_profile.model.inventory import (
    ExportDB,
    build_owned,
    load_inventory_with_fallback,
)
from warframe_profile.model.utils import normalize_path
from warframe_profile.view.report import (
    print_craft_summary,
    print_craft_tree,
)


def _load_export_db(items_cache: str) -> tuple:
    """Load the export DB and build item/recipe/name indices."""
    xdb = ExportDB(items_cache)
    try:
        xdb.load()
    except FileNotFoundError:
        print(
            "Export database not found. Run `python warframe.py --update` first.",
            file=sys.stderr,
        )
        sys.exit(1)
    loc_dict = xdb.locale
    raw = xdb.raw
    items_by_un = build_items_by_un(raw)
    recipes_by_result = build_recipes_by_result(raw)
    by_name = build_lookup(items_by_un, loc_dict)
    return loc_dict, raw, items_by_un, recipes_by_result, by_name


def _load_inventory(inv_path: str, refresh: bool, raw: dict) -> tuple[dict, str]:
    """Load inventory and return ``(owned, inv_path)``."""
    try:
        inv, _account_id = load_inventory_with_fallback(inv_path, refresh=refresh)
        if refresh:
            print(f"  Saved to {inv_path}", file=sys.stderr)
    except (SystemExit, Exception) as e:
        print(f"  Live fetch failed: {e}", file=sys.stderr)
        sys.exit(1)
    return build_owned(inv, raw.get("recipes", {}))


def _run_weapon_chain_mode(
    items_by_un: dict,
    recipes_by_result: dict,
    owned: dict,
    loc_dict: dict,
) -> None:
    """Build and display weapon upgrade chains for unowned final weapons."""
    chains, all_req, all_craft, shown = build_weapon_chains(
        items_by_un,
        recipes_by_result,
        owned,
        loc_dict,
    )
    if not chains:
        print("No weapon chains found in your progression data.")
        return

    print()
    print("=" * 72)
    print("  WEAPON UPGRADE CHAINS \u2014 Crafting Trees")
    print("=" * 72)
    print()

    for chain in chains:
        final_un = chain[-1]
        final_norm = normalize_path(final_un)
        if owned.get(final_norm, 0) > 0:
            continue
        names = [weapon_name(u, items_by_un, loc_dict) for u in chain]
        header = " \u2192 ".join(names)
        print(f"  {header}")
        box = "\u2550"
        print(f"  {box * len(header)}")
        print()
        print_craft_tree(
            final_un, 1, items_by_un, owned, recipes_by_result, loc_dict, depth=0, max_depth=5
        )  # noqa: E501
        print()

    if shown == 0:
        print("  You already own every final-chain weapon!")
        return

    print(f"  ({shown} chain{'s' if shown > 1 else ''} shown)")
    print()
    print_craft_summary(all_req, all_craft)


def _select_matching_item(
    query: str,
    by_name: dict,
    interactive: bool,
    select: int | None,
    qi: int,
) -> dict | None:
    """Search for *query* and interactively or automatically pick one match."""
    matches = find_items(query, by_name)
    if not matches:
        print(f"No items found matching '{query}'")
        return None

    if len(matches) > 1:
        non_cosmetic = [m for m in matches if categorize(m) != "Cosmetic"]
        if non_cosmetic:
            matches = non_cosmetic
        if len(matches) > 1 and (interactive or select is not None):
            if qi == 0 or interactive:
                print(f"\nFound {len(matches)} matching items for '{query}':")
                for i, match in enumerate(matches):
                    print(f"  [{i}] {match.get('_resolved_name', '?')}  ({categorize(match)})")
            if select is not None:
                return matches[select]
            if interactive:
                while True:
                    try:
                        idx = input("\nSelect [0]: ").strip()
                        return matches[int(idx) if idx else 0]
                    except (ValueError, IndexError):
                        print("Invalid selection")
    return matches[0]


def _process_item(
    item_un: str,
    item_name: str,
    items_by_un: dict,
    owned: dict,
    recipes_by_result: dict,
    loc_dict: dict,
    max_depth: int,
) -> tuple[dict, dict]:
    """Print one item's crafting tree and return its (requirements, craftables)."""
    print()
    print("=" * 72)
    print(f"  {item_name}")
    print("=" * 72)
    print("\n\u2500\u2500 Crafting Tree \u2500\u2500")

    print_craft_tree(
        item_un, 1, items_by_un, owned, recipes_by_result, loc_dict, max_depth=max_depth
    )  # noqa: E501
    print()

    req, craft = resolve_tree(
        item_un, 1, items_by_un, owned, recipes_by_result, loc_dict, max_depth=max_depth
    )  # noqa: E501

    item_un_lower = item_un.lower()
    owned_qty = owned.get(normalize_path(item_un), 0)
    if owned_qty < 1 and item_un_lower in recipes_by_result:
        recipe = recipes_by_result[item_un_lower]
        bp_key = recipe.get("_recipeKey", "")
        if bp_key:
            bp_owned = owned.get(normalize_path(bp_key), 0)
            if bp_owned < 1:
                req[bp_key.lower()] = {
                    "name": f"{item_name} Blueprint",
                    "quantity": 1,
                    "owned": bp_owned,
                }  # noqa: E501

    return req, craft


def _aggregate_and_print(
    all_requirements: dict,
    all_craftables: dict,
    recipes_by_result: dict,
    items_by_un: dict,
    owned: dict,
    loc_dict: dict,
) -> None:
    """Decompose raw materials, preserve blueprints, and print the summary."""
    compute_crafting_plan(all_craftables, owned)
    old_requirements = dict(all_requirements)
    all_requirements = decompose_raw_materials(
        all_craftables, recipes_by_result, items_by_un, owned, loc_dict
    )  # noqa: E501
    all_requirements = preserve_blueprints(old_requirements, all_requirements)

    if not all_requirements:
        return

    print()
    print("=" * 72)
    print("  AGGREGATED REQUIREMENTS")
    print("=" * 72)
    print()
    print_craft_summary(all_requirements, all_craftables)


def main(args) -> None:
    """Entry point for the crafting-tree browser sub-command."""
    print("Loading...", file=sys.stderr)
    loc_dict, raw, items_by_un, recipes_by_result, by_name = _load_export_db(args.items_cache)

    inv_path = args.inventory or os.path.join(DATA_DIR, "inventory.json")
    owned = _load_inventory(inv_path, args.refresh, raw)

    if args.weapon_chain:
        _run_weapon_chain_mode(items_by_un, recipes_by_result, owned, loc_dict)
        return

    queries = list(args.items)
    if args.interactive or not queries:
        q = input("Item name: ").strip()
        if q:
            queries = [q]

    if not queries:
        print("No item(s) specified.")
        sys.exit(1)

    all_requirements: dict[str, dict] = {}
    all_craftables: dict[str, dict] = {}

    for qi, query in enumerate(queries):
        sel = _select_matching_item(query, by_name, args.interactive, args.select, qi)
        if sel is None:
            continue
        req, craft = _process_item(
            sel["uniqueName"],
            sel["_resolved_name"],
            items_by_un,
            owned,
            recipes_by_result,
            loc_dict,
            args.depth,
        )
        merge_dicts(all_requirements, req, "quantity")
        merge_dicts(all_craftables, craft, "quantity")

    _aggregate_and_print(
        all_requirements, all_craftables, recipes_by_result, items_by_un, owned, loc_dict
    )  # noqa: E501


if __name__ == "__main__":
    import argparse

    _parser = argparse.ArgumentParser(description="Warframe Crafting Tree Browser")
    _parser.add_argument("items", nargs="*", help="Item name(s) to look up")
    _parser.add_argument("--inventory", "-i", help="Path to inventory file")
    _parser.add_argument(
        "--refresh", "-r", action="store_true", help="Refresh inventory from live data"
    )
    _parser.add_argument(
        "--items-cache",
        default=os.path.join(DATA_DIR, "export_db.json"),
        help="Path to the merged items export database",
    )
    _parser.add_argument("--depth", type=int, default=99, help="Max recursion depth for expansion")
    _parser.add_argument(
        "--select", type=int, default=None, help="Select match index when multiple"
    )
    _parser.add_argument("--interactive", action="store_true", help="Interactive item selection")
    _parser.add_argument(
        "--weapon-chain", action="store_true", help="Analyze all weapon upgrade chains"
    )
    main(_parser.parse_args())
