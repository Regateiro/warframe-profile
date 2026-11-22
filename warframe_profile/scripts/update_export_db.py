#!/usr/bin/env python3
"""Download and merge Warframe export data (``update``).

Downloads two data sources and merges them into a single file:

1. **DE public export** (from DE's official ``origin``/``content`` servers) —
   warframes, weapons, resources, recipes, and related item categories.
2. **WFCD items** (from ``github.com/WFCD/warframe-items``) —
   supplements with fields like ``isPrime``, ``marketCost``,
   ``bpCost``, and tradable component info.

The merged result is saved to ``data/export_db.json``.

The official export is discovered through the language index
(``PublicExport/index_en.txt.lzma``), which points at content-addressed
manifests on ``content.warframe.com``.  This avoids depending on third-party
mirrors that can lag behind DE's releases.

Merge algorithm (see :func:`merge`):
    1. Walk each DE category dict, resolve language keys.
    2. Enrich each item with WFCD fields (isPrime, category, etc.).
    3. Strip bulky drop data from components.
    4. Add WFCD-only items (mods, relics, etc.) that DE does not export.

The official export already contains plain display names, so no separate
locale dictionary is merged.
"""

import json
import lzma
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TypeVar

from warframe_profile import DATA_DIR
from warframe_profile.model.inventory import load_inventory_with_fallback
from warframe_profile.model.utils import atomic_write_json

#: Official DE index; the manifest names are content-addressed and change
#: every time DE publishes an update.
DE_INDEX_BASE = "https://origin.warframe.com/PublicExport/"

#: Official DE manifest host.  Hashed manifest names come from the index.
DE_CONTENT_BASE = "https://content.warframe.com/PublicExport/Manifest/"

#: Language of the official export to download.
DE_LANGUAGE = "en"

#: Base URL for WFCD's per-category JSON files (community-maintained item database).
WFCD_BASE = "https://raw.githubusercontent.com/WFCD/warframe-items/master/data/json/"

#: WFCD category files. Upstream no longer publishes a combined ``All.json``,
#: so we download each category and concatenate them ourselves.
WFCD_FILES = [
    "Arcanes.json",
    "Arch-Gun.json",
    "Arch-Melee.json",
    "Archwing.json",
    "Components.json",
    "Enemy.json",
    "Fish.json",
    "Gear.json",
    "Glyphs.json",
    "Honoria.json",
    "Melee.json",
    "Misc.json",
    "Mods.json",
    "Node.json",
    "Pets.json",
    "Primary.json",
    "Quests.json",
    "Railjack.json",
    "Relics.json",
    "Resources.json",
    "Secondary.json",
    "SentinelWeapons.json",
    "Sentinels.json",
    "Sigils.json",
    "Skins.json",
    "Warframes.json",
]

#: Mapping of official DE manifest stems → labels in the combined dict.
#: The top-level key in each manifest is the same as its stem.
DE_FILES = {
    "ExportWarframes": "warframes",
    "ExportWeapons": "weapons",
    "ExportResources": "resources",
    "ExportCustoms": "customs",
    "ExportGear": "gear",
    "ExportSentinels": "sentinels",
    "ExportKeys": "keys",
    "ExportRecipes": "recipes",
}

#: Labels for item-only categories in the export (excludes recipes).
#: Used by :func:`craft_model.build_items_by_un` to iterate item sources.
DE_ITEM_LABELS = [v for v in DE_FILES.values() if v != "recipes"]

