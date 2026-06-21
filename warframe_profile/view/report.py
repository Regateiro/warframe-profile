"""Report formatting — renders :class:`~warframe_profile.analysis.AnalysisResult`
as human-readable tables using ``prettytable``.

Output modes:

* :func:`print_report` — full analysis with per-item breakdown, missing
  primes, excess parts, and a ducat sell summary.
"""

from collections import defaultdict

from prettytable import PrettyTable, TableStyle

from warframe_profile.model.analysis import (
    AnalysisResult, ItemResult, NeededPart, RelicInfo, SellableEquipment,
    ExcessItem, normalize_path,
)
from warframe_profile.model.craft_model import (
    resolve_name, has_recipe, get_recipe_components,
    _display_name, _is_blueprint_un, _un_to_name, should_expand,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_sell_dict(
    items: list[ItemResult],
) -> dict[tuple[str, str], dict[str, int]]:
    """Aggregate surplus parts into a ``{(item_name, part_name): info}`` dict.

    Only parts with a positive surplus *and* ducat value are included.
    """
    sell: dict[tuple[str, str], dict[str, int]] = \
        defaultdict(lambda: {"count": 0, "ducats": 0})
    for item in items:
        for p in item.parts:
            if p.surplus > 0 and p.ducats > 0:
                key = (item.name, p.name)
                sell[key]["count"] += p.surplus
                sell[key]["ducats"] = p.ducats
    return sell


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def print_report(data: AnalysisResult) -> None:
    """Print the full analysis report with all sections.

    Sections shown (when non-empty):

    1. **Missing Prime Items** — masterable Primes with zero parts owned.
    2. **Summary line** — counts of owned / buildable / partial items.
    3. **Excess Prime Parts** — per-item breakdown of missing and spare parts.
    4. **Sell for Ducats** — aggregated surplus parts table.
    """
    items = data.items
    total_safe = data.total_ducats_safe
    total_keep = data.total_ducats_keep

    # Filter out items that are fully built with no surplus — they are
    # silently counted so the user knows how many items were pruned.
    relevant: list[ItemResult] = []
    n_silent = 0
    for i in items:
        has_surplus = any(p.surplus > 0 for p in i.parts)
        has_missing = any(p.missing > 0 for p in i.parts)
        has_ducats = sum(p.surplus * p.ducats for p in i.parts) > 0
        if i.status == "have_copy" and i.extra_builds == 0 and not has_surplus:
            n_silent += 1
            continue
        if not has_missing and not has_surplus and not has_ducats:
            n_silent += 1
            continue
        relevant.append(i)

    relevant.sort(key=lambda i: i.name)

    # Header.
    print("=" * 60)
    print("  WARFRAME PRIME ANALYSIS")
    print("=" * 60)

    # Missing primes section.
    missing = data.missing
    if missing:
        t = PrettyTable(title="Missing Prime Items (no parts owned)")
        t.field_names = ["Name", "Category", "MR"]
        t.align["Name"] = "l"
        t.align["Category"] = "l"
        t.align["MR"] = "r"
        for m in sorted(missing, key=lambda x: (x.category, x.name)):
            t.add_row([m.name, m.category, m.mastery_req or ""])
        print(t)
        print()

    # Summary counts.
    n_build = sum(1 for i in items if i.status == "buildable")
    n_partial = sum(1 for i in items if i.status == "partial")
    n_have = sum(1 for i in items if i.status == "have_copy")
    n_extra = sum(i.extra_builds for i in items)
    parts_label: list[str] = []
    if n_have:
        lbl = f"{n_have} owned"
        if n_extra:
            lbl += f" ({n_extra} extra needed)"
        parts_label.append(lbl)
    if n_build:
        parts_label.append(f"{n_build} buildable")
    if n_partial:
        parts_label.append(f"{n_partial} partial")
    print(f"  {', '.join(parts_label)}  |  "
          f"{total_safe:,} ducats safe  {total_keep:,} ducats keep")

    # Excess parts table.
    if relevant:
        print()
        t = PrettyTable(title="Excess Prime Parts")
        t.field_names = [
            "Item", "Copies Needed", "Parts Missing",
            "Excess Parts", "Ducats",
        ]
        t.align["Item"] = "l"
        t.align["Copies Needed"] = "r"
        t.align["Parts Missing"] = "l"
        t.align["Excess Parts"] = "l"
        t.align["Ducats"] = "r"

        for i in relevant:
            nb = i.needed_builds
            missing_parts = [p for p in i.parts if p.missing > 0]
            missing_str = (
                ", ".join(f"{p.name} x{p.missing}" for p in missing_parts)
                or "\u2014"
            )
            surplus_parts = [p for p in i.parts if p.surplus > 0]
            excess_str = (
                ", ".join(f"{p.name} x{p.surplus}" for p in surplus_parts)
                or "\u2014"
            )
            excess_duc = sum(p.surplus * p.ducats for p in surplus_parts)
            t.add_row(
                [i.name, nb, missing_str, excess_str,
                 excess_duc if excess_duc else ""],
            )

        print(t)

        if n_silent:
            print(f"  \u2026 {n_silent} more owned \u2014 "
                  "fully built, no spare parts")

    # Sell summary.
    sell = _build_sell_dict(items)
    if sell:
        print()
        t = PrettyTable(title="Sell for Ducats")
        t.field_names = ["Part", "Qty", "Duc", "Total"]
        t.align["Part"] = "l"
        t.align["Qty"] = "r"
        t.align["Duc"] = "r"
        t.align["Total"] = "r"

        sorted_items = sorted(sell.items(), key=lambda x: (x[0][0], x[0][1]))
        for idx, ((_item_name, _part_name), info) in enumerate(sorted_items):
            label = f"{_item_name} {_part_name}"
            t.add_row(
                [label, f"x{info['count']}", info["ducats"],
                 info["count"] * info["ducats"]],
                divider=idx == len(sorted_items) - 1,
            )

        t.add_row(["TOTAL", "", "", total_safe])
        print(t)

    # Footer.
    print()
    print("=" * 60)
    if total_safe > 0:
        print(f"  Sell for {total_safe:,} ducats at Baro Ki'Teer.")
    print("=" * 60)


def print_safe_relics(relics: list[RelicInfo]) -> None:
    """Print a table of relics that are safe to spam for duplicate parts.

    Only relics whose every prime-part reward is already owned are shown.
    """
    safe = [r for r in relics if r.safe and r.count > 0]
    if not safe:
        return

    print()
    t = PrettyTable(title="Safe-to-Spam Relics (all rewards already owned)")
    t.field_names = ["Relic", "Tier", "Count", "Vaulted"]
    t.align["Relic"] = "l"
    t.align["Tier"] = "l"
    t.align["Count"] = "r"
    t.align["Vaulted"] = "l"

    for r in safe:
        t.add_row([r.name, r.tier, r.count, "Yes" if r.vaulted else "No"])

    print(t)
    print(f"  {len(safe)} relic{'s' if len(safe) != 1 else ''} safe to spam "
          f"for duplicate prime parts.")


def print_needed_drops(needed: list[NeededPart]) -> None:
    """Print a table of needed prime parts and which owned relics drop them.

    Args:
        needed: Output of :func:`~warframe_profile.analysis.build_needed_drops`.
    """
    if not needed:
        print("\n  No missing parts \u2014 you have everything!")
        return

    print()
    t = PrettyTable(title="Needed Prime Parts & Relic Drops")
    t.field_names = ["Part", "Item", "Need", "Duc", "Owned Relic Drops"]
    t.align["Part"] = "l"
    t.align["Item"] = "l"
    t.align["Need"] = "r"
    t.align["Duc"] = "r"
    t.align["Owned Relic Drops"] = "l"

    for n in needed:
        need_str = f"x{n.missing}"
        duc_str = str(n.ducats) if n.ducats else ""

        if n.drops:
            # Group drops by relic name, show tiers
            drops_str = ", ".join(
                f"{name} x{count}" for name, count, _ in n.drops
            )
        else:
            drops_str = "\u2014"

        t.add_row([n.part_name, n.item_name, need_str, duc_str, drops_str])

    print(t)
    print(f"  {len(needed)} part{'s' if len(needed) != 1 else ''} still needed.")


# ---------------------------------------------------------------------------
# Cleanup view
# ---------------------------------------------------------------------------

def section_sellable_equipment(sellable: list[SellableEquipment]) -> None:
    """Print the market-re-buyable equipment table."""
    if not sellable:
        return
    t = PrettyTable(
        title="Market Re-Buyable Equipment (safe to sell)",
    )
    t.field_names = ["Name", "Category", "Buy Type", "Rarest Material"]
    t.align["Name"] = "l"
    t.align["Category"] = "l"
    t.align["Buy Type"] = "l"
    t.align["Rarest Material"] = "l"
    for s in sellable:
        rc = s.rare_component or "\u2014"
        t.add_row([s.name, s.category, s.buy_type, rc])
    print(t)
    print()


def section_excess_blueprints_components(excess: list[ExcessItem]) -> None:
    """Print the excess blueprints and components table."""
    if not excess:
        return
    t = PrettyTable(title="Excess Blueprints & Components (safe to sell)")
    t.field_names = ["Name", "Type", "Owned", "Sell", "Crafts Into"]
    t.align["Name"] = "l"
    t.align["Type"] = "l"
    t.align["Owned"] = "r"
    t.align["Sell"] = "r"
    t.align["Crafts Into"] = "l"
    for e in excess:
        t.add_row([e.name, e.item_type, e.owned, e.sell, e.builds])
    print(t)
    print()


def section_items_with_owned_prime(
    owned_regular: set[str],
    reg_to_prime: dict[str, dict],
    owned_primes: set[str],
    items_by_un: dict[str, dict],
) -> None:
    """Print the "Items to Sell (Own Prime Variant)" table."""
    rows: list[dict[str, str]] = []
    for reg_un in owned_regular:
        prime_info = reg_to_prime.get(reg_un)
        if not prime_info:
            continue
        if prime_info["prime_un"] in owned_primes:
            item = items_by_un.get(reg_un, {})
            rows.append({
                "name": item.get("name", reg_un),
                "category": item.get("category", ""),
                "prime_name": prime_info["prime_name"],
            })

    if not rows:
        return

    rows.sort(key=lambda r: (r["category"], r["name"]))

    t = PrettyTable(title="Items to Sell (Own Prime Variant)")
    t.field_names = ["Item", "Category", "Prime Variant Owned"]
    t.align["Item"] = "l"
    t.align["Category"] = "l"
    t.align["Prime Variant Owned"] = "l"
    for r in rows:
        t.add_row([r["name"], r["category"], r["prime_name"]])
    print(t)
    print()


# ---------------------------------------------------------------------------
# Craft-tree view
# ---------------------------------------------------------------------------

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def craft_color_for_owned(owned_qty: int, needed: int) -> str:
    """Return ANSI colour code based on owned vs needed ratio."""
    if owned_qty >= needed:
        return GREEN
    elif owned_qty > 0:
        return YELLOW
    return RED


def print_craft_tree(
    item_un, quantity, items_by_un, owned,
    recipes_by_result, loc_dict,
    depth=0, max_depth=2, prefix="",
    parent_name=None, parent_satisfied=False,
) -> None:
    """Recursively print a colour-coded crafting tree to stdout."""
    import math
    item_un_lower = item_un.lower()
    item = items_by_un.get(item_un_lower)

    recipe_components = (
        get_recipe_components(item_un_lower, items_by_un, recipes_by_result, loc_dict)
        if has_recipe(item_un_lower, recipes_by_result)
        else []
    )
    components = recipe_components

    item_name = (
        resolve_name(item.get("name", ""), loc_dict) if item else _un_to_name(item_un)
    )
    if not item_name:
        item_name = _un_to_name(item_un)

    owned_qty = owned.get(normalize_path(item_un), 0)
    satisfied = owned_qty >= quantity

    if depth == 0:
        satisfied = False
        print(f"{RED}\u25a0 {item_name}{RESET}")

    recipe = recipes_by_result.get(item_un_lower)
    if recipe:
        bp_key = recipe.get("_recipeKey", "")
        if bp_key:
            components = [{
                "uniqueName": bp_key,
                "name": f"{item_name} Blueprint",
                "itemCount": 1,
            }] + components

    if not components or not recipe:
        return

    build_qty = recipe.get("num", 1) or 1
    consumable = recipe.get("consumeOnUse", True)

    num_crafts = max(1, math.ceil(quantity / build_qty))
    if depth > 0:
        remaining_qty = max(0, quantity - owned_qty)
        effective_crafts = max(0, math.ceil(remaining_qty / build_qty)) if remaining_qty > 0 else 0
    else:
        effective_crafts = num_crafts

    consumed: dict[str, int] = defaultdict(int)

    for i, comp in enumerate(components):
        comp_un = comp.get("uniqueName", "")
        comp_un_lower = comp_un.lower()
        comp_count = comp.get("itemCount", 1)
        comp_name = resolve_name(comp.get("name", ""), loc_dict)

        is_last = i == len(components) - 1
        connector = "\u2514\u2500 " if is_last else "\u251c\u2500 "
        child_prefix = prefix + ("    " if is_last else "\u2502   ")

        is_bp = "blueprint" in comp_name.lower() or _is_blueprint_un(comp_un)
        reusable = is_bp and not consumable
        label_mult = 1 if reusable else effective_crafts * comp_count

        comp_display = _display_name(comp, items_by_un, item_name, loc_dict)
        label = f"{comp_display} x{label_mult}"
        if reusable:
            label += " (reusable)"

        comp_owned_qty = (
            owned.get(normalize_path(comp_un), 0) - consumed[comp_un_lower]
        )
        consumed[comp_un_lower] += label_mult

        color = craft_color_for_owned(comp_owned_qty, label_mult)

        expand = (
            should_expand(
                comp_un_lower, recipes_by_result, depth, max_depth, is_bp,
            )
            and not (comp_owned_qty >= label_mult)
        )

        print(f"{prefix}{connector}{color}{label}{RESET}")
        if expand:
            comp_satisfied = False
            print_craft_tree(
                comp_un, label_mult, items_by_un, owned,
                recipes_by_result, loc_dict,
                depth + 1, max_depth, child_prefix,
                item_name, comp_satisfied,
            )


def print_craft_summary(requirements: dict, craftables: dict) -> None:
    """Print aggregated requirement and crafting tables."""
    if not requirements:
        return

    farm_items = [
        {
            "name": info["name"],
            "needed": info["quantity"],
            "owned": info["owned"],
            "missing": max(0, info["quantity"] - info["owned"]),
        }
        for info in sorted(requirements.values(), key=lambda x: x["name"])
        if max(0, info["quantity"] - info["owned"]) > 0
    ]

    craft_list = sorted(craftables.values(), key=lambda x: x["name"])
    craft_list = [c for c in craft_list if c["quantity"] > c["owned"]]
    if craft_list:
        print("\u2500\u2500 Crafting List \u2500\u2500")
        ct = PrettyTable()
        ct.set_style(TableStyle.DEFAULT)
        ct.field_names = ["Item", "Needed", "Owned", "Crafts"]
        ct.align["Item"] = "l"
        ct.align["Needed"] = "r"
        ct.align["Owned"] = "r"
        ct.align["Crafts"] = "r"
        for c in craft_list:
            ct.add_row([c["name"], c["quantity"], c["owned"], c["num_crafts"]])
        print(ct)
        print()

    print("\u2500\u2500 Raw Materials \u2500\u2500")
    t = PrettyTable()
    t.set_style(TableStyle.DEFAULT)
    t.field_names = ["Material", "Needed", "Owned", "Status"]
    t.align["Material"] = "l"
    t.align["Needed"] = "r"
    t.align["Owned"] = "r"
    t.align["Status"] = "r"
    for info in sorted(requirements.values(), key=lambda x: x["name"]):
        missing = max(0, info["quantity"] - info["owned"])
        status = f"FARM {missing}" if missing > 0 else "\u2713"
        t.add_row([info["name"], info["quantity"], info["owned"], status])
    print(t)

    if farm_items:
        print()
        print("\u2500\u2500 Farming List \u2500\u2500")
        ft = PrettyTable()
        ft.set_style(TableStyle.DEFAULT)
        ft.field_names = ["Material", "Needed", "Owned", "To Farm"]
        ft.align["Material"] = "l"
        ft.align["Needed"] = "r"
        ft.align["Owned"] = "r"
        ft.align["To Farm"] = "r"
        for fi in farm_items:
            ft.add_row([fi["name"], fi["needed"], fi["owned"], fi["missing"]])
        print(ft)
    else:
        print("\n  \u2713 You have all the materials needed!")
