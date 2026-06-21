"""Warframe process memory scanning and API inventory fetching.

This module is the **data source** for the entire application.  It provides:

1. Memory scanning of the running Warframe Linux process to extract the
   player's ``accountId`` and ``nonce`` (used for API authentication).
2. HTTP fetching of the DE inventory API using those credentials.
3. Cached loading / saving of inventory JSON to disk.
4. Utility functions (:func:`build_owned`, :func:`build_mastered_set`, etc.)
   that transform raw API responses into lookup-friendly data structures.

Two data-fetching strategies are supported:

* **Live scan** — reads the running Warframe process's memory via
  ``/proc/<pid>/mem`` to discover the player's ``accountId`` and
  ``nonce``, then calls the official DE inventory API.
* **Cached** — loads a previously saved ``inventory.json`` file.

Shared helpers (:func:`build_owned`, :func:`load_items`,
:func:`load_inventory_with_fallback`) are used by multiple sub-commands
to avoid duplicating the fetch / cache logic.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict

from warframe_profile.model.utils import normalize_path

#: DE inventory API endpoint.  Format with ``accountId`` and ``nonce``.
INVENTORY_URL = "https://api.warframe.com/api/inventory.php?accountId={}&nonce={}&ct=STM"

#: Inventory sections that contain equipment (weapons, warframes, sentinels, etc.).
#: These are the top-level keys in the DE inventory API response that hold
#: owned equipment items.  Used by ``build_owned`` and analysis functions.
EQUIPMENT_SECTIONS: list[str] = [
    "Suits",
    "LongGuns",
    "Pistols",
    "Melee",
    "Sentinels",
    "SentinelWeapons",
    "SpaceSuits",
    "SpaceGuns",
    "SpaceMelee",
]


# ---------------------------------------------------------------------------
# Custom exceptions — each maps to a distinct failure mode so callers can
# catch exactly what they need.
# ---------------------------------------------------------------------------


class WarframeNotRunningError(RuntimeError):
    """Raised when no Warframe process can be found in ``/proc``."""


class InventoryFetchError(RuntimeError):
    """Raised when the DE inventory API returns an error or is unreachable."""


class ItemCacheNotFoundError(FileNotFoundError):
    """Raised when the local item export database does not exist."""


class ProfileNotFoundError(RuntimeError):
    """Raised when the DE profile viewing API returns no data."""


# ---------------------------------------------------------------------------
# Generic HTTP helper — used by inventory fetching, profile fetching, and
# the ``--update`` script to download data from DE and WFCD servers.
# ---------------------------------------------------------------------------


def fetch_json(url: str) -> dict | list:
    """Download *url* and deserialise the JSON response.

    Raises:
        InventoryFetchError: on HTTP or network errors.
    """
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise InventoryFetchError(f"HTTP {e.code} fetching {url}") from e
    except Exception as e:
        raise InventoryFetchError(f"Error fetching {url}: {e}") from e


# ---------------------------------------------------------------------------
# Export database — single-load data layer
# ---------------------------------------------------------------------------


class ExportDB:
    """Single-load cache for the merged export database (``export_db.json``).

    Opens and decodes the file exactly once on :meth:`load`, then exposes
    sub-sections as properties without re-reading.  This is the primary
    data source for all game item information — warframes, weapons, relics,
    resources, recipes, and the language dictionary.

    Sub-sections (``items``, ``recipes``, ``locale``, ``raw``) are accessed
    via properties so that callers never touch the raw JSON directly.
    """

    def __init__(self, path: str) -> None:
        """Store the file path; data is loaded lazily on first call."""
        self._path = path
        self._data: dict | None = None

    def load(self, refresh: bool = False) -> None:
        """Load (or reload) the database from disk.

        If *refresh* is ``True`` the cached file is removed first so the
        next ``--update`` run produces fresh data.
        """
        if refresh and self._path and os.path.exists(self._path):
            os.remove(self._path)
        if not self._path or not os.path.exists(self._path):
            raise ItemCacheNotFoundError(
                f"Item cache not found at {self._path}. Run --update first."
            )
        with open(self._path) as f:
            self._data = json.load(f)

    @property
    def items(self) -> list[dict]:
        """The merged item list (``data["items"]``).

        Every masterable, tradable, and cosmetic item in the game, enriched
        with WFCD fields (``isPrime``, ``category``, etc.).
        """
        if self._data is None:
            return []
        if isinstance(self._data, dict):
            data = self._data.get("items", self._data)
            return data if isinstance(data, list) else []
        return []

    @property
    def recipes(self) -> dict:
        """The recipe index (``data.get("recipes", {})``).

        Maps recipe uniqueNames (like
        ``/Lotus/Recipes/Weapons/LatoPrimeBlueprint``) to their ingredient
        lists and metadata.
        """
        if isinstance(self._data, dict):
            return self._data.get("recipes", {})
        return {}

    @property
    def locale(self) -> dict:
        """The language dictionary (``data.get("dict", {})``).

        Maps ``/Lotus/Language/...`` keys to human-readable display names.
        """
        if isinstance(self._data, dict):
            return self._data.get("dict", {})
        return {}

    @property
    def raw(self) -> dict:
        """The full decoded dict — returned as-is for callers that need
        the complete data blob (e.g. ``craft_model.build_items_by_un``)."""
        return self._data or {}


# ---------------------------------------------------------------------------
# Warframe process memory scanning
# ---------------------------------------------------------------------------
# Live inventory fetching requires authentication credentials (accountId and
# nonce) that the game stores in its process memory.  We scan /proc/<pid>/mem
# to find them — this is the same technique used by the Warframe community
# tools like "Inventory Inspector" and "AlecaFrame".
#
# Flow:
#   1. find_warframe_pid() — locate the process by scanning /proc.
#   2. find_in_memory() — binary search of the address space for an ASCII key.
#   3. _find_credentials_in_memory() — extract accountId + nonce from matches.
#   4. fetch_inventory() — use credentials to call the DE API.


def find_warframe_pid() -> int:
    """Return the PID of the running Warframe process.

    Scans ``/proc/<pid>/cmdline`` for ``Warframe.x64.exe``.
    Non-digit /proc entries are skipped (kernel threads, etc.).

    Raises:
        WarframeNotRunningError: if no matching process is found.
    """
    for pid_str in os.listdir("/proc"):
        if not pid_str.isdigit():
            continue
        try:
            with open(f"/proc/{pid_str}/cmdline") as f:
                cmd = f.read()
            if "Warframe.x64.exe" in cmd:
                return int(pid_str)
        except OSError:
            pass
    raise WarframeNotRunningError(
        "Warframe process not found. Launch the game and reach the Orbiter."
    )


def find_in_memory(
    pid: int,
    pattern: str,
    context: int = 256,
    max_per_region: int = 64 * 1024 * 1024,
) -> tuple[int | None, bytes | None]:
    """Search a process's address space for an ASCII *pattern*.

    Works by reading ``/proc/<pid>/maps`` to enumerate memory regions,
    then scanning each readable region in ``/proc/<pid>/mem``.
    Large anonymous (heap) regions are clamped to *max_per_region*;
    file-backed regions are clamped to 8 MiB — this avoids pathological
    scan times while still finding the credential strings.

    Args:
        pid: Target process ID.
        pattern: ASCII string to locate.
        context: Number of extra bytes to return around the match.
        max_per_region: Maximum bytes to scan per memory region.

    Returns:
        ``(address, surrounding_bytes)`` or ``(None, None)``.
    """
    p = pattern.encode("ascii")
    with open(f"/proc/{pid}/maps") as f:
        regions: list[list[str]] = [line.split() for line in f]

    for parts in regions:
        perms = parts[1]
        if "r" not in perms:
            continue
        start_str, end_str = parts[0].split("-")
        start = int(start_str, 16)
        end = int(end_str, 16)

        # Anonymous (heap) regions are scanned up to max_per_region;
        # file-backed regions are limited to 8 MiB.
        is_anon = len(parts) < 6 or parts[-1].startswith("[") or parts[-1] == ""
        limit = max_per_region if is_anon else min(end - start, 8 * 1024 * 1024)
        chunk_size = min(end - start, limit)

        try:
            with open(f"/proc/{pid}/mem", "rb") as mem:
                mem.seek(start)
                data = mem.read(chunk_size)
                idx = data.find(p)
                if idx != -1:
                    lo = max(0, idx - context)
                    hi = min(len(data), idx + context)
                    return start + idx, data[lo:hi]
        except (OSError, PermissionError):
            pass
    return None, None


# ---------------------------------------------------------------------------
# Inventory fetching (live vs cached)
# ---------------------------------------------------------------------------
# The typical flow is:
#   1. Call load_inventory_with_fallback().
#   2. If no cached file or --refresh was passed, it calls fetch_inventory().
#   3. fetch_inventory() calls find_warframe_pid() → _find_credentials_in_memory()
#      → DE API → returns (inventory_dict, account_id).
#   4. The profile data is also fetched and merged.
#   5. The combined data is saved to disk for future use.


def _find_credentials_in_memory(pid: int) -> tuple[str, str]:
    """Scan Warframe process memory for accountId and nonce.

    Warframe stores a URL containing the account's API credentials in
    its heap memory.  We search for ``&nonce=`` and ``accountId=`` strings,
    then extract the actual values via regex.

    Raises:
        InventoryFetchError: if credentials cannot be found.
    """
    for pattern in ("&nonce=", "accountId="):
        addr, chunk = find_in_memory(pid, pattern)
        if addr is None:
            continue
        url_str = chunk.decode("ascii", errors="replace")
        m = re.search(r"accountId=([a-z0-9]+)&nonce=(\d+)", url_str)
        if m:
            return m.group(1), m.group(2)
    raise InventoryFetchError(
        "Could not find accountId/nonce in Warframe memory. Make sure you're in the Orbiter."
    )


def fetch_inventory() -> tuple[dict, str]:
    """Scan Warframe memory for credentials and fetch the live inventory.

    This is the main live-fetch pipeline:
    PID → credentials → DE API → parsed JSON.

    Returns:
        ``(inventory_dict, account_id)``.
    """
    pid = find_warframe_pid()
    account_id, nonce = _find_credentials_in_memory(pid)

    inv_url = INVENTORY_URL.format(account_id, nonce)
    req = urllib.request.Request(
        inv_url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; calamity-inc/Soup)"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    return json.loads(data), account_id


def load_inventory(path: str) -> dict:
    """Load a previously-cached inventory JSON file from disk.

    Used when a cached ``inventory.json`` exists and ``--refresh`` was
    not specified.
    """
    with open(path) as f:
        return json.load(f)


def fetch_de_profile(account_id: str) -> dict:
    """Fetch player profile data from Digital Extremes' profile viewer API.

    The profile API returns mastery rank, XP history (which items have been
    ranked up), and the player's display name.  This data is needed by the
    ``--mastery`` sub-command and to determine which items are "mastered"
    even after being sold.

    Returns:
        The first result dict from the API response.
    """
    url = f"http://content.warframe.com/dynamic/getProfileViewingData.php?playerId={account_id}"
    resp = fetch_json(url)
    if not isinstance(resp, dict):
        raise ProfileNotFoundError("Unexpected response format")
    results = resp.get("Results", [])
    if not results:
        raise ProfileNotFoundError("No profile data found")
    return results[0]


# ---------------------------------------------------------------------------
# Summary / aggregation helpers
# ---------------------------------------------------------------------------
# These functions transform the raw DE API inventory JSON into dicts and
# sets that the analysis layer can work with efficiently.


def build_mastered_set(inv: dict) -> set[str]:
    """Return normalised paths of every item the player has ever ranked up.

    Sources:
    1. ``XPInfo`` from the DE profile viewing API — items that contributed
       XP even if already sold (only present after a ``--refresh``).
    2. Any item with an ``XP`` field > 0 anywhere in the inventory.

    This set is used by the analysis layer to determine which items should
    be considered "owned" even if the physical copy was sold.

    Returns:
        A set of lower-cased item paths.
    """
    leveled: set[str] = set()

    # Source 1: XPInfo from profile (mastered-and-sold items).
    for entry in inv.get("XPInfo") or []:
        path = (entry.get("ItemType") or "").lower()
        if path:
            leveled.add(path)

    # Source 2: any item with XP > 0 anywhere in the inventory.
    # We walk the entire inventory recursively rather than just equipment
    # sections so that items stored outside the usual slots are still
    # caught.  Items with XP == 0 (freshly built, never equipped) are
    # intentionally excluded.
    def _walk(obj):
        if isinstance(obj, dict):
            if "ItemType" in obj and obj.get("XP"):
                leveled.add(obj["ItemType"].lower())
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for v in obj:
                _walk(v)

    _walk(inv)

    return leveled


def inventory_summary(inv: dict) -> tuple[int, int, int]:
    """Return ``(misc_count, recipe_count, equipment_count)``.

    Used only for the one-line summary printed after a ``--refresh``.
    """
    misc = len(inv.get("MiscItems", []))
    rec = len(inv.get("Recipes", []))
    equip = sum(len(inv.get(s, [])) for s in EQUIPMENT_SECTIONS)
    return misc, rec, equip


def parse_pending_recipes(
    inventory: dict,
    recipes: dict | None = None,
) -> defaultdict[str, int]:
    """Parse ``PendingRecipes`` from inventory — items currently in the foundry.

    Each pending recipe is an item being built.  When *recipes* is provided
    (the ``"recipes"`` section of ``export_db.json``) the function resolves
    each recipe key to its ``resultType`` (the item being produced).  As a
    fallback, the ``Blueprint`` suffix is stripped from the recipe key —
    this correctly handles component recipes even when the export DB is
    stale.

    Returns:
        A ``defaultdict[str, int]`` mapping normalised item path → quantity
        (typically 1 per pending slot).
    """
    pending: defaultdict[str, int] = defaultdict(int)
    for pr in inventory.get("PendingRecipes", []):
        recipe_key = pr.get("ItemType", "")
        if not recipe_key:
            continue
        if recipes:
            recipe = recipes.get(recipe_key)
            result_type = recipe.get("resultType", "") if recipe else ""
            if result_type:
                pending[normalize_path(result_type)] += 1
                continue
        # Fallback: strip "Blueprint" suffix — works for component recipes
        # (e.g., /.../KeratinosBladeBlueprint → /.../KeratinosBlade).
        if recipe_key.endswith("Blueprint"):
            stripped = recipe_key[: -len("Blueprint")]
            if stripped:
                pending[normalize_path(stripped)] += 1
    return pending


def build_owned(
    inventory: dict,
    recipes: dict | None = None,
) -> defaultdict[str, int]:
    """Build a flat count of every item owned across all inventory sections.

    Aggregates all items from:
    - MiscItems (resources, relics, mods, etc.)
    - Recipes (blueprints)
    - Equipment sections (Suits, LongGuns, Pistols, etc.)
    - PendingRecipes (items currently in the foundry, if *recipes* provided)

    The resulting dict is the primary input to every analysis function.

    Keys are normalised paths (see :func:`~warframe_profile.analysis.normalize_path`).

    Returns:
        A ``defaultdict[str, int]`` mapping item path → quantity owned.
    """
    owned: defaultdict[str, int] = defaultdict(int)
    for item in inventory.get("MiscItems", []):
        owned[normalize_path(item.get("ItemType", ""))] += item.get("ItemCount", 1)
    for bp in inventory.get("Recipes", []):
        owned[normalize_path(bp.get("ItemType", ""))] += bp.get("ItemCount", 1)
    for sect in EQUIPMENT_SECTIONS:
        for eq in inventory.get(sect, []):
            owned[normalize_path(eq.get("ItemType", ""))] += 1
    # Include items currently being crafted in the foundry.
    pending = parse_pending_recipes(inventory, recipes)
    for k, v in pending.items():
        owned[k] += v
    return owned


def merge_profile_data(inv: dict, profile: dict) -> None:
    """Merge DE profile viewing data into *inv*.

    Adds/updates:
    * ``PlayerLevel`` (Mastery Rank).
    * ``XPInfo`` — the canonical list of every item the player has ever
      ranked up, including items no longer owned.

    This data is needed by the ``--mastery`` sub-command to determine
    which items have been mastered vs never touched.
    """
    inv["PlayerLevel"] = profile.get("PlayerLevel", inv.get("PlayerLevel", 0))
    xp_info = profile.get("XPInfo", [])
    if xp_info:
        inv["XPInfo"] = xp_info


def load_inventory_with_fallback(
    inv_path: str,
    refresh: bool = False,
) -> tuple[dict, str | None]:
    """Return inventory — either freshly fetched or from the local cache.

    When *refresh* is ``True`` (or the cache file is missing) the
    function fetches live data from the Warframe process, also fetches
    the DE profile viewing data (for mastery tracking), merges them,
    saves, and prints a summary to stderr.  Otherwise the cached file
    is loaded.

    This is the main inventory loading entry point used by all presenters.

    Returns:
        ``(inventory_dict, account_id_or_None)``.
    """
    if refresh or not os.path.exists(inv_path):
        inv, account_id = fetch_inventory()
        try:
            profile = fetch_de_profile(account_id)
            merge_profile_data(inv, profile)
        except (ProfileNotFoundError, InventoryFetchError) as e:
            print(f"  Warning: could not fetch profile data: {e}", file=sys.stderr)
        with open(inv_path, "w") as f:
            json.dump(inv, f, indent=2)
        misc, rec, equip = inventory_summary(inv)
        print(
            f"  Inventory: {misc} items, {rec} recipes, {equip} equipment  (account: {account_id})",
            file=sys.stderr,
        )
        return inv, account_id
    return load_inventory(inv_path), None


def load_data(
    items_cache: str,
    refresh_items: bool = False,
    inventory_path: str | None = None,
    refresh: bool = False,
) -> tuple[ExportDB, dict]:
    """Load the export database and inventory in one call.

    Convenience wrapper shared by the ``--ducats`` / ``--relics`` /
    ``--cleanup`` entry points.  Exits the process on error.

    This is the standard "init" pattern for most sub-commands:
    1. Create ExportDB and load items.
    2. Load inventory (live or cached).
    3. Return both.

    Returns:
        ``(ExportDB, inventory_dict)``.
    """
    from warframe_profile import DATA_DIR

    db = ExportDB(items_cache)
    try:
        db.load(refresh=refresh_items)
    except ItemCacheNotFoundError as e:
        print(f"  Error: {e}", file=sys.stderr)
        sys.exit(1)

    inv_path = inventory_path or os.path.join(DATA_DIR, "inventory.json")
    try:
        inv, _account_id = load_inventory_with_fallback(
            inv_path,
            refresh=refresh or (not inventory_path and not os.path.exists(inv_path)),
        )
    except (WarframeNotRunningError, InventoryFetchError) as e:
        print(f"  Error: {e}", file=sys.stderr)
        sys.exit(1)

    return db, inv
