"""HTML templates for the web view — renders model data as HTML pages."""

import math
from collections import defaultdict

from warframe_profile.model.craft_model import (
    resolve_name, has_recipe, display_name, is_blueprint_un,
    un_to_name, should_expand, get_recipe_components,
)
from warframe_profile.model.utils import normalize_path


GREEN = "#27ae60"
RED = "#e74c3c"
YELLOW = "#f1c40f"
DIM = "#7f8c8d"


def _page(title: str, content: str, active: str) -> str:
    tabs = [
        ("home", "Home", "/"),
        ("ducats", "Ducats", "/ducats"),
        ("relics", "Relics", "/relics"),
        ("craft", "Craft", "/craft"),
        ("weapon-chains", "Weapon Chains", "/weapon-chains"),
        ("cleanup", "Cleanup", "/cleanup"),
    ]
    tab_links = "".join(
        f'<a href="{url}"{" class=\"active\"" if key == active else ""}>'
        f"{label}</a>"
        for key, label, url in tabs
    )
    refresh_form = '<form method="post" action="/refresh" style="margin-left:auto">' \
                   '<button type="submit" style="padding:4px 12px;font-size:0.85rem">' \
                   'Refresh Inventory</button></form>'
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — Warframe Profile</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
          background: #0d1117; color: #c9d1d9; padding: 20px; }}
  h1 {{ font-size: 1.6rem; margin-bottom: 16px; color: #58a6ff; }}
  h2 {{ font-size: 1.2rem; margin: 20px 0 10px; color: #8b949e; }}
  nav {{ display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 24px;
         border-bottom: 1px solid #30363d; padding-bottom: 8px; align-items: center; }}
  nav a {{ padding: 6px 14px; border-radius: 6px; text-decoration: none;
           color: #8b949e; font-size: 0.9rem; }}
  nav a:hover {{ background: #161b22; color: #c9d1d9; }}
  nav a.active {{ background: #1f6feb; color: #fff; }}
  table {{ width: 100%; max-width: 960px; border-collapse: collapse; margin: 8px 0 16px;
           font-size: 0.85rem; }}
  th, td {{ text-align: left; padding: 6px 10px; border-bottom: 1px solid #21262d; }}
  th {{ background: #161b22; color: #8b949e; font-weight: 600;
        position: sticky; top: 0; }}
  tbody tr:nth-child(odd) {{ background: transparent; }}
  tbody tr:nth-child(even) {{ background: #111920; }}
  tbody tr:hover {{ background: #161b22; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .green {{ color: {GREEN}; }} .red {{ color: {RED}; }} .yellow {{ color: {YELLOW}; }}
  .dim {{ color: {DIM}; }}
  summary {{ cursor: pointer; padding: 4px 0; font-weight: 600; color: #58a6ff; }}
  details {{ margin: 4px 0; }}
  details[open] > summary {{ margin-bottom: 6px; }}
  .tree {{ margin-left: 16px; border-left: 1px solid #30363d; padding-left: 12px; }}
  .tree-item {{ padding: 2px 0; }}
  .tag {{ display: inline-block; padding: 1px 6px; border-radius: 3px;
          font-size: 0.75rem; font-weight: 600; }}
  .tag-green {{ background: {GREEN}22; color: {GREEN}; }}
  .tag-red {{ background: {RED}22; color: {RED}; }}
  .tag-yellow {{ background: {YELLOW}22; color: {YELLOW}; }}
  .tab-bar {{ display: flex; gap: 6px; margin-bottom: 16px; }}
  .tab-bar a {{ padding: 4px 12px; border-radius: 4px; text-decoration: none;
               background: #161b22; color: #8b949e; font-size: 0.85rem; }}
  .tab-bar a.active {{ background: #1f6feb; color: #fff; }}
  input, select, button {{ background: #161b22; border: 1px solid #30363d;
          color: #c9d1d9; padding: 6px 12px; border-radius: 6px; font-size: 0.85rem; }}
  button {{ cursor: pointer; }}
  button:hover {{ background: #1f6feb; }}
  .flash {{ padding: 10px 14px; border-radius: 6px; margin: 12px 0;
           background: #1f6feb22; border: 1px solid #1f6feb; color: #58a6ff; }}
  .flash-error {{ background: {RED}22; border-color: {RED}; color: {RED}; }}
  @media (max-width: 600px) {{ table {{ font-size: 0.75rem; }}
    th, td {{ padding: 4px 6px; }} }}
</style>
</head>
<body>
<h1>Warframe Profile</h1>
<nav>{tab_links}{refresh_form}</nav>
{content}
</body>
</html>"""


# ---------------------------------------------------------------------------
# Subtab navigation helpers
# ---------------------------------------------------------------------------

def _subtab_bar(subtabs: list[tuple[str, str, str]], active: str) -> str:
    links = "".join(
        f'<a href="{url}"{" class=\"active\"" if key == active else ""}>{label}</a>'
        for key, label, url in subtabs
    )
    return f'<div class="tab-bar">{links}</div>'

_DUCATS_SUBTABS = [
    ("missing", "Missing", "/ducats"),
    ("excess", "Excess", "/ducats/excess"),
    ("sell", "Sell", "/ducats/sell"),
]

_RELICS_SUBTABS = [
    ("needed", "Needed Drops", "/relics"),
    ("safe", "Safe-to-Spam", "/relics/safe"),
]

_CLEANUP_SUBTABS = [
    ("rebuy", "Re-Buyable", "/cleanup"),
    ("sell", "Prime Variant", "/cleanup/sell"),
    ("excess", "Excess", "/cleanup/excess"),
]


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------

def render_home() -> str:
    return _page("Home", """
<div class="flash">Select a tab above to explore your Warframe profile.</div>
<ul style="margin-left:20px;line-height:1.8">
  <li><strong>Ducats</strong> — Prime part analysis, missing items, sell recommendations</li>
  <li><strong>Relics</strong> — Needed prime parts and safe-to-spam relics</li>
  <li><strong>Craft</strong> — Look up crafting trees for any item</li>
  <li><strong>Weapon Chains</strong> — Weapon upgrade chains you haven't completed</li>
  <li><strong>Cleanup</strong> — Equipment safe to sell (market-rebuyable or Prime-owned)</li>
</ul>""", "home")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _status_tag(owned: int, needed: int) -> str:
    if owned >= needed:
        return f'<span class="tag tag-green">owned</span>'
    elif owned > 0:
        return f'<span class="tag tag-yellow">partial {owned}/{needed}</span>'
    return f'<span class="tag tag-red">farm {needed - owned}</span>'


def _color(owned: int, needed: int) -> str:
    if owned >= needed:
        return "green"
    elif owned > 0:
        return "yellow"
    return "red"


def _craft_color(owned: int, needed: int) -> str:
    if owned >= needed:
        return "green"
    elif owned > 0:
        return "yellow"
    return "red"


def _tree_html(
    item_un: str, quantity: int, items_by_un: dict, owned: dict,
    recipes_by_result: dict, loc_dict: dict,
    depth: int = 0, max_depth: int = 2, parent_name: str | None = None,
) -> str:
    lines: list[str] = []
    item_un_lower = item_un.lower()
    item = items_by_un.get(item_un_lower)

    recipe_components = (
        get_recipe_components(item_un_lower, items_by_un, recipes_by_result, loc_dict)
        if has_recipe(item_un_lower, recipes_by_result)
        else []
    )
    components = recipe_components

    item_name = (
        resolve_name(item.get("name", ""), loc_dict) if item else un_to_name(item_un)
    )
    if not item_name:
        item_name = un_to_name(item_un)

    owned_qty = owned.get(normalize_path(item_un), 0)
    sat = owned_qty >= quantity

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
        return ""

    build_qty = recipe.get("num", 1) or 1
    consumable = recipe.get("consumeOnUse", True)
    num_crafts = max(1, math.ceil(quantity / build_qty))
    consumed: dict[str, int] = defaultdict(int)

    for i, comp in enumerate(components):
        comp_un = comp.get("uniqueName", "")
        comp_un_lower = comp_un.lower()
        comp_count = comp.get("itemCount", 1)
        comp_name = resolve_name(comp.get("name", ""), loc_dict)
        is_bp = "blueprint" in comp_name.lower() or is_blueprint_un(comp_un)
        reusable = is_bp and not consumable
        label_mult = 1 if reusable else num_crafts * comp_count

        comp_display = display_name(comp, items_by_un, item_name, loc_dict)
        label = f"{comp_display} x{label_mult}"
        if reusable:
            label += " (reusable)"

        comp_owned_qty = (
            owned.get(normalize_path(comp_un), 0) - consumed[comp_un_lower]
        )
        consumed[comp_un_lower] += label_mult

        cls = _craft_color(comp_owned_qty, label_mult)
        lines.append(
            f'<div class="tree-item"><span class="{cls}">{label}</span></div>'
        )

        expand = (
            should_expand(
                comp_un_lower, recipes_by_result, depth, max_depth, is_bp,
            )
            and not (comp_owned_qty >= label_mult)
        )
        if expand:
            sub = _tree_html(
                comp_un, label_mult, items_by_un, owned,
                recipes_by_result, loc_dict,
                depth + 1, max_depth, item_name,
            )
            if sub:
                lines.append(f'<div class="tree">{sub}</div>')

    return "".join(lines)


def _craft_summary_html(requirements: dict, craftables: dict) -> str:
    parts = []

    craft_list = sorted(craftables.values(), key=lambda x: x["name"])
    craft_list = [c for c in craft_list if c["quantity"] > c["owned"]]
    if craft_list:
        rows = "".join(
            f"<tr><td>{c['name']}</td>"
            f'<td class="num">{c["quantity"]}</td>'
            f'<td class="num">{c["owned"]}</td>'
            f'<td class="num">{c["num_crafts"]}</td></tr>'
            for c in craft_list
        )
        parts.append(f"""<h2>Crafting List</h2>
<table><thead><tr><th>Item</th><th class='num'>Needed</th><th class='num'>Owned</th>
<th class='num'>Crafts</th></tr></thead><tbody>{rows}</tbody></table>""")

    mat_rows = "".join(
        f"<tr><td>{info['name']}</td>"
        f'<td class="num">{info["quantity"]}</td>'
        f'<td class="num">{info["owned"]}</td>'
        f'<td class="num">{_status_tag(info["owned"], info["quantity"])}</td></tr>'
        for info in sorted(requirements.values(), key=lambda x: x["name"])
    )
    parts.append(f"""<h2>Raw Materials</h2>
<table><thead><tr><th>Material</th><th class='num'>Needed</th>
<th class='num'>Owned</th><th class='num'>Status</th></tr></thead>
<tbody>{mat_rows}</tbody></table>""")

    farm_items = [
        {"name": info["name"], "needed": info["quantity"],
         "owned": info["owned"],
         "missing": max(0, info["quantity"] - info["owned"])}
        for info in sorted(requirements.values(), key=lambda x: x["name"])
        if max(0, info["quantity"] - info["owned"]) > 0
    ]
    if farm_items:
        fr = "".join(
            f"<tr><td>{fi['name']}</td>"
            f'<td class="num">{fi["needed"]}</td>'
            f'<td class="num">{fi["owned"]}</td>'
            f'<td class="num red">{fi["missing"]}</td></tr>'
            for fi in farm_items
        )
        parts.append(f"""<h2>Farming List</h2>
<table><thead><tr><th>Material</th><th class='num'>Needed</th>
<th class='num'>Owned</th><th class='num'>To Farm</th></tr></thead>
<tbody>{fr}</tbody></table>""")

    return "".join(parts)


# ---------------------------------------------------------------------------
# Ducats
# ---------------------------------------------------------------------------

def render_ducats_missing(data) -> str:
    items = data.items
    missing = data.missing
    total_safe = data.total_ducats_safe
    total_keep = data.total_ducats_keep
    parts = []

    if missing:
        rows = "".join(
            f"<tr><td>{m.name}</td><td>{m.category}</td>"
            f'<td class="num">{m.mastery_req or ""}</td></tr>'
            for m in sorted(missing, key=lambda x: (x.category, x.name))
        )
        parts.append(f"""<h2>Missing Prime Items (no parts owned)</h2>
<table><thead><tr><th>Name</th><th>Category</th><th class='num'>MR</th></tr></thead>
<tbody>{rows}</tbody></table>""")

    n_build = sum(1 for i in items if i.status == "buildable")
    n_partial = sum(1 for i in items if i.status == "partial")
    n_have = sum(1 for i in items if i.status == "have_copy")
    bits = []
    if n_have:
        bits.append(f"{n_have} owned")
    if n_build:
        bits.append(f"{n_build} buildable")
    if n_partial:
        bits.append(f"{n_partial} partial")
    parts.append(f"""<h2>Summary</h2>
<p>{', '.join(bits)} &mdash; {total_safe:,} ducats safe &mdash; {total_keep:,} ducats keep</p>""")

    content = _subtab_bar(_DUCATS_SUBTABS, "missing") + "".join(parts)
    return _page("Ducats — Missing", content, "ducats")


def render_ducats_excess(data) -> str:
    items = data.items
    parts = []

    relevant = [i for i in items if (
        any(p.surplus > 0 for p in i.parts)
        or any(p.missing > 0 for p in i.parts)
    )]
    if relevant:
        rows = "".join(
            _item_row(i) for i in sorted(relevant, key=lambda x: x.name)
        )
        parts.append(f"""<h2>Excess Prime Parts</h2>
<table><thead><tr><th>Item</th><th class='num'>Copies</th>
<th>Missing</th><th>Excess</th><th class='num'>Ducats</th></tr></thead>
<tbody>{rows}</tbody></table>""")

    content = _subtab_bar(_DUCATS_SUBTABS, "excess") + "".join(parts)
    return _page("Ducats — Excess", content, "ducats")


def render_ducats_sell(data) -> str:
    items = data.items
    total_safe = data.total_ducats_safe
    parts = []

    sell: dict = defaultdict(lambda: {"count": 0, "ducats": 0})
    for item in items:
        for p in item.parts:
            if p.surplus > 0 and p.ducats > 0:
                key = (item.name, p.name)
                sell[key]["count"] += p.surplus
                sell[key]["ducats"] = p.ducats
    if sell:
        srows = "".join(
            f"<tr><td>{item} &mdash; {part}</td>"
            f'<td class="num">x{info["count"]}</td>'
            f'<td class="num">{info["ducats"]}</td>'
            f'<td class="num">{info["count"] * info["ducats"]}</td></tr>'
            for (item, part), info in sorted(sell.items())
        )
        parts.append(f"""<h2>Sell for Ducats</h2>
<table><thead><tr><th>Part</th><th class='num'>Qty</th>
<th class='num'>Duc</th><th class='num'>Total</th></tr></thead>
<tbody>{srows}</tbody></table>
<p><strong>Total: {total_safe:,} ducats</strong></p>""")

    content = _subtab_bar(_DUCATS_SUBTABS, "sell") + "".join(parts)
    return _page("Ducats — Sell", content, "ducats")


def _item_row(i) -> str:
    missing_parts = [p for p in i.parts if p.missing > 0]
    missing_str = (
        ", ".join(f"{p.name} x{p.missing}" for p in missing_parts) or "—"
    )
    surplus_parts = [p for p in i.parts if p.surplus > 0]
    surplus_str = (
        ", ".join(f"{p.name} x{p.surplus}" for p in surplus_parts) or "—"
    )
    excess_duc = sum(p.surplus * p.ducats for p in surplus_parts)
    return (
        f"<tr><td>{i.name}</td>"
        f'<td class="num">{i.needed_builds}</td>'
        f"<td>{missing_str}</td>"
        f"<td>{surplus_str}</td>"
        f'<td class="num">{excess_duc if excess_duc else ""}</td></tr>'
    )


# ---------------------------------------------------------------------------
# Relics
# ---------------------------------------------------------------------------

def render_relics_needed(needed, relics) -> str:
    parts = []

    if needed:
        rows = "".join(
            f"<tr><td>{n.part_name}</td><td>{n.item_name}</td>"
            f'<td class="num">x{n.missing}</td>'
            f'<td class="num">{n.ducats or ""}</td>'
            f"<td>{', '.join(f'{name} x{count}' for name, count, _ in n.drops) if n.drops else '—'}</td></tr>"
            for n in needed
        )
        parts.append(f"""<h2>Needed Prime Parts &amp; Relic Drops</h2>
<table><thead><tr><th>Part</th><th>Item</th><th class='num'>Need</th>
<th class='num'>Duc</th><th>Owned Relic Drops</th></tr></thead>
<tbody>{rows}</tbody></table>""")
    else:
        parts.append('<p class="green">No missing parts — you have everything!</p>')

    content = _subtab_bar(_RELICS_SUBTABS, "needed") + "".join(parts)
    return _page("Relics — Needed", content, "relics")


def render_relics_safe(needed, relics) -> str:
    parts = []

    safe = [r for r in relics if r.safe and r.count > 0]
    if safe:
        rows = "".join(
            f"<tr><td>{r.name}</td><td>{r.tier}</td>"
            f'<td class="num">{r.count}</td>'
            f"<td>{'Yes' if r.vaulted else 'No'}</td></tr>"
            for r in safe
        )
        parts.append(f"""<h2>Safe-to-Spam Relics</h2>
<table><thead><tr><th>Relic</th><th>Tier</th><th class='num'>Count</th>
<th>Vaulted</th></tr></thead>
<tbody>{rows}</tbody></table>
<p>{len(safe)} relic{'s' if len(safe) != 1 else ''} safe to spam.</p>""")

    content = _subtab_bar(_RELICS_SUBTABS, "safe") + "".join(parts)
    return _page("Relics — Safe", content, "relics")


# ---------------------------------------------------------------------------
# Craft
# ---------------------------------------------------------------------------

def render_craft(items_by_un, recipes_by_result, owned, loc_dict) -> str:
    from warframe_profile.model.craft_model import find_items, build_lookup
    by_name = build_lookup(items_by_un, loc_dict)
    content = """
<div class="tab-bar">
  <a href="/craft" class="active">Item Lookup</a>
  <a href="/weapon-chains">Weapon Chains</a>
</div>
<form method="get" action="/craft" style="margin-bottom:16px">
  <input type="text" name="q" placeholder="Search items..."
         value="" style="width:300px">
  <button type="submit">Search</button>
</form>"""
    return _page("Craft", content, "craft")


def render_craft_result(
    query: str, matches: list, items_by_un, recipes_by_result,
    owned, loc_dict, depth: int = 3,
) -> str:
    lines = []
    for m in matches:
        un = m.get("uniqueName", "")
        name = resolve_name(m.get("name", ""), loc_dict) or un.split("/")[-1]
        cat = m.get("category", "")
        lines.append(f"""<details>
<summary style="font-size:1rem">{name} <span class="dim">({cat})</span></summary>
<div class="tree">{_tree_html(un, 1, items_by_un, owned, recipes_by_result, loc_dict, max_depth=depth)}</div>""")
        req, craft = None, None
        from warframe_profile.model.craft_model import resolve_tree
        req, craft = resolve_tree(
            un, 1, items_by_un, owned, recipes_by_result, loc_dict, max_depth=depth,
        )
        lines.append(_craft_summary_html(req, craft))
        lines.append("</details>")

    tab_bar = """
<div class="tab-bar">
  <a href="/craft" class="active">Item Lookup</a>
  <a href="/weapon-chains">Weapon Chains</a>
</div>"""
    content = tab_bar + f"""
<form method="get" action="/craft" style="margin-bottom:16px">
  <input type="text" name="q" placeholder="Search items..."
         value="{query}" style="width:300px">
  <button type="submit">Search</button>
</form>
<div>{''.join(lines)}</div>"""
    return _page(f"Craft: {query}", content, "craft")


# ---------------------------------------------------------------------------
# Weapon Chains
# ---------------------------------------------------------------------------

def render_weapon_chains(
    chains: list, all_requirements: dict, all_craftables: dict,
    shown: int, items_by_un, recipes_by_result, owned, loc_dict,
) -> str:
    parts = []
    if shown == 0:
        return _page("Weapon Chains", """
<div class="flash">No unowned weapon chains found!</div>""", "weapon-chains")

    parts.append(f'<p>{shown} chain{"s" if shown != 1 else ""} with unowned final weapons.</p>')

    for chain in chains:
        final_un = chain[-1]
        final_norm = normalize_path(final_un)
        if owned.get(final_norm, 0) > 0:
            continue

        names = []
        item = items_by_un.get(final_un.lower())
        if item:
            names.append(resolve_name(item.get("name", ""), loc_dict) or un_to_name(final_un))
        else:
            names.append(un_to_name(final_un))

        parts.append(f"""<details>
<summary>{' → '.join(names)}</summary>
<div class="tree">{_tree_html(final_un, 1, items_by_un, owned, recipes_by_result, loc_dict, max_depth=5)}</div>""")
        req, craft = None, None
        from warframe_profile.model.craft_model import resolve_tree
        req, craft = resolve_tree(
            final_un, 1, items_by_un, owned, recipes_by_result, loc_dict, max_depth=5,
        )
        parts.append(_craft_summary_html(req, craft))
        parts.append("</details>")

    parts.append(_craft_summary_html(all_requirements, all_craftables))

    tab_bar = """
<div class="tab-bar">
  <a href="/craft">Item Lookup</a>
  <a href="/weapon-chains" class="active">Weapon Chains</a>
</div>"""
    return _page("Weapon Chains", tab_bar + "".join(parts), "weapon-chains")


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def render_cleanup_rebuy(sellable, owned_prime_pairs, items_by_un, loc_dict) -> str:
    parts = []

    if sellable:
        rows = "".join(
            f"<tr><td>{s.name}</td><td>{s.category}</td>"
            f"<td>{s.buy_type}</td><td>{s.rare_component or '—'}</td></tr>"
            for s in sellable
        )
        parts.append(f"""<h2>Market Re-Buyable Equipment (safe to sell)</h2>
<p class="dim" style="margin:-4px 0 8px;font-size:0.8rem">Includes items that only craft into equipment you already own.</p>
<table><thead><tr><th>Name</th><th>Category</th><th>Buy Type</th>
<th>Rarest Material</th></tr></thead>
<tbody>{rows}</tbody></table>""")

    if not parts:
        parts.append('<p class="green">Nothing to clean up!</p>')

    content = _subtab_bar(_CLEANUP_SUBTABS, "rebuy") + "".join(parts)
    return _page("Cleanup — Re-Buyable", content, "cleanup")


def render_cleanup_sell(sellable, owned_prime_pairs, items_by_un, loc_dict) -> str:
    parts = []

    if owned_prime_pairs:
        rows = "".join(
            f"<tr><td>{name}</td><td>{cat}</td><td>{prime_name}</td></tr>"
            for name, cat, prime_name in sorted(owned_prime_pairs, key=lambda x: (x[1], x[0]))
        )
        parts.append(f"""<h2>Items to Sell (Own Prime Variant)</h2>
<table><thead><tr><th>Item</th><th>Category</th><th>Prime Variant</th></tr></thead>
<tbody>{rows}</tbody></table>""")

    if not parts:
        parts.append('<p class="green">Nothing to clean up!</p>')

    content = _subtab_bar(_CLEANUP_SUBTABS, "sell") + "".join(parts)
    return _page("Cleanup — Prime Variant", content, "cleanup")


def render_cleanup_excess(excess_items) -> str:
    parts = []

    if excess_items:
        rows = "".join(
            f"<tr><td>{e.name}</td><td>{e.item_type}</td>"
            f'<td class="num">{e.owned}</td>'
            f'<td class="num">{e.sell}</td>'
            f"<td>{e.builds}</td></tr>"
            for e in excess_items
        )
        parts.append(f"""<h2>Excess Blueprints &amp; Components</h2>
<p class="dim" style="margin:-4px 0 8px;font-size:0.8rem">
These items only craft into equipment you already own — safe to sell.</p>
<table><thead><tr><th>Name</th><th>Type</th><th class='num'>Owned</th>
<th class='num'>Sell</th><th>Crafts Into</th></tr></thead>
<tbody>{rows}</tbody></table>""")

    if not parts:
        parts.append('<p class="green">No excess items found!</p>')

    content = _subtab_bar(_CLEANUP_SUBTABS, "excess") + "".join(parts)
    return _page("Cleanup — Excess", content, "cleanup")
