"""Crafting-tree domain logic — index builders, recipe resolution, and
weapon-chain analysis.

This module contains only pure data-processing functions (the **model**
in MVP).  Presentation (ANSI colour, PrettyTable, ``print()``) lives in
:mod:`warframe_profile.report`; orchestration lives in
:mod:`warframe_profile.scripts.craft_tree`.

The main entry points are:

* :func:`resolve_tree` — recursively expand a crafting tree for a given item.
* :func:`build_weapon_chains` — find weapon upgrade paths (e.g. MK1-Braton
  → Braton → Braton Prime) where the final tier is unowned.
* :func:`find_items` — search items by name substring for interactive lookup.

Data flow:
  1. Build indices from the raw DE export dict (``build_items_by_un``,
     ``build_recipes_by_result``).
  2. Optionally build a name lookup (``build_lookup``) for search.
  3. Call ``resolve_tree`` to expand crafting requirements recursively.
  4. Post-process results with ``compute_crafting_plan`` → ``decompose_raw_materials``
     → ``preserve_blueprints`` to produce the final aggregated view.
"""

import math
import re
from collections import defaultdict

from warframe_profile.model.utils import normalize_path

# ---------------------------------------------------------------------------
# Index builders
# ---------------------------------------------------------------------------
# These convert the raw DE export dict (with top-level keys like "warframes",
# "weapons", "recipes") into flat lookup dictionaries keyed by uniqueName.
# They are called once at startup by presenters.
#


def build_items_by_un(db: dict) -> dict:
    """Build a ``uniqueName.lower() → item`` lookup from the export.

    Items are sourced from these top-level keys of the DE export dict:
    ``warframes, weapons, resources, customs, gear, sentinels, keys``.
    Each item gets a ``_source`` key so :func:`categorize` can determine
    the human-readable category later.

    The key is lowercased uniqueName for case-insensitive lookups.
    """
    items: dict = {}
    source_labels = [
        "warframes",
        "weapons",
        "resources",
        "customs",
        "gear",
        "sentinels",
        "keys",
    ]
    for label in source_labels:
        for un, val in db.get(label, {}).items():
            val["uniqueName"] = un
            val["_source"] = label
            items[un.lower()] = val
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
# The ``--craft`` sub-command supports name-based item search.  These
# functions build a name index and find items by substring matching with
# relevance scoring (exact → starts-with → word match → loose).
#


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
# The core of the crafting tree analysis.  ``resolve_tree`` recursively
# expands an item into its component requirements, following the recipe
# chain until either:
#   - The component has no recipe (raw material).
#   - Max depth is reached.
#   - The component is a blueprint (blueprints are treated as leaves).
#
# Returns (requirements, craftables) where:
#   requirements — raw materials / non-craftable components needed.
#   craftables   — intermediate items that need to be built first.
#


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


def _leaf_result(
    item_un: str,
    quantity: int,
    item_name: str,
    parent_name: str | None,
    owned: dict,
) -> tuple[dict, dict]:
    """Build a leaf-node result for an item with no further components."""
    name = item_name or un_to_name(item_un)
    if parent_name and ("blueprint" in name.lower() or is_blueprint_un(item_un)):
        name = f"{parent_name} Blueprint"
    return {
        item_un.lower(): {
            "name": name,
            "quantity": quantity,
            "owned": owned.get(normalize_path(item_un), 0),
        }
    }, {}  # noqa: E501


