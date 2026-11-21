"""Prime item indexing and inventory cross-reference analysis.

This is the **core analysis** module.  It receives the raw inventory and
the merged game database, then answers questions like:

- Which Prime parts are missing / surplus?  What's their ducat value?
- Which relics are "safe to spam" (every reward already owned)?
- Which blueprints and components are excess (every crafting path leads
  to an already-owned item)?
- Which non-Prime equipment can be safely sold (re-buyable for credits)?

The main entry point is :func:`analyze`, which produces an
:class:`AnalysisResult` dataclass consumed by the view layer.
"""

import bisect
from collections import defaultdict
from collections.abc import Set
from dataclasses import dataclass, field
from enum import Enum

from warframe_profile.model.data import Inventory, ItemData
from warframe_profile.model.utils import normalize_path


class ItemStatus(str, Enum):
    """Classification of a player's ownership status for a Prime item."""

    HAVE_COPY = "have_copy"
    BUILDABLE = "buildable"
    CRAFTING = "crafting"
    PARTIAL = "partial"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Categories whose prime parts use the "Warframe" naming convention
#: (part names always get "Blueprint" appended).
_FRAME_CATEGORIES: frozenset[str] = frozenset({"Warframes", "Archwing"})

#: Categories whose prime parts use the "Weapon" naming convention
#: (part names are used directly without "Blueprint" suffix).
_WEAPON_PART_CATEGORIES: frozenset[str] = frozenset(
    {
        "Primary",
        "Secondary",
        "Melee",
        "Sentinels",
        "SentinelWeapons",
        "SpaceGuns",
        "SpaceMelee",
        "SpaceSuits",
    }
)

#: Weapon categories that can be primed.  Used to identify which items
#: in the export DB are weapons (as opposed to resources, gear, etc.)
#: when building ingredient indices.
WEAPON_CATEGORIES = {"Primary", "Secondary", "Melee"}

#: Subjective rarity tiers used to determine the "rarest" component of a
#: craftable weapon (for the cleanup sell list).
#: Higher number = rarer material.  Used by :func:`_get_rarest_component`.
_RESOURCE_RARITY: dict[str, int] = {
    "Nitain Extract": 4,
    "Tellurium": 3,
    "Argon Crystal": 3,
    "Forma": 3,
    "Oxium": 2,
    "Cryotic": 2,
    "Kuva": 2,
    "Hexenon": 2,
    "Neurodes": 1,
    "Neural Sensors": 1,
    "Orokin Cell": 1,
    "Gallium": 1,
    "Morphics": 1,
    "Plastids": 1,
    "Polymer Bundle": 1,
    "Control Module": 1,
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
# These dataclasses define the output of every analysis function.  They
# are created by the analysis functions and consumed by the view layer
# (report.py for the CLI, templates.py for the web UI).
#


@dataclass
class PartAnalysis:
    """Analysis of a single Prime part within an :class:`ItemResult`.

    Attributes:
        name:  Display name of the part (e.g. "Neuroptics", "Systems").
        owned: How many copies the player owns.
        needed: Base quantity required per build.
        surplus: ``owned - total_keep`` (can be safely sold).
        missing: How many more copies are needed to build everything.
        ducats: Ducat value of a single copy.
    """

    name: str
    owned: int
    needed: int
    surplus: int
    missing: int
    ducats: int


@dataclass
class ItemResult:
    """Cross-reference result for one Prime item.

    Attributes:
        name:        Display name (e.g. "Rhino Prime").
        category:    Equipment category (e.g. "Warframes", "Primary").
        status:      ``"have_copy"`` | ``"buildable"`` | ``"crafting"`` |
                     ``"partial"``
        needed_builds: How many builds are required *including* the
                       first copy plus extra copies needed for sub-crafts.
        extra_builds: How many of *needed_builds* are for sub-crafts (0
                      if the player already owns the item).
        mastery_req: Mastery Rank requirement.
        has_copy:    Whether at least one finished copy is owned.
        parts:       List of :class:`PartAnalysis` for each component.
        can_build:   ``True`` when no parts are missing (including sub-craft
                     ingredients).
        missing_parts: Total count of missing part copies.
    """

    name: str
    category: str
    status: str
    needed_builds: int
    extra_builds: int
    mastery_req: int
    has_copy: bool
    parts: list[PartAnalysis]
    can_build: bool
    missing_parts: int


@dataclass
class MissingPrime:
    """A Prime item for which the player owns zero parts but is masterable.

    Attributes:
        name:       Display name.
        category:   Equipment category.
        mastery_req: Mastery Rank requirement.
    """

    name: str
    category: str
    mastery_req: int


@dataclass
class SellableEquipment:
    """A non-Prime weapon that can be sold because it can be re-bought for credits.

    Attributes:
        name:           Display name.
        category:       Equipment category.
        buy_type:       ``"direct"`` (buy outright) or ``"blueprint"``.
        rare_component: Name of the rarest required material, if any.
    """

    name: str
    category: str
    buy_type: str
    rare_component: str | None


@dataclass
class ExcessItem:
    """A blueprint or component the player owns more copies of than needed.

    Attributes:
        name:     Display name.
        item_type: ``"Blueprint"`` or ``"Component"``.
        owned:    Total copies owned.
        sell:     Number that can be safely sold.
        builds:   What finished item(s) this crafts into.
    """

    name: str
    item_type: str
    owned: int
    sell: int
    builds: str


@dataclass
class AnalysisResult:
    """Aggregated analysis output.

    Attributes:
        items:            Per-prime-item results.
        missing:          Prime items with zero parts owned.
        total_ducats_safe: Total ducats from surplus parts.
        total_ducats_keep: Total ducats from parts kept for builds.
        sellable:         Optional list of market-buyable equipment.
    """

    items: list[ItemResult] = field(default_factory=list)
    missing: list[MissingPrime] = field(default_factory=list)
    total_ducats_safe: int = 0
    total_ducats_keep: int = 0
    sellable: list[SellableEquipment] | None = None

    def surplus_parts(self) -> dict[tuple[str, str], dict[str, int]]:
        """Aggregate surplus ducat-valued parts by ``(item name, part name)``."""
        sell: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"count": 0, "ducats": 0})
        for item in self.items:
            for part in item.parts:
                if part.surplus > 0 and part.ducats > 0:
                    key = (item.name, part.name)
                    sell[key]["count"] += part.surplus
                    sell[key]["ducats"] = part.ducats
        return dict(sell)


