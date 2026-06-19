"""Mastery Rank progression analysis (``--mastery``).

Cross-references the player's XP history (from DE profile data) against
the masterable items in the game database to identify true gaps — items
that have never been ranked up — and displays them grouped by farming
difficulty tier.
"""

import os
import sys

from prettytable import PrettyTable, TableStyle

from warframe_profile import DATA_DIR
from warframe_profile.model.inventory import (
    ExportDB, load_data, load_inventory,
    EQUIPMENT_SECTIONS,
)


# ---------------------------------------------------------------------------
# Tier definitions — ordered easiest → hardest
# ---------------------------------------------------------------------------

TIERS = [
    {
        "id": "1",
        "label": "Tier 1 — Sentinels, Pets, K-Drives, Kitguns",
        "desc": "Quick wins: buy blueprints from Market or Fortuna, easy materials",
        "filter": lambda i: (
            i.get("category") in ("Sentinels", "Pets")
            or (i.get("category") == "Misc")
        ),
    },
    {
        "id": "2",
        "label": "Tier 2 — Archwing & Arch-Melee",
        "desc": "Clan research / Archwing missions / Cephalon Simaris",
        "filter": lambda i: i.get("category") in ("Archwing", "Arch-Melee"),
    },
    {
        "id": "3",
        "label": "Tier 3 — Syndicate Weapons & Arch-Gun",
        "desc": "Spend syndicate standing; Arch-guns from syndicates, clan research, vents",
        "filter": lambda i: (
            (_has_tag(i, "Syndicate") or i.get("category") == "Arch-Gun")
            and not _is_lich_item(i)
        ),
    },
    {
        "id": "4",
        "label": "Tier 4 — Kuva / Tenet / Coda Weapons (4,000 MR each)",
        "desc": "Lich / Sister / Technocyte Coda system — high MR density",
        "filter": lambda i: _is_lich_item(i),
    },
    {
        "id": "5",
        "label": "Tier 5 — Prime Weapons (Relics)",
        "desc": "Farm relics / radshares; RNG-dependent",
        "filter": lambda i: (
            i.get("isPrime")
            and i.get("category") in ("Primary", "Secondary", "Melee")
        ),
    },
    {
        "id": "6",
        "label": "Tier 6 — Prime Warframes (Relics)",
        "desc": "Farm relics / radshares; 6,000 MR each",
        "filter": lambda i: (
            i.get("isPrime")
            and i.get("category") == "Warframes"
        ),
    },
    {
        "id": "7",
        "label": "Tier 7 — Everything Else",
        "desc": "Market blueprints, Dojo research, quests, invasions",
        "filter": lambda i: True,
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_tag(item: dict, tag: str) -> bool:
    tags = item.get("tags") or []
    return any(tag in str(t) for t in tags)


def _is_lich_item(item: dict) -> bool:
    """Return True if this is a Kuva / Tenet / Coda weapon (rank 40)."""
    name = item.get("name", "")
    return any(name.startswith(p) for p in ("Kuva ", "Tenet ", "Coda ", "Dual Coda "))


def _item_mr(item: dict) -> int:
    """Return the total Mastery Rank contribution of a single item.

    * Warframes, Archwings, Sentinels, Pets: 6,000 (rank 30).
    * Kuva / Tenet / Coda weapons: 4,000 (rank 40).
    * Everything else: 3,000 (rank 30).
    """
    cat = item.get("category", "")
    if cat in ("Warframes", "Archwing", "Sentinels", "Pets"):
        return 6_000
    if _is_lich_item(item):
        return 4_000
    return 3_000


def _tier_index(item: dict) -> int:
    """Return which TIERS entry this item falls into (lowest match)."""
    for i, tier in enumerate(TIERS):
        if tier["filter"](item):
            return i
    return len(TIERS) - 1


# ---------------------------------------------------------------------------
# Mastery analysis
# ---------------------------------------------------------------------------

def _build_ever_leveled(inv: dict) -> set[str]:
    """Build a set of all item paths the player has ever ranked up.

    Sources (in priority order):
    1. ``XPInfo`` from the DE profile viewing API — the canonical list
       of every item that has contributed XP, even those already sold.
       This is only available after a ``--refresh``.
    2. Any item in the entire inventory that has an ``XP`` field > 0 —
       catches items that were formad / rank-up without requiring a live
       fetch (works from cached data).
    3. Items in equipment sections — owned items that have at least been
       equipped.
    """
    leveled: set[str] = set()

    # Source 1: XPInfo from profile (mastered-and-sold items).
    xp_info = inv.get("XPInfo") or []
    for entry in xp_info:
        path = (entry.get("ItemType") or "").lower()
        if path:
            leveled.add(path)

    # Source 2: any item with XP > 0 anywhere in the inventory blob.
    def _walk(obj):
        if isinstance(obj, dict):
            if "ItemType" in obj and obj.get("XP"):
                leveled.add(obj["ItemType"].lower())
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for v in obj:
                _walk(v)

    _walk(inv)

    # Source 3: items in equipment sections (fallback for items without
    # XP field — being in an equipment slot implies at least some use).
    for sect in EQUIPMENT_SECTIONS:
        for eq in inv.get(sect, []):
            path = (eq.get("ItemType") or "").lower()
            if path:
                leveled.add(path)

    return leveled


def analyze_mastery(
    db: ExportDB,
    inv: dict,
) -> tuple[int, int, list[tuple[dict, int]]]:
    """Cross-reference player progress against the masterable item list.

    Args:
        db:  Loaded game export database.
        inv: Player inventory dict (may contain ``XPInfo`` from profile).

    Returns:
        ``(total_masterable, mastered_count, [(item_dict, tier_index), ...])``
        where the last list contains only *unmastered* items, each paired
        with its tier index for sorting.
    """
    ever_leveled = _build_ever_leveled(inv)

    # These items are flagged as masterable in the export DB but can never
    # be obtained (founder exclusives).  Filter them out so they don't
    # appear as false "gaps" in the report.
    _UNOBTAINABLE_PATHS = frozenset({
        "/Lotus/Powersuits/Excalibur/ExcaliburPrime",       # Excalibur Prime
        "/Lotus/Weapons/Tenno/Pistol/LatoPrime",            # Lato Prime
        "/Lotus/Weapons/Tenno/Melee/LongSword/SkanaPrime",  # Skana Prime
    })

    masterable = [
        i for i in db.items
        if i.get("masterable") is True
        and i.get("uniqueName")
        and i["uniqueName"] not in _UNOBTAINABLE_PATHS
    ]

    mastered = 0
    unmastered: list[tuple[dict, int]] = []

    for item in masterable:
        un = item["uniqueName"].lower()
        if un in ever_leveled:
            mastered += 1
        else:
            unmastered.append((item, _tier_index(item)))

    unmastered.sort(key=lambda pair: (pair[1], pair[0].get("name", "")))

    return len(masterable), mastered, unmastered


# ---------------------------------------------------------------------------
# Report printing
# ---------------------------------------------------------------------------

def print_report(
    total: int,
    mastered: int,
    unmastered: list[tuple[dict, int]],
) -> None:
    """Print the full mastery progression report."""
    remaining = total - mastered

    print("=" * 68)
    print("  MASTERY PROGRESSION REPORT")
    print("=" * 68)
    print(f"  {mastered} / {total} masterable items mastered "
          f"({mastered * 100 // total}%)")
    print(f"  {remaining} items never ranked up")

    total_mr = sum(_item_mr(item) for item, _ in unmastered)
    print(f"  ~{total_mr:,} MR available from unmastered items")
    print()

    # Group unmastered items by tier for display.
    tier_items: list[list[dict]] = [[] for _ in TIERS]
    for item, t_idx in unmastered:
        tier_items[t_idx].append(item)

    for t_idx, (tier, items) in enumerate(zip(TIERS, tier_items)):
        if not items:
            continue

        count = len(items)
        mr = sum(_item_mr(i) for i in items)
        print(f"{'─' * 68}")
        print(f"  {tier['label']}")
        print(f"  {tier['desc']}")
        print(f"  {count} items  ~{mr:,} MR")
        print()

        t = PrettyTable()
        t.set_style(TableStyle.DEFAULT)
        t.field_names = ["Item", "Category", "MR"]
        t.align["Item"] = "l"
        t.align["Category"] = "l"
        t.align["MR"] = "r"

        for item in items:
            name = item.get("name", item.get("uniqueName", "?"))
            cat = item.get("category", "")
            mr_val = _item_mr(item)
            t.add_row([name, cat, mr_val])

        print(t)
        print()

    print("=" * 68)
    print(f"  {remaining} items remaining  ~{total_mr:,} MR to earn")
    print("=" * 68)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args) -> None:
    """Entry point for the ``--mastery`` sub-command."""
    db, inv = load_data(
        args.items_cache, args.refresh_items,
        args.inventory, args.refresh,
    )

    total, mastered, unmastered = analyze_mastery(db, inv)
    print_report(total, mastered, unmastered)
