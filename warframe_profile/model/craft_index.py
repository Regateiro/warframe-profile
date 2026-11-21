"""Pure item/recipe indices and search helpers for the crafting model."""

import math
import re
from collections import defaultdict

#: Maps in-game ``productCategory`` values to human-readable labels.
_CATEGORY_BY_PC = {
    "Suits": "Warframe",
    "MechSuits": "Necramech",
    "LongGuns": "Primary",
    "Pistols": "Secondary",
    "Melee": "Melee",
    "SpaceGuns": "Arch-Gun",
    "SpaceMelee": "Arch-Melee",
    "SpaceSuits": "Archwing",
    "Sentinels": "Sentinel",
    "SentinelWeapons": "Sentinel Weapon",
    "OperatorAmps": "Operator Amp",
    "DrifterMelee": "Drifter Melee",
    "Ships": "Ship",
    "CrewShips": "Crew Ship",
    "CrewShipWeaponSkins": "Ship Cosmetic",
    "ShipDecorations": "Cosmetic",
    "WeaponSkins": "Cosmetic",
    "KubrowPets": "Pet",
    "MoaPets": "Pet",
    "FusionTreasures": "Curio",
    "SpecialItems": "Special",
    "SupplyDrop": "Supply",
    "MiscItems": "Resources",
}

_SOURCE_CATEGORY_FALLBACK: dict[str, str] = {
    "resources": "Resources",
    "keys": "Keys",
    "gear": "Gear",
    "customs": "Cosmetic",
}


def build_items_by_un(db: dict) -> dict:
    """Build a ``uniqueName.lower() → item`` lookup from the export.

    Older databases store items in the raw per-category DE sections
    (``warframes, weapons, resources, customs, gear, sentinels, keys``);
    each item gets a ``_source`` key so :func:`categorize` can determine
    the human-readable category later.  Newer, compact databases store the
    already-merged ``items`` list instead, so fall back to that when the raw
    sections are absent.

    The key is lowercased uniqueName for case-insensitive lookups.
    """
    items: dict = {}
    from warframe_profile.scripts.update_export_db import DE_ITEM_LABELS

    raw_sections_present = any(isinstance(db.get(label), dict) for label in DE_ITEM_LABELS)
    if raw_sections_present:
        for label in DE_ITEM_LABELS:
            for un, val in db.get(label, {}).items():
                val["uniqueName"] = un
                val["_source"] = label
                items[un.lower()] = val
        return items

    for item in db.get("items", []):
        # ``_source`` is only set for items that came from a DE item section;
        # WFCD-only extras are intentionally excluded from the craft index.
        if not item.get("_source"):
            continue
        un = item.get("uniqueName", "")
        if un:
            items[un.lower()] = item
    return items


def build_recipes_by_result(db: dict) -> dict:
    """Build a ``resultType.lower() → recipe`` lookup.

    The DE export stores recipes keyed by their uniqueName (like
    ``/Lotus/Recipes/Weapons/LatoPrimeBlueprint``).  This function
    re-indexes them by their ``resultType`` — the uniqueName of the
    item produced — which is what the crafting tree resolver needs.

    Only the first recipe per ``resultType`` is kept (duplicate
    resultTypes are discarded).
    """
    by_result: dict = {}
    for key, val in db.get("recipes", {}).items():
        rt = val.get("resultType")
        if rt and rt not in by_result:
            by_result[rt.lower()] = {
                "_recipeKey": key,
                "ingredients": val.get("ingredients", []),
                "num": val.get("num", 1) or 1,
                "consumeOnUse": val.get("consumeOnUse", True),
            }
    return by_result


# ---------------------------------------------------------------------------
# Name resolution
# ---------------------------------------------------------------------------


def resolve_name(name: str, loc_dict: dict) -> str:
    """Resolve a ``/Lotus/Language/...`` key to its human-readable form.

    Falls back to the last path segment if the key is missing from
    *loc_dict*.
    """
    if isinstance(name, str) and name.startswith("/Lotus/Language/"):
        return loc_dict.get(name, name.split("/")[-1])
    return name


#: Maps in-game ``productCategory`` values to human-readable labels.
_CATEGORY_BY_PC = {
    "Suits": "Warframe",
    "MechSuits": "Necramech",
    "LongGuns": "Primary",
    "Pistols": "Secondary",
    "Melee": "Melee",
    "SpaceGuns": "Arch-Gun",
    "SpaceMelee": "Arch-Melee",
    "SpaceSuits": "Archwing",
    "Sentinels": "Sentinel",
    "SentinelWeapons": "Sentinel Weapon",
    "OperatorAmps": "Operator Amp",
    "DrifterMelee": "Drifter Melee",
    "Ships": "Ship",
    "CrewShips": "Crew Ship",
    "CrewShipWeaponSkins": "Ship Cosmetic",
    "ShipDecorations": "Cosmetic",
    "WeaponSkins": "Cosmetic",
    "KubrowPets": "Pet",
    "MoaPets": "Pet",
    "FusionTreasures": "Curio",
    "SpecialItems": "Special",
    "SupplyDrop": "Supply",
    "MiscItems": "Resources",
}