# ---------------------------------------------------------------------------
# Shared analysis helpers
# ---------------------------------------------------------------------------
# ``_compute_needed_builds`` combines the universal ownership rule from
# ``Inventory`` with the inventory's sub-craft dependency shortfalls:
#   needed_builds = (0 if owned/crafting else 1) + extra_builds


def _compute_needed_builds(item_un: str, inventory: Inventory) -> int:
    """Return the total number of builds still needed for a Prime item.

    One copy when the player neither owns nor is crafting it, plus any extra
    copies required for sub-craft dependencies.  Ownership uses the universal
    :meth:`Inventory.is_owned` rule (inventory **or** foundry).
    """
    needed = 0 if inventory.is_owned(item_un) else 1
    return needed + inventory.extra_builds.get(item_un, 0)


def _iter_required_parts(item_data: ItemData, inventory: Inventory):
    """Yield ``(info, part, needed_builds, owned_qty)`` for ducat-valued parts."""
    for un, info in item_data.prime_map.items():
        if info["is_cosmetic"]:
            continue
        needed_builds = _compute_needed_builds(un, inventory)
        for part in info["parts"]:
            if part["ducats"] == 0:
                continue
            owned_qty = inventory.count(part["uniqueName"])
            if part["uniqueName"] in item_data.prime_map and inventory.is_finished(
                part["uniqueName"]
            ):
                owned_qty = max(0, owned_qty - 1)
            yield info, part, needed_builds, owned_qty


# ---------------------------------------------------------------------------
# Relic analysis
# ---------------------------------------------------------------------------
# Relics are Void Keys that drop Prime parts.  The analysis determines:
#   1. Which relics are "safe to spam" — every prime-part reward is already
#      owned (opening them yields only duplicate parts for ducats).
#   2. Which parts are still needed and which owned relics can drop them.
#
# Flow:
#   1. Build the set of "not needed" parts (enough copies owned).
#   2. For each owned relic, check each reward against this set.
#   3. If all prime-part rewards are not needed → relic is "safe".
#
# Dataclasses: RelicInfo, NeededPart
# Entry points: build_relics_map(), build_needed_drops()


@dataclass
class RelicInfo:
    """Analysis of a single relic the player owns.

    Attributes:
        name:      Short display name (e.g. "Meso P2").
        tier:      Relic tier (Lith / Meso / Neo / Axi).
        count:     Number of copies owned.
        vaulted:   Whether the relic is vaulted.
        safe:      True when *every* prime-part reward is already owned.
    """

    name: str
    tier: str
    count: int
    vaulted: bool
    safe: bool


#: Component names that are NOT component-level items — they are either the
#: blueprint itself or weapon sub-parts (e.g. zaw parts) whose names match
#: generic sub-component terms.  Used by :func:`build_component_blueprint_map`.
_NON_COMPONENT_NAMES: frozenset[str] = frozenset(
    {
        "Blueprint",
        "Ornament",
        "Handle",
        "Blade",
        "Link",
        "Head",
    }
)

#: WFCD categories that are cosmetic (not tradable for ducats).
_COSMETIC_CATEGORIES: frozenset[str] = frozenset(
    {
        "Skins",
        "Glyphs",
        "Sigils",
        "Node",
    }
)

#: Tags / category substrings used to identify Prime items in the export DB.
_PRIME_TAGS: frozenset[str] = frozenset({"Prime", "PrimePart"})

#: Reward names that are never prime parts and always safe to acquire.
_NON_PRIME_REWARDS: set[str] = {
    "Forma Blueprint",
    "2X Forma Blueprint",
    "Riven Sliver",
    "1200X Kuva",
    "Ayatan Amber Star",
    "Exilus Weapon Adapter Blueprint",
    "Fass",
    "Jahu",
    "Khra",
    "Lohk",
    "Netra",
    "Ris",
    "Vome",
    "Xata",
}

