"""Mastery progression model — classification, tiers, and report data.

This module contains the **model** half of the mastery feature: it reads
:class:`ItemData`/:class:`Inventory`, classifies every masterable item, and
produces a :class:`MasteryReport`.  CLI/HTML formatting lives in
:mod:`warframe_profile.view.mastery`; orchestration lives in
:mod:`warframe_profile.presenter.mastery`.

Flow:
    1. Take the masterable items from ``ItemData``.
    2. Use ``Inventory`` for XP history, ownership, and crafting readiness.
    3. Classify as mastered, in progress, or unowned (split into ready,
       owned-blueprint, and missing-blueprint groups).
    4. Group unmastered items by farming difficulty tier (1-7).
"""

from dataclasses import dataclass, field

from warframe_profile.model.craft_model import is_blueprint_un
from warframe_profile.model.data import Inventory, ItemData

# ---------------------------------------------------------------------------
# Category sets used by tier filters and MR calculations
# ---------------------------------------------------------------------------

#: Categories whose items are quick to acquire (Tier 1).
_EASY_CATEGORIES: frozenset[str] = frozenset({"Sentinels", "Pets", "Misc"})

#: Categories for Archwing items (Tier 2).
_ARCHWING_CATEGORIES: frozenset[str] = frozenset({"Archwing", "Arch-Melee"})

#: Categories for warframe-like items that contribute 6,000 MR.
_FRAME_LIKE_CATEGORIES: frozenset[str] = frozenset(
    {
        "Warframes",
        "Archwing",
        "Sentinels",
        "Pets",
    }
)

#: Categories for prime weapons (Tier 5).
_PRIME_WEAPON_CATEGORIES: frozenset[str] = frozenset({"Primary", "Secondary", "Melee"})

#: Categories for market-buyable weapons.
_MARKET_WEAPON_CATEGORIES: frozenset[str] = frozenset(
    {
        "Primary",
        "Secondary",
        "Melee",
        "Arch-Gun",
    }
)

# ---------------------------------------------------------------------------
# Tier definitions — ordered easiest → hardest
# ---------------------------------------------------------------------------

