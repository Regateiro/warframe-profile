#!/usr/bin/env python3
"""Download and merge Warframe export data (``--update``).

Downloads two data sources and merges them into a single file:

1. **DE public export** (from ``browse.wf``) — warframes, weapons,
   resources, recipes, and the language dictionary.
2. **WFCD items** (from ``github.com/WFCD/warframe-items``) —
   supplements with fields like ``isPrime``, ``marketCost``,
   ``bpCost``, and tradable component info.

The merged result is saved to ``data/export_db.json``.

Merge algorithm (see :func:`merge`):
    1. Walk each DE category dict, resolve language keys.
    2. Enrich each item with WFCD fields (isPrime, category, etc.).
    3. Strip bulky drop data from components.
    4. Add WFCD-only items that are Prime-related or have market costs.
"""

import json
import os
import sys
import urllib.request

from warframe_profile import DATA_DIR
from warframe_profile.model.inventory import load_inventory_with_fallback

#: Base URL for Digital Extremes' public export files.
DE_BASE = "https://browse.wf/warframe-public-export-plus/"

#: WFCD All.json URL (community-maintained item database).
WFCD_URL = "https://raw.githubusercontent.com/WFCD/warframe-items/master/data/json/All.json"

#: Mapping of DE export filenames → labels in the combined dict.
DE_FILES = {
    "ExportWarframes.json": "warframes",
    "ExportWeapons.json": "weapons",
    "ExportResources.json": "resources",
    "ExportCustoms.json": "customs",
    "ExportGear.json": "gear",
    "ExportSentinels.json": "sentinels",
    "ExportKeys.json": "keys",
    "ExportRecipes.json": "recipes",
    "dict.en.json": "dict",
}

