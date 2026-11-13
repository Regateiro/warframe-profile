"""Command-line interface for the ducats / prime-part analysis (``ducats``).

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
import sys

from warframe_profile.model.analysis import (
    analyze,
    build_needed_drops,
    build_owned,
    build_prime_map,
    build_relics_map,
    compute_extra_builds,
    compute_owned_finished,
    compute_sellable_equipment,
)
from warframe_profile.model.inventory import (
    EQUIPMENT_SECTIONS,
    ExportDB,
    InventoryFetchError,
    ProfileNotFoundError,
    build_mastered_set,
    fetch_de_profile,
    load_data,
)
from warframe_profile.view.report import (
    print_needed_drops,
    print_report,
    print_safe_relics,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _run(
    args: argparse.Namespace,
    db: ExportDB,
    inv: dict,
    show_relics: bool,
    account_id: str | None = None,
) -> None:
    """Shared logic for both ``ducats`` and ``relics``.

    Flow:
        1. Build Prime map from the item database.
        2. Fetch DE profile for display name + MR (non-fatal if it fails).
        3. Build the set of mastered items (XP history).
        4. Run core analysis (``analyze``).
        5. Compute market-rebuyable equipment.
        6. If ``relics`` mode: compute needed drops + safe relics and print.
           Otherwise: print the full ducat analysis report.
    """
    items = db.items
    prime_map = build_prime_map(items)
    print(f"  {len(prime_map)} prime items", file=sys.stderr)

    # Fetch DE profile for display (non-fatal).  Prefer an explicit --profile,
    # otherwise use the account id discovered by the live scan; skip entirely
    # when the data came from the cache and no id was supplied.
    profile_id = args.profile or account_id
    if profile_id:
        try:
            profile = fetch_de_profile(profile_id)
            name = profile.get("DisplayName", "").rstrip(chr(0))
            mr = profile.get("PlayerLevel", "?")
            print(f"  Player: {name}  MR {mr}", file=sys.stderr)
        except (ProfileNotFoundError, InventoryFetchError):
            pass

    # Build mastered set (items ever ranked up, even if sold).
    mastered = build_mastered_set(inv)

    # Derived ownership state — computed once and shared by every phase.
    owned = build_owned(inv, recipes=db.recipes)
    owned_finished = compute_owned_finished(inv, EQUIPMENT_SECTIONS, mastered)
    extra_builds = compute_extra_builds(prime_map, owned, owned_finished)

    # Run core analysis.
    data = analyze(
        inv,
        prime_map,
        EQUIPMENT_SECTIONS,
        mastered=mastered,
        recipes=db.recipes,
        owned=owned,
        owned_finished=owned_finished,
        extra_builds=extra_builds,
    )

    # Compute sellable equipment index.
    data.sellable = compute_sellable_equipment(
        db,
        inv,
        EQUIPMENT_SECTIONS,
    )

    if not show_relics:
        print_report(data)
        return

    # relics mode: show relic tables only.
    needed = build_needed_drops(
        items,
        inv,
        prime_map,
        owned,
        EQUIPMENT_SECTIONS,
        mastered=mastered,
        owned_finished=owned_finished,
        extra_builds=extra_builds,
    )
    print_needed_drops(needed)
    relics = build_relics_map(
        items,
        inv,
        prime_map,
        owned,
        EQUIPMENT_SECTIONS,
        mastered=mastered,
        owned_finished=owned_finished,
        extra_builds=extra_builds,
    )
    print_safe_relics(relics)


def main(args) -> None:
    """Entry point for the ``ducats`` sub-command."""
    db, inv, account_id = load_data(
        args.items_cache,
        args.refresh_items,
        args.inventory,
        args.refresh,
    )
    _run(args, db, inv, show_relics=False, account_id=account_id)


def relics_main(args) -> None:
    """Entry point for the ``relics`` sub-command."""
    db, inv, account_id = load_data(
        args.items_cache,
        args.refresh_items,
        args.inventory,
        args.refresh,
    )
    _run(args, db, inv, show_relics=True, account_id=account_id)
