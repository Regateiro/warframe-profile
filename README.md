# Warframe Profile

A command-line toolkit for analyzing your Warframe prime-part inventory.
Cross-reference your in-game inventory against the official item database to
decide what to build, what to sell for ducats at Baro Ki'Teer, and what
materials you still need to farm.

## Features

| Command | Description |
|---|---|
| `--ducats` | Prime-part / ducats analysis — shows owned, buildable, and partial items with surplus ducat values |
| `--craft`  | Interactive crafting-tree browser — resolves full dependency trees for any item |
| `--cleanup` | Inventory cleanup — lists weapons safe to sell (re-buyable or replaced by a Prime variant) |
| `--foundry` | Export your inventory in the import format accepted by [Warframe Foundry](https://warframe-foundry.app/) |
| `--update` | Download and merge the latest DE export + WFCD item database |

Additional highlights:

- **Live inventory scanning** — reads the Warframe process memory to discover
  your account credentials and fetches the official DE inventory API
  (no manual file exports needed).
- **Offline cache** — inventory and item databases are cached locally after
  the first fetch.
- **Weapon chain analysis** — traces full crafting upgrade chains (e.g.
  `MK1-Braton → Braton → Braton Prime`) and shows what you need to farm.
- **Colour-coded output** — green / yellow / red terminal output for crafting
  tree browser.

## Requirements

- Python 3.10+
- Linux (uses `/proc/<pid>/mem` for process memory scanning)
- Warframe must be running and in the Orbiter for live inventory fetching

## Installation

```bash
git clone <repo-url> warframe-profile
cd warframe-profile
pip install prettytable
```

## Usage

All sub-commands are accessed through the unified `warframe.py` entrypoint
(or via `python -m warframe_profile`).

### Update the item database

This must be run first to download and merge the game data:

```bash
python warframe.py --update
```

Downloads ~40 JSON files from Digital Extremes' public export and the
WFCD community item database, merging them into `data/export_db.json`.

### Prime-part / ducats analysis

```bash
# With live inventory scan (Warframe must be running):
python warframe.py --ducats --refresh

# With a cached inventory file:
python warframe.py --ducats --inventory path/to/inventory.json

# Refresh the items database first:
python warframe.py --ducats --refresh-items

# Show only the sell-for-ducats list:
python warframe.py --ducats --sell-only
```

The analysis shows:

- **Missing Prime items** — masterable Primes where you own zero parts.
- **Excess Prime parts** — per-item breakdown of missing and spare components.
- **Ducat summary** — total ducats from surplus parts and from parts you
  need to keep for builds.

### Crafting tree browser

```bash
# Look up a specific item:
python warframe.py --craft "Paracesis"
python warframe.py --craft "Acceltra Prime" --depth 3

# Interactive mode (search and pick from multiple matches):
python warframe.py --craft --interactive

# Analyze all weapon upgrade chains:
python warframe.py --craft --weapon-chain

# Refresh inventory:
python warframe.py --craft --refresh
```

The tree uses colour-coded output:

- <span style="color:green">**Green**</span> — you have enough.
- <span style="color:yellow">**Yellow**</span> — you have some but not enough.
- <span style="color:red">**Red**</span> — you have none.

### Inventory cleanup

```bash
python warframe.py --cleanup [--refresh] [--refresh-items]
```

Shows two sections:

1. **Market Re-Buyable Equipment** — weapons you can sell because they
   can be re-purchased from the in-game market for credits.
2. **Items to Sell (Own Prime Variant)** — non-Prime weapons whose Prime
   variant you already own.

### Export for Warframe Foundry

```bash
# Write data/warframe-foundry.json from the cached inventory:
python warframe.py foundry

# Live inventory scan (Warframe must be running):
python warframe.py --refresh foundry

# Write to a custom path:
python warframe.py foundry --output /path/to/warframe-foundry.json

# Add Steel Path progress (not tracked in the inventory export):
# By XP total (Steel Path nodes/junctions award the same ~63/1000 mastery
# as the normal chart, so the single number the game exposes is enough):
python warframe.py foundry --steel-xp 21455
# Or by explicit counts:
python warframe.py foundry --steel-missions 232 --steel-junctions 13
```

Produces a `warframe-foundry.json` file in the exact import format accepted
by [Warframe Foundry](https://warframe-foundry.app/).  It encodes:

- **Items** — every owned piece of equipment marked `built`, every item
  whose total XP reaches its single-pass max (rank 30: 900,000 for
  warframes/archwings/companions, 450,000 for weapons; rank 40: 1,600,000
  for necramechs, 800,000 for Kuva/Tenet/Coda/Paracesis) marked `mastered`,
  and owned blueprints / parts as per-item component counts.  Partially
  ranked items (e.g. a necramech with 460k XP) are *not* marked mastered.
- **Resources** — owned material counts (Orokin Cell, Plastids, etc.).
- **Settings** — mission / junction / intrinsics counts, auto-derived from
  the inventory (`Missions` node tags and `PlayerSkills` intrinsics), so the
  Foundry's Mastery Rank matches your real progress.  Steel Path counts
  aren't exposed in the inventory export; pass `--steel-xp` with your total
  Steel Path mastery gained and it is split into steel mission / junction
  counts automatically (or set them directly with `--steel-missions` /
  `--steel-junctions`; `--missions`, `--junctions`, `--intrinsics` override
  any of the auto-derived values).

After exporting, the command prints an estimate of the Mastery Rank the
Foundry will display.  Open warframe-foundry.app, press **Import Data**, and
select the file.  The web UI (`python warframe.py serve`) also offers a
**Export for Warframe Foundry** button on the home page that downloads the
same file.

## Data sources

- **DE Public Export**: [`https://browse.wf/warframe-public-export-plus/`](https://browse.wf/warframe-public-export-plus/)
- **WFCD Items**: [`https://github.com/WFCD/warframe-items`](https://github.com/WFCD/warframe-items)

The `--update` command downloads both sources and merges them into
`data/export_db.json`.  The merge enriches DE items with fields from
WFCD (e.g., `isPrime`, `marketCost`, `components` with ducat values)
and adds WFCD-only Prime items not present in the DE export.

## Project structure

```
warframe-profile/
  warframe.py                                # Unified entrypoint
  warframe_profile/
    __init__.py                              # Package init, DATA_DIR
    __main__.py                              # Sub-command dispatcher (--craft/--ducats/...)
    model/                                   # Data layer: inventory, export DB, analysis
      inventory.py                           # Memory scanning, API fetching, shared helpers
      analysis.py                            # Prime-map indexing & cross-reference analysis
      craft_model.py                         # Item/recipe indices for the craft tree
      foundry_export.py                      # Warframe Foundry import-file builder
      ...
    presenter/                               # CLI + web sub-command entry points
      cli.py                                 # --ducats / --relics
      craft_tree.py                          # --craft
      inventory_cleanup.py                   # --cleanup
      mastery.py                             # --mastery
      missing_mods.py                        # --mods
      foundry_export.py                      # --foundry
      web_server.py                          # --serve
    view/                                    # Output rendering (tables / HTML)
      report.py                              # prettytable report formatting
      web/templates.py                       # Web UI HTML templates
    scripts/
      update_export_db.py                    # --update: download & merge export data
  data/
    export_db.json                           # Merged item database (generated)
    inventory.json                           # Cached inventory (generated)
    warframe-foundry.json                    # Foundry import file (generated by `foundry`)
```
