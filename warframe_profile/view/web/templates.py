"""HTML templates for the web view — renders model data as HTML pages.

Each ``render_*`` function takes the same model dataclasses as the CLI
view (:mod:`warframe_profile.view.report`) but returns an HTML string
wrapped in the shared dark-theme page layout (:func:`_page`).

The page layout includes:
    - GitHub-dark inspired CSS theme.
    - Navigation tabs (Home, Ducats, Relics, Craft, Weapon Chains, Mastery,
      Mods, Cleanup).
    - Refresh Inventory button (POSTs to /refresh).
    - Subtab bars for views with multiple sub-sections (relics, mastery,
      cleanup).

This module is the **web view** in the MVP pattern.
"""

import html
import math
from collections import defaultdict
from urllib.parse import quote_plus

from warframe_profile.model.craft_model import (
    add_missing_blueprint_requirement,
    display_name,
    get_recipe_components,
    has_recipe,
    is_blueprint_un,
    postprocess_craft_plan,
    resolve_name,
    resolve_tree,
    should_expand,
    un_to_name,
    weapon_name,
)
from warframe_profile.model.utils import normalize_path
from warframe_profile.view.report import part_label

GREEN = "#27ae60"
RED = "#e74c3c"
YELLOW = "#f1c40f"
DIM = "#7f8c8d"


def _esc(value: object) -> str:
    """Escape game-derived text before interpolating it into HTML.

    Item, part, and recipe names come from the DE/WFCD datasets rather than
    from user input, but they are still external data and may contain
    characters (``&``, ``<``) that would break markup.  Escaping every value
    keeps the view consistent with :func:`render_mods`.
    """
    return html.escape(str(value), quote=False)


#: Shared stylesheet for all pages (GitHub-dark theme), built once at import.
_PAGE_STYLES = f"""<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
          background: #0d1117; color: #c9d1d9; padding: 20px; }}
  h1 {{ font-size: 1.6rem; margin-bottom: 16px; color: #58a6ff; }}
  h2 {{ font-size: 1.2rem; margin: 20px 0 10px; color: #8b949e; }}
  h3 {{ font-size: 1rem; margin: 14px 0 6px; color: #c9d1d9; }}
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
  td a {{ color: #58a6ff; text-decoration: none; }}
  td a:hover {{ text-decoration: underline; }}
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
  .notice {{ position: fixed; top: 16px; right: 16px; z-index: 1000;
            max-width: 380px; margin: 0; display: flex; gap: 12px;
            align-items: flex-start; box-shadow: 0 8px 24px #0008; }}
  .notice button {{ background: transparent; border: none; color: inherit;
                   padding: 0 2px; font-size: 1.1rem; line-height: 1; }}
  .notice button:hover {{ background: transparent; color: #fff; }}
  @media (max-width: 600px) {{ table {{ font-size: 0.75rem; }}
    th, td {{ padding: 4px 6px; }} }}
</style>"""

#: Shows a dismissible notification when the page was reached after a failed
#: inventory refresh (``?refresh_error=…``) or a completed item-database
#: refresh (``?notice=…``).  The parameter is stripped from the address bar
#: once the message has been picked up, so a later manual reload does not
#: repeat it.
_NOTICE_JS = """<script>
(() => {
  const params = new URLSearchParams(location.search);
  const error = params.get("refresh_error");
  const info = params.get("notice");
  const message = error || info;
  if (!message) return;
  params.delete("refresh_error");
  params.delete("notice");
  const query = params.toString();
  history.replaceState(
    null, "", location.pathname + (query ? "?" + query : "") + location.hash
  );

  const notice = document.createElement("div");
  notice.className = error ? "flash flash-error notice" : "flash notice";
  notice.setAttribute("role", error ? "alert" : "status");
  notice.textContent = message;

  const close = document.createElement("button");
  close.type = "button";
  close.setAttribute("aria-label", "Dismiss notification");
  close.textContent = "\\u00d7";
  close.addEventListener("click", () => notice.remove());
  notice.appendChild(close);

  const nav = document.querySelector("nav");
  if (nav) nav.after(notice);
  else document.body.prepend(notice);
  setTimeout(() => notice.remove(), 15000);
})();
</script>"""


