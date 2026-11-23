"""Relic and missing-Prime-part analysis.

Owned relics are cross-referenced against the universal ownership state in
:class:`~warframe_profile.model.data.Inventory` to answer two questions:

* which relics are safe to open because every Prime reward is covered; and
* which missing Prime parts can drop from the player's own relics.
"""

from collections import defaultdict
from dataclasses import dataclass

from warframe_profile.model.data import Inventory, ItemData

#: Categories whose prime parts use the "Warframe" naming convention.
_FRAME_CATEGORIES: frozenset[str] = frozenset({"Warframes", "Archwing"})

#: Categories whose prime parts use the "Weapon" naming convention.
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


def _not_needed_part_names(required_parts) -> set[str]:
    """Return reward-style names for parts whose requirements are covered."""
    not_needed: set[str] = set()
    for info, part, needed_builds, owned_qty in required_parts:
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
    not_needed = inventory.not_needed_part_names

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


def _normalized_rarities(rewards: list[dict]) -> list[str]:
    """Return Common/Uncommon/Rare labels for one relic's rewards.

    Some WFCD exports label every non-rare slot as ``Uncommon``.  Relic
    chances always form three groups (3 common, 2 uncommon, 1 rare per
    refinement), so derive the rarity from the chance multiplicities and fall
    back to the stored label when chance data is missing.
    """
    chances = []
    for reward in rewards:
        chance = reward.get("chance")
        if not isinstance(chance, (int, float, str)):
            return [reward.get("rarity") or "" for reward in rewards]
        try:
            chances.append(round(float(chance), 2))
        except (TypeError, ValueError):
            return [reward.get("rarity") or "" for reward in rewards]

    counts = defaultdict(int)
    for chance in chances:
        counts[chance] += 1
    if len(counts) < 2 or max(counts.values()) < 2:
        return [reward.get("rarity") or "" for reward in rewards]

    by_chance = {
        chance: "Common" if count >= 3 else "Uncommon" if count == 2 else "Rare"
        for chance, count in counts.items()
    }
    return [by_chance[chance] for chance in chances]


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
        rewards = relic.get("rewards", [])
        rarities = _normalized_rarities(rewards)
        for reward, rarity in zip(rewards, rarities):
            index[reward["item"]["name"]].append((short_name, relic_count, tier, rarity))
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
    for info, part, needed_builds, owned_qty in inventory.required_parts:
        needed = _process_needed_part(part, info, needed_builds, owned_qty, reward_index)
        if needed:
            result.append(needed)

    result.sort(key=lambda x: (x.item_name, x.part_type))
    return result