#: Fields taken from WFCD items to enrich DE data.
WFCD_FIELDS = [
    "isPrime",
    "category",
    "marketCost",
    "bpCost",
    "tradable",
    "masterable",
    "tags",
    # Mod-specific fields
    "type",
    "rarity",
    "polarity",
    "baseDrain",
    "fusionLimit",
    "compatName",
    "isAugment",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


#: Number of concurrent download workers.  The update is network-bound and
#: downloads ~35 files, so a small pool cuts wall time substantially.
MAX_WORKERS = 8


def download_bytes(url: str) -> bytes:
    """Download *url* and return the raw response body."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    # Callers only pass hardcoded DE/WFCD URLs, not user input.
    # semgrepignore
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def download_json(url: str) -> dict | list:
    """Download a JSON file from *url* and deserialise it."""
    data = download_bytes(url)
    try:
        return json.loads(data)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON from {url}: {exc}") from exc


_T = TypeVar("_T")


def _retry_call(
    operation: Callable[[], _T],
    url: str,
    attempts: int = 3,
    backoff: float = 1.5,
) -> _T:
    """Run *operation*, retrying transient download/parse failures.

    A single flaky response previously aborted the whole update after it had
    already downloaded most of the data, so retries are worth the extra code.
    """
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            ValueError,
            lzma.LZMAError,
        ) as exc:
            last_exc = exc
            if attempt == attempts:
                break
            delay = backoff ** (attempt - 1)
            print(
                f"  warning: {url} failed ({exc}); retrying in {delay:.1f}s",
                file=sys.stderr,
            )
            time.sleep(delay)
    assert last_exc is not None  # the loop always runs at least once
    raise last_exc


def download_json_with_retry(url: str, attempts: int = 3, backoff: float = 1.5) -> dict | list:
    """Download and decode *url*, retrying transient failures."""
    return _retry_call(lambda: download_json(url), url, attempts, backoff)


def download_de_index(language: str = DE_LANGUAGE) -> dict[str, str]:
    """Download DE's compressed export index.

    Returns a mapping of manifest filename (e.g. ``ExportWeapons_en.json``) to
    its content-addressed name (``ExportWeapons_en.json!00_...``).  The hashed
    name must be appended to :data:`DE_CONTENT_BASE` to fetch the manifest.
    """
    url = f"{DE_INDEX_BASE}index_{language}.txt.lzma"

    def parse_index() -> dict[str, str]:
        raw = download_bytes(url)
        text = lzma.decompress(raw).decode("utf-8")
        index: dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or "!" not in line:
                continue
            filename = line.split("!", 1)[0]
            index[filename] = line
        return index

    return _retry_call(parse_index, url)


def normalize_de_manifest(stem: str, data: dict) -> dict[str, dict]:
    """Normalise a DE manifest to a ``uniqueName → entry`` dict.

    The rest of the merge pipeline expects a mapping keyed by uniqueName.
    Official manifests wrap their entries in a top-level key matching the
    filename stem (``{"ExportWeapons": [...]}``), so convert them here.
    """
    entries = data.get(stem, [])
    return {
        entry["uniqueName"]: entry
        for entry in entries
        if isinstance(entry, dict)
        and isinstance(entry.get("uniqueName"), str)
        and entry["uniqueName"]
    }


def download_de_export(language: str = DE_LANGUAGE) -> dict:
    """Download and normalise every configured DE manifest.

    Returns a ``{label: {uniqueName: entry}}`` mapping for the merge pipeline.
    Official manifests already contain plain display names, so no separate
    locale dictionary is needed.
    """
    index = download_de_index(language)
    combined: dict = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        future_to_stem = {}
        for stem in DE_FILES:
            filename = f"{stem}_{language}.json"
            manifest = index.get(filename)
            if not manifest:
                raise ValueError(f"DE export index is missing {filename}")
            future = pool.submit(download_json_with_retry, DE_CONTENT_BASE + manifest)
            future_to_stem[future] = stem

        for future in as_completed(future_to_stem):
            stem = future_to_stem[future]
            label = DE_FILES[stem]
            combined[label] = normalize_de_manifest(stem, future.result())
            print(f"  {label}: {len(combined[label])} entries", file=sys.stderr)
    return combined


def download_wfcd_items() -> list[dict]:
    """Download every WFCD category file and concatenate them into one list."""
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        # ``map`` preserves WFCD_FILES order, keeping the merge deterministic.
        results = list(pool.map(download_json_with_retry, (WFCD_BASE + f for f in WFCD_FILES)))

    items: list[dict] = []
    for data in results:
        if isinstance(data, list):
            items.extend(data)
        elif isinstance(data, dict):
            items.extend(data.values())
    return items


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


def _enrich_component(comp: dict, de_by_un: dict[str, dict], loc: dict) -> None:
    """Fill in ``name`` / ``tradable`` / ``primeSellingPrice`` for *comp*.

    WFCD components carry only ``uniqueName`` + ``itemCount``.  The
    human-readable name, tradability, and ducat sell price live on the
    matching DE public-export item (indexed by uniqueName).

    Blueprints have no ``name`` of their own, so it is derived from the
    parent item's name (``resultType``) with a ``" Blueprint"`` suffix.
    """
    de = de_by_un.get(comp.get("uniqueName", ""))
    if not de:
        return
    if "tradable" in de:
        comp["tradable"] = de["tradable"]
    if de.get("primeSellingPrice"):
        comp["primeSellingPrice"] = de["primeSellingPrice"]

    name = _resolve_name(de["name"], loc) if de.get("name") else ""
    if not name and de.get("resultType"):
        parent = de_by_un.get(de["resultType"], {})
        pname = (
            _resolve_name(parent["name"], loc)
            if parent.get("name")
            else de["resultType"].split("/")[-1]
        )
        name = pname + " Blueprint"
    if not name:
        name = comp.get("uniqueName", "").split("/")[-1]
    comp["name"] = name


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
        or cat == "Mods"
    )


def _correct_amp_masterable(merged: dict, un: str) -> None:
    """Amp prisms contribute MR but WFCD marks them masterable=False.

    Only the actual prism items (not their blueprints) count for MR."""
    if "OperatorAmplifiers" in un and "Barrel" in un and "Blueprint" not in un:
        merged["masterable"] = True


def merge(export: dict, wfcd_items: list[dict]) -> dict:
    """Merge DE export data with WFCD item data.

    The output keeps only ``items`` (the merged list), ``recipes`` and
    ``dict`` — the raw per-category DE sections are not duplicated.
    """
    wfcd_by_un = _index_wfcd_by_un(wfcd_items)
    loc: dict = export.get("dict", {})

    # Index every DE export item by uniqueName.  Used to enrich components
    # (name / tradable / ducat price), which WFCD does not carry.
    de_by_un: dict[str, dict] = {}
    for label in DE_FILES.values():
        cat_data = export.get(label)
        if not isinstance(cat_data, dict):
            continue
        for un, it in cat_data.items():
            if isinstance(it, dict) and un not in de_by_un:
                de_by_un[un] = it

    items: list[dict] = []
    seen: set[str] = set()

    for label in DE_ITEM_LABELS:
        cat_data = export.get(label)
        if not isinstance(cat_data, dict):
            continue
        for un, de_item in cat_data.items():
            if un in seen:
                continue
            seen.add(un)

            merged: dict = {
                "uniqueName": un,
                "name": _resolve_name(de_item.get("name", ""), loc),
                "productCategory": de_item.get("productCategory", ""),
                "masteryReq": de_item.get("masteryReq", 0),
                "_source": label,
            }
            _enrich_from_wfcd(merged, wfcd_by_un.get(un))

            # Fallback: if the name is still empty, try stripping "Blueprint"
            # and looking up the root item in WFCD (e.g. amp prism parts).
            if not merged.get("name") and un.endswith("Blueprint"):
                root_un = un[: -len("Blueprint")]
                root_wfcd = wfcd_by_un.get(root_un)
                if root_wfcd:
                    merged["name"] = root_wfcd.get("name", "")
                if not merged.get("name"):
                    merged["name"] = un.split("/")[-1]

            _correct_amp_masterable(merged, un)
            items.append(merged)

    for un, wfcd_item in wfcd_by_un.items():
        if un in seen:
            continue
        if not _is_wfcd_item_relevant(wfcd_item):
            continue

        merged: dict = {
            "uniqueName": un,
            "name": wfcd_item.get("name") or un.split("/")[-1],
        }
        _enrich_from_wfcd(merged, wfcd_item)
        rewards = wfcd_item.get("rewards")
        if rewards:
            merged["rewards"] = rewards
        _correct_amp_masterable(merged, un)
        items.append(merged)

    # WFCD components lack name/tradable/ducats; fill them from the DE export.
    for item in items:
        for comp in item.get("components", []):
            _enrich_component(comp, de_by_un, loc)

    return {
        "items": items,
        "recipes": export.get("recipes", {}),
        "dict": loc,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main(args=None) -> None:
    """Download DE export + WFCD items, merge, and save to ``data/export_db.json``.

    Flow:
        1. Determine output path (default: ``data/export_db.json``).
        2. Optionally remove old cache (``--refresh-items``).
        3. Download the official DE export index and its manifests.
        4. Download WFCD items (one JSON file per category) from GitHub.
        5. Merge both sources via :func:`merge`.
        6. Write merged result to disk.
        7. Optionally refresh player inventory (``--refresh``).

    When *args* is provided, the following flags are respected:

    * ``--items-cache`` / ``-i`` — output path for the merged database.
    * ``--refresh-items`` — remove the old cache before downloading.
    * ``--refresh`` / ``-r`` — also refresh the player inventory after updating.
    """
    # Determine output path.
    if args is not None and getattr(args, "items_cache", None):
        out_path = args.items_cache
    else:
        out_path = os.path.join(DATA_DIR, "export_db.json")

    # --refresh-items: delete old cache before downloading.
    if args is not None and getattr(args, "refresh_items", False):
        if os.path.exists(out_path):
            print(f"Removing {out_path} ...", file=sys.stderr)
            try:
                os.remove(out_path)
            except OSError as exc:
                print(f"warning: could not remove {out_path}: {exc}", file=sys.stderr)

    # Download DE export manifests from the official index.
    print(f"Downloading {len(DE_FILES)} DE export files ...", file=sys.stderr)
    combined = download_de_export()

    # Download WFCD items.
    print("Downloading WFCD items ...", file=sys.stderr)
    wfcd = download_wfcd_items()
    print(f"  {len(wfcd)} items", file=sys.stderr)

    # Merge.
    print("Merging...", file=sys.stderr)
    merged = merge(combined, wfcd)

    # Write output atomically so an interrupt cannot truncate the cache.
    try:
        atomic_write_json(out_path, merged, separators=(",", ":"))
    except OSError as e:
        print(f"  Error: could not write {out_path}: {e}", file=sys.stderr)
        sys.exit(1)

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
