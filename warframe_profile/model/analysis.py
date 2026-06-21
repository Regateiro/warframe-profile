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

from collections import defaultdict
from dataclasses import dataclass, field

from warframe_profile.model.craft_model import resolve_name
from warframe_profile.model.inventory import ExportDB, build_owned
from warframe_profile.model.utils import normalize_path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Weapon categories that can be primed.  Used to identify which items
#: in the export DB are weapons (as opposed to resources, gear, etc.)
#: when building ingredient indices.
_WEAPON_CATEGORIES = {"Primary", "Secondary", "Melee"}

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
        status:      ``"have_copy"`` | ``"buildable"`` | ``"partial"``
        needed_builds: How many builds are required *including* the
                       first copy plus extra copies needed for sub-crafts.
        extra_builds: How many of *needed_builds* are for sub-crafts (0
                      if the player already owns the item).
        mastery_req: Mastery Rank requirement.
        has_copy:    Whether at least one finished copy is owned.
        parts:       List of :class:`PartAnalysis` for each component.
        can_build:   ``True`` when no parts are missing.
        missing_parts: Total count of missing part copies.
        total_parts: Number of distinct component types.
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
    total_parts: int


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
        keep:     Number that are useful (craft into unowned items).
        sell:     Number that can be safely sold (``owned - keep``).
        builds:   What finished item(s) this crafts into.
    """

    name: str
    item_type: str
    owned: int
    keep: int
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


# ---------------------------------------------------------------------------
# Shared analysis helpers
# ---------------------------------------------------------------------------
# These internal functions are used by both the main ``analyze()`` function
# and the relic / excess analysis functions.  They compute:
#   - owned_finished: set of items the player has built (equipment + mastered)
#   - extra_builds: how many extra copies of a Prime item are needed because
#     it is used as a crafting ingredient in another Prime item
#   - needed_builds: total builds required = (0 if owned else 1) + extras


def _compute_owned_finished(
    inventory: dict,
    equipment_sections: list[str],
    mastered: set[str] | None = None,
) -> set[str]:
    """Build a set of normalised paths for finished items the player owns.

    Includes every equipment entry in the inventory sections, plus any
    mastered items (items ranked up even if later sold).

    This is the "what do I have?" baseline for the entire analysis.
    """
    result: set[str] = {
        normalize_path(eq.get("ItemType", ""))
        for sect in equipment_sections
        for eq in inventory.get(sect, [])
    }
    if mastered:
        result |= mastered
    return result


def _compute_extra_builds(
    prime_map: dict[str, dict],
    owned: dict[str, int],
    owned_finished: set[str],
) -> dict[str, int]:
    """Determine how many extra builds are needed for sub-craft dependencies.

    When a Prime item is itself a component of another Prime (e.g. a weapon
    used as material for a dual-wield variant), the player may need multiple
    copies: one to keep and extras to consume in sub-crafts.

    Example: if Boltor Prime is needed as an ingredient in Akboltor Prime,
    and the player already owns one Boltor Prime but needs two for crafting,
    then extra_builds["BoltorPrime"] = 1.
    """
    buildable_keys = {
        un for un, info in prime_map.items() if info["parts"] and not info["is_cosmetic"]
    }
    component_need: dict[str, int] = defaultdict(int)
    for un, info in prime_map.items():
        if info["is_cosmetic"]:
            continue
        for part in info["parts"]:
            if part["uniqueName"] in buildable_keys:
                component_need[part["uniqueName"]] += part["count"]

    extra_builds: dict[str, int] = {}
    for dep_un, total_needed in component_need.items():
        dep_norm = normalize_path(dep_un)
        owned_copies = owned.get(dep_norm, 0)
        available = owned_copies - (1 if dep_norm in owned_finished else 0)
        extra = max(0, total_needed - available)
        if extra > 0:
            extra_builds[dep_un] = extra
    return extra_builds


def _compute_needed_builds(
    item_un: str,
    prime_map: dict[str, dict],
    owned_finished: set[str],
    extra_builds: dict[str, int],
) -> int:
    """Return the total number of builds needed for a Prime item.

    One copy if not yet owned, plus any extra copies required for sub-craft
    dependencies.

    Returns 0 if the player already owns the item and no extra copies are
    needed for sub-crafts.
    """
    owned = normalize_path(item_un) in owned_finished
    return (0 if owned else 1) + extra_builds.get(item_un, 0)


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
# Dataclasses: RelicReward, RelicInfo, NeededPart
# Entry points: build_relics_map(), build_needed_drops()


@dataclass
class RelicReward:
    """A single reward entry within a relic's drop table.

    Attributes:
        part_name: Human-readable name of the reward.
        rarity:    Rarity tier ("Intact", "Uncommon", "Rare").
        owned:     Whether the player owns at least one copy of this part.
    """

    part_name: str
    rarity: str
    owned: bool


@dataclass
class RelicInfo:
    """Analysis of a single relic the player owns.

    Attributes:
        name:      Short display name (e.g. "Meso P2").
        tier:      Relic tier (Lith / Meso / Neo / Axi).
        count:     Number of copies owned.
        vaulted:   Whether the relic is vaulted.
        rewards:   List of :class:`RelicReward` for each drop.
        safe:      True when *every* prime-part reward is already owned.
        unowned:   Names of rewards not yet owned (empty when *safe*).
    """

    name: str
    tier: str
    count: int
    vaulted: bool
    rewards: list[RelicReward]
    safe: bool
    unowned: list[str]


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


def _tier_from_un(un: str) -> str:
    """Extract the relic tier string from a uniqueName path."""
    if "/T1VoidProjection" in un:
        return "Lith"
    if "/T2VoidProjection" in un:
        return "Meso"
    if "/T3VoidProjection" in un:
        return "Neo"
    if "/T4VoidProjection" in un:
        return "Axi"
    return ""


def _shorten_name(full_name: str) -> str:
    """Strip the refinement qualifier from a relic display name.

    E.g. ``"Axi P7 Intact"`` → ``"Axi P7"``.
    """
    for suffix in (" Intact", " Exceptional", " Flawless", " Radiant"):
        if full_name.endswith(suffix):
            return full_name[: -len(suffix)]
    return full_name


def _build_owned_relics(
    inventory: dict,
    relic_items: list[dict],
) -> dict[str, int]:
    """Return a ``{relic_uniqueName: count}`` map for relics the player owns.

    Only Intact refinement relics are counted (the DE inventory always
    stores the Intact variant uniqueName).
    """
    owned: dict[str, int] = {}
    for item in inventory.get("MiscItems", []):
        un = item.get("ItemType", "")
        count = item.get("ItemCount", 1)
        if not un:
            continue
        # Quick check: relic paths contain "Projections"
        if "/Projections/T" not in un:
            continue
        # Skip the VoidProjectionFeatureItem
        if "VoidProjectionFeatureItem" in un:
            continue
        # Skip Requiem relics and generic placeholder entries
        if "Immortal" in un:
            continue
        owned[un] = owned.get(un, 0) + count
    return owned


def _build_not_needed_part_names(
    prime_map: dict[str, dict],
    inventory: dict,
    owned: dict[str, int],
    equipment_sections: list[str],
    mastered: set[str] | None = None,
) -> set[str]:
    """Build a set of reward-style names for parts the player does NOT need.

    A part is considered "not needed" when the player already has enough
    copies for all required builds (missing == 0).  This correctly handles
    the common case where parts were consumed to craft an item — the player
    may own zero copies but still doesn't need any more.

    Only parts with a ducat value > 0 are considered — parts with 0 ducats
    are either crafting materials (e.g. Orokin Cell), base weapons used as
    akimbo ingredients, or non-Prime items misclassified as Prime.
    """
    owned_finished = _compute_owned_finished(inventory, equipment_sections, mastered)
    extra_builds = _compute_extra_builds(prime_map, owned, owned_finished)

    not_needed: set[str] = set()

    for un, info in prime_map.items():
        if info["is_cosmetic"]:
            continue

        item_name = info["name"]
        cat = info["category"]
        needed_builds = _compute_needed_builds(un, prime_map, owned_finished, extra_builds)

        for p in info["parts"]:
            if p["ducats"] == 0:
                continue

            pn = normalize_path(p["uniqueName"])
            owned_qty = owned.get(pn, 0)

            if p["uniqueName"] in prime_map and pn in owned_finished:
                owned_qty = max(0, owned_qty - 1)

            if owned_qty >= p["count"] * needed_builds:
                names = _reward_names_for_part(item_name, p["name"], cat)
                for n in names:
                    not_needed.add(n)

    return not_needed


def _reward_names_for_part(
    item_name: str,
    part_name: str,
    category: str,
) -> list[str]:
    """Return the WFCD-style reward name(s) for a prime part.

    Handles the different naming conventions used by warframes, weapons,
    sentinels, etc.
    """
    # If the part name is itself a full reward name (e.g. "Kavasa
    # Prime Band" inside "Kavasa Prime Kubrow Collar"), return it
    # directly — don't composite with the parent item name.
    if "Prime" in part_name and part_name != "Blueprint":
        return [part_name]

    if category in ("Warframes", "Archwing"):
        if part_name == "Blueprint":
            return [f"{item_name} Blueprint"]
        return [f"{item_name} {part_name} Blueprint"]

    if category in (
        "Primary",
        "Secondary",
        "Melee",
        "Sentinels",
        "SentinelWeapons",
        "SpaceGuns",
        "SpaceMelee",
        "SpaceSuits",
    ):
        if part_name == "Blueprint":
            return [f"{item_name} Blueprint"]
        return [f"{item_name} {part_name}"]

    # Default: try both forms.
    if part_name == "Blueprint":
        return [f"{item_name} Blueprint"]
    return [f"{item_name} {part_name}", f"{item_name} {part_name} Blueprint"]


def _process_relic_rewards(
    rewards_raw: list[dict],
    not_needed: set[str],
) -> tuple[list[RelicReward], bool, list[str]]:
    """Process relic rewards into ``(reward_list, has_unowned, unowned_names)``."""
    reward_list: list[RelicReward] = []
    has_unowned = False
    unowned_names: list[str] = []
    for rw in rewards_raw:
        rname = rw["item"]["name"]
        rarity = rw["rarity"]
        if rname in _NON_PRIME_REWARDS:
            safe_flag = True
        else:
            safe_flag = rname in not_needed
            if not safe_flag:
                has_unowned = True
                unowned_names.append(rname)
        reward_list.append(RelicReward(part_name=rname, rarity=rarity, owned=safe_flag))
    return reward_list, has_unowned, unowned_names


def build_relics_map(
    items: list[dict],
    inventory: dict,
    prime_map: dict[str, dict],
    owned: dict[str, int],
    equipment_sections: list[str],
    mastered: set[str] | None = None,
) -> list[RelicInfo]:
    """Cross-reference owned relics against needed prime parts."""
    all_relics = [i for i in items if i.get("category") == "Relics"]
    relic_by_un: dict[str, dict] = {r["uniqueName"]: r for r in all_relics}
    owned_relics = _build_owned_relics(inventory, all_relics)
    not_needed = _build_not_needed_part_names(
        prime_map, inventory, owned, equipment_sections, mastered=mastered
    )  # noqa: E501

    results: list[RelicInfo] = []
    for relic_un, relic_count in sorted(owned_relics.items()):
        relic = relic_by_un.get(relic_un)
        if not relic:
            continue

        reward_list, has_unowned, unowned_names = _process_relic_rewards(
            relic.get("rewards", []), not_needed
        )  # noqa: E501

        results.append(
            RelicInfo(
                name=_shorten_name(relic.get("name", "")),
                tier=_tier_from_un(relic_un),
                count=relic_count,
                vaulted=relic.get("vaulted", False),
                rewards=reward_list,
                safe=not has_unowned,
                unowned=unowned_names,
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
        missing:    How many more copies are needed.
        ducats:     Ducat value of a single copy.
        drops:      List of ``(relic_short_name, relic_count, tier)`` tuples.
    """

    part_name: str
    item_name: str
    part_type: str
    missing: int
    ducats: int
    drops: list[tuple[str, int, str]]