TIERS = [
    {
        "id": "1",
        "label": "Tier 1 — Sentinels, Pets, K-Drives, Kitguns",
        "desc": "Quick wins: buy blueprints from Market or Fortuna, easy materials",
        "filter": lambda i: not i.get("isPrime") and i.get("category", "") in _EASY_CATEGORIES,
    },
    {
        "id": "2",
        "label": "Tier 2 — Archwing & Arch-Melee",
        "desc": "Clan research / Archwing missions / Cephalon Simaris",
        "filter": lambda i: not i.get("isPrime") and i.get("category", "") in _ARCHWING_CATEGORIES,
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
        "filter": lambda i: i.get("isPrime") and i.get("category", "") in _PRIME_WEAPON_CATEGORIES,
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


#: uniqueName substring that identifies Paracesis — the only non-lich weapon
#: that also ranks to 40 (uses the same XP curve as Kuva/Tenet/Coda weapons).
_PARACESIS_UN = "BallasSword"

#: Name prefixes for Kuva / Tenet / Coda / Dual Coda lich weapons (rank 40).
_LICH_WEAPON_PREFIXES = ("Kuva ", "Tenet ", "Coda ", "Dual Coda ")


def _is_lich_item(item: dict) -> bool:
    """Return True if this is a Kuva / Tenet / Coda / Paracesis weapon (rank 40)."""
    name = item.get("name", "")
    if any(name.startswith(p) for p in _LICH_WEAPON_PREFIXES):
        return True
    un = item.get("uniqueName", "")
    return _PARACESIS_UN in un


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
    if cat in _FRAME_LIKE_CATEGORIES:
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


#: Tag → source label mapping.  Checked in order; first match wins.
_TAG_SOURCE_MAP: list[tuple[str, str]] = [
    ("Technocyte Coda", "Coda"),
    ("Kuva Lich", "Lich"),
    ("Tenet", "Sister"),
    ("Invasion Reward", "Invasion"),
    ("Stalker", "Stalker"),
    ("Cephalon Simaris", "Simaris"),
    ("Baro", "Baro Ki'teer"),
    ("Dax", "Duviri Circuit"),
    ("Duviri", "Duviri Circuit"),
    ("Zariman", "Zariman (Holdfasts)"),
    ("Entrati", "Deimos (Entrati)"),
]

#: Syndicate names that appear as tags.
_SYNDICATE_TAGS: list[str] = [
    "Steel Meridian",
    "Red Veil",
    "Perrin Sequence",
    "New Loka",
    "Cephalon Suda",
    "Arbiters of Hexis",
]

#: uniqueName path substring → source label mapping.
_UN_SOURCE_MAP: list[tuple[str, str]] = [
    ("ClanTech", "Dojo Research"),
    ("MK1Series", "Market (Credits)"),
    ("/Ostron/", "Cetus (Ostron)"),
    ("/SolarisUnited/", "Fortuna (Solaris)"),
    ("ThanoTech", "Deimos (Entrati)"),
    ("Thanotech", "Deimos (Entrati)"),
    ("/VoidTrader/", "Baro Ki'teer"),
    ("/Archon/", "Archon Hunt"),
    ("/Lasria/", "1999"),
    ("/Conclave/", "Conclave"),
    ("/Vehicles/Hoverboard", "Fortuna (Ventkids)"),
    ("/Sentinels/SentinelWeapons", "Market"),
    ("StalkerTwo", "Stalker"),
]

#: Item name → source label mapping (exact matches).
_NAME_SOURCE_MAP: dict[str, str] = {
    "Excalibur Umbra": "Quest (The Second Dream)",
    "Wolf Sledge": "Nightwave",
}

#: Item names that are daily tribute rewards.
_DAILY_TRIBUTE_NAMES: frozenset[str] = frozenset(
    {
        "Azima",
        "Zenistar",
        "Zenith",
        "Sigma & Octantis",
    }
)

#: Item names that are quest rewards.
_QUEST_REWARD_NAMES: frozenset[str] = frozenset(
    {
        "Broken War",
        "Broken Scepter",
        "Skiajati",
        "Paracesis",
        "Nataruk",
        "Sirocco",
        "Xoris",
        "Orvius",
        "Shedu",
    }
)

#: Deimos bounty reward names.
_DEIMOS_BOUNTY_NAMES: frozenset[str] = frozenset(
    {
        "Arum Spinosa",
        "Bubonico",
        "Catabolyst",
        "Corthe",
        "Keratinos",
        "Pulmonars",
        "Quassus",
        "Sporothrix",
    }
)


def _source_by_tags(item: dict, tags: set[str]) -> str | None:
    """Determine source from tag checks (Lich, Invasion, Syndicate, etc.)."""
    for tag, source in _TAG_SOURCE_MAP:
        if tag in tags:
            return source
    if _is_lich_item(item):
        name_prefix = item.get("name", "").split(" ")[0]
        if name_prefix in ("Kuva",):
            return "Lich"
        if name_prefix in ("Tenet",):
            return "Sister"
        if name_prefix in ("Coda",) or item.get("name", "").startswith("Dual Coda"):
            return "Coda"
    for synd in _SYNDICATE_TAGS:
        if synd in tags:
            return f"Syndicate ({synd})"
    return None


def _source_by_unique_name(un: str) -> str | None:
    """Determine source from *uniqueName* path patterns."""
    for pattern, source in _UN_SOURCE_MAP:
        if pattern in un:
            return source
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
    if name in _NAME_SOURCE_MAP:
        return _NAME_SOURCE_MAP[name]
    if name in _DAILY_TRIBUTE_NAMES:
        return "Daily Tribute"
    if name in _QUEST_REWARD_NAMES:
        return "Quest"
    if name in _DEIMOS_BOUNTY_NAMES:
        return "Deimos (Entrati)"
    return None


def _source_by_category(item: dict) -> str:
    """Determine source from item category and prime status."""
    if item.get("isPrime"):
        return "Relics"
    cat = item.get("category", "")
    if cat in ("Sentinels", "Pets"):
        return "Market"
    if cat in _ARCHWING_CATEGORIES:
        return "Dojo Research"
    if cat == "Warframes":
        return "Market"
    if cat in _MARKET_WEAPON_CATEGORIES:
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
    if cat in _FRAME_LIKE_CATEGORIES:
        return 900_000
    if _is_lich_item(item):
        return 800_000
    return 450_000


def _filter_masterable_items(item_data: ItemData):
    """Return masterable items from the item database (excl. unobtainable)."""
    _unobtainable_paths = frozenset(
        {
            "/Lotus/Powersuits/Excalibur/ExcaliburPrime",
            "/Lotus/Weapons/Tenno/Pistol/LatoPrime",
            "/Lotus/Weapons/Tenno/Melee/LongSword/SkanaPrime",
        }
    )
    masterable = []
    for item in item_data.items:
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


#: Masterable item types that are modular components; they only count as
#: owned when an assembled item lists them in ``ModularParts``.
_MODULAR_COMPONENT_TYPES: frozenset[str] = frozenset(
    {"K-Drive Component", "Zaw Component", "Kitgun Component", "Amp"}
)


def _is_modular_component(item: dict) -> bool:
    """Whether *item* is a modular part rather than an assembled item."""
    if item.get("type") in _MODULAR_COMPONENT_TYPES:
        return True
    return "PetPart" in item.get("uniqueName", "")


def _owns_root_blueprint(item: dict, item_data: ItemData, inventory: Inventory) -> bool:
    """Whether the blueprint required to craft *item* itself is owned."""
    recipe = item_data.recipe_for(item.get("uniqueName", ""))
    if not recipe:
        return False
    bp_key = recipe.get("_recipeKey", "")
    return bool(bp_key) and inventory.count(bp_key) > 0


def _classify_mastery_item(item: dict, inventory: Inventory) -> tuple[str, int]:
    """Classify one masterable item and return ``(bucket, tier_index)``.

    An item the player owns — including one currently being crafted — is
    "in progress" even at 0 XP; only unowned items can fall into the
    blueprint/unowned buckets. Modular components count as owned only when an
    assembled item's ``ModularParts`` references them.
    """
    un = item["uniqueName"].lower()
    owned_now = inventory.owns_masterable(
        item,
        modular_component=_is_modular_component(item),
    )

    if un not in inventory.mastered:
        if owned_now:
            return "in_progress", _tier_index(item)
        return "unowned", _tier_index(item)

    xp = inventory.xp_by_path.get(un, 0)
    max_xp = _max_xp_for_item(item)
    features = inventory.modular_features.get(item["uniqueName"].lower())
    if features is not None and (features & 8) == 0:
        return "in_progress", _tier_index(item)
    if xp >= max_xp:
        return "mastered", _tier_index(item)
    return "in_progress", _tier_index(item)


def _craft_requirements(
    item: dict,
    item_data: ItemData,
    inventory: Inventory,
) -> dict | None:
    """Return the full crafting requirements for *item*, or None if absent.

    The plan covers the whole tree: intermediate components are decomposed
    into raw materials and every required blueprint is included when missing.
    """
    un = item.get("uniqueName", "")
    if not item_data.has_recipe(un):
        return None

    requirements, craftables = item_data.resolve_tree(un, 1, inventory, max_depth=5)
    item_data.add_missing_blueprint(un, item.get("name", ""), requirements, inventory)
    return item_data.finish_plan(requirements, craftables, inventory)


def _requirements_ready(requirements: dict) -> bool:
    """Whether every blueprint and material in *requirements* is owned."""
    return bool(requirements) and all(v["owned"] >= v["quantity"] for v in requirements.values())


def _missing_any_blueprint(requirements: dict) -> bool:
    """Whether the plan is missing at least one required blueprint."""
    return any(
        is_blueprint_un(key) and value["owned"] < value["quantity"]
        for key, value in requirements.items()
    )


def analyze_mastery(
    item_data: ItemData,
    inventory: Inventory,
) -> tuple[
    int,
    int,
    list[tuple[dict, int]],
    list[tuple[dict, int]],
    list[tuple[dict, int]],
    list[tuple[dict, int]],
]:
    """Cross-reference player progress against the masterable item list.

    Returns:
        ``(total, mastered, ready_to_build, owned_blueprints,
        missing_blueprints, in_progress)``.

        Unowned items are categorized by the full crafting tree: *ready* when
        every blueprint and material is owned, *owned_blueprints* when all
        required blueprints are owned but materials are missing, and
        *missing_blueprints* otherwise. Owned items at 0 XP count as
        *in_progress* alongside partially leveled ones.
    """
    masterable = _filter_masterable_items(item_data)

    mastered = 0
    unowned: list[tuple[dict, int]] = []
    in_progress: list[tuple[dict, int]] = []

    for item in masterable:
        bucket, t_idx = _classify_mastery_item(item, inventory)
        if bucket == "unowned":
            unowned.append((item, t_idx))
        elif bucket == "in_progress":
            in_progress.append((item, t_idx))
        else:
            mastered += 1

    ready_to_build: list[tuple[dict, int]] = []
    owned_blueprints: list[tuple[dict, int]] = []
    missing_blueprints: list[tuple[dict, int]] = []
    for pair in unowned:
        if _is_modular_component(pair[0]):
            # Loose modular components are not assembled items; the generic
            # recipe does not describe assembling the finished modular item.
            if _owns_root_blueprint(pair[0], item_data, inventory):
                owned_blueprints.append(pair)
            else:
                missing_blueprints.append(pair)
            continue

        requirements = _craft_requirements(pair[0], item_data, inventory)
        if requirements is not None and _requirements_ready(requirements):
            ready_to_build.append(pair)
        elif requirements is not None and not _missing_any_blueprint(requirements):
            owned_blueprints.append(pair)
        else:
            missing_blueprints.append(pair)

    ready_to_build.sort(key=lambda pair: pair[0].get("name") or "")
    owned_blueprints.sort(key=lambda pair: pair[0].get("name") or "")
    missing_blueprints.sort(key=lambda pair: (pair[1], pair[0].get("name") or ""))
    in_progress.sort(key=lambda pair: (pair[1], pair[0].get("name") or ""))

    return (
        len(masterable),
        mastered,
        ready_to_build,
        owned_blueprints,
        missing_blueprints,
        in_progress,
    )


# ---------------------------------------------------------------------------
# Report model (shared by the CLI and web views)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MasteryRow:
    """One masterable item, ready for display."""

    name: str
    category: str
    mr: int
    source: str


@dataclass
class MasteryTierGroup:
    """A group of items sharing a farming-difficulty tier."""

    label: str
    desc: str
    rows: list[MasteryRow] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.rows)

    @property
    def mr(self) -> int:
        return sum(row.mr for row in self.rows)


@dataclass
class MasterySection:
    """One report section (unmastered owned, owned blueprints, missing blueprints)."""

    header: str
    desc: str
    rows: list[MasteryRow] = field(default_factory=list)
    group_by_tier: bool = True
    tier_groups: list[MasteryTierGroup] = field(default_factory=list)
    link_craft: bool = False

    @property
    def count(self) -> int:
        return len(self.rows)

    @property
    def mr(self) -> int:
        return sum(row.mr for row in self.rows)


@dataclass
class MasteryReport:
    """Complete mastery progression report shared by the CLI and web views."""

    total: int
    mastered: int
    sections: list[MasterySection] = field(default_factory=list)
    remaining: int = 0
    total_mr: int = 0

    @property
    def percent(self) -> int:
        return self.mastered * 100 // self.total if self.total else 0


def _item_name(item: dict) -> str:
    """Return a display name for *item* with fallbacks."""
    name = item.get("name") or ""
    if name:
        return name
    un = item.get("uniqueName", "")
    if un:
        return un.split("/")[-1]
    return "?"


def _mastery_row(item: dict) -> MasteryRow:
    """Convert one export item into a display row."""
    return MasteryRow(
        name=_item_name(item),
        category=item.get("category", ""),
        mr=_item_mr(item),
        source=_item_source(item),
    )


def _mastery_rows(items: list[tuple[dict, int]]) -> list[MasteryRow]:
    """Convert ``(item, tier)`` pairs into display rows."""
    return [_mastery_row(item) for item, _ in items]


def _mastery_tier_groups(items: list[tuple[dict, int]]) -> list[MasteryTierGroup]:
    """Group ``(item, tier)`` pairs by their difficulty tier."""
    buckets: list[list[dict]] = [[] for _ in TIERS]
    for item, tier_idx in items:
        buckets[tier_idx].append(item)

    groups: list[MasteryTierGroup] = []
    for tier, bucket in zip(TIERS, buckets):
        if bucket:
            groups.append(
                MasteryTierGroup(
                    label=tier["label"],
                    desc=tier["desc"],
                    rows=[_mastery_row(item) for item in bucket],
                )
            )
    return groups


def build_mastery_report(item_data: ItemData, inventory: Inventory) -> MasteryReport:
    """Build the full mastery report shared by ``mastery`` and the web UI."""
    (
        total,
        mastered,
        ready_to_build,
        owned_blueprints,
        missing_blueprints,
        in_progress,
    ) = analyze_mastery(item_data, inventory)
    remaining = (
        len(ready_to_build) + len(owned_blueprints) + len(missing_blueprints) + len(in_progress)
    )
    sections = [
        MasterySection(
            header="UNMASTERED OWNED ITEMS",
            desc="Owned or partially leveled, not yet max rank",
            rows=_mastery_rows(in_progress),
            tier_groups=_mastery_tier_groups(in_progress),
        ),
        MasterySection(
            header="READY TO BUILD",
            desc="All blueprints and materials owned — craft now",
            rows=_mastery_rows(ready_to_build),
            group_by_tier=False,
            link_craft=True,
        ),
        MasterySection(
            header="OWNED BLUEPRINTS",
            desc="All blueprints owned — farm materials to build",
            rows=_mastery_rows(owned_blueprints),
            group_by_tier=False,
            link_craft=True,
        ),
        MasterySection(
            header="MISSING BLUEPRINTS",
            desc="Not owned — missing blueprint",
            rows=_mastery_rows(missing_blueprints),
            tier_groups=_mastery_tier_groups(missing_blueprints),
        ),
    ]
    return MasteryReport(
        total=total,
        mastered=mastered,
        sections=sections,
        remaining=remaining,
        total_mr=sum(section.mr for section in sections),
    )
