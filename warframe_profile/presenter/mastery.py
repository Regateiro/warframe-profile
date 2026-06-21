"""Mastery Rank progression analysis (``--mastery``).

Cross-references the player's XP history (from DE profile data) against
the masterable items in the game database to identify true gaps — items
that have never been ranked up — and displays them grouped by farming
difficulty tier.

Flow:
    1. Load the export DB (masterable items) and inventory (XP history).
    2. Build the set of "ever leveled" items from XPInfo + XP fields.
    3. For each masterable item, classify as:
       - Mastered: cumulative XP >= expected max for its type.
       - In progress: some XP but below max.
       - Never touched: no XP at all.
    4. Group unmastered items by farming difficulty tier (1-7).
    5. Print the report with MR estimates and acquisition sources.
"""

from prettytable import PrettyTable, TableStyle

from warframe_profile.model.inventory import (
    ExportDB,
    load_data,
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
            not i.get("isPrime")
            and (i.get("category") in ("Sentinels", "Pets") or i.get("category") == "Misc")
        ),
    },
    {
        "id": "2",
        "label": "Tier 2 — Archwing & Arch-Melee",
        "desc": "Clan research / Archwing missions / Cephalon Simaris",
        "filter": lambda i: (
            not i.get("isPrime") and i.get("category") in ("Archwing", "Arch-Melee")
        ),
    },
    {
        "id": "3",
        "label": "Tier 3 — Syndicate Weapons & Arch-Gun",
        "desc": "Spend syndicate standing; Arch-guns from syndicates, clan research, vents",
        "filter": lambda i: (
            not i.get("isPrime")
            and (_has_tag(i, "Syndicate") or i.get("category") == "Arch-Gun")
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
            i.get("isPrime") and i.get("category") in ("Primary", "Secondary", "Melee")
        ),
    },
    {
        "id": "6",
        "label": "Tier 6 — Prime Warframes (Relics)",
        "desc": "Farm relics / radshares; 6,000 MR each",
        "filter": lambda i: i.get("isPrime") and i.get("category") == "Warframes",
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
    """Return True if this is a Kuva / Tenet / Coda / Paracesis weapon (rank 40)."""
    name = item.get("name", "")
    if any(name.startswith(p) for p in ("Kuva ", "Tenet ", "Coda ", "Dual Coda ")):
        return True
    un = item.get("uniqueName", "")
    return "BallasSword" in un


def _is_necramech(item: dict) -> bool:
    """Return True if this is a Necramech (rank 40 warframe-like)."""
    return item.get("productCategory") == "MechSuits"


def _item_mr(item: dict) -> int:
    """Return the total Mastery Rank contribution of a single item.

    * Necramechs: 8,000 (rank 40, 200 MR/rank).
    * Warframes, Archwings, Sentinels, Pets: 6,000 (rank 30).
    * Kuva / Tenet / Coda weapons: 4,000 (rank 40).
    * Everything else: 3,000 (rank 30).
    """
    if _is_necramech(item):
        return 8_000
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


def _source_by_tags(item: dict, tags: set[str]) -> str | None:
    """Determine source from tag checks (Lich, Invasion, Syndicate, etc.)."""
    if "Technocyte Coda" in tags:
        return "Coda"
    if "Kuva Lich" in tags:
        return "Lich"
    if "Tenet" in tags:
        return "Sister"
    if _is_lich_item(item):
        name_prefix = item.get("name", "").split(" ")[0]
        if name_prefix in ("Kuva",):
            return "Lich"
        if name_prefix in ("Tenet",):
            return "Sister"
        if name_prefix in ("Coda",) or item.get("name", "").startswith("Dual Coda"):
            return "Coda"
    if "Invasion Reward" in tags:
        return "Invasion"
    if "Stalker" in tags:
        return "Stalker"
    if "Cephalon Simaris" in tags:
        return "Simaris"
    if "Baro" in tags:
        return "Baro Ki'teer"
    if "Dax" in tags or "Duviri" in tags:
        return "Duviri Circuit"
    if "Zariman" in tags:
        return "Zariman (Holdfasts)"
    if "Entrati" in tags:
        return "Deimos (Entrati)"
    for synd in (
        "Steel Meridian",
        "Red Veil",
        "Perrin Sequence",
        "New Loka",
        "Cephalon Suda",
        "Arbiters of Hexis",
    ):
        if synd in tags:
            return f"Syndicate ({synd})"
    return None


def _source_by_unique_name(un: str) -> str | None:
    """Determine source from *uniqueName* path patterns."""
    if "ClanTech" in un:
        return "Dojo Research"
    if "MK1Series" in un:
        return "Market (Credits)"
    if "/Ostron/" in un:
        return "Cetus (Ostron)"
    if "/SolarisUnited/" in un:
        return "Fortuna (Solaris)"
    if "ThanoTech" in un or "Thanotech" in un:
        return "Deimos (Entrati)"
    if "/VoidTrader/" in un:
        return "Baro Ki'teer"
    if "/Archon/" in un:
        return "Archon Hunt"
    if "/Lasria/" in un:
        return "1999"
    if "/Conclave/" in un:
        return "Conclave"
    if "/Vehicles/Hoverboard" in un:
        return "Fortuna (Ventkids)"
    if "/Sentinels/SentinelWeapons" in un:
        return "Market"
    if "/MoaPets/" in un and "Weapon" in un:
        return "Fortuna (Solaris)"
    if "OperatorAmplifiers" in un and "Barrel" in un:
        return "Fortuna (Vox Solaris)" if "CorpAmp" in un else "Cetus (Quills)"
    return None


def _source_by_name(name: str) -> str | None:
    """Determine source from *name* patterns and known quest rewards."""
    if name.startswith("Dex "):
        return "Daily Tribute"
    if name.startswith("MK1-"):
        return "Market (Credits)"
    if name.startswith("Prisma "):
        return "Baro Ki'teer"
    if name == "Excalibur Umbra":
        return "Quest (The Second Dream)"
    if name in ("Azima", "Zenistar", "Zenith", "Sigma & Octantis"):
        return "Daily Tribute"
    if name in (
        "Broken War",
        "Broken Scepter",
        "Skiajati",
        "Paracesis",
        "Nataruk",
        "Sirocco",
        "Xoris",
        "Orvius",
        "Shedu",
    ):
        return "Quest"
    if name == "Wolf Sledge":
        return "Nightwave"
    return None


def _source_by_category(item: dict) -> str:
    """Determine source from item category and prime status."""
    if item.get("isPrime"):
        return "Relics"
    cat = item.get("category", "")
    if cat in ("Sentinels", "Pets"):
        return "Market"
    if cat in ("Archwing", "Arch-Melee"):
        return "Dojo Research"
    if cat == "Warframes":
        return "Market"
    if cat in ("Primary", "Secondary", "Melee", "Arch-Gun"):
        return "Market"
    return "Various"


def _item_source(item: dict) -> str:
    """Return the acquisition source label for *item*."""
    name = item.get("name", "")
    un = item.get("uniqueName", "")
    tags = {str(t) for t in (item.get("tags") or [])}

    source = _source_by_tags(item, tags)
    if source:
        return source

    source = _source_by_unique_name(un)
    if source:
        return source

    if _is_necramech(item):
        return "Deimos (Necraloid)"

    source = _source_by_name(name)
    if source:
        return source

    return _source_by_category(item)


def _max_xp_for_item(item: dict) -> int:
    """Return the expected cumulative XP when *item* reaches max rank.

    Warframe's XP-per-rank curve is quadratic (per the official wiki):
        Cumulative XP at rank R = base × R²

    * Necramechs (rank 40)             → base 1 000 → rank 40 = **1 600 000**
    * Warframes, Archwings, Sentinels,
      Pets                             → base 1 000 → rank 30 = **900 000**
    * Kuva / Tenet / Coda weapons
      (rank 40)                        → base 500   → rank 40 = **800 000**
    * Everything else (weapons, amps)   → base 500   → rank 30 = **450 000**
    """
    if _is_necramech(item):
        return 1_600_000
    cat = item.get("category", "")
    if cat in ("Warframes", "Archwing", "Sentinels", "Pets"):
        return 900_000
    if _is_lich_item(item):
        return 800_000
    return 450_000


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


def _modular_features(inv: dict, component_path: str) -> int | None:
    """Return the ``Features`` bitmask of the modular weapon using *component_path*.

    Searches inventory sections that can hold modular or pet items for an item
    whose ``ModularParts`` list contains *component_path*, or whose ``ItemType``
    matches it directly (for companion pets that are themselves masterable).

    The ``Features`` field is a bitmask whose lower bits are:

    * ``1``  — Orokin Catalyst / Reactor installed
    * ``2``  — Forma applied
    * ``4``  — (unknown / unused in this data)
    * ``8``  — **Gilded** (modular items — required for MR)
    """
    needle = component_path.lower()

    for sec in (
        "OperatorAmps",
        "Primary",
        "Secondary",
        "Melee",
        "MoaPets",
        "KubrowPets",
    ):
        for item in inv.get(sec) or []:
            if not isinstance(item, dict):
                continue
            # Match via ModularParts (amp prisms, zaw strikes, kitgun
            # chambers, MOA heads, K-drive parts).
            parts = item.get("ModularParts") or []
            if any(p.lower() == needle for p in parts):
                return item.get("Features")
            # Direct ItemType match: only for items that have ModularParts
            # (companion pets like Predasites / Vulpaphylas / MOAs).  Regular
            # pets, weapons and K-drives don't have ModularParts and should
            # never be subject to a gilding check.
            if item.get("ModularParts") and (item.get("ItemType") or "").lower() == needle:
                return item.get("Features")

    return None


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


def _filter_masterable_items(db):
    """Return list of masterable items from the export DB (excl. unobtainable)."""
    _unobtainable_paths = frozenset(
        {
            "/Lotus/Powersuits/Excalibur/ExcaliburPrime",
            "/Lotus/Weapons/Tenno/Pistol/LatoPrime",
            "/Lotus/Weapons/Tenno/Melee/LongSword/SkanaPrime",
        }
    )
    masterable = []
    for item in db.items:
        un = item.get("uniqueName")
        if not un or un in _unobtainable_paths:
            continue
        is_masterable = item.get("masterable") is True
        if not is_masterable and "OperatorAmplifiers" in un and "Barrel" in un:
            if "Blueprint" not in un:
                is_masterable = True
        if is_masterable:
            masterable.append(item)
    return masterable


def _classify_mastery_item(
    item: dict,
    ever_leveled: set,
    inv: dict,
) -> tuple[str, int]:  # ("unmastered"|"in_progress"|"mastered", tier_index)
    """Classify one masterable item and return ``(bucket, tier_index)``."""
    un = item["uniqueName"].lower()
    if un not in ever_leveled:
        return "unmastered", _tier_index(item)
    xp = _item_xp(inv, un)
    max_xp = _max_xp_for_item(item)
    features = _modular_features(inv, item["uniqueName"])
    if features is not None and (features & 8) == 0:
        return "in_progress", _tier_index(item)
    if xp >= max_xp:
        return "mastered", _tier_index(item)
    return "in_progress", _tier_index(item)


def analyze_mastery(
    db: ExportDB,
    inv: dict,
) -> tuple[int, int, list[tuple[dict, int]], list[tuple[dict, int]]]:
    """Cross-reference player progress against the masterable item list."""
    ever_leveled = _build_ever_leveled(inv)
    masterable = _filter_masterable_items(db)

    mastered = 0
    unmastered: list[tuple[dict, int]] = []
    in_progress: list[tuple[dict, int]] = []

    for item in masterable:
        bucket, t_idx = _classify_mastery_item(item, ever_leveled, inv)
        if bucket == "unmastered":
            unmastered.append((item, t_idx))
        elif bucket == "in_progress":
            in_progress.append((item, t_idx))
        else:
            mastered += 1

    unmastered.sort(key=lambda pair: (pair[1], pair[0].get("name", "")))
    in_progress.sort(key=lambda pair: (pair[1], pair[0].get("name", "")))

    return len(masterable), mastered, unmastered, in_progress


# ---------------------------------------------------------------------------
# Report printing
# ---------------------------------------------------------------------------


def _print_mastery_section(
    header: str,
    desc: str,
    items: list[tuple[dict, int]],
    show_detail: bool = True,
) -> None:
    """Print one mastery section (never-touched or in-progress) grouped by tier."""
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
        t.field_names = ["Item", "Category", "MR", "Source"]
        t.align["Item"] = "l"
        t.align["Category"] = "l"
        t.align["MR"] = "r"
        t.align["Source"] = "l"

        for item in group:
            name = item.get("name", item.get("uniqueName", "?"))
            cat = item.get("category", "")
            mr_val = _item_mr(item)
            src = _item_source(item)
            t.add_row([name, cat, mr_val, src])

        print(t)
        print()


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
    print(f"  {mastered} / {total} masterable items mastered ({mastered * 100 // total}%)")
    print(
        f"  {len(unmastered)} items never touched"
        f"  {len(in_progress)} in progress"
        f"  {remaining} remaining"
    )
    total_mr = sum(_item_mr(item) for item, _ in unmastered) + sum(
        _item_mr(item) for item, _ in in_progress
    )
    print(f"  ~{total_mr:,} MR available from unmastered items")
    print()

    _print_mastery_section("NEVER TOUCHED", "No XP recorded", unmastered)
    _print_mastery_section("IN PROGRESS", "Partial XP, not yet max rank", in_progress)

    print("=" * 68)
    print(f"  {remaining} items remaining  ~{total_mr:,} MR to earn")
    print("=" * 68)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(args) -> None:
    """Entry point for the ``--mastery`` sub-command."""
    db, inv = load_data(
        args.items_cache,
        args.refresh_items,
        args.inventory,
        args.refresh,
    )

    total, mastered, unmastered, in_progress = analyze_mastery(db, inv)
    print_report(total, mastered, unmastered, in_progress)
