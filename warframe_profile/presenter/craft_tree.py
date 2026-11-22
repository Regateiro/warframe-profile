#!/usr/bin/env python3
"""Crafting tree browser (``craft``) — presenter.

Orchestrates data loading, delegates domain logic to
:mod:`warframe_profile.craft_model`, and displays output via
:mod:`warframe_profile.report`.
"""

import sys

from warframe_profile.model.craft_model import categorize, merge_dicts
from warframe_profile.model.data import Inventory, ItemData
from warframe_profile.model.inventory import load_data
from warframe_profile.view.report import (
    print_craft_summary,
    print_craft_tree,
)


def _run_weapon_chain_mode(item_data: ItemData, inventory: Inventory) -> None:
    """Build and display weapon upgrade chains for unowned final weapons."""
    chains, all_req, all_craft, shown = inventory.weapon_chains
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
        if inventory.is_owned(final_un):
            continue
        names = [item_data.weapon_name(un) for un in chain]
        header = " \u2192 ".join(names)
        print(f"  {header}")
        box = "\u2550"
        print(f"  {box * len(header)}")
        print()
        print_craft_tree(item_data.craft_tree(final_un, 1, inventory, max_depth=5))
        print()

    if shown == 0:
        print("  You already own every final-chain weapon!")
        return

    print(f"  ({shown} chain{'s' if shown > 1 else ''} shown)")
    print()
    print_craft_summary(all_req, all_craft)


def _select_matching_item(
    query: str,
    item_data: ItemData,
    interactive: bool,
    select: int | None,
    qi: int,
) -> dict | None:
    """Search for *query* and interactively or automatically pick one match."""
    matches = item_data.find(query)
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
    item_data: ItemData,
    inventory: Inventory,
    max_depth: int,
) -> tuple[dict, dict]:
    """Print one item's crafting tree and return its (requirements, craftables)."""
    print()
    print("=" * 72)
    print(f"  {item_name}")
    print("=" * 72)
    print("\n\u2500\u2500 Crafting Tree \u2500\u2500")

    print_craft_tree(item_data.craft_tree(item_un, 1, inventory, max_depth=max_depth))
    print()

    req, craft = item_data.resolve_tree(item_un, 1, inventory, max_depth=max_depth)
    item_data.add_blueprint(item_un, item_name, req, inventory)

    return req, craft


def _aggregate_and_print(
    all_requirements: dict,
    all_craftables: dict,
    item_data: ItemData,
    inventory: Inventory,
) -> None:
    """Decompose raw materials, preserve blueprints, and print the summary."""
    all_requirements = item_data.finish_plan(all_requirements, all_craftables, inventory)

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
    item_data, inventory, _account_id = load_data(
        args.items_cache,
        args.refresh_items,
        args.inventory,
        args.refresh,
    )

    if args.weapon_chain:
        _run_weapon_chain_mode(item_data, inventory)
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
        sel = _select_matching_item(query, item_data, args.interactive, args.select, qi)
        if sel is None:
            continue
        req, craft = _process_item(
            sel["uniqueName"],
            sel["_resolved_name"],
            item_data,
            inventory,
            args.depth,
        )
        merge_dicts(all_requirements, req, "quantity")
        merge_dicts(all_craftables, craft, "quantity")

    _aggregate_and_print(all_requirements, all_craftables, item_data, inventory)
