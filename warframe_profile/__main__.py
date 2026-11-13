"""Entry point for ``python -m warframe_profile`` and ``python warframe.py``.

Dispatches to the corresponding module based on the selected sub-command.
"""

import argparse
import importlib
import os
import sys

from warframe_profile import DATA_DIR
from warframe_profile.compat import configure_stdio
from warframe_profile.model.errors import (
    InventoryFetchError,
    ItemCacheNotFoundError,
    WarframeNotRunningError,
)
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


#: Default for shared options on subparsers.  ``SUPPRESS`` keeps the
#: subparser from overwriting a value that was already parsed by the root
#: parser (argparse copies every subnamespace attribute back onto the main
#: namespace, including defaults).
_SUB_DEFAULT = argparse.SUPPRESS


def _add_shared_args(parser: argparse.ArgumentParser, *, suppress_defaults: bool) -> None:
    """Add options accepted by every sub-command to *parser*.

    The same set is registered on the root parser and on each subparser, so
    shared flags work on either side of the command name (e.g. both
    ``warframe ducats --refresh`` and ``warframe --refresh ducats``).
    Subparsers use :data:`_SUB_DEFAULT` so an absent flag does not clobber a
    value supplied before the command.
    """

    def default(value):
        return _SUB_DEFAULT if suppress_defaults else value

    shared = parser.add_argument_group("shared options")
    shared.add_argument(
        "--inventory", "-i", default=default(None), help="Path to cached inventory.json"
    )
    shared.add_argument(
        "--refresh",
        "-r",
        action="store_true",
        default=default(False),
        help="Fetch inventory from live Warframe process",
    )
    shared.add_argument(
        "--profile",
        "-p",
        default=default(None),
        help="DE account ID for player display (default: the live account, if known)",
    )
    shared.add_argument(
        "--items-cache",
        default=default(os.path.join(DATA_DIR, "export_db.json")),
        help="Path to the merged items export database",
    )
    shared.add_argument(
        "--refresh-items",
        action="store_true",
        default=default(False),
        help="Delete and re-load the items cache",
    )
    shared.add_argument(
        "--port",
        type=int,
        default=default(None),
        help="Web server port (default: 8090, or $WARFRAME_WEB_PORT)",
    )
    shared.add_argument(
        "--host",
        default=default(None),
        help="Web server bind address (default: 127.0.0.1, or $WARFRAME_WEB_HOST)",
    )


def _build_parser() -> argparse.ArgumentParser:
    """Build the single, unified argument parser for all sub-commands."""
    parser = argparse.ArgumentParser(
        description="Warframe Profile Tool (runs the web UI when no sub-command is given)"
    )
    _add_shared_args(parser, suppress_defaults=False)

    # -- Sub-commands (optional so bare --refresh works).
    sub = parser.add_subparsers(dest="command", help="Available commands")

    def add_command(name: str, help_text: str) -> argparse.ArgumentParser:
        command_parser = sub.add_parser(name, help=help_text)
        _add_shared_args(command_parser, suppress_defaults=True)
        return command_parser

    craft_sub = add_command("craft", "Crafting tree browser")
    craft_sub.add_argument("items", nargs="*", help="Item name(s) to look up")
    craft_sub.add_argument(
        "--depth", type=int, default=99, help="Max recursion depth for expansion"
    )
    craft_sub.add_argument(
        "--select", type=int, default=None, help="Select match index when multiple"
    )
    craft_sub.add_argument("--interactive", action="store_true", help="Interactive item selection")
    craft_sub.add_argument(
        "--weapon-chain", action="store_true", help="Analyze all weapon upgrade chains"
    )

    add_command("ducats", "Prime part / ducats analyzer")
    add_command("cleanup", "Inventory cleanup suggestions")
    add_command("update", "Update export database")
    add_command("relics", "Relic drop analysis")
    add_command("serve", "Start web UI server (default when no command is given)")
    add_command("mastery", "Mastery rank progression & farming guide")
    add_command("mods", "List mods you don't own yet")

    foundry_sub = add_command("foundry", "Export inventory for Warframe Foundry")
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
    configure_stdio()
    parser = _build_parser()
    args = parser.parse_args()

    # Standalone --refresh (no sub-command): just fetch inventory and exit.
    if args.refresh and not args.command:
        inv_path = args.inventory or os.path.join(DATA_DIR, "inventory.json")
        try:
            load_inventory_with_fallback(inv_path, refresh=True)
        except (WarframeNotRunningError, InventoryFetchError) as exc:
            print(f"  Error: {exc}", file=sys.stderr)
            return 1
        return 0

    # No sub-command: open the web UI, which mirrors the CLI analyses.
    if not args.command:
        args.command = "serve"

    # Dynamic import and dispatch to the target module.
    name = args.command
    if name not in _COMMAND_TABLE:
        parser.print_help()
        return 1

    mod_name, func_name = _COMMAND_TABLE[name]
    # mod_name comes only from the _COMMAND_TABLE whitelist above (validated
    # against args.command), so this dynamic import is safe.
    mod = importlib.import_module(mod_name)  # nosemgrep
    func = getattr(mod, func_name)

    try:
        func(args)
    except (ItemCacheNotFoundError, InventoryFetchError, WarframeNotRunningError) as exc:
        print(f"  Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
