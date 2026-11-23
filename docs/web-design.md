# Warframe Profile — Web UI Design

**Status:** Current — maintained design reference  
**Scope:** The `serve` web UI plus the shared MVP model/presenter/view boundaries
behind every table and subtab.  
**Audience:** Maintainers and reviewers. This document describes current
behaviour; it is not a roadmap.

### Current implementation status

- Model: `ItemData`, `Inventory`, prime/relic/cleanup analysis, mastery and mod
  report models. Model modules do not import presenters or views.
- Presenter: sub-command modules load data, call model methods, and delegate
  rendering; the web server owns HTTP routing and view-model preparation.
- View: passive renderers only; they consume report/view dataclasses and craft
  trees and never read `ItemData` or `Inventory`.
- CI runs Ruff, Pyright, and pytest; tagged builds publish Linux and Windows
  executables with SHA-256 checksums.

> Named items in examples are illustrative only. Inventory-dependent lists
> change as items are built, mastered, or sold; do not treat a specific item
> as permanent expected state.

---

## 1. Architecture at a glance

```
Browser
  │  GET /… , POST /refresh | /refresh-items
  ▼
WarframeHandler (http.server, threaded)
  │  validates POST origin, routes path
  ▼
DataCache (process singleton)
  │  ItemData + Inventory
  ▼
Model (data + logic)                 Presenters (orchestration)   View (passive)
  ItemData  — items/recipes/locale     cli.py                       report.py
  Inventory — owned/finished/          craft_tree.py                mastery.py
              crafting/mastered/       inventory_cleanup.py         mods.py
              relics/mods/analyses     mastery.py                   web/templates.py
  mastery.py / mods.py                 missing_mods.py
  analysis.py / craft_model.py         web_server.py
```

Key points:

- The UI is **server-rendered HTML**; there is no client-side framework or API.
- **`ItemData`** is the only entry point for item, recipe, locale, and index
  access. **`Inventory`** is the only entry point for player data.
- “Owned” has one definition: present in the inventory, mastered/finished, or
  currently in the foundry. `Inventory.is_owned()` is that rule; callers that
  specifically need a completed copy use `is_finished()`.
- MVP boundaries are enforced by imports: `view/*` only receives report/view
  dataclasses and never reads `ItemData`/`Inventory`; presenters load data,
  call model methods, and delegate rendering; model modules never import the
  presenter or view layers.
- The item database and inventory are loaded once at startup into `DataCache`.
- Expensive analyses live on `Inventory` (`analysis`, `needed_drops`,
  `relic_info`, `mastery_report`, `mods_report`, …) and are computed lazily
  and cached until the inventory object is replaced.
- All game-data text is HTML-escaped before interpolation.
- The server binds to `127.0.0.1` by default and has no authentication.

### Startup

`warframe.py serve` loads:

1. The merged item export (`export_db.json`) into `ItemData`, which owns all
   item/recipe/locale indices (`items_by_un`, `recipes_by_result`, `by_name`,
   Prime map, …).
2. The inventory (`inventory.json`, or a live scan when `--refresh` is used)
   into `Inventory`, which owns owned counts, foundry results, finished/
   mastered state, relics, mods, and the lazy analysis properties.

### Refresh lifecycle

| Action | What happens |
|---|---|
| **Refresh Inventory** (`POST /refresh`) | Re-scans/fetches the inventory and replaces the `Inventory` object, atomically dropping every cached analysis; redirects back to the current page. Success shows `notice=…`; failure shows `refresh_error=…` and keeps cached data. |
| **Refresh Item DB** (`POST /refresh-items`) | Runs the `update` download/merge, reloads the `ItemData` object, rebuilds `Inventory` against the new item database, and redirects back with a notice. A lock prevents concurrent database refreshes. |

Both POSTs reject cross-origin browser requests via an Origin/Referer check;
clients that send neither header (e.g. `curl`) are allowed.

### Shared page shell

- Dark GitHub-style theme.
- Top navigation: **Home, Ducats, Relics, Craft, Weapon Chains, Mastery, Mods,
  Cleanup**, plus the two refresh buttons.
- Pages with multiple views use a horizontal subtab bar.
- Query-string notifications (`notice`, `refresh_error`) render as a dismissible
  toast and are removed from the URL.
