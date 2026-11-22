"""Crafting-tree domain logic — index builders, recipe resolution, and
weapon-chain analysis.

This module contains only pure data-processing functions (the **model**
in MVP).  Presentation lives in :mod:`warframe_profile.view.report` /
:mod:`warframe_profile.view.web.templates`; orchestration lives in
:mod:`warframe_profile.presenter.craft_tree`.

The main entry points are:

* :class:`CraftNode` / :class:`CraftTree` / :class:`CraftPlan` — display-ready
  tree structures produced by :class:`~warframe_profile.model.data.ItemData`.
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

from __future__ import annotations

import math
from dataclasses import dataclass, field

from warframe_profile.model.craft_index import (
    build_items_by_un,
    build_lookup,
    build_recipes_by_result,
    categorize,
    compute_crafting_plan,
    find_items,
    get_recipe_components,
    has_recipe,
    is_blueprint_un,
    merge_dicts,
    resolve_name,
    un_to_name,
    weapon_name,
)
from warframe_profile.model.utils import normalize_path
from warframe_profile.model.weapon_chains import build_weapon_chains

__all__ = [
    "CraftNode",
    "CraftPlan",
    "CraftTree",
    "add_blueprint_requirement",
    "build_items_by_un",
    "build_lookup",
    "build_recipes_by_result",
    "build_weapon_chains",
    "categorize",
    "compute_crafting_plan",
    "decompose_raw_materials",
    "display_name",
    "find_items",
    "get_recipe_components",
    "has_recipe",
    "is_blueprint_un",
    "merge_dicts",
    "postprocess_craft_plan",
    "preserve_blueprints",
    "resolve_name",
    "resolve_tree",
    "should_expand",
    "un_to_name",
    "weapon_name",
]


@dataclass
class CraftNode:
    """One display-ready node in a resolved crafting tree."""

    name: str
    quantity: int
    owned: int
    crafting: int = 0
    reusable: bool = False
    children: list[CraftNode] = field(default_factory=list)

    @property
    def crafting_needed(self) -> int:
        """Copies in the foundry that cover part of this node's required amount."""
        available = max(0, self.owned - self.crafting)
        return min(self.crafting, max(0, self.quantity - available))


@dataclass
class CraftTree:
    """Display-ready crafting tree for a requested item."""

    name: str
    nodes: list[CraftNode] = field(default_factory=list)


@dataclass
class CraftPlan:
    """Complete, display-ready crafting plan for one item."""

    name: str
    category: str
    tree: CraftTree
    requirements: dict
    craftables: dict