#: DE internal tier codes → relic display names.
_RELIC_TIER_MAP: dict[str, str] = {
    "/T1VoidProjection": "Lith",
    "/T2VoidProjection": "Meso",
    "/T3VoidProjection": "Neo",
    "/T4VoidProjection": "Axi",
}

#: Relic refinement tier suffixes (from lowest to highest).
_RELIC_REFINEMENTS: tuple[str, ...] = (" Intact", " Exceptional", " Flawless", " Radiant")


def _tier_from_un(un: str) -> str:
    """Extract the relic tier string from a uniqueName path."""
    for pattern, tier in _RELIC_TIER_MAP.items():
        if pattern in un:
            return tier
    return ""


def _shorten_name(full_name: str) -> str:
    """Strip the refinement qualifier from a relic display name.

    E.g. ``"Axi P7 Intact"`` → ``"Axi P7"``.
    """
    for suffix in _RELIC_REFINEMENTS:
        if full_name.endswith(suffix):
            return full_name[: -len(suffix)]
    return full_name


def _build_not_needed_part_names(
    item_data: ItemData,
    inventory: Inventory,
) -> set[str]:
    """Build reward-style names for parts the player does NOT need.

    A part is "not needed" when the player's universal owned count — inventory
    copies, finished/mastered items, or foundry results — covers every
    required build.  Only parts with a ducat value > 0 participate.
    """
    not_needed: set[str] = set()
    for info, part, needed_builds, owned_qty in _iter_required_parts(item_data, inventory):
        if owned_qty < part["count"] * needed_builds:
            continue
        for name in _reward_names_for_part(info["name"], part["name"], info["category"]):
            not_needed.add(name)
    return not_needed


def _reward_names_for_part(
    item_name: str,
    part_name: str,
    category: str,
) -> list[str]:
    """Return the WFCD-style reward name(s) for a prime part.

    Handles the different naming conventions used by warframes, weapons,
    sentinels, etc. Part names that already include the parent item (nearly
    all Prime parts) are used directly; bare names are prefixed with
    *item_name*. Archwing display prefixes are removed because relic rewards
    omit them.
    """
    item_name = item_name.replace("<ARCHWING> ", "")
    part_name = part_name.replace("<ARCHWING> ", "")

    if "Prime" in part_name and part_name != "Blueprint":
        base = part_name
    elif part_name == "Blueprint":
        base = f"{item_name} Blueprint"
    else:
        base = f"{item_name} {part_name}"

    if category in _FRAME_CATEGORIES:
        # Warframe/Archwing component rewards carry a "Blueprint" suffix
        # (e.g. "Valkyr Prime Chassis Blueprint"), even though the internal
        # part name does not.
        return [base] if base.endswith(" Blueprint") else [f"{base} Blueprint"]

    if category in _WEAPON_PART_CATEGORIES:
        return [base]

    # Unknown categories: accept either convention.
    candidates = [base]
    if not base.endswith(" Blueprint"):
        candidates.append(f"{base} Blueprint")
    return candidates


def _process_relic_rewards(
    rewards_raw: list[dict],
    not_needed: set[str],
) -> bool:
    """Return whether any prime-part reward is still needed."""
    for rw in rewards_raw:
        rname = rw["item"]["name"]
        if rname in _NON_PRIME_REWARDS:
            continue
        if rname not in not_needed:
            return True
    return False


def build_relics_map(item_data: ItemData, inventory: Inventory) -> list[RelicInfo]:
    """Cross-reference owned relics against needed prime parts."""
    all_relics = [item for item in item_data.items if item.get("category") == "Relics"]
    relic_by_un: dict[str, dict] = {relic["uniqueName"]: relic for relic in all_relics}
    not_needed = _build_not_needed_part_names(item_data, inventory)

    results: list[RelicInfo] = []
    for relic_un, relic_count in sorted(inventory.owned_relics.items()):
        relic = relic_by_un.get(relic_un)
        if not relic:
            continue

        has_unowned = _process_relic_rewards(relic.get("rewards", []), not_needed)

        results.append(
            RelicInfo(
                name=_shorten_name(relic.get("name", "")),
                tier=_tier_from_un(relic_un),
                count=relic_count,
                vaulted=relic.get("vaulted", False),
                safe=not has_unowned,
            )
        )

    return results


# ---------------------------------------------------------------------------
# Needed-parts / relic-drops table
# ---------------------------------------------------------------------------
# Builds a list of every prime part the player is still missing, together
# with the owned relics that can drop those parts.  This is the data behind
# the "Needed Drops" view.
#


@dataclass
class NeededPart:
    """A prime part the player still needs, and which owned relics drop it.

    Attributes:
        part_name:  WFCD-style reward name (e.g. "Ash Prime Chassis Blueprint").
        item_name:  Parent prime item name (e.g. "Ash Prime").
        part_type:  Short part name (e.g. "Chassis").
        category:   Parent item category (e.g. "Warframes", "Primary").
        missing:    How many more copies are needed.
        ducats:     Ducat value of a single copy.
        drops:      List of ``(relic_short_name, relic_count, tier, rarity)``
                    tuples.
    """

    part_name: str
    item_name: str
    part_type: str
    category: str
    missing: int
    ducats: int
    drops: list[tuple[str, int, str, str]]


