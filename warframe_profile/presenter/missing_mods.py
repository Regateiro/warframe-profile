"""Missing-mods sub-command presenter (``mods``).

Loads the shared data objects, asks the model for a :class:`ModsReport`, and
delegates all terminal rendering to :mod:`warframe_profile.view.mods`.
"""

from warframe_profile.model.inventory import load_data
from warframe_profile.model.mods import build_mods_report
from warframe_profile.view.mods import print_missing_mods_report


def main(args) -> None:
    """Entry point for the ``mods`` sub-command."""
    item_data, inventory, _account_id = load_data(
        args.items_cache,
        args.refresh_items,
        args.inventory,
        args.refresh,
    )

    print_missing_mods_report(build_mods_report(item_data, inventory))