#: Restores open ``<details>`` sections after the Refresh Inventory reload.
#:
#: The refresh form stores the open/closed state in ``sessionStorage`` keyed
#: by the current path and query, then the reloaded page restores it.  This
#: keeps expanded crafting results (and other detail sections) open.
_DETAILS_STATE_JS = """<script>
(() => {
  const key = "wf-open-details:" + location.pathname + location.search;

  document.querySelectorAll('form[action^="/refresh"]').forEach((form) => {
    form.addEventListener("submit", () => {
      const open = Array.from(document.querySelectorAll("details")).map((d) => d.open);
      try {
        sessionStorage.setItem(key, JSON.stringify(open));
      } catch (e) {
        /* sessionStorage may be unavailable */
      }
    });
  });

  let saved = null;
  try {
    saved = JSON.parse(sessionStorage.getItem(key) || "null");
  } catch (e) {
    return;
  }
  if (Array.isArray(saved)) {
    document.querySelectorAll("details").forEach((details, index) => {
      if (saved[index]) details.open = true;
    });
    try {
      sessionStorage.removeItem(key);
    } catch (e) {
      /* ignore */
    }
  }
})();
</script>"""


def _nav_link(key: str, label: str, url: str, active: str) -> str:
    """Return a single nav link, marked active if *key* matches *active*."""
    active_cls = ' class="active"' if key == active else ""
    return f'<a href="{url}"{active_cls}>{label}</a>'


def _refresh_actions() -> str:
    """Return the POST forms that refresh the inventory and the item database."""
    return (
        '<div style="margin-left:auto;display:flex;gap:6px">'
        '<form method="post" action="/refresh">'
        '<button type="submit" title="Re-scan the running game for your inventory" '
        'style="padding:4px 12px;font-size:0.85rem">'
        "Refresh Inventory</button></form>"
        '<form method="post" action="/refresh-items">'
        '<button type="submit" title="Re-download the merged item database '
        '(same as `warframe update`)" '
        'style="padding:4px 12px;font-size:0.85rem">'
        "Refresh Item DB</button></form>"
        "</div>"
    )


def _page(title: str, content: str, active: str) -> str:
    """Wrap *content* in the full HTML page layout with nav, styles, and title."""
    tabs = [
        ("home", "Home", "/"),
        ("ducats", "Ducats", "/ducats"),
        ("relics", "Relics", "/relics"),
        ("craft", "Craft", "/craft"),
        ("weapon-chains", "Weapon Chains", "/weapon-chains"),
        ("mastery", "Mastery", "/mastery"),
        ("mods", "Mods", "/mods"),
        ("cleanup", "Cleanup", "/cleanup"),
    ]
    tab_links = "".join(_nav_link(key, label, url, active) for key, label, url in tabs)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} — Warframe Profile</title>
{_PAGE_STYLES}
</head>
<body>
<h1>Warframe Profile</h1>
<nav>{tab_links}{_refresh_actions()}</nav>
{content}
{_NOTICE_JS}
{_DETAILS_STATE_JS}
</body>
</html>"""


# ---------------------------------------------------------------------------
# Subtab navigation helpers
# ---------------------------------------------------------------------------


def _subtab_bar(subtabs: list[tuple[str, str, str]], active: str) -> str:
    """Render a horizontal subtab bar, marking the *active* tab."""

    def _link(key, label, url):
        active_cls = ' class="active"' if key == active else ""
        return f'<a href="{url}"{active_cls}>{label}</a>'

    links = "".join(_link(key, label, url) for key, label, url in subtabs)
    return f'<div class="tab-bar">{links}</div>'


_MASTERY_SUBTABS = [
    ("ready", "Ready to Build", "/mastery"),
    ("blueprint", "Blueprint Ready", "/mastery/blueprint-ready"),
    ("never", "Never Touched", "/mastery/never-touched"),
    ("progress", "In Progress", "/mastery/in-progress"),
]

#: Preferred tab order for needed-drop categories; unknown categories follow
#: alphabetically. Safe-to-Spam is always first.
_RELIC_CATEGORY_ORDER = (
    "Warframes",
    "Primary",
    "Secondary",
    "Melee",
    "Archwing",
    "Sentinels",
    "Arch-Gun",
)


def relic_category_slug(category: str) -> str:
    """Return the URL slug used for a needed-drop category tab."""
    return category.lower().replace(" ", "-")


def relic_category_tabs(needed) -> list[tuple[str, str, str]]:
    """Return the Relics subtabs: Safe to Spam first, then needed categories."""
    categories = {n.category for n in needed if n.category}
    ordered = [c for c in _RELIC_CATEGORY_ORDER if c in categories]
    ordered.extend(sorted(categories - set(ordered)))

    tabs = [("safe", "Safe to Spam", "/relics")]
    for category in ordered:
        slug = relic_category_slug(category)
        tabs.append((slug, category, f"/relics/needed/{slug}"))
    return tabs


_CLEANUP_SUBTABS = [
    ("rebuy", "Re-Buyable", "/cleanup"),
    ("sell", "Prime Variant", "/cleanup/sell"),
    ("excess", "Excess", "/cleanup/excess"),
]


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------


def render_home() -> str:
    """Render the home page listing all available views."""
    return _page(
        "Home",
        """