def _build_reward_index(
    inventory: Inventory,
    all_relics: list[dict],
) -> dict[str, list[tuple[str, int, str, str]]]:
    """Build mapping: reward_name -> [(relic_short_name, count, tier, rarity)]."""
    owned_relics = inventory.owned_relics
    relic_by_un: dict[str, dict] = {r["uniqueName"]: r for r in all_relics}
    index: dict[str, list[tuple[str, int, str, str]]] = defaultdict(list)
    for relic_un, relic_count in owned_relics.items():
        relic = relic_by_un.get(relic_un)
        if not relic:
            continue
        short_name = _shorten_name(relic.get("name", ""))
        tier = _tier_from_un(relic_un)
        for rw in relic.get("rewards", []):
            rarity = rw.get("rarity") or ""
            index[rw["item"]["name"]].append((short_name, relic_count, tier, rarity))
    return index


def _process_needed_part(
    part: dict,
    info: dict,
    needed_builds: int,
    owned_qty: int,
    reward_index: dict,
) -> NeededPart | None:
    """Build a NeededPart if this part is missing."""
    missing = max(0, part["count"] * needed_builds - owned_qty)
    if missing == 0:
        return None
    pnames = _reward_names_for_part(info["name"], part["name"], info["category"])
    part_drops = []
    for pname in pnames:
        for drop in reward_index.get(pname, []):
            if drop not in part_drops:
                part_drops.append(drop)
    primary_name = pnames[0]
    for pname in pnames:
        if pname in reward_index:
            primary_name = pname
            break
    return NeededPart(
        part_name=primary_name,
        item_name=info["name"],
        part_type=part["name"],
        category=info["category"],
        missing=missing,
        ducats=part["ducats"],
        drops=part_drops,
    )


def build_needed_drops(item_data: ItemData, inventory: Inventory) -> list[NeededPart]:
    """Identify needed prime parts and which owned relics can drop them."""
    all_relics = [item for item in item_data.items if item.get("category") == "Relics"]
    reward_index = _build_reward_index(inventory, all_relics)

    result: list[NeededPart] = []
    for info, part, needed_builds, owned_qty in _iter_required_parts(item_data, inventory):
        needed = _process_needed_part(part, info, needed_builds, owned_qty, reward_index)
        if needed:
            result.append(needed)

    result.sort(key=lambda x: (x.item_name, x.part_type))
    return result


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------
# These are the main public API of this module.  They take the export
# database and inventory (both loaded by the model layer) and produce the
# dataclass instances consumed by the view layer.
#
# Key functions:
#   build_prime_map()              — index all Prime items with tradable parts
#   analyze()                      — cross-reference inventory vs prime_map
#   find_excess_blueprints_and_components() — what can be safely sold
#   compute_sellable_equipment()   — market-rebuyable weapons
#   build_relics_map()             — safe-to-spam relic analysis
#   build_needed_drops()           — needed parts + relic sources
#


def _get_rarest_component(item_data: dict) -> str | None:
    """Return the name of the rarest required resource for *item_data*.

    Rarity is defined by :data:`_RESOURCE_RARITY`.  Blueprint components
    are skipped.
    """
    comps = item_data.get("components", [])
    if not comps:
        return None
    rarest: str | None = None
    rarest_tier = -1
    for comp in comps:
        cname = comp.get("name", "")
        if not cname or "Blueprint" in cname:
            continue
        tier = _RESOURCE_RARITY.get(cname, 0)
        if tier > rarest_tier:
            rarest_tier = tier
            rarest = cname
    return rarest if rarest_tier > 0 else None


def build_ingredient_to_results(recipes: dict) -> dict[str, set[str]]:
    """Build a reverse index: ingredient ``ItemType`` → set of result types."""
    index: dict[str, set[str]] = defaultdict(set)
    for recipe_data in recipes.values():
        result_type = recipe_data.get("resultType", "")
        if not result_type:
            continue
        for ingredient in recipe_data.get("ingredients", []):
            item_type = ingredient.get("ItemType", "")
            if item_type:
                index[item_type].add(result_type)
    return index


def build_ingredient_index_craftable_to_owned(
    item_data: ItemData,
    inventory: Inventory,
) -> set[str]:
    """Build a set of item uniqueNames that should *not* be sold.

    Only ingredients needed for items the player does not already own (or is
    not currently crafting) are excluded.  Ingredients whose every crafting
    path leads to an owned/mastered/foundry item are safe to sell.
    """
    weapon_uns: set[str] = {
        item["uniqueName"]
        for item in item_data.items
        if item.get("uniqueName") and item.get("category") in WEAPON_CATEGORIES
    }

    result_to_recipe: dict[str, dict] = {}
    for recipe_data in item_data.recipes.values():
        result_type = recipe_data.get("resultType", "")
        if result_type:
            result_to_recipe[result_type] = recipe_data

    ingredient_to_results = item_data.ingredient_to_results

    exclude: set[str] = set()

    # Case 1: ingredients that craft into at least one unowned item.
    for recipe_data in item_data.recipes.values():
        for ingredient in recipe_data.get("ingredients", []):
            item_type = ingredient.get("ItemType", "")
            if not item_type:
                continue

            results = ingredient_to_results.get(item_type, set())
            # If the ingredient has no known results or any result is not
            # owned/crafting, keep it — it is still needed.
            if not results or not all(inventory.is_owned(result) for result in results):
                exclude.add(item_type)

    # Case 2: items whose recipe requires a weapon as an ingredient and the
    # result is not already owned or being crafted.
    for result_type, recipe_data in result_to_recipe.items():
        if inventory.is_owned(result_type):
            continue
        for ingredient in recipe_data.get("ingredients", []):
            if ingredient.get("ItemType", "") in weapon_uns:
                exclude.add(result_type)
                break

    return exclude