- `<details>` expansion state is saved in `sessionStorage` and restored after an
  inventory refresh, so expanded crafting trees stay open.

---

## 2. Route map

| Route | View |
|---|---|
| `/` | Home |
| `/ducats` | Ducats — sell list |
| `/relics` | Relics — **Safe to Spam** (default) |
| `/relics/age` | Relics — missing parts grouped by **Relic Age** |
| `/relics/age/<lith\|meso\|neo\|axi>` | One relic age |
| `/relics/rarity` | Relics — missing parts grouped by drop **Rarity** |
| `/relics/rarity/<common\|uncommon\|rare>` | One drop rarity |
| `/relics/needed/<category-slug>` | Needed drops for one item type |
| `/craft?q=…` | Craft search / results |
| `/weapon-chains` | Weapon upgrade chains |
| `/mastery` | Mastery — **Unmastered Owned Items** (default) |
| `/mastery/ready` | Mastery — Ready to Build |
| `/mastery/missing-parts` | Mastery — Missing Parts |
| `/mods` | Missing mods |
| `/cleanup` | Cleanup — **Re-Buyable** (default) |
| `/cleanup/sell` | Cleanup — Prime Variant |
| `/cleanup/excess` | Cleanup — Excess |

There is no legacy-URL compatibility layer: unsupported subpaths return **404**.
That includes removed URLs such as `/primes`, `/ducats/sell`, `/relics/safe`,
`/mastery/in-progress`, `/mastery/owned-blueprints`,
`/mastery/missing-blueprints`, and unknown relic category slugs. All links are
app-internal, so these paths are not needed.

Unexpected server exceptions return a generic 500 page (traceback stays in the
server console).

---

## 3. Home

**Route:** `/`  
**Data:** none.

Static list of tabs and a one-line description of each. No analysis is loaded.

---

## 4. Ducats

**Route:** `/ducats`  
**Subtabs:** none.  
**Data:** `AnalysisResult` from the Prime analysis (`analyze`).

### Display rules

- Only **surplus tradable Prime parts with a ducat value > 0** are shown.
- Rows are aggregated by `(item name, part name)`, though in practice each part
  maps to one Prime item.
- Columns:
  - **Part** — `<item> <part>` (avoids duplicating the item name).
  - **Qty** — summed surplus copies.
  - **Duc** — ducats per copy.
  - **Total** — `Qty × Duc`.
- Footer total is `AnalysisResult.total_ducats_safe`.
- Empty state: “Nothing to sell for ducats right now — surplus Prime parts will
  appear here.”

### Logic notes

- The underlying Prime analysis excludes cosmetic items and items whose status
  is `crafting`.
- Items currently in the foundry are status `crafting`; their parts and consumed
  blueprint are committed, so they do not contribute surplus.
- The former Missing / Excess / Ready-to-Build web subviews were removed. The
  CLI `ducats` command still prints the full report.

---

## 5. Relics — Needed Part Overviews

**Subtabs:** By Relic Age · By Rarity, under the Relics tab.  
**Data:** `NeededPart` list (`build_needed_drops`).

### Display rules

- These views show only the Prime parts that are still missing.
- Columns:
  - **Part** — WFCD-style reward name (e.g. `Ash Prime Chassis Blueprint`).
  - **Item** — parent Prime item.
  - **Type** — parent item category.
  - **Need** — missing copies.
  - **Duc** — ducats per copy.
  - **Owned Relic Drops** — owned relics that can drop the part, as
    `relic name ×count`, or `—` when no owned relic drops it.
- Within a group, rows are sorted by item name, then part name.
- The header line reports the **unique** number of missing parts and how many
  can drop from an owned relic.
- Empty state: “No Prime parts are missing — you have every part needed for
  the Prime items in your crafting plans.”

### 5.1 By Relic Age

**Paths:** `/relics/age`, `/relics/age/<lith|meso|neo|axi>`

- A second-level tab bar provides **All**, **Lith**, **Meso**, **Neo**, and
  **Axi**; the single-age views filter the table to that age.
- Parts are grouped under **Lith**, **Meso**, **Neo**, and **Axi** headings,
  with `Unknown age` for drops whose tier is unknown.