#: Fallback category labels when ``productCategory`` is unavailable,
#: keyed by the ``_source`` label assigned during import.
_SOURCE_CATEGORY_FALLBACK: dict[str, str] = {
    "resources": "Resources",
    "keys": "Keys",
    "gear": "Gear",
    "customs": "Cosmetic",
}


def categorize(item: dict) -> str:
    """Return a human-readable category for *item*.

    Uses ``productCategory`` first, then falls back to ``_source``.
    """
    pc = item.get("productCategory")
    if pc:
        return _CATEGORY_BY_PC.get(pc, str(pc))
    source = item.get("_source", "")
    return _SOURCE_CATEGORY_FALLBACK.get(source, "Misc")


# ---------------------------------------------------------------------------
# Recipe helpers
# ---------------------------------------------------------------------------


def has_recipe(item_un_lower: str, recipes_by_result: dict) -> bool:
    """Check if an item has a known recipe."""
    return item_un_lower in recipes_by_result


def get_recipe_components(
    item_un_lower: str,
    items_by_un: dict,
    recipes_by_result: dict,
    loc_dict: dict | None = None,
) -> list[dict]:
    """Return the ingredient list for *item_un_lower*, with resolved names."""
    recipe = recipes_by_result.get(item_un_lower)
    if not recipe:
        return []
    _loc = loc_dict or {}
    comps: list[dict] = []
    for ing in recipe["ingredients"]:
        ing_un = ing["ItemType"]
        ing_item = items_by_un.get(ing_un.lower())
        ing_name = resolve_name(ing_item.get("name", ""), _loc) if ing_item else ""
        comps.append(
            {
                "uniqueName": ing_un,
                "name": ing_name,
                "itemCount": ing["ItemCount"],
            }
        )
    return comps


# ---------------------------------------------------------------------------
# Search / lookup
# ---------------------------------------------------------------------------
# The ``craft`` sub-command supports name-based item search.  These
# functions build a name index and find items by substring matching with
# relevance scoring (exact → starts-with → word match → loose).
#


def build_lookup(items_by_un: dict, loc_dict: dict) -> dict:
    """Build a ``name.lower() → [items]`` lookup for text search."""
    by_name: dict = defaultdict(list)
    for item in items_by_un.values():
        name = resolve_name(item.get("name", ""), loc_dict)
        if name:
            item["_resolved_name"] = name
            by_name[name.lower()].append(item)
    return by_name


def find_items(query: str, by_name: dict) -> list[dict]:
    """Search items by name substring, returning results sorted by relevance.

    Relevance scoring:
        4 = exact match
        3 = starts-with match
        2 = word / hyphen-separated match
        1 = loose substring match
    """
    q = query.lower()
    results: list[tuple[int, dict]] = []
    for name_lower, items in by_name.items():
        if q in name_lower:
            for item in items:
                if name_lower == q:
                    score = 4
                elif name_lower.startswith(q):
                    score = 3
                elif f" {q}" in name_lower or f"-{q}" in name_lower:
                    score = 2
                else:
                    score = 1
                results.append((score, item))
    results.sort(key=lambda x: (-x[0], len(x[1].get("_resolved_name", ""))))
    return [item for _, item in results]


def is_blueprint_un(un: str) -> bool:
    """Check if a uniqueName represents a blueprint."""
    return "/Blueprint" in un or un.lower().endswith("blueprint")


_CAMEL_CASE_RE = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def un_to_name(un: str) -> str:
    """Convert a raw uniqueName to a rough display name."""
    name = un.split("/")[-1]
    name = name.replace("Component", "").replace("Blueprint", "")
    name = _CAMEL_CASE_RE.sub(" ", name)
    return name.strip()


def weapon_name(un_lower: str, items_by_un: dict, loc_dict: dict) -> str:
    """Return the display name for a weapon uniqueName."""
    item = items_by_un.get(un_lower)
    if item:
        name = resolve_name(item.get("name", ""), loc_dict)
        if name:
            return name
    return un_to_name(un_lower)


def merge_dicts(acc: dict, src: dict, *fields: str) -> None:
    """Merge *src* into *acc*, summing the named *fields* for duplicate keys."""
    for k, v in src.items():
        if k in acc:
            for f in fields:
                acc[k][f] += v.get(f, 0)
        else:
            acc[k] = dict(v)


def compute_crafting_plan(craftables: dict) -> dict:
    """Compute ``num_crafts`` for each craftable from its remaining quantity.

    *craftables* is mutated in place and returned.
    """
    for v in craftables.values():
        bq = v.get("build_qty", 1) or 1
        oq = v.get("owned", 0)
        remaining = max(0, v["quantity"] - oq)
        v["num_crafts"] = max(0, math.ceil(remaining / bq))
    return craftables
