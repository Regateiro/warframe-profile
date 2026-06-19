#!/usr/bin/env python3
"""Inventory Cleanup sub-command (``--cleanup``).

Identifies items and parts you can safely sell:

* **Market Re‑Buyable Equipment** — non-Prime weapons that can be
  re-purchased from the in-game market for credits (safe to sell).
* **Regular Items to Sell** — non-Prime weapons whose Prime variant
  you already own (no need to keep the base version).
"""

import sys

from warframe_profile.model.inventory import (
    EQUIPMENT_SECTIONS, load_data, build_owned,
)
from warframe_profile.model.analysis import (
    build_prime_map, build_item_index, compute_sellable_equipment,
    build_regular_to_prime_map, find_owned_item_uns, find_owned_prime_uns,
    find_excess_blueprints_and_components, normalize_path,
)
from warframe_profile.view.report import (
    section_sellable_equipment, section_items_with_owned_prime,
    section_excess_blueprints_components,
)


def main(args) -> None:
    """Entry point for the inventory-cleanup sub-command."""
    db, inv = load_data(
        args.items_cache, args.refresh_items,
        args.inventory, args.refresh,
    )

    items = db.items
    items_by_un = build_item_index(items)

    prime_map = build_prime_map(items)
    print(f"  {len(prime_map)} prime items indexed", file=sys.stderr)

    sellable = compute_sellable_equipment(db, inv, EQUIPMENT_SECTIONS)

    reg_to_prime = build_regular_to_prime_map(items)
    owned_regular = find_owned_item_uns(inv, items_by_un, EQUIPMENT_SECTIONS)
    owned_primes = find_owned_prime_uns(inv, prime_map, EQUIPMENT_SECTIONS)

    print()
    print("=" * 72)
    print("  INVENTORY CLEANUP")
    print("=" * 72)
    print()

    section_sellable_equipment(sellable)
    section_items_with_owned_prime(
        owned_regular, reg_to_prime, owned_primes, items_by_un,
    )

    owned_finished: set[str] = {
        normalize_path(eq.get("ItemType", ""))
        for sect in EQUIPMENT_SECTIONS
        for eq in inv.get(sect, [])
    }
    excess = find_excess_blueprints_and_components(
        inv, items_by_un, db.recipes, build_owned(inv),
        owned_finished, {},
    )
    section_excess_blueprints_components(excess)


if __name__ == "__main__":
    main()