def _all_crafting_paths_lead_to_owned(
    item_un: str,
    inventory: Inventory,
    ingredient_to_results: dict[str, set[str]],
    visited: set[str],
) -> bool:
    """Return whether every item *item_un* crafts into is already covered."""
    if inventory.is_owned(item_un):
        return True

    if item_un in visited:
        return True
    visited.add(item_un)

    results = ingredient_to_results.get(item_un, set())
    if not results:
        return inventory.is_owned(item_un)

    return all(
        _all_crafting_paths_lead_to_owned(result, inventory, ingredient_to_results, visited)
        for result in results
    )


def _excess_item(
    item_un: str,
    count: int,
    item_type: str,
    item_data: ItemData,
    inventory: Inventory,
    ingredient_to_results: dict,
) -> ExcessItem | None:
    """Return an ExcessItem if *item_un* only crafts into covered items."""
    if count == 0:
        return None
    if not _all_crafting_paths_lead_to_owned(item_un, inventory, ingredient_to_results, set()):
        return None
    item = item_data.item(item_un)
    name = item_data.name(item.get("name", "")) if item else item_un.split("/")[-1]
    results = ingredient_to_results.get(item_un, set())
    builds = ", ".join(item_data.item_name(result) for result in results) if results else "?"
    return ExcessItem(name=name, item_type=item_type, owned=count, sell=count, builds=builds)


def find_excess_blueprints_and_components(
    item_data: ItemData,
    inventory: Inventory,
) -> list[ExcessItem]:
    """Identify blueprints and components the player can safely sell.

    A blueprint or component is *excess* when every finished item it can
    ultimately be used to craft is already owned, mastered, or being crafted.
    """
    ingredient_to_results = item_data.ingredient_to_results

    excess: list[ExcessItem] = []

    for blueprint in inventory.blueprints:
        bp_un = blueprint.get("ItemType", "")
        if not bp_un:
            continue
        item = _excess_item(
            bp_un,
            blueprint.get("ItemCount", 1),
            "Blueprint",
            item_data,
            inventory,
            ingredient_to_results,
        )
        if item:
            excess.append(item)

    for entry in inventory.components:
        item_un = entry.get("ItemType", "")
        if not item_un or not ingredient_to_results.get(item_un):
            continue
        item = _excess_item(
            item_un,
            entry.get("ItemCount", 1),
            "Component",
            item_data,
            inventory,
            ingredient_to_results,
        )
        if item:
            excess.append(item)

    excess.sort(key=lambda x: (x.item_type, x.name))
    return excess


def build_market_credit_map(items: list[dict]) -> dict[str, dict]:
    """Build a map of weapon ``uniqueName`` → purchase info for items
    that can be re-bought from the in-game market for credits.

    Two buy types are recognised:
    * ``"direct"`` — MK1 variants bought outright for credits.
    * ``"blueprint"`` — weapons bought as blueprints (credit cost).
    """
    market: dict[str, dict] = {}
    for item in items:
        un = item.get("uniqueName", "")
        name = item.get("name", "")
        if not un or not name:
            continue
        cat = item.get("category", "")
        if cat not in WEAPON_CATEGORIES:
            continue
        if name.lower().startswith("mk1-"):
            market[un] = {"name": name, "category": cat, "buy_type": "direct"}
            continue
        if item.get("marketCost") is not None and item.get("bpCost") is not None:
            info: dict = {"name": name, "category": cat, "buy_type": "blueprint"}
            rarest = _get_rarest_component(item)
            if rarest:
                info["rare_component"] = rarest
            market[un] = info
    return market


def find_sellable_equipment(
    inventory: Inventory,
    market_map: dict[str, dict],
    exclude_ingredients: Set[str] = frozenset(),
) -> list[SellableEquipment]:
    """Find owned equipment that can be safely sold (re-buyable for credits).

    Items whose ``uniqueName`` appears in *exclude_ingredients* are kept
    because they are needed for other crafts.
    """
    owned: list[SellableEquipment] = []
    seen: set[str] = set()
    for equipment in inventory.equipment_entries():
        un = equipment.get("ItemType", "")
        npath = normalize_path(un)
        if npath in seen:
            continue
        seen.add(npath)
        if un in market_map and un not in exclude_ingredients:
            info = market_map[un]
            owned.append(
                SellableEquipment(
                    name=info["name"],
                    category=info["category"],
                    buy_type=info["buy_type"],
                    rare_component=info.get("rare_component"),
                )
            )
    owned.sort(key=lambda x: (x.category, x.name))
    return owned