- A part is repeated under every owned-relic age that can drop it. Its
  **Owned Relic Drops** cell is filtered to that age, so the row answers
  “which of my Lith relics can drop this?” rather than repeating unrelated
  relics.
- Parts that no owned relic can drop are shown in a final
  `No owned relic drops` group; they are not repeated under every age.
- Single-age views show only parts with a drop in that age.

### 5.2 By Rarity

**Paths:** `/relics/rarity`, `/relics/rarity/<common|uncommon|rare>`

- A second-level tab bar provides **All**, **Common**, **Uncommon**, and
  **Rare**; the single-rarity views filter the table to that rarity.
- Parts are grouped under **Common**, **Uncommon**, and **Rare** headings,
  with `Unknown rarity` for rewards without a rarity value.
- The standard **Common / Uncommon / Rare** headings are always rendered, even
  when a rarity currently has no missing parts; empty groups show “No missing
  parts in this group.”
- A part is repeated under every drop rarity available from the player's
  relics; the **Owned Relic Drops** cell is filtered to that rarity.
- Parts that no owned relic can drop are shown in a final
  `No owned relic drops` group; they are not repeated under every rarity.
- Single-rarity views show only parts with a drop in that rarity.

### Logic notes

- This is the same data as the per-category **Relics → Needed Drops** views,
  presented as overview subtabs.
- A part only counts as missing when `missing > 0` and it has a ducat value,
  matching the relic logic (§6.2).
- Only **owned** relics are listed; the view is a “what can I open now”
  checklist, not a global relic drop table.
- Relic reward rarity is normalized from the reward chances in each relic:
  the three-slot chance tier is **Common**, the two-slot tier is **Uncommon**,
  and the single-slot tier is **Rare**. This corrects WFCD exports that label
  common slots as `Uncommon`; the stored label is used only when chance data is
  unavailable.

## 6. Relics

**Default:** Safe to Spam at `/relics`.  
**Subtabs:** Safe to Spam (default) · By Relic Age · By Rarity · one tab per
needed item category.  
**Data:** `RelicInfo` list (`build_relics_map`) and `NeededPart` list
(`build_needed_drops`).

### 6.1 Safe to Spam (default, first subtab)

**Path:** `/relics`

A relic is **safe to spam** when every Prime-part reward it can drop is already
covered:

- Only owned relics are considered. The inventory path must be an Intact
  projection (`/Projections/T…`); feature/Immortal/Requiem placeholders are
  ignored.
- For each owned relic, every reward is checked:
  - Non-Prime rewards (Forma, Kuva, Riven Sliver, etc.) are ignored.
  - A Prime part is “not needed” when owned copies already cover all required
    builds (`missing == 0`). This accounts for parts consumed by crafting.
  - Items and components currently in the foundry count as covered: a pending
    finished item satisfies one build, and a pending component satisfies one
    copy of that part.
  - Only parts with ducats > 0 participate.
- If no reward is still needed, the relic is safe.

**Table columns:** Relic, Tier, Count, Vaulted.  
**Empty state:** “No relics are safe to spam yet.”

Subtab bar: **Safe to Spam** first, then **By Relic Age** and **By Rarity**,
followed by one tab per item category that currently has needed parts.

### 6.2 Needed Drops by item type

**Paths:** `/relics/needed/<category-slug>`

Tab categories are generated from the categories present in the needed list,
ordered:

`Warframes → Primary → Secondary → Melee → Archwing → Sentinels → Arch-Gun`,
then any unknown categories alphabetically.

For each Prime item and each tradable part:

- Required copies = `part count × needed builds`, where needed builds is 1 for
  an unowned item plus any extra copies consumed by sub-crafts (akimbo variants).
- Built copies of a Prime component that are already finished are subtracted.
- A row is included only when `missing > 0` and the part has a ducat value.
- The **Owned Relic Drops** column lists only the player’s owned relics that can
  drop the part, as `relic name ×count`, or `—`.
- Reward-name matching handles the different in-game conventions:
  - Warframes/Archwings append `Blueprint` to component names.
  - Archwing display prefixes (`<ARCHWING>`) are stripped.
  - Weapon parts keep their bare reward name.