<div class="flash">Select a tab above to explore your Warframe profile.</div>
<ul style="margin-left:20px;line-height:1.8">
  <li><strong>Ducats</strong> — Surplus Prime parts to sell at Baro Ki'Teer</li>
  <li><strong>Relics</strong> — Needed prime parts and safe-to-spam relics</li>
  <li><strong>Craft</strong> — Look up crafting trees for any item</li>
  <li><strong>Weapon Chains</strong> — Weapon upgrade chains you haven't completed</li>
  <li><strong>Mastery</strong> — Ready to build, blueprint, never-touched, and in-progress</li>
  <li><strong>Mods</strong> — Missing mods grouped by in-game category</li>
  <li><strong>Cleanup</strong> — Equipment safe to sell (market-rebuyable or Prime-owned)</li>
</ul>
<p class="dim" style="font-size:0.8rem">Use <strong>Refresh Inventory</strong> to re-scan the
game, or <strong>Refresh Item DB</strong> to re-download the item database.</p>""",
        "home",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
# Shared rendering helpers used by multiple render_* functions.
# _status_tag — HTML span with colour-coded owned/partial/farm status.
# _tree_html  — recursive crafting tree HTML builder (mirrors print_craft_tree).
# _craft_summary_html — aggregated requirements and farming tables.
#


def _status_tag(owned: int, needed: int) -> str:
    """Return an HTML span showing owned/partial/farm status for *owned*/*needed*."""
    if owned >= needed:
        return '<span class="tag tag-green">owned</span>'
    elif owned > 0:
        return f'<span class="tag tag-yellow">partial {owned}/{needed}</span>'
    return f'<span class="tag tag-red">farm {needed - owned}</span>'


def _craft_color(owned: int, needed: int) -> str:
    """Return CSS colour name for crafting tree nodes (green/yellow/red)."""
    if owned >= needed:
        return "green"
    elif owned > 0:
        return "yellow"
    return "red"


def _tree_component_html(
    comp: dict,
    effective_crafts: int,
    consumable: bool,
    items_by_un: dict,
    owned: dict,
    consumed: dict,
    recipes_by_result: dict,
    loc_dict: dict,
    depth: int,
    max_depth: int,
    item_name: str,
) -> str:
    """Build the HTML fragment for one component in the tree."""
    comp_un = comp.get("uniqueName", "")
    comp_un_lower = comp_un.lower()
    comp_count = comp.get("itemCount", 1)
    comp_name = resolve_name(comp.get("name", ""), loc_dict)
    is_bp = "blueprint" in comp_name.lower() or is_blueprint_un(comp_un)
    reusable = is_bp and not consumable
    label_mult = 1 if reusable else effective_crafts * comp_count

    comp_display = display_name(comp, items_by_un, item_name, loc_dict)
    label = f"{comp_display} x{label_mult}"
    if reusable:
        label += " (reusable)"

    comp_owned_qty = owned.get(normalize_path(comp_un), 0) - consumed[comp_un_lower]
    consumed[comp_un_lower] += label_mult

    cls = _craft_color(comp_owned_qty, label_mult)
    parts = [f'<div class="tree-item"><span class="{cls}">{_esc(label)}</span></div>']

    expand = should_expand(comp_un_lower, recipes_by_result, depth, max_depth, is_bp) and not (
        comp_owned_qty >= label_mult
    )
    if expand:
        sub = _tree_html(
            comp_un,
            label_mult,
            items_by_un,
            owned,
            recipes_by_result,
            loc_dict,
            depth + 1,
            max_depth,
        )
        if sub:
            parts.append(f'<div class="tree">{sub}</div>')

    return "".join(parts)


def _tree_html(
    item_un: str,
    quantity: int,
    items_by_un: dict,
    owned: dict,
    recipes_by_result: dict,
    loc_dict: dict,
    depth: int = 0,
    max_depth: int = 2,
) -> str:
    """Build recursive HTML crafting tree for *item_un* up to *max_depth*."""
    lines: list[str] = []
    item_un_lower = item_un.lower()
    item = items_by_un.get(item_un_lower)

    components = (
        get_recipe_components(item_un_lower, items_by_un, recipes_by_result, loc_dict)
        if has_recipe(item_un_lower, recipes_by_result)
        else []
    )

    item_name = resolve_name(item.get("name", ""), loc_dict) if item else un_to_name(item_un)
    if not item_name:
        item_name = un_to_name(item_un)

    recipe = recipes_by_result.get(item_un_lower)
    if recipe:
        bp_key = recipe.get("_recipeKey", "")
        if bp_key:
            components = [
                {"uniqueName": bp_key, "name": f"{item_name} Blueprint", "itemCount": 1}
            ] + components

    if not components or not recipe:
        return ""

    build_qty = recipe.get("num", 1) or 1
    consumable = recipe.get("consumeOnUse", True)
    num_crafts = max(1, math.ceil(quantity / build_qty))

    consumed = defaultdict(int)
    for comp in components:
        lines.append(
            _tree_component_html(
                comp,
                num_crafts,
                consumable,
                items_by_un,
                owned,
                consumed,
                recipes_by_result,
                loc_dict,
                depth,
                max_depth,
                item_name,
            )
        )

    return "".join(lines)


def _craft_summary_html(requirements: dict, craftables: dict) -> str:
    """Render aggregated crafting list, raw materials, and farming tables."""
    parts = []

    craft_list = sorted(craftables.values(), key=lambda x: x["name"])
    craft_list = [c for c in craft_list if c["quantity"] > c["owned"]]
    if craft_list:
        rows = "".join(
            f"<tr><td>{_esc(c['name'])}</td>"
            f'<td class="num">{c["quantity"]}</td>'
            f'<td class="num">{c["owned"]}</td>'
            f'<td class="num">{c["num_crafts"]}</td></tr>'
            for c in craft_list
        )
        parts.append(f"""<h2>Crafting List</h2>
<table><thead><tr><th>Item</th><th class='num'>Needed</th><th class='num'>Owned</th>
<th class='num'>Crafts</th></tr></thead><tbody>{rows}</tbody></table>""")

    mat_rows = "".join(
        f"<tr><td>{_esc(info['name'])}</td>"
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
        {
            "name": info["name"],
            "needed": info["quantity"],
            "owned": info["owned"],
            "missing": max(0, info["quantity"] - info["owned"]),
        }
        for info in sorted(requirements.values(), key=lambda x: x["name"])
        if max(0, info["quantity"] - info["owned"]) > 0
    ]
    if farm_items:
        fr = "".join(
            f"<tr><td>{_esc(fi['name'])}</td>"
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
# Single view: aggregated surplus parts with ducat values.
#


def render_ducats(data) -> str:
    """Render the Ducats tab (surplus parts with ducat values)."""
    items = data.items
    total_safe = data.total_ducats_safe

    sell: dict = defaultdict(lambda: {"count": 0, "ducats": 0})
    for item in items:
        for p in item.parts:
            if p.surplus > 0 and p.ducats > 0:
                key = (item.name, p.name)
                sell[key]["count"] += p.surplus
                sell[key]["ducats"] = p.ducats

    if not sell:
        return _page(
            "Ducats",
            '<div class="flash">Nothing to sell for ducats right now '
            "\u2014 surplus Prime parts will appear here.</div>",
            "ducats",
        )

    srows = "".join(
        f"<tr><td>{_esc(part_label(item, part))}</td>"
        f'<td class="num">x{info["count"]}</td>'
        f'<td class="num">{info["ducats"]}</td>'
        f'<td class="num">{info["count"] * info["ducats"]}</td></tr>'
        for (item, part), info in sorted(sell.items())
    )
    content = f"""<h2>Sell for Ducats</h2>
<table><thead><tr><th>Part</th><th class='num'>Qty</th>
<th class='num'>Duc</th><th class='num'>Total</th></tr></thead>
<tbody>{srows}</tbody></table>
<p><strong>Total: {total_safe:,} ducats</strong></p>"""
    return _page("Ducats", content, "ducats")


# ---------------------------------------------------------------------------
# Relics
# ---------------------------------------------------------------------------
# Safe to Spam (default) plus one subtab per needed-drop item category:
# Warframes, Primary, Secondary, Melee, Archwing, etc.
#


def render_relics_needed(needed, category_slug: str) -> str:
    """Render one needed-drops category (missing parts + owned relic drops)."""
    items = [n for n in needed if relic_category_slug(n.category) == category_slug]
    label = items[0].category if items else category_slug

    def _drops_cell(part) -> str:
        if not part.drops:
            return "—"
        return _esc(", ".join(f"{name} x{count}" for name, count, _ in part.drops))

    parts = []
    if items:
        rows = "".join(
            f"<tr><td>{_esc(n.part_name)}</td><td>{_esc(n.item_name)}</td>"
            f'<td class="num">x{n.missing}</td>'
            f'<td class="num">{n.ducats or ""}</td>'
            f"<td>{_drops_cell(n)}</td></tr>"
            for n in items
        )
        parts.append(f"""<h2>{_esc(label)} &mdash; Needed Prime Parts</h2>
<table><thead><tr><th>Part</th><th>Item</th><th class='num'>Need</th>
<th class='num'>Duc</th><th>Owned Relic Drops</th></tr></thead>
<tbody>{rows}</tbody></table>""")
    else:
        parts.append('<p class="green">No missing parts in this category.</p>')

    content = _subtab_bar(relic_category_tabs(needed), category_slug) + "".join(parts)
    return _page(f"Relics — {label}", content, "relics")


def render_relics_safe(needed, relics) -> str:
    """Render the Relics — Safe-to-Spam view (relics safe to crack)."""
    parts = []

    safe = [r for r in relics if r.safe and r.count > 0]
    if safe:
        rows = "".join(
            f"<tr><td>{_esc(r.name)}</td><td>{_esc(r.tier)}</td>"
            f'<td class="num">{r.count}</td>'
            f"<td>{'Yes' if r.vaulted else 'No'}</td></tr>"
            for r in safe
        )
        parts.append(f"""<h2>Safe-to-Spam Relics</h2>
<table><thead><tr><th>Relic</th><th>Tier</th><th class='num'>Count</th>
<th>Vaulted</th></tr></thead>
<tbody>{rows}</tbody></table>
<p>{len(safe)} relic{"s" if len(safe) != 1 else ""} safe to spam.</p>""")
    else:
        parts.append('<div class="flash">No relics are safe to spam yet.</div>')

    content = _subtab_bar(relic_category_tabs(needed), "safe") + "".join(parts)
    return _page("Relics — Safe to Spam", content, "relics")


# ---------------------------------------------------------------------------
# Craft
# ---------------------------------------------------------------------------
# Item lookup with search form + expandable crafting trees per result.
#


def render_craft(query: str = "", error: str | None = None) -> str:
    """Render the Craft tab with the search form and an optional error message."""
    error_html = f'<p class="red">{html.escape(error)}</p>' if error else ""
    content = f"""<form method="get" action="/craft" style="margin-bottom:16px">
  <input type="text" name="q" placeholder="Search items..."
         value="{html.escape(query, quote=True)}" style="width:300px">
  <button type="submit">Search</button>
</form>{error_html}"""
    return _page("Craft", content, "craft")


def render_craft_result(
    query: str,
    matches: list,
    items_by_un,
    recipes_by_result,
    owned,
    loc_dict,
    depth: int = 3,
) -> str:
    """Render search results as expandable crafting trees with summaries."""
    lines = []
    query_norm = query.strip().lower()
    for m in matches:
        un = m.get("uniqueName", "")
        name = resolve_name(m.get("name", ""), loc_dict) or un.split("/")[-1]
        cat = m.get("category", "")
        exact = name.lower() == query_norm
        tree = _tree_html(un, 1, items_by_un, owned, recipes_by_result, loc_dict, max_depth=depth)
        req, craft = resolve_tree(
            un,
            1,
            items_by_un,
            owned,
            recipes_by_result,
            loc_dict,
            max_depth=depth,
        )
        add_missing_blueprint_requirement(un, name, req, owned, recipes_by_result)
        requirements = postprocess_craft_plan(
            req, craft, owned, items_by_un, recipes_by_result, loc_dict
        )
        lines.append(f"""<details{" open" if exact else ""}>
<summary style="font-size:1rem">{_esc(name)} <span class="dim">({_esc(cat)})</span></summary>
<div class="tree">{tree}</div>{_craft_summary_html(requirements, craft)}</details>""")

    content = f"""
<form method="get" action="/craft" style="margin-bottom:16px">
  <input type="text" name="q" placeholder="Search items..."
         value="{html.escape(query, quote=True)}" style="width:300px">
  <button type="submit">Search</button>
</form>
<div>{"".join(lines)}</div>"""
    return _page(f"Craft: {query}", content, "craft")


# ---------------------------------------------------------------------------
# Weapon Chains
# ---------------------------------------------------------------------------
# Lists weapon upgrade chains whose final weapon is not yet owned, with
# expandable crafting trees and aggregated requirements.
#


def render_weapon_chains(
    chains: list,
    all_requirements: dict,
    all_craftables: dict,
    shown: int,
    items_by_un,
    recipes_by_result,
    owned,
    loc_dict,
) -> str:
    """Render the Weapon Chains tab (unowned upgrade chains with crafting trees)."""
    parts = []
    if shown == 0:
        return _page(
            "Weapon Chains",
            """
<div class="flash">No unowned weapon chains found!</div>""",
            "weapon-chains",
        )

    parts.append(f"<p>{shown} chain{'s' if shown != 1 else ''} with unowned final weapons.</p>")

    for chain in chains:
        final_un = chain[-1]
        final_norm = normalize_path(final_un)
        if owned.get(final_norm, 0) > 0:
            continue

        # Show the whole upgrade path (e.g. "Magnus Prime → Akmagnus Prime"),
        # matching the CLI; only the final weapon is actually built.
        names = [weapon_name(un, items_by_un, loc_dict) for un in chain]

        tree = _tree_html(final_un, 1, items_by_un, owned, recipes_by_result, loc_dict, max_depth=5)
        req, craft = resolve_tree(
            final_un,
            1,
            items_by_un,
            owned,
            recipes_by_result,
            loc_dict,
            max_depth=5,
        )
        add_missing_blueprint_requirement(final_un, names[-1], req, owned, recipes_by_result)
        requirements = postprocess_craft_plan(
            req, craft, owned, items_by_un, recipes_by_result, loc_dict
        )
        parts.append(f"""<details>
<summary>{_esc(" → ".join(names))}</summary>
<div class="tree">{tree}</div>{_craft_summary_html(requirements, craft)}</details>""")

    parts.append(_craft_summary_html(all_requirements, all_craftables))

    return _page("Weapon Chains", "".join(parts), "weapon-chains")


# ---------------------------------------------------------------------------
# Mastery
# ---------------------------------------------------------------------------
# Mirrors the CLI mastery report, split into subtabs: Ready to Build (Prime
# parts complete), Blueprint Ready, Never Touched (tiered), and In Progress
# (tiered). The progression summary is shown above every subtab.
#


def _mastery_table(rows, link_craft: bool = False) -> str:
    """Render one Item / Category / MR / Source table.

    When *link_craft* is true each item name links to its crafting breakdown
    on the Craft tab.
    """

    def _name_cell(row) -> str:
        name = html.escape(row.name)
        if not link_craft:
            return name
        href = html.escape(f"/craft?q={quote_plus(row.name)}", quote=True)
        return f'<a href="{href}">{name}</a>'

    body = "".join(
        f"<tr><td>{_name_cell(row)}</td>"
        f"<td>{html.escape(row.category)}</td>"
        f'<td class="num">{row.mr:,}</td>'
        f"<td>{html.escape(row.source)}</td></tr>"
        for row in rows
    )
    return f"""<table><thead><tr><th>Item</th><th>Category</th>
<th class='num'>MR</th><th>Source</th></tr></thead><tbody>{body}</tbody></table>"""


def _mastery_summary(report) -> str:
    """Render the shared progression summary shown above every subtab."""
    blueprint_ready, never_touched, in_progress = report.sections
    return f"""<h2>Summary</h2>
<p>{report.mastered:,} / {report.total:,} masterable items mastered
   ({report.percent}%)</p>
<p>{blueprint_ready.count} blueprints ready
   &middot; {never_touched.count} items never touched
   &middot; {in_progress.count} in progress
   &middot; {report.remaining} remaining</p>
<p>~{report.total_mr:,} MR available from unmastered items</p>"""


def _mastery_section_body(section) -> str:
    """Render one mastery section, optionally grouped by farming tier."""
    if not section.rows:
        return '<div class="flash">No items in this section.</div>'

    parts = []
    if section.desc:
        parts.append(f'<p class="dim">{html.escape(section.desc)}</p>')
    parts.append(
        f"<p>{section.count} item{'s' if section.count != 1 else ''} &mdash; ~{section.mr:,} MR</p>"
    )

    if not section.group_by_tier:
        parts.append(_mastery_table(section.rows, link_craft=section.link_craft))
        return "".join(parts)

    for group in section.tier_groups:
        parts.append(f"<h3>{html.escape(group.label)}</h3>")
        if group.desc:
            parts.append(f'<p class="dim">{html.escape(group.desc)}</p>')
        parts.append(
            f"<p>{group.count} item{'s' if group.count != 1 else ''} &mdash; ~{group.mr:,} MR</p>"
        )
        parts.append(_mastery_table(group.rows, link_craft=section.link_craft))
    return "".join(parts)


def _mastery_page(report, active: str, title: str, body: str) -> str:
    """Wrap a mastery subtab in the subtab bar and shared summary."""
    content = _subtab_bar(_MASTERY_SUBTABS, active) + _mastery_summary(report) + body
    return _page(title, content, "mastery")


def render_mastery_ready(report, data) -> str:
    """Render the Ready to Build subtab (Prime items with all parts owned)."""
    buildable = sorted(
        (i for i in data.items if i.status == "buildable"),
        key=lambda i: (i.category, i.name),
    )
    if buildable:
        rows = "".join(
            f"<tr><td>{_esc(i.name)}</td><td>{_esc(i.category)}</td>"
            f'<td class="num">{i.mastery_req or ""}</td></tr>'
            for i in buildable
        )
        body = f"""<h2>Ready to Build</h2>
<table><thead><tr><th>Name</th><th>Category</th><th class='num'>MR</th></tr></thead>
<tbody>{rows}</tbody></table>
<p class="dim">Every part and sub-craft ingredient is owned.</p>"""
    else:
        body = (
            '<div class="flash">Nothing is ready to build right now \u2014 '
            "Prime items appear here when every part and sub-craft ingredient is owned.</div>"
        )
    return _mastery_page(report, "ready", "Mastery — Ready to Build", body)


def render_mastery_blueprint(report) -> str:
    """Render the Blueprint Ready subtab."""
    body = f"<h2>Blueprint Ready</h2>{_mastery_section_body(report.sections[0])}"
    return _mastery_page(report, "blueprint", "Mastery — Blueprint Ready", body)


def render_mastery_never_touched(report) -> str:
    """Render the Never Touched subtab."""
    body = f"<h2>Never Touched</h2>{_mastery_section_body(report.sections[1])}"
    return _mastery_page(report, "never", "Mastery — Never Touched", body)


def render_mastery_in_progress(report) -> str:
    """Render the In Progress subtab."""
    body = f"<h2>In Progress</h2>{_mastery_section_body(report.sections[2])}"
    return _mastery_page(report, "progress", "Mastery — In Progress", body)


# ---------------------------------------------------------------------------
# Mods
# ---------------------------------------------------------------------------
# Mirrors the CLI missing-mods report: category summary plus per-category
# tables with rarity and acquisition hints.
#


def render_mods(report) -> str:
    """Render the Mods tab (category summary + missing mods per category)."""
    parts = [
        f"""<h2>Summary</h2>
<p>Owned: {report.owned_unique:,} unique mods
   ({report.total_copies:,} total copies)</p>
<p>Missing: {report.total_missing:,} mods
   <span class="dim">(excluding rivens, focus, transmutation)</span></p>"""
    ]

    if not report.categories:
        parts.append('<p class="green">You have all mods!</p>')
        return _page("Mods", "".join(parts), "mods")

    summary_rows = "".join(
        f"<tr><td>{html.escape(cat.name)}</td>"
        f'<td class="num">{cat.owned}</td>'
        f'<td class="num">{cat.missing}</td></tr>'
        for cat in sorted(report.categories, key=lambda c: c.missing, reverse=True)
    )
    parts.append(f"""<h2>Missing Mods by Category</h2>
<table><thead><tr><th>Category</th><th class='num'>Owned</th>
<th class='num'>Missing</th></tr></thead><tbody>{summary_rows}</tbody></table>""")

    for cat in report.categories:
        rows = "".join(
            f"<tr><td>{html.escape(row.name)}</td>"
            f"<td>{html.escape(row.rarity)}</td>"
            f"<td>{html.escape(row.source)}</td></tr>"
            for row in cat.rows
        )
        parts.append(f"""<details>
<summary>{html.escape(cat.name)}
  <span class="dim">({cat.missing} missing)</span></summary>
<table><thead><tr><th>Mod</th><th>Rarity</th><th>Source</th></tr></thead>
<tbody>{rows}</tbody></table></details>""")

    return _page("Mods", "".join(parts), "mods")


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
# Three sub-views:
#   Re-Buyable — market-rebuyable equipment safe to sell.
#   Prime Variant — non-Prime items whose Prime variant is already owned.
#   Excess — blueprints/components whose every crafting path is covered.
#


def render_cleanup_rebuy(sellable) -> str:
    """Render Cleanup — Re-Buyable sub-view (market-rebuyable equipment safe to sell)."""
    parts = []

    if sellable:
        rows = "".join(
            f"<tr><td>{_esc(s.name)}</td><td>{_esc(s.category)}</td>"
            f"<td>{_esc(s.buy_type)}</td>"
            f"<td>{_esc(s.rare_component) if s.rare_component else '—'}</td></tr>"
            for s in sellable
        )
        parts.append(f"""<h2>Market Re-Buyable Equipment (safe to sell)</h2>
<p class="dim" style="margin:-4px 0 8px;font-size:0.8rem">
Includes items that only craft into equipment you already own.</p>
<table><thead><tr><th>Name</th><th>Category</th><th>Buy Type</th>
<th>Rarest Material</th></tr></thead>
<tbody>{rows}</tbody></table>""")

    if not parts:
        parts.append('<p class="green">Nothing to clean up!</p>')

    content = _subtab_bar(_CLEANUP_SUBTABS, "rebuy") + "".join(parts)
    return _page("Cleanup — Re-Buyable", content, "cleanup")


def render_cleanup_sell(owned_prime_pairs) -> str:
    """Render Cleanup — Prime Variant sub-view (non-Prime items with owned Prime)."""
    parts = []

    if owned_prime_pairs:
        rows = "".join(
            f"<tr><td>{_esc(name)}</td><td>{_esc(cat)}</td><td>{_esc(prime_name)}</td></tr>"
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
    """Render Cleanup — Excess sub-view (blueprints/components with all paths covered)."""
    parts = []

    if excess_items:
        rows = "".join(
            f"<tr><td>{_esc(e.name)}</td><td>{_esc(e.item_type)}</td>"
            f'<td class="num">{e.owned}</td>'
            f'<td class="num">{e.sell}</td>'
            f"<td>{_esc(e.builds)}</td></tr>"
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