def build_regular_to_prime_map(items: list[dict]) -> dict[str, dict]:
    """Build a map from non-Prime weapon uniqueNames to their Prime counterpart.

    Matching is based on item name — the function first indexes all
    Prime items by ``(name, category)``, then for each non-Prime item,
    it searches for a matching Prime entry with the same name (or a name
    that starts with the non-Prime name, e.g. ``"Braton"`` →
    ``"Braton Prime"``).  Prefix candidates are found with a binary
    search over per-category sorted Prime names.

    Returns:
        ``{non_prime_un: {"prime_name": ..., "prime_un": ...}}``.
    """
    by_name_cat: dict[tuple[str, str], list[dict]] = defaultdict(list)
    prime_names_by_cat: dict[str, list[str]] = defaultdict(list)
    for item in items:
        if item.get("isPrime") is True:
            key = (item["name"], item.get("category", ""))
            by_name_cat[key].append(item)
            prime_names_by_cat[key[1]].append(key[0])

    for names in prime_names_by_cat.values():
        names.sort()

    def _prefix_keys(name: str, cat: str) -> list[str]:
        """Return sorted Prime names in *cat* that start with *name*."""
        names = prime_names_by_cat.get(cat)
        if not names:
            return []
        i = bisect.bisect_left(names, name)
        found: list[str] = []
        while i < len(names) and names[i].startswith(name):
            found.append(names[i])
            i += 1
        return found

    reg_to_prime: dict[str, dict] = {}
    for item in items:
        if item.get("isPrime"):
            continue
        name = item.get("name", "")
        cat = item.get("category", "")
        matches = by_name_cat.get((name, cat), [])
        if not matches:
            candidates = _prefix_keys(name, cat)
            if len(candidates) == 1:
                matches = by_name_cat[(candidates[0], cat)]
        if matches:
            p = matches[0]
            reg_to_prime[item["uniqueName"]] = {
                "prime_name": p["name"],
                "prime_un": p["uniqueName"],
            }
    return reg_to_prime


def compute_sellable_equipment(
    item_data: ItemData, inventory: Inventory
) -> list[SellableEquipment]:
    """Compute the market-re-buyable equipment list for an inventory snapshot."""
    ingredient_index = build_ingredient_index_craftable_to_owned(item_data, inventory)
    market_map = build_market_credit_map(item_data.items)
    return find_sellable_equipment(inventory, market_map, ingredient_index)


def build_prime_map(items: list[dict]) -> dict[str, dict]:
    """Index all Prime items and their tradable components.

    This is the core index that enables the entire prime analysis.  It
    walks the merged item database and extracts every item whose name
    or uniqueName contains "Prime", then collects its tradable components
    (parts that can be traded between players).

    Returns a dict::

        {
            "<uniqueName>": {
                "name": ...,
                "category": ...,
                "masteryReq": ...,
                "parts": [{"uniqueName", "name", "count", "ducats"}, ...],
                "all_parts": [{"uniqueName", "name", "count"}, ...],
                "is_cosmetic": bool,
                "masterable": bool,
            },
            ...
        }

    Cosmetic categories (Skins, Glyphs, Sigils, Node) are marked with
    ``is_cosmetic=True`` so they can be filtered out of the main analysis.

    Parts are deduplicated by uniqueName — if the same component appears
    multiple times in the component list (e.g. in different recipes), its
    count is summed.
    """
    primes: dict[str, dict] = {}
    for item in items:
        un = item.get("uniqueName", "")
        name_lower = item.get("name", "").lower()
        # Quick reject: name or uniqueName must contain "Prime".
        if "Prime" not in un and "prime" not in name_lower:
            continue
        tags = item.get("tags", []) or []
        cat = item.get("category", "")
        if not any(t in tags or t in cat for t in _PRIME_TAGS) and "prime" not in name_lower:
            continue

        # Collect components, deduplicating by uniqueName.  ``parts`` holds
        # only tradable parts (for ducat / surplus analysis); ``all_parts``
        # holds every component so sub-craft dependencies on buildable
        # weapons (e.g. Magnus Prime inside Akmagnus Prime) are visible to
        # sub-craft dependency logic — those weapons are non-tradable.
        comps = item.get("components", [])
        parts_by_un: dict[str, dict] = {}
        all_by_un: dict[str, dict] = {}
        for c in comps:
            cu = c.get("uniqueName", "")
            if not cu:
                continue
            cnt = c.get("itemCount", 1)
            if cu in all_by_un:
                all_by_un[cu]["count"] += cnt
            else:
                all_by_un[cu] = {
                    "uniqueName": cu,
                    "name": c.get("name", ""),
                    "count": cnt,
                }
            if c.get("tradable") is False:
                continue
            if cu in parts_by_un:
                parts_by_un[cu]["count"] += cnt
            else:
                parts_by_un[cu] = {
                    "uniqueName": cu,
                    "name": c.get("name", ""),
                    "count": cnt,
                    "ducats": c.get("primeSellingPrice") or c.get("ducats", 0),
                }

        parts_needed = list(parts_by_un.values())
        all_parts = list(all_by_un.values())
        primes[un] = {
            "name": item.get("name", un),
            "category": cat,
            "masteryReq": item.get("masteryReq", 0),
            "parts": parts_needed,
            "all_parts": all_parts,
            "is_cosmetic": cat in _COSMETIC_CATEGORIES,
            "masterable": item.get("masterable", False),
        }
    return primes


