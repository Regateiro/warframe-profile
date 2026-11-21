"""Mastery sub-command presenter (``mastery``).

Loads the shared data objects, asks the model for a :class:`MasteryReport`,
and delegates all terminal rendering to :mod:`warframe_profile.view.mastery`.
"""

from warframe_profile.model.inventory import load_data
from warframe_profile.model.mastery import build_mastery_report
from warframe_profile.view.mastery import print_mastery_report


def main(args) -> None:
    """Entry point for the ``mastery`` sub-command."""
    item_data, inventory, _account_id = load_data(
        args.items_cache,
        args.refresh_items,
        args.inventory,
        args.refresh,
    )

    print_mastery_report(build_mastery_report(item_data, inventory))
