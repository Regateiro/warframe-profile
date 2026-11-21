"""Model layer — data fetching, analysis, and game-domain logic.

Sub-packages:
    inventory.py   — Warframe process memory scanning, DE API fetching, and
                     cached inventory loading.
    data.py        — ItemData + Inventory: the central item/player data access
                     classes and the single definition of ownership.
    analysis.py    — Prime-item indexing and ducat cross-reference analysis.
    relics.py      — Relic coverage and missing-Prime-part analysis.
    cleanup.py     — Excess-component and market-rebuyable cleanup analysis.
    craft_index.py — Item/recipe indices, name resolution, and search.
    craft_model.py — Craft-tree resolution and plan post-processing.
    weapon_chains.py — Weapon upgrade-chain discovery.
    mastery.py     — Mastery classification and report model.
    mods.py        — Missing-mod categorization and report model.
    utils.py       — Shared helpers (path normalisation).
"""