#: Fields taken from WFCD items to enrich DE data.
WFCD_FIELDS = [
    "isPrime",
    "category",
    "marketCost",
    "bpCost",
    "tradable",
    "masterable",
    "tags",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def download_json(url: str) -> dict | list:
    """Download a JSON file from *url* and deserialise it."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def _strip_drops(components: list[dict]) -> list[dict]:
    """Strip bulky drop data from component entries, keeping only
    essential fields."""
    keep = {
        "uniqueName",
        "name",
        "itemCount",
        "tradable",
        "primeSellingPrice",
        "ducats",
    }
    return [{k: c[k] for k in keep if k in c} for c in components]


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------


def _index_wfcd_by_un(wfcd_items: list[dict]) -> dict[str, dict]:
    """Index WFCD items by uniqueName."""
    wfcd_by_un: dict[str, dict] = {}
    for item in wfcd_items:
        un = item.get("uniqueName", "")
        if un:
            wfcd_by_un[un] = item
    return wfcd_by_un


def _resolve_name(name_key: str, loc: dict) -> str:
    """Resolve a language-keyed name from the locale dict."""
    if name_key.startswith("/Lotus/Language/"):
        return loc.get(name_key, name_key.split("/")[-1])
    return name_key


def _enrich_from_wfcd(merged: dict, wfcd_item: dict | None) -> None:
    """Overlay WFCD fields and components onto *merged*."""
    if not wfcd_item:
        return
    for field in WFCD_FIELDS:
        if field in wfcd_item:
            merged[field] = wfcd_item[field]
    comps = wfcd_item.get("components")
    if comps:
        merged["components"] = _strip_drops(comps)


def _is_wfcd_item_relevant(wfcd_item: dict) -> bool:
    """Check whether a WFCD-only item should be included in the export."""
    cat = wfcd_item.get("category", "")
    has_prime_comps = any(
        c.get("primeSellingPrice") or c.get("ducats") for c in wfcd_item.get("components", [])
    )
    return (
        wfcd_item.get("isPrime")
        or wfcd_item.get("marketCost") is not None
        or wfcd_item.get("bpCost") is not None
        or "Prime" in cat
        or has_prime_comps
        or cat == "Relics"
    )


def _correct_amp_masterable(merged: dict, un: str) -> None:
    """Amp prisms contribute MR but WFCD marks them masterable=False."""
    if "OperatorAmplifiers" in un and "Barrel" in un:
        merged["masterable"] = True


def merge(export: dict, wfcd_items: list[dict]) -> dict:
    """Merge DE export data with WFCD item data."""
    wfcd_by_un = _index_wfcd_by_un(wfcd_items)
    loc: dict = export.get("dict", {})
    items: list[dict] = []
    seen: set[str] = set()

    de_categories = [v for k, v in export.items() if isinstance(v, dict) and k != "dict"]

    for cat_data in de_categories:
        for un, de_item in cat_data.items():
            seen.add(un)

            merged: dict = {
                "uniqueName": un,
                "name": _resolve_name(de_item.get("name", ""), loc),
                "productCategory": de_item.get("productCategory", ""),
                "masteryReq": de_item.get("masteryReq", 0),
            }
            _enrich_from_wfcd(merged, wfcd_by_un.get(un))
            _correct_amp_masterable(merged, un)
            items.append(merged)

    for un, wfcd_item in wfcd_by_un.items():
        if un in seen:
            continue
        if not _is_wfcd_item_relevant(wfcd_item):
            continue

        merged: dict = {
            "uniqueName": un,
            "name": wfcd_item.get("name", un.split("/")[-1]),
        }
        _enrich_from_wfcd(merged, wfcd_item)
        rewards = wfcd_item.get("rewards")
        if rewards:
            merged["rewards"] = rewards
        _correct_amp_masterable(merged, un)
        items.append(merged)

    result = dict(export)
    result["items"] = items
    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main(args=None) -> None:
    """Download DE export + WFCD items, merge, and save to ``data/export_db.json``.

    Flow:
        1. Determine output path (default: ``data/export_db.json``).
        2. Optionally remove old cache (``--refresh-items``).
        3. Download all DE export JSON files from ``browse.wf``.
        4. Download WFCD ``All.json`` from GitHub.
        5. Merge both sources via :func:`merge`.
        6. Write merged result to disk.
        7. Optionally refresh player inventory (``--refresh``).

    When *args* is provided, the following flags are respected:

    * ``--items-cache`` / ``-i`` — output path for the merged database.
    * ``--refresh-items`` — remove the old cache before downloading.
    * ``--refresh`` / ``-r`` — also refresh the player inventory after updating.
    """
    combined: dict = {}

    # Determine output path.
    if args is not None and getattr(args, "items_cache", None):
        out_path = args.items_cache
    else:
        out_path = os.path.join(DATA_DIR, "export_db.json")

    # --refresh-items: delete old cache before downloading.
    if args is not None and getattr(args, "refresh_items", False):
        if os.path.exists(out_path):
            print(f"Removing {out_path} ...", file=sys.stderr)
            os.remove(out_path)

    # Download DE export files.
    for fname, label in DE_FILES.items():
        url = DE_BASE + fname
        print(f"Downloading {url} ...", file=sys.stderr)
        combined[label] = download_json(url)
        count = len(combined[label]) if isinstance(combined[label], dict) else len(combined[label])
        print(f"  {count} items", file=sys.stderr)

    # Download WFCD items.
    print("Downloading WFCD items ...", file=sys.stderr)
    wfcd = download_json(WFCD_URL)
    print(f"  {len(wfcd)} items", file=sys.stderr)

    # Merge.
    print("Merging...", file=sys.stderr)
    merged = merge(combined, wfcd)

    # Write output.
    with open(out_path, "w") as f:
        json.dump(merged, f, indent=2)

    n_items = len(merged["items"])
    n_dict = len(merged.get("dict", {}))
    print(
        f"\nSaved {out_path} ({n_items} merged items, {n_dict} dict entries)",
        file=sys.stderr,
    )

    # --refresh / -r: also refresh the player inventory.
    if args is not None and getattr(args, "refresh", False):
        inv_path = os.path.join(DATA_DIR, "inventory.json")
        print("Refreshing inventory ...", file=sys.stderr)
        load_inventory_with_fallback(inv_path, refresh=True)


if __name__ == "__main__":
    main()