def _build_reward_index(
    inventory: dict,
    all_relics: list[dict],
) -> dict[str, list[tuple[str, int, str]]]:
    """Build mapping: reward_name -> [(relic_short_name, count, tier)]."""
    owned_relics = _build_owned_relics(inventory, all_relics)
    relic_by_un: dict[str, dict] = {r["uniqueName"]: r for r in all_relics}
    index: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    for relic_un, relic_count in owned_relics.items():
        relic = relic_by_un.get(relic_un)
        if not relic:
            continue
        short_name = _shorten_name(relic.get("name", ""))
        tier = _tier_from_un(relic_un)
        for rw in relic.get("rewards", []):
            index[rw["item"]["name"]].append((short_name, relic_count, tier))
    return index


def _process_needed_part(
    p: dict,
    item_name: str,
    cat: str,
    needed_builds: int,
    prime_map: dict,
    owned: dict,
    owned_finished: set,
    reward_index: dict,
) -> NeededPart | None:
    """Build a NeededPart if this part is missing."""
    if p["ducats"] == 0:
        return None
    pn = normalize_path(p["uniqueName"])
    owned_qty = owned.get(pn, 0)
    if p["uniqueName"] in prime_map and pn in owned_finished:
        owned_qty = max(0, owned_qty - 1)
    missing = max(0, p["count"] * needed_builds - owned_qty)
    if missing == 0:
        return None
    pnames = _reward_names_for_part(item_name, p["name"], cat)
    part_drops = []
    for pn_ in pnames:
        for drop in reward_index.get(pn_, []):
            if drop not in part_drops:
                part_drops.append(drop)
    primary_name = pnames[0]
    for pn_ in pnames:
        if pn_ in reward_index:
            primary_name = pn_
            break
    return NeededPart(
        part_name=primary_name,
        item_name=item_name,
        part_type=p["name"],
        missing=missing,
        ducats=p["ducats"],
        drops=part_drops,
    )  # noqa: E501


