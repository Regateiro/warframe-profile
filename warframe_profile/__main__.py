"""Entry point for ``python -m warframe_profile`` and ``python warframe.py``.

Dispatches to the corresponding module based on the selected sub-command.
"""

import argparse
import importlib
import os
import sys

from warframe_profile import DATA_DIR, DEFAULT_ACCOUNT_ID
from warframe_profile.model.inventory import load_inventory_with_fallback

#: Maps sub-command names to (module_path, function_name) pairs.
_COMMAND_TABLE = {
    "craft": ("warframe_profile.presenter.craft_tree", "main"),
    "ducats": ("warframe_profile.presenter.cli", "main"),
    "cleanup": ("warframe_profile.presenter.inventory_cleanup", "main"),
    "update": ("warframe_profile.scripts.update_export_db", "main"),
    "relics": ("warframe_profile.presenter.cli", "relics_main"),
    "serve": ("warframe_profile.presenter.web_server", "main"),
    "mastery": ("warframe_profile.presenter.mastery", "main"),
    "mods": ("warframe_profile.presenter.missing_mods", "main"),
    "foundry": ("warframe_profile.presenter.foundry_export", "main"),
}


def _build_parser() -> argparse.ArgumentParser:
    """Build the single, unified argument parser for all sub-commands."""
    parser = argparse.ArgumentParser(description="Warframe Profile Tool")

    # -- Shared options (used by multiple sub-commands).
    # These are on the main parser so shared flags like --refresh must be
    # placed *before* the sub-command name (e.g. ``--refresh ducats``).
    shared = parser.add_argument_group("shared options")
    shared.add_argument("--inventory", "-i", help="Path to cached inventory.json")
    shared.add_argument(
        "--refresh",
        "-r",
        action="store_true",
        help="Fetch inventory from live Warframe process",
    )
    shared.add_argument(
        "--profile",
        "-p",
        default=DEFAULT_ACCOUNT_ID,
        help="DE account ID for player display",
    )
    shared.add_argument(
        "--items-cache",
        default=os.path.join(DATA_DIR, "export_db.json"),
        help="Path to the merged items export database",
    )
    shared.add_argument(
        "--refresh-items", action="store_true", help="Delete and re-load the items cache"
    )
    shared.add_argument("--port", type=int, default=8080, help="Web server port (default: 8080)")

    # -- Sub-commands (optional so bare --refresh works).
    sub = parser.add_subparsers(dest="command", help="Available commands")

    craft_sub = sub.add_parser("craft", help="Crafting tree browser")
    craft_sub.add_argument("items", nargs="*", help="Item name(s) to look up")
    craft_sub.add_argument("--depth", type=int, default=99, help="Max recursion depth for expansion")
    craft_sub.add_argument("--select", type=int, default=None, help="Select match index when multiple")
    craft_sub.add_argument("--interactive", action="store_true", help="Interactive item selection")
    craft_sub.add_argument(
        "--weapon-chain", action="store_true", help="Analyze all weapon upgrade chains"
    )

    sub.add_parser("ducats", help="Prime part / ducats analyzer")
    sub.add_parser("cleanup", help="Inventory cleanup suggestions")
    sub.add_parser("update", help="Update export database")
    sub.add_parser("relics", help="Relic drop analysis")
    sub.add_parser("serve", help="Start web UI server")
    sub.add_parser("mastery", help="Mastery rank progression & farming guide")
    sub.add_parser("mods", help="List mods you don't own yet")

    foundry_sub = sub.add_parser("foundry", help="Export inventory for Warframe Foundry")
    foundry_sub.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output path for the Foundry import file (default: data/warframe-foundry.json)",
    )
    foundry_sub.add_argument(
        "--steel-xp",
        type=int,
        default=None,
        help="Total Steel Path mastery XP gained (converted to steel mission/junction counts)",
    )
    for _arg, _field in (
        ("--missions", "missionCount"),
        ("--junctions", "junctionCount"),
        ("--steel-missions", "steelMissionCount"),
        ("--steel-junctions", "steelJunctionCount"),
        ("--intrinsics", "intrinsicsCount"),
    ):
        foundry_sub.add_argument(
            _arg,
            type=int,
            default=None,
            help=f"Override the Foundry '{_field}' count (auto-derived otherwise)",
        )

    return parser


def main() -> int:
    """Parse CLI, select a sub-command, and dispatch.

    Returns:
        Exit code (0 on success, 1 on error).
    """
    parser = _build_parser()
    args = parser.parse_args()

    # Standalone --refresh (no sub-command): just fetch inventory and exit.
    if args.refresh and not args.command:
        inv_path = args.inventory or os.path.join(DATA_DIR, "inventory.json")
        load_inventory_with_fallback(inv_path, refresh=True)
        return 0

    # Dynamic import and dispatch to the target module.
    name = args.command
    if name not in _COMMAND_TABLE:
        parser.print_help()
        return 1

    mod_name, func_name = _COMMAND_TABLE[name]
    mod = importlib.import_module(mod_name)
    func = getattr(mod, func_name)

    func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