**Table columns:** Part, Item, Need, Duc, Owned Relic Drops.  
**Heading:** `<Category> — Needed Prime Parts`.  
**Empty state:** “No missing parts in this category.”

> This table is Prime-only by design: it answers “which owned relics help finish
> Prime items?”

---

## 7. Craft

**Route:** `/craft?q=<query>`

### Search

- No query → search form.
- Query → substring search over all game items, scored:
  - exact match (4), starts-with (3), word/hyphen match (2), loose substring (1).
  - Ties sort by shorter display name.
- The first **5** matches are rendered.
- Exact matches are expanded by default; other results are collapsed.
- No matches → form with an inline error.

### Result rendering

Each result is a `<details>` block containing:

1. **Crafting tree** — recursive tree to depth 3.
   - Component colours: green (enough owned), cyan (a required copy is in the
     foundry), yellow (some owned), red (none).
   - Blueprint rows are included even when the blueprint is already owned;
     the main blueprint is consumed once.
   - Craftable components expand only when not enough copies are owned.
2. **Crafting List** — intermediate items that must be built, with needed,
   owned, and craft count.
3. **Raw Materials** — aggregated materials with needed/owned and status.
4. **Farming List** — materials still short.

The crafting plan is produced by `resolve_tree` → `add_blueprint_requirement`
→ `postprocess_craft_plan`, so intermediate components are decomposed into raw
materials and blueprint entries are preserved.

Mastery rows link to this page with `/craft?q=<item name>`.

---

## 8. Weapon Chains

**Route:** `/weapon-chains`  
**Data:** `build_weapon_chains` (cached lazily).

### Chain discovery

1. Restrict to items whose category is a weapon category.
2. Build a graph from recipes where both the ingredient and result are weapons.
3. Start from roots that build into another weapon but are not themselves built
   from another weapon; follow the first successor to form a linear chain.
4. Keep chains of length ≥ 2 whose **final** weapon is neither owned nor
   mastered.
5. Resolve the final weapon’s crafting tree to depth 5 and aggregate
   requirements/craftables across all shown chains.

### Display

- Header: `N chains with unowned final weapons`.
- One `<details>` per chain:
  - Summary shows the full upgrade path (`A → B → C`).
  - Tree for the final weapon only.
  - Per-chain crafting summary.
- A final aggregated crafting/materials/farming summary covers all shown chains.
- Empty state: “No unowned weapon chains found!”

---

## 9. Mastery

**Default:** `/mastery` = **Unmastered Owned Items**.  
**Data:** `MasteryReport` from `build_mastery_report`.  
**Subtabs (in order):**

1. Unmastered Owned Items
2. Ready to Build
3. Missing Parts

Every table is driven by the **masterable item universe**, not by the Prime
index.

### 9.1 Masterable universe

- Items with `masterable: true`, plus the special Operator Amp barrel rule.
- Excludes a small unobtainable set (Excalibur Prime, Lato Prime, Skana Prime).

### 9.2 Per-item classification

The report combines XP history, ownership, and the crafting tree.

**Mastered**

- Ever leveled, and current XP ≥ the item’s expected maximum:
  - Necramechs: 1,600,000
  - Warframes / Archwings / Sentinels / pets: 900,000
  - Kuva/Tenet/Coda weapons: 800,000
  - Everything else: 450,000
- Modular items also use a feature check for gilding/progress where available.

**Unmastered Owned Items**

- Owned, but not mastered.
- Includes owned items at **0 XP** and partially leveled items.
- Ownership comes from equipment/modular/pet/vehicle sections, pending foundry
  results, and assembled `ModularParts`.
- `MiscItems` is deliberately excluded: a loose modular component (e.g. an
  unassembled K-Drive deck) does not make the masterable item owned.
- Modular component items (K-Drive/Zaw/Kitgun components, Amp parts, pet parts)
  count as owned only when an assembled item references them in
  `ModularParts`.

**Unowned items** are split using the full crafting plan:

- **Ready to Build** — a recipe exists and every blueprint + material in the
  whole tree is owned.
- **Missing Parts** — everything not ready to build. This covers both items
  with all required blueprints owned but at least one missing material, and
  items with at least one required blueprint missing (or no recipe).