def build_needed_drops(
    items: list[dict],
    inventory: dict,
    prime_map: dict[str, dict],
    owned: dict[str, int],
    equipment_sections: list[str],
    mastered: set[str] | None = None,
) -> list[NeededPart]:
    """Identify needed prime parts and which owned relics can drop them."""
    owned_finished = _compute_owned_finished(inventory, equipment_sections, mastered)
    extra_builds = _compute_extra_builds(prime_map, owned, owned_finished)
    all_relics = [i for i in items if i.get("category") == "Relics"]
    reward_index = _build_reward_index(inventory, all_relics)

    result: list[NeededPart] = []
    for un, info in prime_map.items():
        if info["is_cosmetic"]:
            continue
        needed_builds = _compute_needed_builds(un, prime_map, owned_finished, extra_builds)
        for p in info["parts"]:
            np = _process_needed_part(
                p,
                info["name"],
                info["category"],
                needed_builds,
                prime_map,
                owned,
                owned_finished,
                reward_index,
            )
            if np:
                result.append(np)

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


def build_ingredient_index_craftable_to_owned(
    recipes: dict,
    items: list[dict],
    inventory: dict,
    equipment_sections: list[str],
    mastered: set[str] | None = None,
) -> set[str]:
    """Build a set of item uniqueNames that should *not* be sold.

    Unlike :func:`build_ingredient_index`, this function only excludes
    ingredients that are needed to craft items the player doesn't fully
    own yet.  Ingredients that only craft into already-owned items are
    considered safe to sell.

    Two cases are covered (mirroring :func:`build_ingredient_index`):

    1. Items used as ingredients where at least one recipe result is
       **not** already owned or mastered.
    2. Items whose own recipe requires a weapon as an ingredient **and**
       the result is not already owned or mastered.
    """
    owned_finished: set[str] = {
        normalize_path(eq.get("ItemType", ""))
        for sect in equipment_sections
        for eq in inventory.get(sect, [])
    }
    if mastered:
        owned_finished |= mastered

    weapon_uns: set[str] = {
        item["uniqueName"]
        for item in items
        if item.get("uniqueName") and item.get("category") in _WEAPON_CATEGORIES
    }

    # Build reverse indices: resultType → recipe_data and
    # ingredient → set of resultTypes.
    result_to_recipe: dict[str, dict] = {}
    ingredient_to_results: dict[str, set[str]] = defaultdict(set)

    for recipe_data in recipes.values():
        rt = recipe_data.get("resultType", "")
        if rt:
            result_to_recipe[rt] = recipe_data
        for ingredient in recipe_data.get("ingredients", []):
            item_type = ingredient.get("ItemType", "")
            if item_type and rt:
                ingredient_to_results[item_type].add(rt)

    exclude: set[str] = set()

    # Case 1: ingredients that craft into at least one unowned item.
    for recipe_data in recipes.values():
        for ingredient in recipe_data.get("ingredients", []):
            item_type = ingredient.get("ItemType", "")
            if not item_type:
                continue

            results = ingredient_to_results.get(item_type, set())
            # If ingredient has no known results or any result is not owned,
            # keep it excluded — it's still needed.
            if not results or not all(normalize_path(r) in owned_finished for r in results):
                exclude.add(item_type)

    # Case 2: items whose recipe requires a weapon as ingredient and
    # the result is not already owned.
    for rt, recipe_data in result_to_recipe.items():
        if normalize_path(rt) in owned_finished:
            continue
        for ingredient in recipe_data.get("ingredients", []):
            if ingredient.get("ItemType", "") in weapon_uns:
                exclude.add(rt)
                break

    return exclude


