"""Central data-access objects: :class:`ItemData` and :class:`Inventory`.

Every part of the application reads game and player data through these two
classes:

* :class:`ItemData` owns the merged item/recipe/locale database and every
  item-derived index (name lookup, Prime map, crafting trees, …).
* :class:`Inventory` owns the raw player inventory and every inventory-derived
  concept — owned counts, foundry results, mastered items, XP, modular parts,
  owned mods, owned relics, and extra sub-craft builds.

Keeping ownership logic here guarantees one definition of “owned”: an item is
owned when it is in the inventory, already finished/mastered, **or currently
being crafted in the foundry**.  Callers that specifically need a finished
copy use :meth:`Inventory.is_finished`; callers that ask “do I still need this?”
use :meth:`Inventory.is_owned`.
"""

from __future__ import annotations

from collections import defaultdict
from functools import cache, cached_property
from typing import TYPE_CHECKING

from warframe_profile.model.craft_model import (
    CraftPlan,
    CraftTree,
    add_blueprint_requirement,
    build_items_by_un,
    build_lookup,
    build_recipes_by_result,
    display_name,
    find_items,
    get_recipe_components,
    has_recipe,
    postprocess_craft_plan,
    resolve_name,
    resolve_tree,
    un_to_name,
    weapon_name,
)
from warframe_profile.model.inventory import (
    EQUIPMENT_SECTIONS,
    ExportDB,
)
from warframe_profile.model.utils import normalize_path

if TYPE_CHECKING:
    from warframe_profile.model.analysis import (
        AnalysisResult,
        ExcessItem,
        NeededPart,
        RelicInfo,
        SellableEquipment,
    )
    from warframe_profile.model.mastery import MasteryReport
    from warframe_profile.model.mods import ModsReport

#: Inventory sections that hold built items rather than loose components.
#: Modular vehicles/pets keep their masterable path in ``ModularParts`` or in
#: their own section, so components in ``MiscItems`` are not assembled items.
OWNED_SECTIONS: tuple[str, ...] = (
    *EQUIPMENT_SECTIONS,
    "Hoverboards",
    "KubrowPets",
    "MoaPets",
    "OperatorAmps",
    "OperatorSuits",
    "Motorcycles",
    "DrifterMelee",
)

#: Inventory sections that can contain modular or pet items (gilding checks).
MODULAR_SECTIONS: tuple[str, ...] = (
    "OperatorAmps",
    "Primary",
    "Secondary",
    "Melee",
    "MoaPets",
    "KubrowPets",
)


def _compute_pending(raw: dict, item_data: ItemData) -> dict[str, int]:
    """Foundry results keyed by normalised result path."""
    pending: dict[str, int] = defaultdict(int)
    for entry in raw.get("PendingRecipes", []):
        recipe_key = entry.get("ItemType", "")
        if not recipe_key:
            continue
        result_type = item_data.result_for_recipe(recipe_key)
        if result_type:
            pending[normalize_path(result_type)] += 1
            continue
        # Fallback for stale exports: strip the Blueprint suffix.
        if recipe_key.endswith("Blueprint"):
            stripped = recipe_key[: -len("Blueprint")]
            if stripped:
                pending[normalize_path(stripped)] += 1
    return dict(pending)


def _compute_owned(raw: dict, pending: dict[str, int]) -> dict[str, int]:
    """Every owned copy, including foundry results and adjusted blueprints."""
    owned: dict[str, int] = defaultdict(int)
    for item in raw.get("MiscItems", []):
        owned[normalize_path(item.get("ItemType", ""))] += item.get("ItemCount", 1)
    for blueprint in raw.get("Recipes", []):
        owned[normalize_path(blueprint.get("ItemType", ""))] += blueprint.get("ItemCount", 1)
    for section in EQUIPMENT_SECTIONS:
        for equipment in raw.get(section, []):
            owned[normalize_path(equipment.get("ItemType", ""))] += 1

    # A pending recipe has consumed its blueprint even though DE keeps listing
    # it under ``Recipes`` until the foundry item is claimed.
    for entry in raw.get("PendingRecipes", []):
        blueprint = normalize_path(entry.get("ItemType", ""))
        if blueprint and owned.get(blueprint, 0) > 0:
            owned[blueprint] -= 1

    for path, count in pending.items():
        owned[path] += count
    return dict(owned)