Modular component items are not evaluated for Ready to Build, because their
recipe builds the component rather than assembling the finished modular item;
they are classified by whether their own blueprint is owned, and appear under
Missing Parts unless ready to build.

The readiness plan uses `resolve_tree` (depth 5) +
`add_blueprint_requirement` + `postprocess_craft_plan`, so it includes
component blueprints and decomposes intermediate components into materials.

### 9.3 Report sections and ordering

| Section | Grouping | Sort |
|---|---|---|
| Unmastered Owned Items | Farming tier groups | Tier, then name |
| Ready to Build | Flat | Name |
| Missing Parts | Farming tier groups | Tier, then name |

Every item row has **Item, Category, MR, Source**.  
“MR” is the item’s mastery-point contribution, not the required mastery rank.
“Source” is the acquisition hint.

### 9.4 Web presentation

- The subtab bar is shown first; the progression summary sits **below** it.
- Summary shows: masterable items mastered, unmastered owned, ready to build,
  missing parts, remaining, and total MR available.
- Ready to Build and Missing Parts rows link to the Craft tab.
- Empty states:
  - Ready to Build has a dedicated message.
  - Other empty sections say “No items in this section.”

---

## 10. Mods

**Route:** `/mods`  
**Data:** `ModsReport` from `build_mods_report`.

### Logic

- Consider only items in the `Mods` category.
- Owned mods come from `RawUpgrades`, `Upgrades`, and `FocusUpgrades`, filtered
  to known game mods.
- Duplicate names are collapsed; a mod counts as owned if **any** path variant
  is owned.
- Excluded types: Rivens, Focus Ways, Transmutation mods.
- WFCD mod type is mapped to an in-game category (Warframe, Aura, Augment,
  Exilus, Primary, Secondary, Melee, Companion Beast/Robotic, etc.).
- Missing mods are grouped by category and sorted by name.
- Acquisition hints come from the cached WFCD mod-drop table.

### Display

- Summary: owned unique mods, total owned copies, total missing.
- Category summary table, sorted by missing count descending.
- One expandable `<details>` per category with **Mod / Rarity / Source**.
- Empty state: “You have all mods!”

---

## 11. Cleanup

**Subtabs:** Re-Buyable (default) · Prime Variant · Excess.

### 11.1 Re-Buyable

**Path:** `/cleanup`

- Build a market map of weapons that can be re-bought for credits:
  - `MK1-` weapons: direct purchase.
  - Weapons with market and blueprint credit costs: blueprint purchase.
- Exclude weapons that are ingredients for a recipe whose result is not already
  owned (they may be needed for future crafting).
- Display owned equipment that is market-re-buyable and not excluded.

**Columns:** Name, Category, Buy Type, Rarest Material.  
**Empty state:** “Nothing to clean up!”

### 11.2 Prime Variant

**Path:** `/cleanup/sell`

- Find owned non-Prime equipment.
- Match each to a Prime counterpart by name/category (exact or unique prefix).
- Keep only pairs where the Prime variant is also owned.

**Columns:** Item, Category, Prime Variant.  
**Empty state:** “Nothing to clean up!”

### 11.3 Excess

**Path:** `/cleanup/excess`

- Consider blueprints in `Recipes` and components in `MiscItems`.
- An item is excess when **every crafting path** from it ultimately leads to an
  item that is already owned or mastered.
- The whole owned stack is marked as sellable.

**Columns:** Name, Type, Owned, Sell, Crafts Into.  
**Empty state:** “No excess items found!”

---

## 12. Shared model rules worth validating

These rules affect more than one tab.

### Ownership and “finished” items

- `Inventory.owned` counts every inventory item plus foundry results.
- Blueprints consumed by a pending recipe are subtracted from the blueprint
  count, preventing the same build from being counted twice.
- `Inventory.is_owned(path)` is the universal ownership rule: inventory copy,
  finished/mastered item, **or** foundry result.
- `Inventory.finished` contains equipment plus mastered items and excludes
  foundry results. Ducat/surplus logic uses `is_finished`; missing-part and
  relic logic use `is_owned`, so an item or component being crafted is not
  reported as missing.

### Ducats

- A part is safe to sell only when `surplus > 0` and `ducats > 0`.
- Surplus is calculated against the number of copies required for all builds,
  including extra copies consumed by sub-crafts.

