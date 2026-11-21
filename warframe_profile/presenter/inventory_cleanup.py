#!/usr/bin/env python3
"""Inventory Cleanup sub-command (``cleanup``).

Identifies items and parts you can safely sell:

* **Market Re‑Buyable Equipment** — non-Prime weapons that can be
  re-purchased from the in-game market for credits (safe to sell).
* **Regular Items to Sell** — non-Prime weapons whose Prime variant
  you already own (no need to keep the base version).
"""

import sys

from warframe_profile.model.inventory import load_data
from warframe_profile.view.report import (
    section_excess_blueprints_components,
    section_items_with_owned_prime,
    section_sellable_equipment,
)


def main(args) -> None:
    """Entry point for the inventory-cleanup sub-command.

    Flow:
        1. Load ItemData + Inventory.
        2. Compute market-rebuyable equipment (safe to sell).
        3. Identify owned regular weapons whose Prime variant is also owned.
        4. Find excess blueprints and components (every crafting path
           leads to an owned/mastered/crafting item).
        5. Print all three sections.
    """
    item_data, inventory, _account_id = load_data(
        args.items_cache,
        args.refresh_items,
        args.inventory,
        args.refresh,
    )

    print(f"  {len(item_data.prime_map)} prime items indexed", file=sys.stderr)

    sellable = inventory.sellable_equipment

    print()
    print("=" * 72)
    print("  INVENTORY CLEANUP")
    print("=" * 72)
    print()

    section_sellable_equipment(sellable)
    section_items_with_owned_prime(inventory.owned_prime_pairs)
    section_excess_blueprints_components(inventory.excess_items)