def _all_crafting_paths_lead_to_owned(
    item_un: str,
    owned_finished: set[str],
    ingredient_to_results: dict[str, set[str]],
    visited: set[str],
    mastered: set[str] | None = None,
) -> bool:
    """Return ``True`` if every item this *item_un* can craft into is already owned
    (or mastered, when *mastered* is provided).

    Recursively follows the recipe chain (component → sub-assembly → finished
    item).  If *any* path leads to an unowned and unmastered item, returns
    ``False``.
    """
    norm = normalize_path(item_un)
    if norm in owned_finished or (mastered and norm in mastered):
        return True

    if item_un in visited:
        return True
    visited.add(item_un)

    results = ingredient_to_results.get(item_un, set())
    if not results:
        return norm in owned_finished

    return all(
        _all_crafting_paths_lead_to_owned(
            r,
            owned_finished,
            ingredient_to_results,
            visited,
            mastered=mastered,
        )
        for r in results
    )


def _excess_item(
    item_un: str,
    count: int,
    item_type: str,
    items_by_un: dict,
    owned_finished: set,
    loc_dict: dict,
    ingredient_to_results: dict,
    mastered: set | None,
) -> ExcessItem | None:
    """Return an ExcessItem if *item_un* only crafts into owned items."""
    if count == 0:
        return None
    if not _all_crafting_paths_lead_to_owned(
        item_un, owned_finished, ingredient_to_results, set(), mastered=mastered
    ):  # noqa: E501
        return None
    item_data = items_by_un.get(normalize_path(item_un))
    name = (
        resolve_name(item_data.get("name", ""), loc_dict) if item_data else item_un.split("/")[-1]
    )  # noqa: E501
    results = ingredient_to_results.get(item_un, set())
    builds = (
        ", ".join(_item_display_name(r, items_by_un, loc_dict) for r in results) if results else "?"
    )  # noqa: E501
    return ExcessItem(
        name=name, item_type=item_type, owned=count, keep=0, sell=count, builds=builds
    )  # noqa: E501


