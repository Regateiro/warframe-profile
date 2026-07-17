"""Drop data fetcher for Warframe mod locations.

Fetches mod drop tables from WFCD/warframe-drop-data on GitHub and
caches them locally for use by the missing mods presenter.
"""

import json
import os
import sys
import urllib.request
from typing import Any

from warframe_profile import HTTP_TIMEOUT, USER_AGENT_COMMUNITY

#: URL for mod location data from WFCD drop data repository.
MOD_LOCATIONS_URL = (
    "https://raw.githubusercontent.com/WFCD/warframe-drop-data/"
    "main/data/modLocations.json"
)

#: Local cache path.
_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
_CACHE_PATH = os.path.join(_CACHE_DIR, "mod_drop_locations.json")


def load_mod_drops(force_refresh: bool = False) -> dict[str, list[dict[str, Any]]]:
    """Load mod drop location data.

    Fetches from WFCD GitHub repo and caches locally. Returns a dict
    mapping mod name → list of drop source dicts with keys:
    ``enemy``, ``rarity``, ``chance``.

    Args:
        force_refresh: If True, re-fetch even if cache exists.

    Returns:
        Dict mapping mod name → list of drop sources.
    """
    cache_path = os.path.abspath(_CACHE_PATH)

    # Use cache if it exists and is < 24 hours old
    if not force_refresh and os.path.exists(cache_path):
        age_hours = (os.path.getmtime(cache_path) - os.path.getmtime(cache_path)) / 3600
        if age_hours < 24:
            try:
                with open(cache_path) as f:
                    data = json.load(f)
                return _build_lookup(data)
            except (json.JSONDecodeError, OSError):
                pass

    # Fetch fresh data
    try:
        req = urllib.request.Request(
            MOD_LOCATIONS_URL,
            headers={"User-Agent": USER_AGENT_COMMUNITY},
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = json.loads(resp.read().decode())

        # Cache to disk
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(raw, f)

        return _build_lookup(raw)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Warning: could not fetch mod drop data: {exc}", file=sys.stderr)
        # Try stale cache
        if os.path.exists(cache_path):
            with open(cache_path) as f:
                data = json.load(f)
            return _build_lookup(data)
        return {}


def _build_lookup(raw: dict) -> dict[str, list[dict[str, Any]]]:
    """Convert raw WFCD format to a name→sources lookup."""
    lookup: dict[str, list[dict[str, Any]]] = {}
    for entry in raw.get("modLocations", []):
        mod_name = entry.get("modName", "")
        sources = []
        for enemy in entry.get("enemies", []):
            sources.append({
                "enemy": enemy.get("enemyName", "Unknown"),
                "rarity": enemy.get("rarity", ""),
                "chance": enemy.get("chance", 0),
            })
        if mod_name:
            lookup.setdefault(mod_name, []).extend(sources)
    return lookup


def get_best_source(
    mod_name: str,
    drop_data: dict[str, list[dict[str, Any]]],
) -> str | None:
    """Return a short description of the best drop source for a mod.

    Picks the source with the highest drop chance.
    """
    sources = drop_data.get(mod_name, [])
    if not sources:
        return None

    # Sort by chance descending
    best = max(sources, key=lambda s: s["chance"] or 0)

    # Summarise: if multiple enemies, group them
    if len(sources) <= 3:
        parts = []
        for s in sources:
            chance = s["chance"]
            chance_str = f" ({chance:.1f}%)" if chance else ""
            parts.append(f"{s['enemy']}{chance_str}")
        return ", ".join(parts)

    # Many sources — show top enemy + count
    enemy_count = len(set(s["enemy"] for s in sources))
    chance = best["chance"]
    chance_str = f" ({chance:.1f}%)" if chance else ""
    return f"{best['enemy']}{chance_str} + {enemy_count - 1} more"
