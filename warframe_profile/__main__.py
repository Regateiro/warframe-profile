"""Entry point for ``python -m warframe_profile`` and ``python warframe.py``.

Parses exactly one of ``--craft``, ``--ducats``, ``--cleanup``,
``--update``, ``--relics`` and dispatches to the corresponding module.
All arguments are defined in a single parser so sub-commands never
need their own :class:`argparse.ArgumentParser`.
"""

import argparse
import importlib
import os
import sys

from warframe_profile import DATA_DIR


#: Maps CLI flag names to (module_path, function_name) pairs.
_COMMAND_TABLE = {
    "craft":   ("warframe_profile.presenter.craft_tree",        "main"),
    "ducats":  ("warframe_profile.presenter.cli",               "main"),
    "cleanup": ("warframe_profile.presenter.inventory_cleanup", "main"),
    "update":  ("warframe_profile.scripts.update_export_db",    "main"),
    "relics":  ("warframe_profile.presenter.cli",               "relics_main"),
    "serve":   ("warframe_profile.presenter.web_server",        "main"),
}


def _build_parser() -> argparse.ArgumentParser:
    """Build the single, unified argument parser for all sub-commands."""
    parser = argparse.ArgumentParser(description="Warframe Profile Tool")

    # -- Mode selector (exactly one required).
    mode = parser.add_argument_group("mode (exactly one required)")
    mode.add_argument("--craft",   action="store_true",
                      help="Crafting tree browser")
    mode.add_argument("--ducats",  action="store_true",
                      help="Prime part / ducats analyzer")
    mode.add_argument("--cleanup", action="store_true",
                      help="Inventory cleanup suggestions")
    mode.add_argument("--update",  action="store_true",
                      help="Update export database")
    mode.add_argument("--relics",  action="store_true",
                       help="Relic drop analysis")
    mode.add_argument("--serve",   action="store_true",
                       help="Start web UI server")

    # -- Shared options (used by multiple sub-commands).
    shared = parser.add_argument_group("shared options")
    shared.add_argument("--inventory", "-i",
                        help="Path to cached inventory.json")
    shared.add_argument("--refresh", "-r", action="store_true",
                        help="Fetch inventory from live Warframe process")
    shared.add_argument("--profile", "-p",
                        default="57c71e613ade7fa2a211997b",
                        help="DE account ID for player display")
    shared.add_argument("--items-cache",
                        default=os.path.join(DATA_DIR, "export_db.json"),
                        help="Path to the merged items export database")
    shared.add_argument("--refresh-items", action="store_true",
                        help="Delete and re-load the items cache")
    shared.add_argument("--port", type=int, default=8080,
                        help="Web server port (default: 8080)")

    # -- Craft-tree options (only meaningful with --craft).
    craft = parser.add_argument_group("craft-tree options")
    craft.add_argument("items", nargs="*",
                       help="Item name(s) to look up")
    craft.add_argument("--depth", type=int, default=99,
                       help="Max recursion depth for expansion")
    craft.add_argument("--select", type=int, default=None,
                       help="Select match index when multiple")
    craft.add_argument("--interactive", action="store_true",
                       help="Interactive item selection")
    craft.add_argument("--weapon-chain", action="store_true",
                       help="Analyze all weapon upgrade chains")

    return parser


def main() -> int:
    """Parse CLI, select a sub-command, and dispatch.

    Returns:
        Exit code (0 on success, 1 on error).
    """
    parser = _build_parser()
    args = parser.parse_args()

    # Find the single flag that was set.
    selected = [name for name in ("craft", "ducats", "cleanup",
                                   "update", "relics", "serve")
                if getattr(args, name)]
    if len(selected) != 1:
        parser.print_help()
        print("\nSpecify exactly one of: --craft, --ducats, --cleanup, "
              "--update, --relics, --serve", file=sys.stderr)
        return 1

    name = selected[0]
    mod_name, func_name = _COMMAND_TABLE[name]
    mod = importlib.import_module(mod_name)
    func = getattr(mod, func_name)

    func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