def find_excess_blueprints_and_components(
    inventory: dict,
    items_by_un: dict[str, dict],
    recipes: dict,
    owned: dict[str, int],
    owned_finished: set[str],
    loc_dict: dict,
    mastered: set[str] | None = None,
) -> list[ExcessItem]:
    """Identify blueprints and components the player can safely sell.

    A blueprint or component is *excess* when every finished item it can
    ultimately be used to craft is already owned or mastered by the player.

    Returns:
        A list of :class:`ExcessItem` sorted by name.
    """

    ingredient_to_results: dict[str, set[str]] = defaultdict(set)
    for recipe_data in recipes.values():
        rt = recipe_data.get("resultType", "")
        if not rt:
            continue
        for ingredient in recipe_data.get("ingredients", []):
            item_type = ingredient.get("ItemType", "")
            if item_type:
                ingredient_to_results[item_type].add(rt)

    excess: list[ExcessItem] = []

    for bp in inventory.get("Recipes", []):
        bp_un = bp.get("ItemType", "")
        if bp_un:
            item = _excess_item(
                bp_un,
                bp.get("ItemCount", 1),
                "Blueprint",
                items_by_un,
                owned_finished,
                loc_dict,
                ingredient_to_results,
                mastered,
            )  # noqa: E501
            if item:
                excess.append(item)

    for item in inventory.get("MiscItems", []):
        item_un = item.get("ItemType", "")
        if item_un and ingredient_to_results.get(item_un):
            ex = _excess_item(
                item_un,
                item.get("ItemCount", 1),
                "Component",
                items_by_un,
                owned_finished,
                loc_dict,
                ingredient_to_results,
                mastered,
            )  # noqa: E501
            if ex:
                excess.append(ex)

    excess.sort(key=lambda x: (x.item_type, x.name))
    return excess