def _register_self_craftable(
    item_un: str,
    quantity: int,
    item_name: str,
    owned_qty: int,
    owned: dict,
    num_crafts: int,
    build_qty: int,
    recipe: dict,
) -> tuple[dict, dict]:
    """Register the item itself in craftables and its blueprint in requirements."""
    result: dict = {}
    craftables: dict = {}
    if owned_qty < quantity:
        craftables[item_un.lower()] = {
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
                bp_name = f"{item_name} Blueprint" if item_name else un_to_name(recipe_key)
                result[recipe_key.lower()] = {"name": bp_name, "quantity": 1, "owned": bp_owned}
    return result, craftables


def _compute_effective_crafts(
    quantity: int,
    build_qty: int,
    owned: dict,
    item_un: str,
    depth: int,
    num_crafts: int,
) -> int:
    """Compute how many times we must craft to cover the shortage."""
    if depth > 0:
        item_owned_qty = owned.get(normalize_path(item_un), 0)
        remaining_qty = max(0, quantity - item_owned_qty)
        return max(0, math.ceil(remaining_qty / build_qty)) if remaining_qty > 0 else 0
    return num_crafts


def _aggregate_components(components: list) -> dict[str, dict]:
    """Group duplicate components and sum their counts."""
    agg: dict[str, dict] = {}
    for comp in components:
        key = comp.get("uniqueName", "").lower()
        if key in agg:
            agg[key]["itemCount"] += comp.get("itemCount", 1)
        else:
            agg[key] = dict(comp)
    return agg


def _merge_sub_into(dest: dict, sub: dict) -> None:
    """Merge *sub* entries into *dest*, summing quantities on conflict."""
    for k, v in sub.items():
        if k in dest:
            dest[k]["quantity"] += v["quantity"]
        else:
            dest[k] = dict(v)


def _process_expanded_component(
    comp_un: str,
    total: int,
    items_by_un: dict,
    owned: dict,
    recipes_by_result: dict,
    loc_dict: dict,
    depth: int,
    max_depth: int,
    parent_name: str | None,
) -> tuple[dict, dict]:
    """Recursively resolve a component that should be expanded."""
    return resolve_tree(
        comp_un,
        total,
        items_by_un,
        owned,
        recipes_by_result,
        loc_dict,
        depth + 1,
        max_depth,
        parent_name,
    )  # noqa: E501


def _process_unexpanded_component(
    comp_un_lower: str,
    total: int,
    comp: dict,
    items_by_un: dict,
    owned: dict,
    recipes_by_result: dict,
    loc_dict: dict,
    depth: int,
    max_depth: int,
    parent_name: str | None,
) -> tuple[dict, dict]:
    """Add an unexpanded component to craftables or requirements."""
    is_bp = "blueprint" in comp.get("name", "").lower() or is_blueprint_un(
        comp.get("uniqueName", "")
    )  # noqa: E501
    is_craftable = should_expand(comp_un_lower, recipes_by_result, depth, max_depth, is_bp)
    name = display_name(comp, items_by_un, parent_name, loc_dict)
    comp_owned = owned.get(normalize_path(comp.get("uniqueName", "")), 0)

    if is_craftable:
        comp_recipe = recipes_by_result.get(comp_un_lower, {})
        return {}, {
            comp_un_lower: {
                "name": name,
                "quantity": total,
                "owned": comp_owned,
                "build_qty": comp_recipe.get("num", 1) or 1,
            }
        }  # noqa: E501
    return {comp_un_lower: {"name": name, "quantity": total, "owned": comp_owned}}, {}


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
    components = (
        get_recipe_components(item_un_lower, items_by_un, recipes_by_result, loc_dict)
        if has_recipe(item_un_lower, recipes_by_result)
        else []
    )  # noqa: E501

    if not item and not components:
        return {}, {}

    item_name = resolve_name(item.get("name", ""), loc_dict) if item else un_to_name(item_un)

    if not components or depth >= max_depth:
        return _leaf_result(item_un, quantity, item_name, parent_name, owned)

    recipe = recipes_by_result[item_un_lower]
    build_qty = recipe.get("num", 1) or 1
    consumable = recipe.get("consumeOnUse", True)
    num_crafts = max(1, math.ceil(quantity / build_qty))

    result: dict = {}
    craftables: dict = {}

    if depth > 0 and components:
        norm = normalize_path(item_un)
        owned_qty = owned.get(norm, 0)
        bp_result, self_craft = _register_self_craftable(
            item_un, quantity, item_name, owned_qty, owned, num_crafts, build_qty, recipe
        )  # noqa: E501
        result.update(bp_result)
        craftables.update(self_craft)

    effective_crafts = _compute_effective_crafts(
        quantity, build_qty, owned, item_un, depth, num_crafts
    )  # noqa: E501
    agg_components = _aggregate_components(components)

    for comp_un_lower, comp in agg_components.items():
        comp_un = comp.get("uniqueName", "")
        comp_count = comp.get("itemCount", 1)

        is_bp = "blueprint" in comp.get("name", "").lower() or is_blueprint_un(comp_un)
        total = comp_count if (is_bp and not consumable) else effective_crafts * comp_count

        comp_owned = owned.get(normalize_path(comp_un), 0)
        expand = (
            should_expand(comp_un_lower, recipes_by_result, depth, max_depth, is_bp)
            and comp_owned < total
        )  # noqa: E501

        if expand:
            sub, sub_craft = _process_expanded_component(
                comp_un,
                total,
                items_by_un,
                owned,
                recipes_by_result,
                loc_dict,
                depth,
                max_depth,
                item_name,
            )  # noqa: E501
            _merge_sub_into(result, sub)
            _merge_sub_into(craftables, sub_craft)
        else:
            sub_req, sub_craft = _process_unexpanded_component(
                comp_un_lower,
                total,
                comp,
                items_by_un,
                owned,
                recipes_by_result,
                loc_dict,
                depth,
                max_depth,
                item_name,
            )  # noqa: E501
            _merge_sub_into(result, sub_req)
            _merge_sub_into(craftables, sub_craft)

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
        " ",
        name,
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
# After ``resolve_tree`` produces (requirements, craftables), the
# post-processing steps refine the output:
#
# 1. compute_crafting_plan — adjusts num_crafts based on owned quantities.
# 2. decompose_raw_materials — replaces craftable intermediates with their
#    raw ingredients, and adds blueprint entries for items not yet owned.
# 3. preserve_blueprints — ensures blueprint requirements that were replaced
#    by raw materials are still shown if not yet owned.
#


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
                    resolve_name(item.get("name", ""), loc_dict) if item else un_to_name(ing_type)
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
# Warframe has weapon upgrade paths where a lower-tier weapon is used as
# an ingredient to craft a higher-tier version.  For example:
#
#   MK1-Braton → Braton → Braton Prime
#
# ``build_weapon_chains`` discovers all such chains in the game database
# and filters to those where the final weapon is not yet owned.  This
# helps players identify which weapons to keep (they're needed for the
# upgrade) vs sell (they're dead ends).
#


def _build_weapon_graph(
    items_by_un: dict,
    recipes_by_result: dict,
    weapon_uns: set[str],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Build weapon dependency graph: ``builds_into`` and ``built_from``."""
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

    return builds_into, built_from


def _find_chains(
    weapon_uns: set[str],
    builds_into: dict,
    built_from: dict,
) -> list[list[str]]:
    """Walk dependency graph to find linear upgrade chains."""
    roots = sorted(
        un
        for un in weapon_uns
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
    return chains


def _aggregate_chain_requirements(
    chain: list[str],
    items_by_un: dict,
    owned: dict,
    recipes_by_result: dict,
    loc_dict: dict,
    all_requirements: dict,
    all_craftables: dict,
    max_depth: int,
) -> None:
    """Resolve crafting tree for one chain and merge into accumulators."""
    final_un = chain[-1]
    final_norm = normalize_path(final_un)
    if owned.get(final_norm, 0) > 0:
        return

    req, craft = resolve_tree(
        final_un, 1, items_by_un, owned, recipes_by_result, loc_dict, max_depth=max_depth
    )  # noqa: E501
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
                    all_requirements[key]["quantity"] = max(all_requirements[key]["quantity"], 1)
                else:
                    all_requirements[key] = {"name": bp_name, "quantity": 1, "owned": bp_owned}


def build_weapon_chains(
    items_by_un: dict,
    recipes_by_result: dict,
    owned: dict,
    loc_dict: dict,
    max_depth: int = 5,
) -> tuple[list[list[str]], dict, dict, int]:
    """Build weapon upgrade chains whose final weapon is not yet owned."""
    _weapon_cats = {"Primary", "Secondary", "Melee"}

    weapon_uns = {
        un_lower for un_lower, item in items_by_un.items() if categorize(item) in _weapon_cats
    }  # noqa: E501

    builds_into, built_from = _build_weapon_graph(items_by_un, recipes_by_result, weapon_uns)
    chains = _find_chains(weapon_uns, builds_into, built_from)

    all_requirements: dict[str, dict] = {}
    all_craftables: dict[str, dict] = {}
    shown = 0

    for chain in chains:
        _aggregate_chain_requirements(
            chain,
            items_by_un,
            owned,
            recipes_by_result,
            loc_dict,
            all_requirements,
            all_craftables,
            max_depth,
        )  # noqa: E501
        shown += 1

    compute_crafting_plan(all_craftables, owned)

    return chains, all_requirements, all_craftables, shown
