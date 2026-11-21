"""Report formatting — renders :class:`~warframe_profile.analysis.AnalysisResult`
as human-readable tables using ``prettytable``.

Output modes:

* :func:`print_report` — full analysis with per-item breakdown, missing
  primes, excess parts, and a ducat sell summary.
* :func:`print_safe_relics` — relics whose every reward is already owned.
* :func:`print_needed_drops` — needed prime parts + owned relic sources.
* :func:`section_sellable_equipment` — market-rebuyable equipment table.
* :func:`section_excess_blueprints_components` — excess BPs/components.
* :func:`section_items_with_owned_prime` — items whose Prime variant owned.
* :func:`print_craft_tree` — recursive colour-coded crafting tree.
* :func:`print_craft_summary` — aggregated requirements and farming list.

This module is the **terminal view** in the MVP pattern.  Web rendering
lives in :mod:`warframe_profile.view.web.templates`.
"""

from typing import Literal

from prettytable import PrettyTable, TableStyle

from warframe_profile.compat import color_enabled, enable_windows_vt
from warframe_profile.model.analysis import (
    AnalysisResult,
    ExcessItem,
    NeededPart,
    RelicInfo,
    SellableEquipment,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _new_table(
    columns: list[tuple[str, Literal["l", "c", "r"]]],
    title: str | None = None,
) -> PrettyTable:
    """Create a :class:`PrettyTable` from ``(column, alignment)`` pairs.

    Every report table is built this way: field names, ``DEFAULT`` style and
    per-column alignment in one call.
    """
    t = PrettyTable(title=title)
    t.set_style(TableStyle.DEFAULT)
    t.field_names = [name for name, _ in columns]
    for name, align in columns:
        t.align[name] = align
    return t


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
# Each function in this section maps to a CLI sub-command or a sub-section
# of the output.  They read model dataclasses and write to stdout.


def _filter_relevant_items(items: list) -> tuple[list, int]:
    """Filter out fully-built items with no surplus; return (relevant, n_silent)."""
    relevant = []
    n_silent = 0
    for i in items:
        if i.status == "crafting":
            continue
        has_surplus = any(p.surplus > 0 and is_prime_part(p.name) for p in i.parts)
        has_missing = any(p.missing > 0 and is_prime_part(p.name) for p in i.parts)
        has_ducats = sum(p.surplus * p.ducats for p in i.parts if is_prime_part(p.name)) > 0
        if i.status == "have_copy" and i.extra_builds == 0 and not has_surplus:
            n_silent += 1
            continue
        if not has_missing and not has_surplus and not has_ducats:
            n_silent += 1
            continue
        relevant.append(i)
    relevant.sort(key=lambda i: i.name)
    return relevant, n_silent


def _print_missing_primes_section(missing) -> None:
    """Print the 'Missing Prime Items' table."""
    if not missing:
        return
    t = _new_table(
        [("Name", "l"), ("Category", "l"), ("MR", "r")],
        title="Missing Prime Items (no parts owned)",
    )
    for m in sorted(missing, key=lambda x: (x.category, x.name)):
        t.add_row([m.name, m.category, m.mastery_req or ""])
    print(t)
    print()


def _print_summary_line(items, total_safe, total_keep) -> None:
    """Print the one-line owned/buildable/partial/ducats summary."""
    n_build = sum(1 for i in items if i.status == "buildable")
    n_craft = sum(1 for i in items if i.status == "crafting")
    n_partial = sum(1 for i in items if i.status == "partial")
    n_have = sum(1 for i in items if i.status == "have_copy")
    n_extra = sum(i.extra_builds for i in items)
    parts_label = []
    if n_have:
        lbl = f"{n_have} owned"
        if n_extra:
            lbl += f" ({n_extra} extra needed)"
        parts_label.append(lbl)
    if n_build:
        parts_label.append(f"{n_build} buildable")
    if n_craft:
        parts_label.append(f"{n_craft} crafting")
    if n_partial:
        parts_label.append(f"{n_partial} partial")
    print(f"  {', '.join(parts_label)}  |  {total_safe:,} ducats safe  {total_keep:,} ducats keep")


def _print_buildable_section(items) -> None:
    """Print the 'Ready to Build' table."""
    buildable = sorted(
        (i for i in items if i.status == "buildable"),
        key=lambda i: (i.category, i.name),
    )
    if not buildable:
        return

    t = _new_table(
        [("Name", "l"), ("Category", "l"), ("MR", "r")],
        title="Ready to Build",
    )
    for i in buildable:
        t.add_row([i.name, i.category, i.mastery_req or ""])
    print(t)
    print()


def _print_excess_parts_section(relevant, n_silent) -> None:
    """Print the 'Excess Prime Parts' table."""
    if not relevant:
        return
    print()
    t = _new_table(
        [
            ("Item", "l"),
            ("Copies Needed", "r"),
            ("Parts Missing", "l"),
            ("Excess Parts", "l"),
            ("Ducats", "r"),
        ],
        title="Excess Prime Parts",
    )

    for i in relevant:
        nb = i.needed_builds
        missing_parts = [p for p in i.parts if p.missing > 0 and is_prime_part(p.name)]
        missing_str = ", ".join(f"{p.name} x{p.missing}" for p in missing_parts) or "\u2014"
        surplus_parts = [p for p in i.parts if p.surplus > 0 and is_prime_part(p.name)]
        excess_str = ", ".join(f"{p.name} x{p.surplus}" for p in surplus_parts) or "\u2014"
        excess_duc = sum(p.surplus * p.ducats for p in surplus_parts)
        t.add_row([i.name, nb, missing_str, excess_str, excess_duc if excess_duc else ""])

    print(t)
    if n_silent:
        print(f"  \u2026 {n_silent} more owned \u2014 fully built, no spare parts")


def part_label(item_name: str, part_name: str) -> str:
    """Show a part without duplicating the parent item name.

    Part names already include the parent (e.g. ``'Acceltra Prime
    Receiver'``), so prepend the item name only for a bare part type
    (e.g. ``'Receiver'``).
    """
    if part_name == item_name or part_name.startswith(item_name + " "):
        return part_name
    return f"{item_name} {part_name}"


def is_prime_part(name: str) -> bool:
    """Whether *name* is an item-specific Prime part (not a shared resource).

    Prime parts carry the item's name (``'Odonata Prime Harness'``); shared
    crafting resources (``'Orokin Cell'``, ``'Nitain Extract'``) do not.
    """
    return "Prime" in name


def _print_sell_ducats_section(data: AnalysisResult) -> None:
    """Print the 'Sell for Ducats' table."""
    sell = data.surplus_parts()
    if not sell:
        return
    print()
    t = _new_table(
        [("Part", "l"), ("Qty", "r"), ("Duc", "r"), ("Total", "r")],
        title="Sell for Ducats",
    )

    sorted_items = sorted(sell.items(), key=lambda x: (x[0][0], x[0][1]))
    for idx, ((_item_name, _part_name), info) in enumerate(sorted_items):
        t.add_row(
            [
                part_label(_item_name, _part_name),
                f"x{info['count']}",
                info["ducats"],
                info["count"] * info["ducats"],
            ],
            divider=idx == len(sorted_items) - 1,
        )
    t.add_row(["TOTAL", "", "", data.total_ducats_safe])
    print(t)


def print_report(data: AnalysisResult) -> None:
    """Print the full analysis report with all sections."""
    relevant, n_silent = _filter_relevant_items(data.items)

    print("=" * 60)
    print("  WARFRAME PRIME ANALYSIS")
    print("=" * 60)

    _print_missing_primes_section(data.missing)
    _print_summary_line(data.items, data.total_ducats_safe, data.total_ducats_keep)
    _print_buildable_section(data.items)
    _print_excess_parts_section(relevant, n_silent)
    _print_sell_ducats_section(data)

    print()
    print("=" * 60)
    if data.total_ducats_safe > 0:
        print(f"  Sell for {data.total_ducats_safe:,} ducats at Baro Ki'Teer.")
    print("=" * 60)


def print_safe_relics(relics: list[RelicInfo]) -> None:
    """Print a table of relics that are safe to spam for duplicate parts.

    Only relics whose every prime-part reward is already owned are shown.
    """
    safe = [r for r in relics if r.safe and r.count > 0]
    if not safe:
        return

    print()
    t = _new_table(
        [("Relic", "l"), ("Tier", "l"), ("Count", "r"), ("Vaulted", "l")],
        title="Safe-to-Spam Relics (all rewards already owned)",
    )

    for r in safe:
        t.add_row([r.name, r.tier, r.count, "Yes" if r.vaulted else "No"])

    print(t)
    print(
        f"  {len(safe)} relic{'s' if len(safe) != 1 else ''} safe to spam "
        f"for duplicate prime parts."
    )


def print_needed_drops(needed: list[NeededPart]) -> None:
    """Print a table of needed prime parts and which owned relics drop them.

    Args:
        needed: Output of :func:`~warframe_profile.analysis.build_needed_drops`.
    """
    if not needed:
        print("\n  No missing parts \u2014 you have everything!")
        return

    print()
    t = _new_table(
        [
            ("Part", "l"),
            ("Item", "l"),
            ("Need", "r"),
            ("Duc", "r"),
            ("Owned Relic Drops", "l"),
        ],
        title="Needed Prime Parts & Relic Drops",
    )

    for n in needed:
        need_str = f"x{n.missing}"
        duc_str = str(n.ducats) if n.ducats else ""

        if n.drops:
            # Group drops by relic name, show tiers
            drops_str = ", ".join(f"{name} x{count}" for name, count, _, _ in n.drops)
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
    t = _new_table(
        [
            ("Name", "l"),
            ("Category", "l"),
            ("Buy Type", "l"),
            ("Rarest Material", "l"),
        ],
        title="Market Re-Buyable Equipment (safe to sell)",
    )
    for s in sellable:
        rc = s.rare_component or "\u2014"
        t.add_row([s.name, s.category, s.buy_type, rc])
    print(t)
    print()


def section_excess_blueprints_components(excess: list[ExcessItem]) -> None:
    """Print the excess blueprints and components table."""
    if not excess:
        return
    t = _new_table(
        [
            ("Name", "l"),
            ("Type", "l"),
            ("Owned", "r"),
            ("Sell", "r"),
            ("Crafts Into", "l"),
        ],
        title="Excess Blueprints & Components (safe to sell)",
    )
    for e in excess:
        t.add_row([e.name, e.item_type, e.owned, e.sell, e.builds])
    print(t)
    print()


def section_items_with_owned_prime(pairs: list[tuple[str, str, str]]) -> None:
    """Print the "Items to Sell (Own Prime Variant)" table."""
    rows = [
        {"name": name, "category": category, "prime_name": prime_name}
        for name, category, prime_name in pairs
    ]
    if not rows:
        return

    rows.sort(key=lambda r: (r["category"], r["name"]))

    t = _new_table(
        [("Item", "l"), ("Category", "l"), ("Prime Variant Owned", "l")],
        title="Items to Sell (Own Prime Variant)",
    )
    for row in rows:
        t.add_row([row["name"], row["category"], row["prime_name"]])
    print(t)
    print()


# ---------------------------------------------------------------------------
# Craft-tree view
# ---------------------------------------------------------------------------

# ANSI colour is emitted only for an interactive terminal (or FORCE_COLOR).
# ``enable_windows_vt`` makes classic Windows consoles understand the codes;
# otherwise they would print literal escape sequences.
enable_windows_vt()
_COLOR = color_enabled()
GREEN = "\033[92m" if _COLOR else ""
RED = "\033[91m" if _COLOR else ""
YELLOW = "\033[93m" if _COLOR else ""
RESET = "\033[0m" if _COLOR else ""


def craft_color_for_owned(owned_qty: int, needed: int) -> str:
    """Return ANSI colour code based on owned vs needed ratio."""
    if owned_qty >= needed:
        return GREEN
    elif owned_qty > 0:
        return YELLOW
    return RED


def _print_node(node, prefix: str, is_last: bool) -> None:
    """Print one display-ready craft node and recurse into its children."""
    connector = "\u2514\u2500 " if is_last else "\u251c\u2500 "
    child_prefix = prefix + ("    " if is_last else "\u2502   ")
    label = f"{node.name} x{node.quantity}"
    if node.reusable:
        label += " (reusable)"
    color = craft_color_for_owned(node.owned, node.quantity)
    print(f"{prefix}{connector}{color}{label}{RESET}")
    for index, child in enumerate(node.children):
        _print_node(child, child_prefix, index == len(node.children) - 1)


def print_craft_tree(tree) -> None:
    """Print a display-ready :class:`CraftTree` to stdout."""
    print(f"{RED}\u25a0 {tree.name}{RESET}")
    for index, node in enumerate(tree.nodes):
        _print_node(node, "", index == len(tree.nodes) - 1)


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
        ct = _new_table([("Item", "l"), ("Needed", "r"), ("Owned", "r"), ("Crafts", "r")])
        for c in craft_list:
            ct.add_row([c["name"], c["quantity"], c["owned"], c["num_crafts"]])
        print(ct)
        print()

    print("\u2500\u2500 Raw Materials \u2500\u2500")
    t = _new_table([("Material", "l"), ("Needed", "r"), ("Owned", "r"), ("Status", "r")])
    for info in sorted(requirements.values(), key=lambda x: x["name"]):
        missing = max(0, info["quantity"] - info["owned"])
        status = f"FARM {missing}" if missing > 0 else "\u2713"
        t.add_row([info["name"], info["quantity"], info["owned"], status])
    print(t)

    if farm_items:
        print()
        print("\u2500\u2500 Farming List \u2500\u2500")
        ft = _new_table([("Material", "l"), ("Needed", "r"), ("Owned", "r"), ("To Farm", "r")])
        for fi in farm_items:
            ft.add_row([fi["name"], fi["needed"], fi["owned"], fi["missing"]])
        print(ft)
    else:
        print("\n  \u2713 You have all the materials needed!")