class ItemData:
    """Read-only access to the merged item database and derived indices."""

    def __init__(self, db: ExportDB) -> None:
        """Wrap an already-loaded :class:`ExportDB`."""
        self._db = db

    @classmethod
    def from_path(cls, path: str, refresh: bool = False) -> ItemData:
        """Load the item database from *path* and wrap it."""
        db = ExportDB(path)
        db.load(refresh=refresh)
        return cls(db)

    # ------------------------------------------------------------------
    # Raw database views
    # ------------------------------------------------------------------

    @property
    def items(self) -> list[dict]:
        """The merged item list."""
        return self._db.items

    @property
    def recipes(self) -> dict:
        """Raw recipes keyed by recipe uniqueName."""
        return self._db.recipes

    @property
    def locale(self) -> dict:
        """Localisation dictionary (``/Lotus/Language/...`` keys)."""
        return self._db.locale

    @property
    def _raw(self) -> dict:
        """The full decoded export database (model-internal)."""
        return self._db.raw

    # ------------------------------------------------------------------
    # Indexed lookups
    # ------------------------------------------------------------------

    @cached_property
    def items_by_un(self) -> dict[str, dict]:
        """``uniqueName.lower() -> item`` crafting index."""
        return build_items_by_un(self._raw)

    @cached_property
    def recipes_by_result(self) -> dict[str, dict]:
        """``resultType.lower() -> recipe`` crafting index."""
        return build_recipes_by_result(self._raw)

    @cached_property
    def by_name(self) -> dict[str, list[dict]]:
        """``name.lower() -> [items]`` search index."""
        return build_lookup(self.items_by_un, self.locale)

    @cached_property
    def item_index(self) -> dict[str, dict]:
        """``normalised uniqueName -> item`` lookup over the full item list."""
        return {
            normalize_path(item["uniqueName"]): item
            for item in self.items
            if item.get("uniqueName")
        }

    @cached_property
    def prime_map(self) -> dict[str, dict]:
        """Prime item index (parts, ducats, categories, cosmetics)."""
        # Imported lazily: analysis imports this module for the classes.
        from warframe_profile.model.analysis import build_prime_map

        return build_prime_map(self.items)

    @cached_property
    def component_blueprint_map(self) -> dict[str, tuple[str, int]]:
        """Prime component uniqueName → (blueprint uniqueName, ducats)."""
        from warframe_profile.model.analysis import build_component_blueprint_map

        return build_component_blueprint_map(self.recipes, self.prime_map)

    @cached_property
    def ingredient_to_results(self) -> dict[str, set[str]]:
        """Recipe ingredient uniqueName → set of result uniqueNames."""
        from warframe_profile.model.cleanup import build_ingredient_to_results

        return build_ingredient_to_results(self.recipes)

    @cached_property
    def regular_to_prime(self) -> dict[str, dict]:
        """Non-Prime weapon uniqueName → its Prime counterpart."""
        from warframe_profile.model.analysis import build_regular_to_prime_map

        return build_regular_to_prime_map(self.items)

    # ------------------------------------------------------------------
    # Item helpers
    # ------------------------------------------------------------------

    def item(self, un: str) -> dict | None:
        """Return the crafting-index item for *un*, if present."""
        if not un:
            return None
        return self.items_by_un.get(un.lower())

    def recipe(self, recipe_key: str) -> dict | None:
        """Return the raw recipe stored under *recipe_key*."""
        return self.recipes.get(recipe_key)

    def recipe_for(self, un: str) -> dict | None:
        """Return the recipe that produces *un*, if any."""
        if not un:
            return None
        return self.recipes_by_result.get(un.lower())

    def result_for_recipe(self, recipe_key: str) -> str:
        """Resolve a recipe key to the uniqueName of the item it produces."""
        recipe = self.recipes.get(recipe_key) or {}
        return recipe.get("resultType", "") or ""

    def name(self, value: str) -> str:
        """Resolve a localisation key (or return the plain name unchanged)."""
        return resolve_name(value, self.locale)

    def item_name(self, un: str) -> str:
        """Return a display name for *un*, falling back to its last path segment."""
        item = self.item(un)
        if item:
            return self.name(item.get("name", "")) or un_to_name(un)
        return un_to_name(un)

    def find(self, query: str) -> list[dict]:
        """Search items by name using the standard relevance ordering."""
        return find_items(query, self.by_name)

    @cache
    def components(self, un: str) -> tuple[dict, ...]:
        """Return the recipe ingredients for *un* with resolved names.

        The recipe components are shared by the display-tree resolver and the
        requirement resolver, so parsing them once avoids duplicate work on
        the Craft and Weapon Chains pages.
        """
        return tuple(
            get_recipe_components(un.lower(), self.items_by_un, self.recipes_by_result, self.locale)
        )

    def has_recipe(self, un: str) -> bool:
        """Whether *un* has a craftable recipe."""
        return has_recipe(un.lower(), self.recipes_by_result)

    def should_expand(
        self,
        un: str,
        depth: int,
        max_depth: int,
        is_blueprint: bool,
    ) -> bool:
        """Whether a crafting-tree component should be expanded further."""
        from warframe_profile.model.craft_model import should_expand

        return should_expand(un, self.recipes_by_result, depth, max_depth, is_blueprint)

    def display_name(self, component: dict, parent_name: str | None) -> str:
        """Return the best display name for a recipe component."""
        return display_name(component, self.items_by_un, parent_name, self.locale)

    def weapon_name(self, un: str) -> str:
        """Return a weapon display name without its ``<ARCHWING>`` prefix."""
        return weapon_name(un, self.items_by_un, self.locale)

    # ------------------------------------------------------------------
    # Crafting plans
    # ------------------------------------------------------------------

    def craft_tree(
        self,
        un: str,
        quantity: int,
        inventory: Inventory,
        max_depth: int = 2,
    ) -> CraftTree:
        """Build a display-ready crafting tree for *un*."""
        tree = CraftTree(name=self.item_name(un))
        self.resolve_tree(un, quantity, inventory, max_depth=max_depth, tree_parent=tree)
        return tree

    def craft_plan(
        self,
        un: str,
        inventory: Inventory,
        quantity: int = 1,
        max_depth: int = 2,
    ) -> CraftPlan:
        """Build a display-ready crafting plan (tree + requirements) for *un*."""
        item = self.item(un)
        name = self.item_name(un)
        tree = CraftTree(name=name)
        requirements, craftables = self.resolve_tree(
            un,
            quantity,
            inventory,
            max_depth=max_depth,
            tree_parent=tree,
        )
        self.add_blueprint(un, name, requirements, inventory)
        requirements = self.finish_plan(requirements, craftables, inventory)
        return CraftPlan(
            name=name,
            category=item.get("category", "") if item else "",
            tree=tree,
            requirements=requirements,
            craftables=craftables,
        )

    def resolve_tree(
        self,
        un: str,
        quantity: int,
        inventory: Inventory,
        max_depth: int = 2,
        tree_parent=None,
    ) -> tuple[dict, dict]:
        """Resolve raw requirements and optionally collect a display tree."""
        return resolve_tree(
            un,
            quantity,
            self.items_by_un,
            inventory.owned,
            self.recipes_by_result,
            self.locale,
            max_depth=max_depth,
            tree_parent=tree_parent,
            pending=inventory.pending,
        )

    def add_blueprint(
        self,
        un: str,
        name: str,
        requirements: dict,
        inventory: Inventory,
    ) -> None:
        """Add the item's own blueprint requirement to *requirements* in place."""
        add_blueprint_requirement(
            un,
            name,
            requirements,
            inventory.owned,
            self.recipes_by_result,
        )

    def finish_plan(
        self,
        requirements: dict,
        craftables: dict,
        inventory: Inventory,
    ) -> dict:
        """Decompose intermediates and preserve blueprint entries."""
        return postprocess_craft_plan(
            requirements,
            craftables,
            inventory.owned,
            self.items_by_un,
            self.recipes_by_result,
            self.locale,
        )