def _item_display_name(
    item_un: str,
    items_by_un: dict[str, dict],
    loc_dict: dict,
) -> str:
    """Return the human-readable name for an item uniqueName."""
    from warframe_profile.model.craft_model import resolve_name

    item = items_by_un.get(normalize_path(item_un))
    if item:
        name = resolve_name(item.get("name", ""), loc_dict)
        if name:
            return name
    return item_un.split("/")[-1]


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
        if cat not in _WEAPON_CATEGORIES:
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
    inventory: dict,
    market_map: dict[str, dict],
    equipment_sections: list[str],
    exclude_ingredients: set[str] = frozenset(),
) -> list[SellableEquipment]:
    """Find owned equipment that can be safely sold (re-buyable for credits).

    Items whose ``uniqueName`` appears in *exclude_ingredients* are
    kept because they are needed for other crafts.
    """
    owned: list[SellableEquipment] = []
    seen: set[str] = set()
    for sect in equipment_sections:
        for eq in inventory.get(sect, []):
            un = eq.get("ItemType", "")
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


def build_item_index(items: list[dict]) -> dict[str, dict]:
    """Build a ``uniqueName → item`` lookup from the item list."""
    by_un: dict[str, dict] = {}
    for item in items:
        un = item.get("uniqueName", "")
        if un:
            by_un[un] = item
    return by_un


def build_regular_to_prime_map(items: list[dict]) -> dict[str, dict]:
    """Build a map from non-Prime weapon uniqueNames to their Prime counterpart.

    Matching is based on item name — the function first indexes all
    Prime items by ``(name, category)``, then for each non-Prime item,
    it searches for a matching Prime entry with the same name (or a name
    that starts with the non-Prime name, e.g. ``"Braton"`` →
    ``"Braton Prime"``).

    Returns:
        ``{non_prime_un: {"prime_name": ..., "prime_un": ...}}``.
    """
    by_name_cat: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for item in items:
        if item.get("isPrime") is True:
            by_name_cat[(item["name"], item.get("category", ""))].append(item)

    reg_to_prime: dict[str, dict] = {}
    for item in items:
        if item.get("isPrime"):
            continue
        name = item.get("name", "")
        cat = item.get("category", "")
        matches = by_name_cat.get((name, cat), [])
        if not matches:
            candidates = [k for k in by_name_cat if k[0].startswith(name) and k[1] == cat]
            if len(candidates) == 1:
                matches = by_name_cat[candidates[0]]
        if matches:
            p = matches[0]
            reg_to_prime[item["uniqueName"]] = {
                "prime_name": p["name"],
                "prime_un": p["uniqueName"],
            }
    return reg_to_prime