# ---------------------------------------------------------------------------
# Index builders
# ---------------------------------------------------------------------------
# These convert the raw DE export dict (with top-level keys like "warframes",
# "weapons", "recipes") into flat lookup dictionaries keyed by uniqueName.
# They are called once at startup by presenters.
#


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
    return un_to_name(comp.get("uniqueName", ""))


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
    }, {}


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
    )
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
        }
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
    tree_parent: CraftTree | CraftNode | None = None,
    pending: dict | None = None,
) -> tuple[dict, dict]:
    """Recursively resolve an item into requirements and an optional view tree.

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
    )

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

    # Show the item's own blueprint in the tree even when it is already owned.
    # Official recipes list the blueprint separately from the ingredients; if a
    # legacy recipe includes it in ``components``, that row is rendered below.
    blueprint_key = recipe.get("_recipeKey", "")
    if tree_parent is not None and blueprint_key:
        has_blueprint_component = any(
            comp.get("uniqueName", "").lower() == blueprint_key.lower() for comp in components
        )
        if not has_blueprint_component:
            blueprint_node = CraftNode(
                name=f"{item_name} Blueprint",
                quantity=1,
                owned=owned.get(normalize_path(blueprint_key), 0),
                reusable=not consumable,
            )
            if isinstance(tree_parent, CraftTree):
                tree_parent.nodes.append(blueprint_node)
            else:
                tree_parent.children.append(blueprint_node)

    if depth > 0 and components:
        norm = normalize_path(item_un)
        owned_qty = owned.get(norm, 0)
        bp_result, self_craft = _register_self_craftable(
            item_un, quantity, item_name, owned_qty, owned, num_crafts, build_qty, recipe
        )
        result.update(bp_result)
        craftables.update(self_craft)

    effective_crafts = _compute_effective_crafts(
        quantity, build_qty, owned, item_un, depth, num_crafts
    )
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
        )

        node = None
        if tree_parent is not None:
            reusable = is_bp and not consumable
            node = CraftNode(
                name=display_name(comp, items_by_un, item_name, loc_dict),
                quantity=comp_count if reusable else max(total, comp_count),
                owned=comp_owned,
                crafting=pending.get(normalize_path(comp_un), 0) if pending else 0,
                reusable=reusable,
            )
            if isinstance(tree_parent, CraftTree):
                tree_parent.nodes.append(node)
            else:
                tree_parent.children.append(node)

        if expand:
            sub, sub_craft = resolve_tree(
                comp_un,
                total,
                items_by_un,
                owned,
                recipes_by_result,
                loc_dict,
                depth + 1,
                max_depth,
                item_name,
                tree_parent=node,
                pending=pending,
            )
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
            )
            _merge_sub_into(result, sub_req)
            _merge_sub_into(craftables, sub_craft)

    return result, craftables


# ---------------------------------------------------------------------------
# Blueprint / name helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Post-processing pipeline
# ---------------------------------------------------------------------------
# After ``resolve_tree`` produces (requirements, craftables), the
# post-processing steps refine the output:
#
# 1. compute_crafting_plan — adjusts num_crafts based on owned quantities.
# 2. decompose_raw_materials — replaces craftable intermediates with their
#    raw ingredients, and adds blueprint entries for each craftable.
# 3. preserve_blueprints — ensures blueprint requirements that were replaced
#    by raw materials are still shown.
#


def decompose_raw_materials(
    craftables: dict,
    recipes_by_result: dict,
    items_by_un: dict,
    owned: dict,
    loc_dict: dict,
) -> dict:
    """Decompose corrected *craftables* into raw material requirements.

    Blueprint entries for each craftable are also added, including owned ones,
    so the summary shows the complete build.  Returns a
    ``{un_lower: {name, quantity, owned}}`` dict of non-craftable ingredients.
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
            if bp_lower not in requirements:
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


def add_blueprint_requirement(
    item_un: str,
    item_name: str,
    requirements: dict,
    owned: dict,
    recipes_by_result: dict,
) -> dict:
    """Add the item's own blueprint to *requirements*, including when owned.

    The crafting summary should always list the blueprint needed to build the
    requested item, even though the decomposition step only adds blueprints for
    intermediate craftables.  Shared by the CLI and web views so both report
    the same requirements.
    """
    recipe = recipes_by_result.get(item_un.lower())
    if not recipe:
        return requirements
    bp_key = recipe.get("_recipeKey", "")
    if not bp_key:
        return requirements
    bp_lower = bp_key.lower()
    if bp_lower in requirements:
        return requirements
    requirements[bp_lower] = {
        "name": f"{item_name} Blueprint",
        "quantity": 1,
        "owned": owned.get(normalize_path(bp_key), 0),
    }
    return requirements


def postprocess_craft_plan(
    requirements: dict,
    craftables: dict,
    owned: dict,
    items_by_un: dict,
    recipes_by_result: dict,
    loc_dict: dict,
) -> dict:
    """Apply the standard craft post-processing pipeline.

    This is the single source of truth shared by the CLI and the web view.
    It computes the corrected crafting plan, decomposes intermediate
    craftables into raw materials, preserves needed blueprints, and keeps
    non-blueprint requirements that the decomposition did not cover.

    *requirements* and *craftables* come from :func:`resolve_tree`;
    *craftables* is mutated in place.  Returns the final requirements dict.
    """
    compute_crafting_plan(craftables)
    processed = decompose_raw_materials(craftables, recipes_by_result, items_by_un, owned, loc_dict)
    processed = preserve_blueprints(requirements, processed)
    for key, value in requirements.items():
        if key not in processed and not is_blueprint_un(key):
            processed[key] = value
    return processed


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