class Inventory:
    """Player inventory plus every inventory-derived ownership concept."""

    def __init__(
        self,
        raw: dict,
        item_data: ItemData,
        mastered: set[str] | None = None,
    ) -> None:
        """Wrap *raw* inventory data and link it to *item_data*."""
        self._raw = raw
        self.item_data = item_data
        self._mastered_override = set(mastered) if mastered is not None else None

        #: Foundry results keyed by normalised item path.
        self.pending: dict[str, int] = _compute_pending(raw, item_data)
        #: Every owned item/copy, including pending foundry results and with
        #: blueprints consumed by pending recipes subtracted.
        self.owned: dict[str, int] = _compute_owned(raw, self.pending)

    def rebind(self, item_data: ItemData) -> Inventory:
        """Return a new snapshot using the same raw inventory and new items."""
        return Inventory(self._raw, item_data, mastered=self._mastered_override)

    # ------------------------------------------------------------------
    # Ownership primitives (the single source of truth)
    # ------------------------------------------------------------------

    @cached_property
    def finished(self) -> set[str]:
        """Built equipment plus mastered items (excludes foundry results)."""
        result = {
            normalize_path(entry.get("ItemType", ""))
            for section in EQUIPMENT_SECTIONS
            for entry in self._raw.get(section, [])
        }
        result |= self.mastered
        return result

    def count(self, un: str) -> int:
        """Owned copies of *un*, including items in the foundry."""
        return self.owned.get(normalize_path(un), 0)

    def pending_count(self, un: str) -> int:
        """Copies of *un* currently being crafted."""
        return self.pending.get(normalize_path(un), 0)

    def is_finished(self, un: str) -> bool:
        """Whether a finished copy of *un* exists (owned or mastered)."""
        return normalize_path(un) in self.finished

    def is_crafting(self, un: str) -> bool:
        """Whether *un* is currently in the foundry."""
        return self.pending_count(un) > 0

    def is_owned(self, un: str) -> bool:
        """Whether *un* is owned — in inventory **or** being crafted."""
        norm = normalize_path(un)
        return norm in self.finished or self.owned.get(norm, 0) > 0

    # ------------------------------------------------------------------
    # Mastery / progression data
    # ------------------------------------------------------------------

    @cached_property
    def xp_by_path(self) -> dict[str, int]:
        """``normalised item path -> XP`` (XPInfo wins over inventory copies)."""
        xp: dict[str, int] = {}
        for entry in self._raw.get("XPInfo") or []:
            path = normalize_path(entry.get("ItemType") or "")
            if path:
                xp.setdefault(path, entry.get("XP") or 0)

        def _walk(obj) -> None:
            if isinstance(obj, dict):
                path = normalize_path(obj.get("ItemType") or "")
                value = obj.get("XP")
                if path and value and path not in xp:
                    xp[path] = value
                for child in obj.values():
                    _walk(child)
            elif isinstance(obj, list):
                for child in obj:
                    _walk(child)

        _walk(self._raw)
        return xp

    @cached_property
    def _derived_mastered(self) -> set[str]:
        """Mastered paths derived from the XP index."""
        return set(self.xp_by_path)

    @property
    def mastered(self) -> set[str]:
        """Every item path the player has ever ranked up."""
        if self._mastered_override is not None:
            return self._mastered_override
        return self._derived_mastered

    @cached_property
    def modular_features(self) -> dict[str, int | None]:
        """``component path -> Features bitmask`` for gilding checks."""
        features: dict[str, int | None] = {}
        for section in MODULAR_SECTIONS:
            for item in self._raw.get(section) or []:
                if not isinstance(item, dict):
                    continue
                parts = item.get("ModularParts") or []
                for part in parts:
                    features.setdefault(part.lower(), item.get("Features"))
                if parts:
                    item_type = (item.get("ItemType") or "").lower()
                    if item_type:
                        features.setdefault(item_type, item.get("Features"))
        return features

    @cached_property
    def masterable_owned(self) -> set[str]:
        """Assembled/equipment paths (excluding loose ``MiscItems`` components)."""
        owned: set[str] = set()
        for section in OWNED_SECTIONS:
            for entry in self._raw.get(section, []):
                un = entry.get("ItemType", "")
                if un:
                    owned.add(normalize_path(un))
        owned.update(self.pending)
        return owned

    @cached_property
    def assembled_modular_parts(self) -> set[str]:
        """Modular component paths referenced by assembled items."""
        parts: set[str] = set()
        for section in OWNED_SECTIONS:
            for entry in self._raw.get(section, []):
                for part in entry.get("ModularParts") or []:
                    parts.add(normalize_path(part))
        return parts

    def owns_masterable(self, item: dict, *, modular_component: bool = False) -> bool:
        """Whether a masterable item is owned (or being crafted).

        Loose modular components only count when an assembled item references
        them in ``ModularParts``; other items use the assembled/equipment set,
        which already includes pending foundry results.
        """
        norm = normalize_path(item.get("uniqueName", ""))
        if modular_component:
            return norm in self.assembled_modular_parts
        return norm in self.masterable_owned

    # ------------------------------------------------------------------
    # Raw inventory sections (exposed through the class, never via dicts)
    # ------------------------------------------------------------------

    @property
    def blueprints(self) -> list[dict]:
        """Blueprint entries currently in the inventory."""
        return self._raw.get("Recipes", [])

    @property
    def components(self) -> list[dict]:
        """Loose component/resource entries in the inventory."""
        return self._raw.get("MiscItems", [])

    def equipment_entries(self, sections: list[str] | None = None):
        """Yield equipment entries from the requested (or default) sections."""
        for section in sections or EQUIPMENT_SECTIONS:
            yield from self._raw.get(section, [])

    # ------------------------------------------------------------------
    # Mods / relics / sub-crafts
    # ------------------------------------------------------------------

    def owned_mods(self, game_mod_paths: set[str] | None = None) -> dict[str, int]:
        """Owned mod counts, optionally restricted to known game mods."""
        owned: dict[str, int] = defaultdict(int)
        for item in self._raw.get("RawUpgrades", []):
            path = normalize_path(item.get("ItemType", ""))
            if not path or (game_mod_paths is not None and path not in game_mod_paths):
                continue
            owned[path] += item.get("ItemCount", 1)
        for section in ("Upgrades", "FocusUpgrades"):
            for item in self._raw.get(section, []):
                path = normalize_path(item.get("ItemType", ""))
                if not path or (game_mod_paths is not None and path not in game_mod_paths):
                    continue
                owned[path] += 1
        return dict(owned)

    @cached_property
    def owned_relics(self) -> dict[str, int]:
        """``{relic uniqueName: count}`` for owned Intact relics."""
        relics: dict[str, int] = {}
        for item in self.components:
            un = item.get("ItemType", "")
            count = item.get("ItemCount", 1)
            if not un or "/Projections/T" not in un:
                continue
            if "VoidProjectionFeatureItem" in un or "Immortal" in un:
                continue
            relics[un] = relics.get(un, 0) + count
        return relics

    @cached_property
    def required_parts(self) -> list[tuple[dict, dict, int, int]]:
        """Prime part requirements shared by relic and missing-part views."""
        from warframe_profile.model.relics import _iter_required_parts

        return list(_iter_required_parts(self.item_data, self))

    @cached_property
    def not_needed_part_names(self) -> set[str]:
        """Reward names whose required copies are already covered."""
        from warframe_profile.model.relics import _not_needed_part_names

        return _not_needed_part_names(self.required_parts)

    @cached_property
    def extra_builds(self) -> dict[str, int]:
        """Extra copies needed because a Prime item is a sub-craft ingredient."""
        prime_map = self.item_data.prime_map
        buildable_keys = {
            un for un, info in prime_map.items() if info["parts"] and not info["is_cosmetic"]
        }
        component_need: dict[str, int] = {}
        for un, info in prime_map.items():
            if info["is_cosmetic"] or self.is_owned(un):
                # Do not count sub-craft dependencies for items already owned
                # or currently being crafted.
                continue
            for part in info.get("all_parts", info["parts"]):
                if part["uniqueName"] in buildable_keys:
                    component_need[part["uniqueName"]] = (
                        component_need.get(part["uniqueName"], 0) + part["count"]
                    )

        extra: dict[str, int] = {}
        for dependency, total_needed in component_need.items():
            shortfall = max(0, total_needed - self.count(dependency))
            if shortfall > 0:
                extra[dependency] = shortfall
        return extra

    # ------------------------------------------------------------------
    # Equipment helpers used by cleanup views
    # ------------------------------------------------------------------

    def owned_equipment(self, equipment_sections: list[str] | None = None) -> set[str]:
        """UniqueNames of equipment present in the inventory."""
        return {
            entry.get("ItemType", "")
            for entry in self.equipment_entries(equipment_sections)
            if entry.get("ItemType")
        }

    def owned_non_prime_equipment(self) -> set[str]:
        """UniqueNames of owned non-Prime equipment."""
        owned: set[str] = set()
        for un in self.owned_equipment():
            item = self.item_data.item_index.get(normalize_path(un))
            if item is not None and not item.get("isPrime"):
                owned.add(un)
        return owned

    def owned_prime_equipment(self) -> set[str]:
        """UniqueNames of owned Prime equipment or blueprints."""
        owned = {un for un in self.owned_equipment() if un in self.item_data.prime_map}
        for blueprint in self.blueprints:
            un = blueprint.get("ItemType", "")
            if un in self.item_data.prime_map:
                owned.add(un)
        return owned

    @cached_property
    def owned_prime_pairs(self) -> list[tuple[str, str, str]]:
        """Owned regular equipment whose Prime variant is also owned."""
        primes = self.owned_prime_equipment()
        pairs: list[tuple[str, str, str]] = []
        for regular_un in self.owned_non_prime_equipment():
            prime = self.item_data.regular_to_prime.get(regular_un)
            if not prime or prime["prime_un"] not in primes:
                continue
            item = self.item_data.item_index.get(normalize_path(regular_un)) or {}
            pairs.append(
                (
                    item.get("name", regular_un),
                    item.get("category", ""),
                    prime["prime_name"],
                )
            )
        return pairs

    # ------------------------------------------------------------------
    # High-level analyses (lazy, cached per inventory snapshot)
    # ------------------------------------------------------------------

    @cached_property
    def analysis(self) -> AnalysisResult:
        """Prime/ducat analysis for this inventory snapshot."""
        from warframe_profile.model.analysis import analyze

        result = analyze(self.item_data, self)
        # CLI parity: ``print_report`` reads sellable off the analysis result.
        result.sellable = self.sellable_equipment
        return result

    @cached_property
    def sellable_equipment(self) -> list[SellableEquipment]:
        """Market-rebuyable equipment that is safe to sell."""
        from warframe_profile.model.analysis import compute_sellable_equipment

        return compute_sellable_equipment(self.item_data, self)

    @cached_property
    def relic_info(self) -> list[RelicInfo]:
        """Relic safe-to-spam analysis."""
        from warframe_profile.model.analysis import build_relics_map

        return build_relics_map(self.item_data, self)

    @cached_property
    def needed_drops(self) -> list[NeededPart]:
        """Missing Prime parts and the owned relics that drop them."""
        from warframe_profile.model.analysis import build_needed_drops

        return build_needed_drops(self.item_data, self)

    @cached_property
    def excess_items(self) -> list[ExcessItem]:
        """Excess blueprints/components whose crafting paths are covered."""
        from warframe_profile.model.analysis import find_excess_blueprints_and_components

        return find_excess_blueprints_and_components(self.item_data, self)

    @cached_property
    def weapon_chains(self) -> tuple[list[list[str]], dict, dict, int]:
        """Unowned weapon upgrade chains and their aggregated requirements."""
        from warframe_profile.model.weapon_chains import build_weapon_chains

        return build_weapon_chains(self.item_data, self)

    @cached_property
    def mastery_report(self) -> MasteryReport:
        """Mastery progression report."""
        from warframe_profile.model.mastery import build_mastery_report

        return build_mastery_report(self.item_data, self)

    @cached_property
    def mods_report(self) -> ModsReport:
        """Missing-mods report."""
        from warframe_profile.model.mods import build_mods_report

        return build_mods_report(self.item_data, self)
