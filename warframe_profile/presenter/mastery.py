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
    ExportDB, load_data,
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
# XP helpers — estimate max rank from cumulative XP
# ---------------------------------------------------------------------------

def _max_xp_for_item(item: dict) -> int:
    """Return the expected cumulative XP when *item* reaches max rank.

    Warframe's XP-per-rank curve is linear:
        XP needed for rank N = N × base_rate

    Total cumulative XP at rank R = base × R × (R + 1) / 2.

    * Warframes, Archwings, Sentinels, Pets, Necramechs → base 2 000 → 930 000
    * Kuva / Tenet / Coda weapons (rank 40)           → base 1 000 → 820 000
    * Everything else (weapons, amps, etc.)            → base 1 000 → 465 000
    """
    cat = item.get("category", "")
    if cat in ("Warframes", "Archwing", "Sentinels", "Pets", "Necramech"):
        return 930_000
    if _is_lich_item(item):
        return 820_000
    return 465_000


def _item_xp(inv: dict, path: str) -> int:
    """Return the cumulative XP recorded for *path* across the inventory.

    Checks ``XPInfo`` first (canonical, survives selling), then falls
    back to any equipment slot with an ``XP`` field.
    """
    for entry in inv.get("XPInfo") or []:
        if (entry.get("ItemType") or "").lower() == path:
            return entry.get("XP") or 0

    def _walk(obj):
        if isinstance(obj, dict):
            if (obj.get("ItemType") or "").lower() == path and obj.get("XP"):
                return obj["XP"]
            for v in obj.values():
                result = _walk(v)
                if result:
                    return result
        elif isinstance(obj, list):
            for v in obj:
                result = _walk(v)
                if result:
                    return result
        return 0

    return _walk(inv)


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

    Items with ``XP`` = 0 (freshly built, never equipped) are not
    included — ownership alone does not imply mastery.
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

    return leveled


def analyze_mastery(
    db: ExportDB,
    inv: dict,
) -> tuple[int, int, list[tuple[dict, int]], list[tuple[dict, int]]]:
    """Cross-reference player progress against the masterable item list.

    Items are classified into three buckets:

    * **Mastered** — cumulative XP >= the expected max for that item type.
    * **In progress** — some XP but below the max threshold.
    * **Never touched** — no XP at all.

    Args:
        db:  Loaded game export database.
        inv: Player inventory dict (may contain ``XPInfo`` from profile).

    Returns:
        ``(total, mastered_count, unmastered, in_progress)``
        where the last two are ``[(item_dict, tier_index), ...]`` lists.
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
    in_progress: list[tuple[dict, int]] = []

    for item in masterable:
        un = item["uniqueName"].lower()
        if un not in ever_leveled:
            unmastered.append((item, _tier_index(item)))
        else:
            xp = _item_xp(inv, un)
            if xp >= _max_xp_for_item(item):
                mastered += 1
            else:
                in_progress.append((item, _tier_index(item)))

    unmastered.sort(key=lambda pair: (pair[1], pair[0].get("name", "")))
    in_progress.sort(key=lambda pair: (pair[1], pair[0].get("name", "")))

    return len(masterable), mastered, unmastered, in_progress


# ---------------------------------------------------------------------------
# Report printing
# ---------------------------------------------------------------------------

def print_report(
    total: int,
    mastered: int,
    unmastered: list[tuple[dict, int]],
    in_progress: list[tuple[dict, int]],
) -> None:
    """Print the full mastery progression report."""
    remaining = len(unmastered) + len(in_progress)

    print("=" * 68)
    print("  MASTERY PROGRESSION REPORT")
    print("=" * 68)
    print(f"  {mastered} / {total} masterable items mastered "
          f"({mastered * 100 // total}%)")
    print(f"  {len(unmastered)} items never touched"
          f"  {len(in_progress)} in progress"
          f"  {remaining} remaining")
    total_mr = (
        sum(_item_mr(item) for item, _ in unmastered)
        + sum(_item_mr(item) for item, _ in in_progress)
    )
    print(f"  ~{total_mr:,} MR available from unmastered items")
    print()

    # Group by tier for display.
    def _print_section(
        header: str,
        desc: str,
        items: list[tuple[dict, int]],
        show_detail: bool = True,
    ) -> None:
        if not items:
            return
        tier_groups: list[list[dict]] = [[] for _ in TIERS]
        for item, t_idx in items:
            tier_groups[t_idx].append(item)

        for t_idx, (tier, group) in enumerate(zip(TIERS, tier_groups)):
            if not group:
                continue
            count = len(group)
            mr = sum(_item_mr(i) for i in group)
            print(f"{'─' * 68}")
            label = f"{header} — {tier['label']}" if show_detail else header
            print(f"  {label}")
            if show_detail:
                print(f"  {tier['desc']}")
            print(f"  {count} items  ~{mr:,} MR")
            print()

            t = PrettyTable()
            t.set_style(TableStyle.DEFAULT)
            t.field_names = ["Item", "Category", "MR"]
            t.align["Item"] = "l"
            t.align["Category"] = "l"
            t.align["MR"] = "r"

            for item in group:
                name = item.get("name", item.get("uniqueName", "?"))
                cat = item.get("category", "")
                mr_val = _item_mr(item)
                t.add_row([name, cat, mr_val])

            print(t)
            print()

    _print_section("NEVER TOUCHED", "No XP recorded", unmastered)
    _print_section("IN PROGRESS", "Partial XP, not yet max rank", in_progress)

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

    total, mastered, unmastered, in_progress = analyze_mastery(db, inv)
    print_report(total, mastered, unmastered, in_progress)
