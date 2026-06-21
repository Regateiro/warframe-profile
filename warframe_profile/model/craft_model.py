"""Crafting-tree domain logic — index builders, recipe resolution, and
weapon-chain analysis.

This module contains only pure data-processing functions (the **model**
in MVP).  Presentation (ANSI colour, PrettyTable, ``print()``) lives in
:mod:`warframe_profile.report`; orchestration lives in
:mod:`warframe_profile.scripts.craft_tree`.
"""

import math
import re
from collections import defaultdict

from warframe_profile.model.utils import normalize_path


# ---------------------------------------------------------------------------
# Index builders
# ---------------------------------------------------------------------------

def build_items_by_un(db: dict) -> dict:
    """Build a ``uniqueName.lower() → item`` lookup from the export.

    Items are sourced from these top-level keys:
    ``warframes, weapons, resources, customs, gear, sentinels, keys``.
    Each item gets a ``_source`` key so :func:`categorize` can work.
    """
    items: dict = {}
    source_labels = [
        "warframes", "weapons", "resources", "customs",
        "gear", "sentinels", "keys",
    ]
    for label in source_labels:
        for un, val in db.get(label, {}).items():
            val["uniqueName"] = un
            val["_source"] = label
            items[un.lower()] = val
    return items


def build_recipes_by_result(db: dict) -> dict:
    """Build a ``resultType.lower() → recipe`` lookup.

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
    "Suits":             "Warframe",
    "MechSuits":         "Necramech",
    "LongGuns":          "Primary",
    "Pistols":           "Secondary",
    "Melee":             "Melee",
    "SpaceGuns":         "Arch-Gun",
    "SpaceMelee":        "Arch-Melee",
    "SpaceSuits":        "Archwing",
    "Sentinels":         "Sentinel",
    "SentinelWeapons":   "Sentinel Weapon",
    "OperatorAmps":      "Operator Amp",
    "DrifterMelee":      "Drifter Melee",
    "Ships":             "Ship",
    "CrewShips":         "Crew Ship",
    "CrewShipWeaponSkins": "Ship Cosmetic",
    "ShipDecorations":   "Cosmetic",
    "WeaponSkins":       "Cosmetic",
    "KubrowPets":        "Pet",
    "MoaPets":           "Pet",
    "FusionTreasures":   "Curio",
    "SpecialItems":      "Special",
    "SupplyDrop":        "Supply",
    "MiscItems":         "Resources",
}


def categorize(item: dict) -> str:
    """Return a human-readable category for *item*.

    Uses ``productCategory`` first, then falls back to ``_source``.
    """
    pc = item.get("productCategory")
    if pc:
        return _CATEGORY_BY_PC.get(pc, pc)
    source = item.get("_source", "")
    if source == "resources":
        return "Resources"
    if source == "keys":
        return "Keys"
    if source == "gear":
        return "Gear"
    if source == "customs":
        return "Cosmetic"
    return "Misc"


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
        ing_name = (
            resolve_name(ing_item.get("name", ""), _loc)
            if ing_item else ""
        )
        comps.append({
            "uniqueName": ing_un,
            "name": ing_name,
            "itemCount": ing["ItemCount"],
        })
    return comps


# ---------------------------------------------------------------------------
# Search / lookup
# ---------------------------------------------------------------------------

def build_lookup(items_by_un: dict, loc_dict: dict) -> dict:
    """Build a ``name.lower() → [items]`` lookup for text search."""
    by_name: dict = defaultdict(list)
    for un_lower, item in items_by_un.items():
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


# ---------------------------------------------------------------------------
# Tree resolution
# ---------------------------------------------------------------------------

def _is_resource_transform(recipe: dict) -> bool:
    """Return ``True`` if the recipe key ends with ``ResourceBlueprint``."""
    key = recipe.get("_recipeKey", "")
    return key.endswith("ResourceBlueprint")


def should_expand(
    item_un_lower: str,
    recipes_by_result: dict,
    depth: int,
    max_depth: int,
    is_bp: bool,
) -> bool:
    """Decide whether a component should be expanded into its own recipe tree.

    Expansions are skipped for blueprints, at max depth, when no recipe
    exists, and for resource-transformation recipes.
    """
    if is_bp:
        return False
    if depth >= max_depth - 1:
        return False
    if item_un_lower not in recipes_by_result:
        return False
    recipe = recipes_by_result[item_un_lower]
    if _is_resource_transform(recipe):
        return False
    return True


def display_name(
    comp: dict,
    items_by_un: dict,
    parent_name: str | None,
    loc_dict: dict,
) -> str:
    """Return the best display name for a component.

    Blueprint components are labelled ``"{parent_name} Blueprint"``.
    """
    comp_un_lower = comp.get("uniqueName", "").lower()
    comp_name = comp.get("name", "")
    if comp_name:
        comp_name = resolve_name(comp_name, loc_dict)
    is_bp = "blueprint" in comp_name.lower() or "blueprint" in comp_un_lower
    if is_bp and parent_name:
        return f"{parent_name} Blueprint"
    item = items_by_un.get(comp_un_lower)
    if item:
        return resolve_name(item.get("name", comp_name), loc_dict) or comp_name
    if comp_name:
        return comp_name
    return comp_un_lower.split("/")[-1]


def resolve_tree(
    item_un: str,
    quantity: int,
    items_by_un: dict,
    owned: dict,
    recipes_by_result: dict,
    loc_dict: dict,
    depth: int = 0,
    max_depth: int = 2,
    parent_name: str | None = None,
) -> tuple[dict, dict]:
    """Recursively resolve the full crafting tree for an item.

    Returns ``(requirements, craftables)`` where:

    * *requirements* maps ``uniqueName.lower()`` → ``{name, quantity, owned}``
    * *craftables* maps ``uniqueName.lower()`` → ``{name, quantity, owned, num_crafts, build_qty}``
    """
    item_un_lower = item_un.lower()
    item = items_by_un.get(item_un_lower)
    recipe_components = (
        get_recipe_components(item_un_lower, items_by_un, recipes_by_result, loc_dict)
        if has_recipe(item_un_lower, recipes_by_result)
        else []
    )
    components = recipe_components

    if not item and not components:
        return {}, {}

    item_name = (
        resolve_name(item.get("name", ""), loc_dict)
        if item else un_to_name(item_un)
    )

    if not components or depth >= max_depth:
        name = item_name or un_to_name(item_un)
        if parent_name and ("blueprint" in name.lower()
                            or is_blueprint_un(item_un)):
            name = f"{parent_name} Blueprint"
        return (
            {item_un_lower: {
                "name": name,
                "quantity": quantity,
                "owned": owned.get(normalize_path(item_un), 0),
            }},
            {},
        )

    recipe = recipes_by_result.get(item_un_lower)
    build_qty = recipe.get("num", 1) or 1
    consumable = recipe.get("consumeOnUse", True)

    num_crafts = max(1, math.ceil(quantity / build_qty))

    craftables: dict = {}
    result: dict = {}

    if depth > 0 and components:
        norm = normalize_path(item_un)
        owned_qty = owned.get(norm, 0)
        if owned_qty < quantity:
            craftables[item_un_lower] = {
                "name": item_name or un_to_name(item_un),
                "quantity": quantity,
                "owned": owned_qty,
                "num_crafts": num_crafts,
                "build_qty": build_qty,
            }

            recipe_key = recipe.get("_recipeKey", "")
            if recipe_key:
                bp_owned = owned.get(normalize_path(recipe_key), 0)
                if bp_owned < 1:
                    bp_name = (
                        f"{item_name} Blueprint"
                        if item_name else un_to_name(recipe_key)
                    )
                    result[recipe_key.lower()] = {
                        "name": bp_name,
                        "quantity": 1,
                        "owned": bp_owned,
                    }

    if depth > 0:
        item_owned_qty = owned.get(normalize_path(item_un), 0)
        remaining_qty = max(0, quantity - item_owned_qty)
        effective_crafts = max(0, math.ceil(remaining_qty / build_qty)) if remaining_qty > 0 else 0
    else:
        effective_crafts = num_crafts

    agg_components: dict[str, dict] = {}
    for comp in components:
        key = comp.get("uniqueName", "").lower()
        if key in agg_components:
            agg_components[key]["itemCount"] += comp.get("itemCount", 1)
        else:
            agg_components[key] = dict(comp)

    for comp_un_lower, comp in agg_components.items():
        comp_un = comp.get("uniqueName", "")
        comp_count = comp.get("itemCount", 1)

        is_bp = "blueprint" in comp.get("name", "").lower() or is_blueprint_un(comp_un)
        if is_bp and not consumable:
            total = comp_count
        else:
            total = effective_crafts * comp_count

        comp_owned = owned.get(normalize_path(comp_un), 0)
        expand = (
            should_expand(comp_un_lower, recipes_by_result, depth, max_depth, is_bp)
            and comp_owned < total
        )

        if expand:
            sub, sub_craft = resolve_tree(
                comp_un, total, items_by_un, owned,
                recipes_by_result, loc_dict,
                depth + 1, max_depth, item_name,
            )
            for k, v in sub.items():
                if k in result:
                    result[k]["quantity"] += v["quantity"]
                else:
                    result[k] = dict(v)
            for k, v in sub_craft.items():
                if k in craftables:
                    craftables[k]["quantity"] += v["quantity"]
                else:
                    craftables[k] = dict(v)
        else:
            # Check if this component has a recipe (is craftable)
            is_craftable = should_expand(
                comp_un_lower, recipes_by_result, depth, max_depth, is_bp,
            )
            name = display_name(comp, items_by_un, item_name, loc_dict)
            if is_craftable:
                if comp_un_lower in craftables:
                    craftables[comp_un_lower]["quantity"] += total
                else:
                    comp_recipe = recipes_by_result.get(comp_un_lower, {})
                    craftables[comp_un_lower] = {
                        "name": name,
                        "quantity": total,
                        "owned": comp_owned,
                        "build_qty": comp_recipe.get("num", 1) or 1,
                    }
            else:
                if comp_un_lower in result:
                    result[comp_un_lower]["quantity"] += total
                else:
                    result[comp_un_lower] = {
                        "name": name,
                        "quantity": total,
                        "owned": comp_owned,
                    }

    return result, craftables


# ---------------------------------------------------------------------------
# Blueprint / name helpers
# ---------------------------------------------------------------------------

def is_blueprint_un(un: str) -> bool:
    """Check if a uniqueName represents a blueprint."""
    return "/Blueprint" in un or un.lower().endswith("blueprint")


def un_to_name(un: str) -> str:
    """Convert a raw uniqueName to a rough display name."""
    name = un.split("/")[-1]
    name = name.replace("Component", "").replace("Blueprint", "")
    name = re.sub(
        r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])",
        " ", name,
    )
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


# ---------------------------------------------------------------------------
# Post-processing pipeline
# ---------------------------------------------------------------------------

def compute_crafting_plan(craftables: dict, owned: dict) -> dict:
    """Compute ``num_crafts`` for each craftable from remaining after owned.

    *craftables* is mutated in place and returned.
    """
    for v in craftables.values():
        bq = v.get("build_qty", 1) or 1
        oq = v.get("owned", 0)
        remaining = max(0, v["quantity"] - oq)
        v["num_crafts"] = max(0, math.ceil(remaining / bq))
    return craftables


def decompose_raw_materials(
    craftables: dict,
    recipes_by_result: dict,
    items_by_un: dict,
    owned: dict,
    loc_dict: dict,
) -> dict:
    """Decompose corrected *craftables* into raw material requirements.

    Blueprint entries for each craftable are also added when the blueprint
    is not yet owned.  Returns a ``{un_lower: {name, quantity, owned}}``
    dict of non-craftable ingredients only.
    """
    craftable_keys = set(craftables.keys())
    requirements: dict = {}

    for craft_key, craft_info in craftables.items():
        num_crafts = craft_info.get("num_crafts", 0)
        if num_crafts == 0:
            continue

        recipe = recipes_by_result.get(craft_key)
        if not recipe:
            continue

        # Blueprint for this craftable
        bp_key = recipe.get("_recipeKey", "")
        if bp_key:
            bp_lower = bp_key.lower()
            bp_owned = owned.get(normalize_path(bp_key), 0)
            if bp_owned < 1 and bp_lower not in requirements:
                requirements[bp_lower] = {
                    "name": f"{craft_info['name']} Blueprint",
                    "quantity": 1,
                    "owned": bp_owned,
                }

        # Raw ingredients
        for ing in recipe.get("ingredients", []):
            ing_type = ing["ItemType"]
            ing_lower = ing_type.lower()

            if ing_lower in craftable_keys:
                continue

            total = num_crafts * ing["ItemCount"]
            ing_norm = normalize_path(ing_type)

            if ing_lower in requirements:
                requirements[ing_lower]["quantity"] += total
            else:
                item = items_by_un.get(ing_lower, {})
                ing_name = (
                    resolve_name(item.get("name", ""), loc_dict)
                    if item else un_to_name(ing_type)
                )
                requirements[ing_lower] = {
                    "name": ing_name,
                    "quantity": total,
                    "owned": owned.get(ing_norm, 0),
                }

    return requirements


def preserve_blueprints(
    all_requirements: dict,
    new_requirements: dict,
) -> dict:
    """Carry over any blueprint entries from *all_requirements* not already
    covered by the raw-material decomposition."""
    for k, v in all_requirements.items():
        if k not in new_requirements and is_blueprint_un(k):
            new_requirements[k] = dict(v)
    return new_requirements


# ---------------------------------------------------------------------------
# Weapon chain analysis
# ---------------------------------------------------------------------------

def build_weapon_chains(
    items_by_un: dict,
    recipes_by_result: dict,
    owned: dict,
    loc_dict: dict,
    max_depth: int = 5,
) -> tuple[list[list[str]], dict, dict, int]:
    """Build weapon upgrade chains whose final weapon is not yet owned.

    Returns ``(chains, all_requirements, all_craftables, shown_count)``
    where *chains* is a list of ``[root_un, ..., final_un]`` paths and the
    remaining values aggregate their crafting requirements.
    """
    _WEAPON_CATS = {"Primary", "Secondary", "Melee"}

    weapon_uns: set[str] = {
        un_lower
        for un_lower, item in items_by_un.items()
        if categorize(item) in _WEAPON_CATS
    }

    builds_into: dict[str, list[str]] = defaultdict(list)
    built_from: dict[str, list[str]] = defaultdict(list)

    for result_un_lower, recipe in recipes_by_result.items():
        if result_un_lower not in weapon_uns:
            continue
        for ing in recipe.get("ingredients", []):
            ing_un_lower = ing["ItemType"].lower()
            if ing_un_lower in weapon_uns:
                builds_into[ing_un_lower].append(result_un_lower)
                built_from[result_un_lower].append(ing_un_lower)

    roots = sorted(
        un for un in weapon_uns
        if un in builds_into and (un not in built_from or not built_from[un])
    )

    chains: list[list[str]] = []
    for root in roots:
        chain = [root]
        current = root
        while current in builds_into and builds_into[current]:
            nxt = builds_into[current][0]
            chain.append(nxt)
            current = nxt
            if len(chain) > 20:
                break
        if len(chain) >= 2:
            chains.append(chain)

    all_requirements: dict[str, dict] = {}
    all_craftables: dict[str, dict] = {}
    shown = 0

    for chain in chains:
        final_un = chain[-1]
        final_norm = normalize_path(final_un)
        if owned.get(final_norm, 0) > 0:
            continue

        req, craft = resolve_tree(
            final_un, 1, items_by_un, owned,
            recipes_by_result, loc_dict, max_depth=max_depth,
        )
        merge_dicts(all_requirements, req, "quantity")
        merge_dicts(all_craftables, craft, "quantity")

        recipe = recipes_by_result.get(final_un.lower())
        if recipe:
            bp_key = recipe.get("_recipeKey", "")
            if bp_key:
                bp_owned = owned.get(normalize_path(bp_key), 0)
                if bp_owned < 1:
                    names = [weapon_name(u, items_by_un, loc_dict) for u in chain]
                    bp_name = f"{names[-1]} Blueprint"
                    key = bp_key.lower()
                    if key in all_requirements:
                        all_requirements[key]["quantity"] = max(
                            all_requirements[key]["quantity"], 1,
                        )
                    else:
                        all_requirements[key] = {
                            "name": bp_name,
                            "quantity": 1,
                            "owned": bp_owned,
                        }

        shown += 1

    compute_crafting_plan(all_craftables, owned)

    return chains, all_requirements, all_craftables, shown