### Relics

- Relic drop lists show **owned relics only**.
- Safe-to-spam ignores non-Prime rewards and treats a reward as unneeded when
  owned copies cover every required build.

### Mastery

- “In Progress” is now **Unmastered Owned Items**: ownership matters more than
  whether XP has been earned.
- “Ready to Build” means the complete blueprint + material tree is owned; it is
  not limited to Primes.
- “Missing Parts” combines the former Owned Blueprints and Missing Blueprints
  groups: every item that is not ready to build.
- An item being crafted is owned (pending result), so it belongs in Unmastered
  Owned Items, not Ready to Build.

### Ordering

- Tables are sorted deterministically:
  - Ducats: item name, then part name.
  - Relics overviews: By Relic Age in Lith → Meso → Neo → Axi order; By
    Rarity in Common → Uncommon → Rare order; item/part within each group.
    Parts with no owned relic appear only in the final `No owned relic drops`
    group of an overview, never in a single age/rarity filter.
  - Relics categories: needed rows by item/part; categories by the fixed order.
  - Mastery: tier/name or name as listed in §9.3.
  - Mods: category by missing count; mods by name.
  - Cleanup: category/name (re-buyable, prime variant); name (excess).

---

## 13. Known limitations / design decisions

Open questions for the reviewer:

1. **Ducats web view is sell-only.** Missing/Excess/Ready views exist only in
   the CLI now.
2. **Relic drops are owned-only.** There is no “where can I farm this part”
   view across all relics.
3. **Mastery readiness depth is 5.** A deeper recipe chain could theoretically
   be misclassified as not ready.
4. **Items without recipes** always fall into Missing Parts, even if they
   are acquired through other means (adversaries, drops, vendors).
5. **Weapon chains are linear.** Only the first successor is followed, and
   cycles are capped at 20 links.
6. **Cleanup Excess sells the entire stack.** It does not reserve copies for
   unowned future crafts if all paths are already covered.
7. **Modular assembly is not modeled.** Loose K-Drive/Kitgun/Zaw/Amp/pet
   components are not treated as owned; those masterable items are classified
   by their own blueprint until an assembled item references the component.
8. **Re-Buyable** is weapon-only and does not consider mastered-but-sold status
   when deciding whether an ingredient is still needed.
9. **No authentication.** Default bind is localhost; exposing on a LAN is
   explicitly opt-in.
10. **Credential discovery depends on the game heap.** The scanner retries,
    scans whole regions as a fallback, and tries multiple nonce candidates,
    but a game update can still change the credential layout. Errors are
    reported by stage rather than exposing credential values.

---

## 14. Review checklist

- [ ] Every top-level tab and subtab has the expected default and order.
- [ ] Unsupported/removed paths return 404 (no legacy redirects).
- [ ] Refresh Inventory succeeds with a success toast and preserves the current
      page; failure keeps cached data and shows an error toast.
- [ ] Refresh Item DB reloads indices and clears derived caches.
- [ ] Ducats lists only surplus parts with ducats and shows the expected total.
- [ ] Relics includes By Relic Age and By Rarity subtabs with All plus
      Lith/Meso/Neo/Axi and Common/Uncommon/Rare second-level filters.
- [ ] Rarity always shows Common, and each group's drop list is filtered to
      that group.
- [ ] The removed `/primes` URL returns 404 and no Primes nav tab is shown.
- [ ] A Prime item (or component) in the foundry is not listed as a missing
      part, and its relics can become safe to spam.
- [ ] Safe-to-Spam is first/default, and needed categories match the fixed order.
- [ ] Warframe/Archwing relic reward names include the `Blueprint` suffix.
- [ ] Craft exact matches open automatically; other matches stay collapsed.
- [ ] Mastery tables contain masterable items only (not Prime-only).
- [ ] An unowned masterable item with every blueprint and material appears
      under Ready to Build, and leaves that tab once it is owned or mastered.
- [ ] Owned 0-XP items appear under Unmastered Owned Items.
- [ ] Ready-to-build items are excluded from Missing Parts.
- [ ] Mods excludes Rivens, Focus Ways, and Transmutation mods.
- [ ] Cleanup subtabs show the documented columns and empty states.
