#!/usr/bin/env python3
"""Crafting tree browser (``--craft``) — presenter.

Orchestrates data loading, delegates domain logic to
:mod:`warframe_profile.craft_model`, and displays output via
:mod:`warframe_profile.report`.
"""

import os
import sys

from warframe_profile import DATA_DIR
from warframe_profile.model.analysis import normalize_path
from warframe_profile.model.inventory import (
    ExportDB, build_owned, load_inventory_with_fallback,
)
from warframe_profile.model.craft_model import (
    build_items_by_un, build_recipes_by_result, build_lookup,
    find_items, categorize, resolve_tree, build_weapon_chains, _merge_dicts,
    _weapon_name,
    compute_crafting_plan, decompose_raw_materials, preserve_blueprints,
)
from warframe_profile.view.report import (
    GREEN, RED, YELLOW, RESET,
    print_craft_tree, print_craft_summary,
)


def main(args) -> None:
    """Entry point for the crafting-tree browser sub-command."""
    print("Loading...", file=sys.stderr)
    xdb = ExportDB(args.items_cache)
    try:
        xdb.load()
    except FileNotFoundError:
        print("Export database not found. Run `python warframe.py --update` first.",
              file=sys.stderr)
        sys.exit(1)
    loc_dict = xdb.locale
    raw = xdb.raw
    items_by_un = build_items_by_un(raw)
    recipes_by_result = build_recipes_by_result(raw)
    by_name = build_lookup(items_by_un, loc_dict)

    inv_path = args.inventory or os.path.join(DATA_DIR, "inventory.json")
    try:
        inv, _account_id = load_inventory_with_fallback(
            inv_path, refresh=args.refresh,
        )
        if args.refresh:
            print(f"  Saved to {inv_path}", file=sys.stderr)
    except (SystemExit, Exception) as e:
        print(f"  Live fetch failed: {e}", file=sys.stderr)
        sys.exit(1)

    owned = build_owned(inv, raw.get("recipes", {}))

    if args.weapon_chain:
        chains, all_req, all_craft, shown = build_weapon_chains(
            items_by_un, recipes_by_result, owned, loc_dict,
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
            names = [_weapon_name(u, items_by_un, loc_dict) for u in chain]
            header = " \u2192 ".join(names)
            print(f"  {header}")
            print(f"  {'\u2550' * len(header)}")
            print()
            print_craft_tree(
                final_un, 1,
                items_by_un, owned, recipes_by_result, loc_dict,
                depth=0, max_depth=5,
            )
            print()

        if shown == 0:
            print("  You already own every final-chain weapon!")
            return

        print(f"  ({shown} chain{'s' if shown > 1 else ''} shown)")
        print()
        print_craft_summary(all_req, all_craft)
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
        matches = find_items(query, by_name)
        if not matches:
            print(f"No items found matching '{query}'")
            continue

        if len(matches) > 1:
            non_cosmetic = [m for m in matches if categorize(m) != "Cosmetic"]
            if non_cosmetic:
                matches = non_cosmetic
            if len(matches) > 1 and (args.interactive
                                      or args.select is not None):
                if qi == 0 or args.interactive:
                    print(f"\nFound {len(matches)} matching items "
                          f"for '{query}':")
                    for i, match in enumerate(matches):
                        cat = categorize(match)
                        print(
                            f"  [{i}] "
                            f"{match.get('_resolved_name', '?')}  ({cat})",
                        )
                if args.select is not None:
                    sel = matches[args.select]
                elif args.interactive:
                    while True:
                        try:
                            idx = input("\nSelect [0]: ").strip()
                            sel = matches[int(idx) if idx else 0]
                            break
                        except (ValueError, IndexError):
                            print("Invalid selection")
                else:
                    sel = matches[0]
            else:
                sel = matches[0]
        else:
            sel = matches[0]

        item_un = sel.get("uniqueName", "")
        item_name = sel.get("_resolved_name", "?")

        print()
        print("=" * 72)
        print(f"  {item_name}")
        print("=" * 72)

        print("\n\u2500\u2500 Crafting Tree \u2500\u2500")
        print_craft_tree(
            item_un, 1, items_by_un, owned,
            recipes_by_result, loc_dict,
            max_depth=args.depth,
        )
        print()

        req, craft = resolve_tree(
            item_un, 1, items_by_un, owned,
            recipes_by_result, loc_dict,
            max_depth=args.depth,
        )

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
                    }

        _merge_dicts(all_requirements, req, "quantity")
        _merge_dicts(all_craftables, craft, "quantity")

    compute_crafting_plan(all_craftables, owned)
    old_requirements = dict(all_requirements)
    all_requirements = decompose_raw_materials(
        all_craftables, recipes_by_result, items_by_un, owned, loc_dict,
    )
    all_requirements = preserve_blueprints(old_requirements, all_requirements)

    if not all_requirements:
        return

    print()
    print("=" * 72)
    print("  AGGREGATED REQUIREMENTS")
    print("=" * 72)
    print()
    print_craft_summary(all_requirements, all_craftables)


if __name__ == "__main__":
    import argparse
    _parser = argparse.ArgumentParser(description="Warframe Crafting Tree Browser")
    _parser.add_argument("items", nargs="*", help="Item name(s) to look up")
    _parser.add_argument("--inventory", "-i", help="Path to inventory file")
    _parser.add_argument("--refresh", "-r", action="store_true", help="Refresh inventory from live data")
    _parser.add_argument("--items-cache", default=os.path.join(DATA_DIR, "export_db.json"), help="Path to the merged items export database")
    _parser.add_argument("--depth", type=int, default=99, help="Max recursion depth for expansion")
    _parser.add_argument("--select", type=int, default=None, help="Select match index when multiple")
    _parser.add_argument("--interactive", action="store_true", help="Interactive item selection")
    _parser.add_argument("--weapon-chain", action="store_true", help="Analyze all weapon upgrade chains")
    main(_parser.parse_args())
