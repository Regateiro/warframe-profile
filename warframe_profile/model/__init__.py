"""Model layer — data fetching, analysis, and game-domain logic.

Sub-packages:
    inventory.py   — Warframe process memory scanning, DE API fetching, and
                     cached inventory loading.
    data.py        — ItemData + Inventory: the central item/player data access
                     classes and the single definition of ownership.
    analysis.py    — Prime-item indexing, cross-reference analysis, relic
                     analysis, and inventory-cleanup logic.
    craft_model.py — Crafting-tree resolution, recipe expansion, and weapon-
                     chain analysis.
    utils.py       — Shared helpers (path normalisation).
"""
