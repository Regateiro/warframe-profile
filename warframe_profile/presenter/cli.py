"""Command-line interface for the ducats / prime-part analysis (``--ducats``).

Workflow
--------

1. Load (or refresh) the merged item export database.
2. Fetch (or load cached) inventory.
3. Optionally display the DE profile name and MR.
4. Run :func:`~warframe_profile.analysis.analyze` to cross-reference.
5. Compute sellable-equipment and ingredient indices.
6. Print the report via :mod:`warframe_profile.report`.
"""

import argparse
import os
import sys

from warframe_profile import DATA_DIR
from warframe_profile.model.inventory import (
    ExportDB, fetch_de_profile, EQUIPMENT_SECTIONS, load_data,
    InventoryFetchError, ProfileNotFoundError, WarframeNotRunningError,
    build_mastered_set,
)
from warframe_profile.model.analysis import (
    build_prime_map, analyze, build_relics_map, build_needed_drops,
    build_owned, compute_sellable_equipment,
)
from warframe_profile.view.report import (
    print_report, print_safe_relics, print_needed_drops,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _run(
    args: argparse.Namespace,
    db: ExportDB,
    inv: dict,
    show_relics: bool,
) -> None:
    """Shared logic for both ``--ducats`` and ``--relics``."""
    items = db.items
    prime_map = build_prime_map(items)
    print(f"  {len(prime_map)} prime items", file=sys.stderr)

    # Fetch DE profile for display (non-fatal).
    try:
        profile = fetch_de_profile(args.profile)
        name = profile.get("DisplayName", "").rstrip(chr(0))
        mr = profile.get("PlayerLevel", "?")
        print(f"  Player: {name}  MR {mr}", file=sys.stderr)
    except (ProfileNotFoundError, InventoryFetchError) as e:
        print(f"  Warning: {e}", file=sys.stderr)

    # Build mastered set (items ever ranked up, even if sold).
    mastered = build_mastered_set(inv)

    # Run core analysis.
    data = analyze(inv, prime_map, EQUIPMENT_SECTIONS, mastered=mastered)

    # Compute sellable equipment index.
    data.sellable = compute_sellable_equipment(
        db, inv, EQUIPMENT_SECTIONS,
    )

    owned = build_owned(inv)

    if not show_relics:
        print_report(data)
        return

    # --relics mode: show relic tables only.
    needed = build_needed_drops(items, inv, prime_map, owned,
                                EQUIPMENT_SECTIONS, mastered=mastered)
    print_needed_drops(needed)
    relics = build_relics_map(items, inv, prime_map, owned,
                              EQUIPMENT_SECTIONS, mastered=mastered)
    print_safe_relics(relics)


def main(args) -> None:
    """Entry point for the ``--ducats`` sub-command."""
    db, inv = load_data(
        args.items_cache, args.refresh_items,
        args.inventory, args.refresh,
    )
    _run(args, db, inv, show_relics=False)


def relics_main(args) -> None:
    """Entry point for the ``--relics`` sub-command."""
    db, inv = load_data(
        args.items_cache, args.refresh_items,
        args.inventory, args.refresh,
    )
    _run(args, db, inv, show_relics=True)


if __name__ == "__main__":
    import argparse
    _parser = argparse.ArgumentParser(description="Warframe Prime Part Analyzer")
    _parser.add_argument("--inventory", "-i", help="Path to cached inventory.json")
    _parser.add_argument("--refresh", "-r", action="store_true", help="Fetch inventory from live Warframe process")
    _parser.add_argument("--profile", "-p", default="57c71e613ade7fa2a211997b", help="DE account ID for player display")
    _parser.add_argument("--items-cache", default=os.path.join(DATA_DIR, "export_db.json"), help="Path to the merged items export database")
    _parser.add_argument("--refresh-items", action="store_true", help="Delete and re-load the items cache")
    main(_parser.parse_args())