def build_component_blueprint_map(
    recipes: dict,
    prime_map: dict[str, dict],
) -> dict[str, tuple[str, int]]:
    """Map prime component uniqueName → (blueprint uniqueName, ducats).

    For every component that appears in a prime item's parts list, check
    whether a blueprint exists that produces it.  This is limited to
    component-level items (Chassis, Neuroptics, Systems) so that main
    item blueprints (full weapons/warframes) are not included.
    """
    # Collect component uniqueNames from the prime map, excluding
    # components that are themselves full prime items (e.g. Bronco Prime
    # used as ingredient for Akbronco Prime) — a full prime item has a
    # non-empty parts list in the prime_map.  Those would produce
    # confusing names if mapped through their parent akimbo entry.
    component_uns: set[str] = set()
    for info in prime_map.values():
        for part in info["parts"]:
            if part["uniqueName"] in prime_map and prime_map[part["uniqueName"]]["parts"]:
                continue
            if part["name"] not in _NON_COMPONENT_NAMES:
                component_uns.add(part["uniqueName"])

    comp_to_bp: dict[str, tuple[str, int]] = {}
    for bp_un, recipe in recipes.items():
        result_un = recipe.get("resultType", "")
        if not result_un:
            continue
        if result_un not in component_uns:
            continue
        ducats = recipe.get("primeSellingPrice") or recipe.get("ducats", 0)
        if ducats:
            comp_to_bp[result_un] = (bp_un, ducats)
    return comp_to_bp


def _analyze_parts(
    info: dict,
    prime_map: dict,
    needed_builds: int,
    inventory: Inventory,
    comp_to_bp: dict[str, tuple[str, int]] | None = None,
) -> tuple[list[PartAnalysis], int]:
    """Analyse parts for a prime item; return ``(part_analyses, missing_count)``."""
    part_analysis: list[PartAnalysis] = []
    missing_parts = 0
    for part in info["parts"]:
        owned_qty = inventory.count(part["uniqueName"])

        if part["uniqueName"] in prime_map and inventory.is_finished(part["uniqueName"]):
            owned_qty = max(0, owned_qty - 1)

        total_keep = part["count"] * needed_builds
        surplus = max(0, owned_qty - total_keep)
        missing = max(0, total_keep - owned_qty)

        if owned_qty > 0 or missing > 0:
            part_analysis.append(
                PartAnalysis(
                    name=part["name"],
                    owned=owned_qty,
                    needed=part["count"],
                    surplus=surplus,
                    missing=missing,
                    ducats=part["ducats"],
                )
            )
        missing_parts += missing

        # Add blueprint entries for component parts that the player owns.
        # Only mark as surplus (sellable) if the component's need is already
        # covered — otherwise the blueprint is better kept for crafting.
        if comp_to_bp and part["name"] != "Blueprint":
            bp_info = comp_to_bp.get(part["uniqueName"])
            if bp_info:
                bp_un, bp_ducats = bp_info
                bp_owned = inventory.count(bp_un)
                if bp_owned > 0:
                    bp_surplus = max(0, bp_owned - missing)
                    if bp_surplus > 0:
                        part_analysis.append(
                            PartAnalysis(
                                name=part["name"] + " Blueprint",
                                owned=bp_owned,
                                needed=0,
                                surplus=bp_surplus,
                                missing=0,
                                ducats=bp_ducats,
                            )
                        )

    return part_analysis, missing_parts


def _can_build_copies(
    un: str,
    copies: int,
    prime_map: dict,
    inventory: Inventory,
    depth: int = 0,
) -> bool:
    """Whether *copies* more of a Prime item can be built from owned parts.

    Checks every recipe component (tradable or not) and recurses into Prime
    ingredients, so nested sub-crafts (an akimbo variant that itself needs a
    finished Prime weapon) are accounted for.  Recursion is capped at two
    levels because deeper Prime-in-Prime chains do not occur in practice.
    """
    info = prime_map.get(un)
    if not info or not info["parts"]:
        return True

    for part in info.get("all_parts") or info["parts"]:
        need = part["count"] * copies
        have = inventory.count(part["uniqueName"])
        if have >= need:
            continue
        gap = need - have
        dep = prime_map.get(part["uniqueName"])
        if (
            depth < 2
            and dep
            and dep["parts"]
            and _can_build_copies(part["uniqueName"], gap, prime_map, inventory, depth + 1)
        ):
            continue
        return False
    return True


def _analyze_subcraft_parts(
    info: dict,
    prime_map: dict,
    needed_builds: int,
    inventory: Inventory,
) -> tuple[list[PartAnalysis], int]:
    """Find Prime ingredients that are missing and cannot be crafted.

    Akimbo Prime weapons consume finished Prime weapons (e.g. two Magnus
    Prime for Akmagnus Prime).  Those ingredients are non-tradable, so they
    are absent from ``info["parts"]`` and would otherwise be invisible to
    the buildable check.  Returns synthetic part entries plus the number of
    missing ingredient copies.
    """
    tradable = {part["uniqueName"] for part in info["parts"]}
    analysis: list[PartAnalysis] = []
    missing_parts = 0

    for part in info.get("all_parts", []):
        dep_un = part["uniqueName"]
        if dep_un in tradable:
            continue
        dep = prime_map.get(dep_un)
        if not dep or not dep["parts"]:
            continue

        required = part["count"] * needed_builds
        owned_qty = inventory.count(dep_un)
        if owned_qty >= required:
            continue
        deficit = required - owned_qty
        if _can_build_copies(dep_un, deficit, prime_map, inventory):
            continue

        analysis.append(
            PartAnalysis(
                name=part.get("name") or dep["name"],
                owned=owned_qty,
                needed=part["count"],
                surplus=0,
                missing=deficit,
                ducats=0,
            )
        )
        missing_parts += deficit

    return analysis, missing_parts


