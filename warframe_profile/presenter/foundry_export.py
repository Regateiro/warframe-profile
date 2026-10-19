"""Warframe Foundry export sub-command (``foundry``).

Builds a ``warframe-foundry.json`` file in the import format accepted by
https://warframe-foundry.app/ so the player can transfer their inventory,
mastery, and resources into the Foundry tracker.

Flow:
    1. Load the export DB and inventory (live or cached).
    2. Build the Foundry export dict (:func:`build_foundry_export`).
    3. Write it to ``data/warframe-foundry.json`` (or ``--output``).
"""

import argparse
import json
import os

from warframe_profile import DATA_DIR
from warframe_profile.model.foundry_export import (
    build_foundry_export,
    foundry_mastery_points,
    mastery_rank,
    steel_settings_from_xp,
)
from warframe_profile.model.inventory import load_data

#: Default output path for the Foundry import file.
DEFAULT_OUTPUT = os.path.join(DATA_DIR, "warframe-foundry.json")

#: Settings fields accepted on the command line, mapped to Foundry field names.
_SETTINGS_ARG_TO_FIELD = {
    "missions": "missionCount",
    "junctions": "junctionCount",
    "steel_missions": "steelMissionCount",
    "steel_junctions": "steelJunctionCount",
    "intrinsics": "intrinsicsCount",
}


def _settings_from_args(args) -> dict:
    """Collect explicit ``--<setting>`` overrides from the parsed *args*.

    ``--steel-xp`` is converted into steel mission/junction counts
    (:func:`steel_settings_from_xp`) so the player only needs the one number
    the game exposes.
    """
    overrides = {
        field: getattr(args, arg)
        for arg, field in _SETTINGS_ARG_TO_FIELD.items()
        if getattr(args, arg, None) is not None
    }
    steel_xp = getattr(args, "steel_xp", None)
    if steel_xp is not None:
        overrides.update(steel_settings_from_xp(steel_xp))
    return overrides


def main(args) -> None:
    """Entry point for the ``foundry`` sub-command."""
    db, inv = load_data(
        args.items_cache,
        args.refresh_items,
        args.inventory,
        args.refresh,
    )

    export = build_foundry_export(db, inv, settings=_settings_from_args(args))
    out_path = getattr(args, "output", None) or DEFAULT_OUTPUT
    with open(out_path, "w") as f:
        json.dump(export, f, indent=2)

    settings = export["settings"]
    points = foundry_mastery_points(db, export["items"]["data"], settings)
    rank = mastery_rank(points)
    next_rank = 2500 * (rank + 1) * (rank + 1)
    n_built = sum(1 for v in export["items"]["data"].values() if v.get("built"))
    n_mastered = sum(1 for v in export["items"]["data"].values() if v.get("mastered"))
    print(f"  Wrote {out_path}")
    print(f"  {n_built} items built, {n_mastered} mastered, {len(export['resources'])} resources")
    print(
        "  Foundry settings: {} missions, {} junctions, {} steel missions, "
        "{} steel junctions, {} intrinsics".format(
            settings["missionCount"],
            settings["junctionCount"],
            settings["steelMissionCount"],
            settings["steelJunctionCount"],
            settings["intrinsicsCount"],
        )
    )
    if getattr(args, "steel_xp", None) is not None:
        print(
            "  Steel Path mastery: {} pts (from --steel-xp {})".format(
                settings["steelMissionCount"] * 63
                + settings["steelJunctionCount"] * 1000,
                args.steel_xp,
            )
        )
    print(
        f"  Estimated mastery: {points:,} points (MR {rank}, "
        f"{next_rank - points:,} to MR {rank + 1})"
    )
    print("  Import it via the 'Import Data' button at https://warframe-foundry.app/")
    print(
        "  Tip: Steel Path isn't in the inventory export — pass --steel-xp "
        "<xp> (your Steel Path mastery gained)."
    )


if __name__ == "__main__":
    import sys

    from warframe_profile.model.inventory import (
        InventoryFetchError,
        ItemCacheNotFoundError,
        WarframeNotRunningError,
    )

    parser = argparse.ArgumentParser(description="Export inventory for Warframe Foundry")
    parser.add_argument("--inventory", "-i", help="Path to cached inventory.json")
    parser.add_argument("--refresh", "-r", action="store_true", help="Fetch live inventory")
    parser.add_argument(
        "--items-cache",
        default=os.path.join(DATA_DIR, "export_db.json"),
        help="Path to the merged items export database",
    )
    parser.add_argument(
        "--refresh-items", action="store_true", help="Delete and re-load the items cache"
    )
    parser.add_argument(
        "--output",
        "-o",
        default=DEFAULT_OUTPUT,
        help="Output path for the Foundry import file",
    )
    for arg, field in _SETTINGS_ARG_TO_FIELD.items():
        parser.add_argument(
            f"--{arg.replace('_', '-')}",
            type=int,
            default=None,
            help=f"Override the Foundry '{field}' count",
        )
    parser.add_argument(
        "--steel-xp",
        type=int,
        default=None,
        help="Total Steel Path mastery XP gained; converted to steel mission/"
        "junction counts",
    )
    args = parser.parse_args()
    try:
        main(args)
    except (ItemCacheNotFoundError, InventoryFetchError, WarframeNotRunningError) as e:
        print(f"  Error: {e}", file=sys.stderr)
        sys.exit(1)
