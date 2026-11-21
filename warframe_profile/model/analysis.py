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
from dataclasses import dataclass, field
from enum import Enum

from warframe_profile.model.cleanup import (
    ExcessItem,
    SellableEquipment,
    compute_sellable_equipment,
    find_excess_blueprints_and_components,
)
from warframe_profile.model.data import Inventory, ItemData
from warframe_profile.model.relics import (
    NeededPart,
    RelicInfo,
    _compute_needed_builds,
    build_needed_drops,
    build_relics_map,
)

__all__ = [
    "AnalysisResult",
    "ExcessItem",
    "ItemResult",
    "MissingPrime",
    "NeededPart",
    "PartAnalysis",
    "RelicInfo",
    "SellableEquipment",
    "analyze",
    "build_component_blueprint_map",
    "build_needed_drops",
    "build_prime_map",
    "build_relics_map",
    "compute_sellable_equipment",
    "find_excess_blueprints_and_components",
]

#: Tags / category substrings used to identify Prime items.
_PRIME_TAGS: frozenset[str] = frozenset({"Prime", "PrimePart"})

#: WFCD categories that are cosmetic (not tradable for ducats).
_COSMETIC_CATEGORIES: frozenset[str] = frozenset({"Skins", "Glyphs", "Sigils", "Node"})

#: Component names that are not component-level items.
_NON_COMPONENT_NAMES: frozenset[str] = frozenset(
    {"Blueprint", "Ornament", "Handle", "Blade", "Link", "Head"}
)


class ItemStatus(str, Enum):
    """Classification of a player's ownership status for a Prime item."""

    HAVE_COPY = "have_copy"
    BUILDABLE = "buildable"
    CRAFTING = "crafting"
    PARTIAL = "partial"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


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