def find_owned_item_uns(
    inventory: dict,
    items_by_un: dict[str, dict],
    equipment_sections: list[str],
) -> set[str]:
    """Return uniqueNames of non-Prime equipment owned in inventory."""
    owned: set[str] = set()
    for sect in equipment_sections:
        for eq in inventory.get(sect, []):
            un = eq.get("ItemType", "")
            item = items_by_un.get(un)
            if item and not item.get("isPrime"):
                owned.add(un)
    return owned


def find_owned_prime_uns(
    inventory: dict,
    prime_map: dict[str, dict],
    equipment_sections: list[str],
) -> set[str]:
    """Return uniqueNames of Prime equipment (or blueprints) owned."""
    owned: set[str] = set()
    for sect in equipment_sections:
        for eq in inventory.get(sect, []):
            un = eq.get("ItemType", "")
            if un in prime_map:
                owned.add(un)
    for bp in inventory.get("Recipes", []):
        un = bp.get("ItemType", "")
        if un in prime_map:
            owned.add(un)
    return owned


def compute_sellable_equipment(
    db: "ExportDB",
    inventory: dict,
    equipment_sections: list[str],
) -> list[SellableEquipment]:
    """Compute the market-re-buyable equipment list from an :class:`ExportDB`.

    Convenience wrapper shared by the ``--ducats`` / ``--relics`` /
    ``--cleanup`` entry points.
    """
    ingredient_index = build_ingredient_index_craftable_to_owned(
        db.recipes,
        db.items,
        inventory,
        equipment_sections,
    )
    market_map = build_market_credit_map(db.items)
    return find_sellable_equipment(
        inventory,
        market_map,
        equipment_sections,
        ingredient_index,
    )


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
        # Quick reject: name or uniqueName must contain "Prime".
        if "Prime" not in un and "prime" not in item.get("name", "").lower():
            continue
        tags = item.get("tags", []) or []
        cat = item.get("category", "")
        if not any(t in str(tags) or t in cat for t in ["Prime", "PrimePart"]):
            if "prime" not in item.get("name", "").lower():
                continue

        # Collect tradable components, deduplicating by uniqueName.
        comps = item.get("components", [])
        parts_by_un: dict[str, dict] = {}
        for c in comps:
            cu = c.get("uniqueName", "")
            if c.get("tradable") is False:
                continue
            if cu in parts_by_un:
                parts_by_un[cu]["count"] += c.get("itemCount", 1)
            else:
                parts_by_un[cu] = {
                    "uniqueName": cu,
                    "name": c.get("name", ""),
                    "count": c.get("itemCount", 1),
                    "ducats": c.get("primeSellingPrice") or c.get("ducats", 0),
                }

        parts_needed = list(parts_by_un.values())
        primes[un] = {
            "name": item.get("name", un),
            "category": cat,
            "masteryReq": item.get("masteryReq", 0),
            "parts": parts_needed,
            "is_cosmetic": cat in ("Skins", "Glyphs", "Sigils", "Node"),
            "masterable": item.get("masterable", False),
        }
    return primes