def _make_item_result(
    name: str,
    category: str,
    status: str,
    needed_builds: int,
    extra_builds: int,
    mastery_req: int,
    has_copy: bool,
    can_build: bool,
    missing_parts: int,
    part_analysis: list[PartAnalysis],
) -> ItemResult:
    """Construct an :class:`ItemResult` with common boilerplate."""
    return ItemResult(
        name=name,
        category=category,
        status=status,
        needed_builds=needed_builds,
        extra_builds=extra_builds,
        mastery_req=mastery_req,
        has_copy=has_copy,
        parts=part_analysis,
        can_build=can_build,
        missing_parts=missing_parts,
    )


def _classify_prime_item(
    un: str,
    info: dict,
    item_data: ItemData,
    inventory: Inventory,
    comp_to_bp: dict[str, tuple[str, int]] | None = None,
) -> tuple[ItemResult | None, MissingPrime | None]:
    """Analyse one prime item and return ``(item_result, missing_prime)``."""
    name = info["name"]
    cat = info["category"]

    has_copy = inventory.is_finished(un)
    needed_builds = _compute_needed_builds(un, inventory)
    extra = inventory.extra_builds.get(un, 0)

    # An item in the foundry is neither owned yet nor still to be built; its
    # parts (including the consumed blueprint) are already committed.
    if not has_copy and inventory.is_crafting(un):
        return _make_item_result(
            name,
            cat,
            ItemStatus.CRAFTING,
            needed_builds,
            extra,
            info["masteryReq"],
            False,
            False,
            0,
            [],
        ), None

    part_analysis, missing_parts = _analyze_parts(
        info, item_data.prime_map, needed_builds, inventory, comp_to_bp=comp_to_bp
    )
    subcraft_parts, subcraft_missing = _analyze_subcraft_parts(
        info, item_data.prime_map, needed_builds, inventory
    )
    if subcraft_parts:
        part_analysis.extend(subcraft_parts)
        missing_parts += subcraft_missing

    if has_copy:
        return _make_item_result(
            name,
            cat,
            ItemStatus.HAVE_COPY,
            needed_builds,
            extra,
            info["masteryReq"],
            True,
            missing_parts == 0,
            missing_parts,
            part_analysis,
        ), None
    elif missing_parts == 0 and info["parts"]:
        return _make_item_result(
            name,
            cat,
            ItemStatus.BUILDABLE,
            needed_builds,
            extra,
            info["masteryReq"],
            False,
            True,
            0,
            part_analysis,
        ), None
    elif any(part.owned > 0 for part in part_analysis):
        return _make_item_result(
            name,
            cat,
            ItemStatus.PARTIAL,
            needed_builds,
            extra,
            info["masteryReq"],
            False,
            missing_parts == 0,
            missing_parts,
            part_analysis,
        ), None
    elif info["masterable"]:
        return None, MissingPrime(name=name, category=cat, mastery_req=info["masteryReq"])

    return None, None


def analyze(item_data: ItemData, inventory: Inventory) -> AnalysisResult:
    """Cross-reference an inventory snapshot against the Prime item index.

    This is the **main analysis pipeline** for the ``ducats`` sub-command.
    It classifies every Prime item into one of four statuses:

    * ``have_copy`` — player owns a finished copy.
    * ``buildable`` — all parts are owned, including sub-craft ingredients
      such as the finished Prime weapons consumed by akimbo variants.
    * ``crafting`` — currently in the foundry; its blueprint and parts are
      already committed, so it is not counted as owned or buildable.
    * ``partial`` — some parts owned, some missing.
    * Missing — zero parts owned (added to ``missing_primes`` list).
    """
    comp_to_bp = item_data.component_blueprint_map

    results: list[ItemResult] = []
    missing_primes: list[MissingPrime] = []
    total_ducats_safe = 0
    total_ducats_keep = 0

    for un, info in item_data.prime_map.items():
        if info["is_cosmetic"]:
            continue

        item_result, missing_prime = _classify_prime_item(
            un,
            info,
            item_data,
            inventory,
            comp_to_bp=comp_to_bp,
        )

        if item_result:
            results.append(item_result)
            total_ducats_safe += sum(part.surplus * part.ducats for part in item_result.parts)
            total_ducats_keep += sum(
                (part.owned - part.surplus) * part.ducats for part in item_result.parts
            )
        if missing_prime:
            missing_primes.append(missing_prime)

    return AnalysisResult(
        items=results,
        missing=missing_primes,
        total_ducats_safe=total_ducats_safe,
        total_ducats_keep=total_ducats_keep,
    )
