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

from warframe_profile.model.data import Inventory, ItemData
from warframe_profile.model.inventory import (
    InventoryFetchError,
    ProfileNotFoundError,
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
    item_data: ItemData,
    inventory: Inventory,
    show_relics: bool,
    account_id: str | None = None,
) -> None:
    """Shared logic for both ``ducats`` and ``relics``.

    Flow:
        1. Report the Prime index size.
        2. Fetch DE profile for display name + MR (non-fatal if it fails).
        3. Run the cached Prime analysis on the inventory snapshot.
        4. If ``relics`` mode: print needed drops + safe relics.
           Otherwise: print the full ducat analysis report.
    """
    print(f"  {len(item_data.prime_map)} prime items", file=sys.stderr)

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

    if not show_relics:
        print_report(inventory.analysis)
        return

    print_needed_drops(inventory.needed_drops)
    print_safe_relics(inventory.relic_info)


def main(args) -> None:
    """Entry point for the ``ducats`` sub-command."""
    item_data, inventory, account_id = load_data(
        args.items_cache,
        args.refresh_items,
        args.inventory,
        args.refresh,
    )
    _run(args, item_data, inventory, show_relics=False, account_id=account_id)


def relics_main(args) -> None:
    """Entry point for the ``relics`` sub-command."""
    item_data, inventory, account_id = load_data(
        args.items_cache,
        args.refresh_items,
        args.inventory,
        args.refresh,
    )
    _run(args, item_data, inventory, show_relics=True, account_id=account_id)