def _analyze_parts(
    info: dict,
    prime_map: dict,
    needed_builds: int,
    owned: dict,
    owned_finished: set,
) -> tuple[list[PartAnalysis], int]:
    """Analyse parts for a prime item; return ``(part_analyses, missing_count)``."""
    part_analysis: list[PartAnalysis] = []
    missing_parts = 0
    for part in info["parts"]:
        pn = normalize_path(part["uniqueName"])
        owned_qty = owned.get(pn, 0)

        if part["uniqueName"] in prime_map and pn in owned_finished:
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
    return part_analysis, missing_parts


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
    total_parts: int,
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
        total_parts=total_parts,
    )


def _classify_prime_item(
    un: str,
    info: dict,
    owned: dict,
    owned_finished: set,
    extra_builds: dict,
    prime_map: dict,
) -> tuple[ItemResult | None, MissingPrime | None]:
    """Analyse one prime item and return ``(item_result, missing_prime)``."""
    norm = normalize_path(un)
    name = info["name"]
    cat = info["category"]

    has_copy = norm in owned_finished
    needed_builds = (0 if has_copy else 1) + extra_builds.get(un, 0)
    extra = extra_builds.get(un, 0)

    part_analysis, missing_parts = _analyze_parts(
        info, prime_map, needed_builds, owned, owned_finished
    )  # noqa: E501

    if has_copy:
        return _make_item_result(
            name,
            cat,
            "have_copy",
            needed_builds,
            extra,
            info["masteryReq"],
            True,
            missing_parts == 0,
            missing_parts,
            len(info["parts"]),
            part_analysis,
        ), None  # noqa: E501
    elif missing_parts == 0 and info["parts"]:
        return _make_item_result(
            name,
            cat,
            "buildable",
            needed_builds,
            extra,
            info["masteryReq"],
            False,
            True,
            0,
            len(info["parts"]),
            part_analysis,
        ), None  # noqa: E501
    elif any(p.owned > 0 for p in part_analysis):
        return _make_item_result(
            name,
            cat,
            "partial",
            needed_builds,
            extra,
            info["masteryReq"],
            False,
            missing_parts == 0,
            missing_parts,
            len(info["parts"]),
            part_analysis,
        ), None  # noqa: E501
    elif info["masterable"]:
        return None, MissingPrime(name=name, category=cat, mastery_req=info["masteryReq"])

    return None, None


def analyze(
    inventory: dict,
    prime_map: dict[str, dict],
    equipment_sections: list[str],
    mastered: set[str] | None = None,
) -> AnalysisResult:
    """Cross-reference inventory against prime_map.

    This is the **main analysis pipeline** for the ``--ducats`` sub-command.
    It classifies every Prime item into one of four statuses:

    * ``have_copy`` — player owns a finished copy.
    * ``buildable`` — all parts owned, ready to build.
    * ``partial`` — some parts owned, some missing.
    * Missing — zero parts owned (added to ``missing_primes`` list).

    Two phases:
    1. Extra-build calculation — determine how many copies of sub-craft
       dependencies must be built.
    2. Per-item analysis — for each Prime, compute owned/needed/surplus/missing
       for every tradable component, and aggregate ducat values.
    """
    owned = build_owned(inventory)
    owned_finished = _compute_owned_finished(inventory, equipment_sections, mastered)
    extra_builds = _compute_extra_builds(prime_map, owned, owned_finished)

    results: list[ItemResult] = []
    missing_primes: list[MissingPrime] = []
    total_ducats_safe = 0
    total_ducats_keep = 0

    for un, info in prime_map.items():
        if info["is_cosmetic"]:
            continue

        item_result, missing_prime = _classify_prime_item(
            un, info, owned, owned_finished, extra_builds, prime_map
        )  # noqa: E501

        if item_result:
            results.append(item_result)
            total_ducats_safe += sum(p.surplus * p.ducats for p in item_result.parts)
            total_ducats_keep += sum((p.owned - p.surplus) * p.ducats for p in item_result.parts)
        if missing_prime:
            missing_primes.append(missing_prime)

    return AnalysisResult(
        items=results,
        missing=missing_primes,
        total_ducats_safe=total_ducats_safe,
        total_ducats_keep=total_ducats_keep,
    )
