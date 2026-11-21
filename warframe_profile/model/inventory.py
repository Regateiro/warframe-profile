"""Warframe process memory scanning and API inventory fetching.

This module is the **data source** for the entire application.  It provides:

1. Memory scanning of the running Warframe process (Linux ``/proc`` or
   the Windows Win32 API) to extract the player's ``accountId`` and
   ``nonce`` (used for API authentication).
2. HTTP fetching of the DE inventory API using those credentials.
3. Cached loading / saving of inventory JSON to disk.
4. Low-level load/save helpers used by :class:`~warframe_profile.model.data.Inventory`,
   which owns all ownership and mastery semantics.

Two data-fetching strategies are supported:

* **Live scan** — reads the running Warframe process's memory (via the
  platform backend in :mod:`warframe_profile.model.scanner`) to discover
  the player's ``accountId`` and ``nonce``, then calls the official DE
  inventory API.
* **Cached** — loads a previously saved ``inventory.json`` file.

The loader helpers (:func:`load_inventory`, :func:`load_inventory_with_fallback`,
:func:`load_data`) are shared by every presenter.  Player-data interpretation
lives in :class:`~warframe_profile.model.data.Inventory`.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import Counter

from warframe_profile import HTTP_TIMEOUT, USER_AGENT_DE
from warframe_profile.log import get_logger
from warframe_profile.model.errors import (
    InventoryFetchError,
    ItemCacheNotFoundError,
    ProfileNotFoundError,
)
from warframe_profile.model.scanner import find_warframe_pid, iter_in_memory
from warframe_profile.model.utils import atomic_write_json

#: DE inventory API endpoint.  Format with ``accountId`` and ``nonce``.
INVENTORY_URL = "https://api.warframe.com/api/inventory.php?accountId={}&nonce={}&ct=STM"

_log = get_logger("inventory")

#: DE profile viewing API endpoint.  Format with ``playerId``.
PROFILE_URL = "https://content.warframe.com/dynamic/getProfileViewingData.php?playerId={}"

#: Credentials are embedded in a URL such as
#: ``?accountId=<24 chars>&nonce=<alphanumeric>``.  The nonce was historically
#: numeric but may contain hex letters, so accept both forms and let the DE
#: API reject anything invalid.  Extra query parameters are tolerated in case
#: the client ever changes the URL layout.
_CREDENTIAL_RE = re.compile(r"accountId=([A-Za-z0-9]{24})[^\x00]{0,256}?[&?]nonce=([A-Za-z0-9_-]+)")
_NONCE_FIRST_RE = re.compile(
    r"nonce=([A-Za-z0-9_-]+)[^\x00]{0,256}?[&?]accountId=([A-Za-z0-9]{24})"
)

#: Retry settings used when the credential string is not in memory yet.
_CREDENTIAL_RETRY_ATTEMPTS = 3
_CREDENTIAL_RETRY_DELAY = 1.0


#: Inventory sections that contain equipment (weapons, warframes, necramechs,
#: sentinels, etc.).  These are the top-level keys in the DE inventory API
#: response that hold owned equipment items.  Used by ``Inventory`` and
#: analysis functions.
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
    "MechSuits",
]


# ---------------------------------------------------------------------------
# Custom exceptions are defined in ``model.errors`` and re-exported here so
# existing imports keep working.  Each maps to a distinct failure mode.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Generic HTTP helper — used by inventory fetching, profile fetching, and
# the ``update`` script to download data from DE and WFCD servers.
# ---------------------------------------------------------------------------


def fetch_json(url: str) -> dict | list:
    """Download *url* and deserialise the JSON response.

    Raises:
        InventoryFetchError: on HTTP, network, or decoding errors.
    """
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as r:
            payload = r.read()
    except urllib.error.HTTPError as e:
        raise InventoryFetchError(f"HTTP {e.code} fetching {url}") from e
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise InventoryFetchError(f"Error fetching {url}: {e}") from e
    try:
        return json.loads(payload)
    except json.JSONDecodeError as e:
        raise InventoryFetchError(f"Invalid JSON from {url}: {e}") from e


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
        next ``update`` run produces fresh data.  When the file is missing
        (including after a refresh), the ``update`` command runs
        automatically to download it.
        """
        if refresh and self._path and os.path.exists(self._path):
            try:
                os.remove(self._path)
            except OSError as exc:
                # Best-effort cleanup of a stale cache; existence is re-checked below.
                _log.warning("could not remove stale cache %s: %s", self._path, exc)
        if not self._path or not os.path.exists(self._path):
            self._download_missing_cache()
        try:
            self._data = self._read()
        except (OSError, json.JSONDecodeError) as exc:
            # A truncated/corrupt cache (e.g. from an interrupted write or an
            # older version) is not recoverable by the user without manual
            # cleanup.  Move it aside and fetch a fresh copy once.
            corrupt_path = f"{self._path}.corrupt"
            try:
                os.replace(self._path, corrupt_path)
            except OSError:
                pass
            _log.warning(
                "item cache at %s is unreadable (%s); re-downloading (old copy kept at %s)",
                self._path,
                exc,
                corrupt_path,
            )
            self._download_missing_cache()
            try:
                self._data = self._read()
            except (OSError, json.JSONDecodeError) as retry_exc:
                raise ItemCacheNotFoundError(
                    f"Could not read item cache at {self._path}: {retry_exc}. Run update first."
                ) from retry_exc

    def _read(self) -> dict:
        """Open and decode the database file (no existence checks)."""
        with open(self._path, encoding="utf-8") as f:
            return json.load(f)

    def _download_missing_cache(self) -> None:
        """Run the ``update`` command to create a missing export database."""
        if not self._path:
            raise ItemCacheNotFoundError("Item cache path is not set. Run update first.")

        _log.info("Item cache not found at %s. Downloading game data...", self._path)
        # Imported lazily to avoid a circular import (the update script imports
        # this module for inventory helpers).
        from types import SimpleNamespace

        from warframe_profile.scripts.update_export_db import main as update_main

        try:
            update_main(
                SimpleNamespace(
                    items_cache=self._path,
                    refresh_items=False,
                    refresh=False,
                )
            )
        except SystemExit as exc:
            raise ItemCacheNotFoundError(
                f"Could not create item cache at {self._path}. Run update first."
            ) from exc

        if not os.path.exists(self._path):
            raise ItemCacheNotFoundError(
                f"Could not create item cache at {self._path}. Run update first."
            )

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
# The platform-specific implementations live in ``model/scanner`` and are
# re-exported above (``find_warframe_pid``, ``find_in_memory``).  Live
# inventory fetching requires authentication credentials (accountId and
# nonce) that the game stores in its process memory — the same technique
# used by community tools like "Inventory Inspector" and "AlecaFrame".
#
# Flow:
#   1. find_warframe_pid() — locate the process (Linux /proc or Win32 API).
#   2. find_in_memory() — search the address space for an ASCII key.
#   3. _find_credentials_in_memory() — extract accountId + nonce from matches.
#   4. fetch_inventory() — use credentials to call the DE API.


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


def _extract_credentials(chunk: bytes) -> tuple[str, str] | None:
    """Return an ``(account_id, nonce)`` pair from a memory chunk, if present."""
    text = chunk.decode("ascii", errors="replace")
    match = _CREDENTIAL_RE.search(text)
    if match:
        return match.group(1), match.group(2)
    match = _NONCE_FIRST_RE.search(text)
    if match:
        return match.group(2), match.group(1)
    return None


def _find_credential_candidates(pid: int) -> list[tuple[str, str]]:
    """Return credential candidates ordered by how often they appear.

    The fast pass scans the usual heap range.  If nothing valid is found,
    a second pass scans whole regions in bounded chunks, which catches
    credentials that moved past the default per-region limit after a game
    update.
    """
    candidates: Counter[tuple[str, str]] = Counter()

    def collect(pattern: str, *, full: bool) -> None:
        kwargs = {"max_per_region": None} if full else {}
        for _address, chunk in iter_in_memory(pid, pattern, context=512, **kwargs):
            credentials = _extract_credentials(chunk)
            if credentials is not None:
                candidates[credentials] += 1

    # Account-first is the known layout and avoids the many unrelated
    # ``nonce=`` fragments in HTTP auth strings.
    collect("accountId=", full=False)
    if not candidates:
        collect("nonce=", full=False)
    if not candidates:
        collect("accountId=", full=True)
    if not candidates:
        collect("nonce=", full=True)

    return [pair for pair, _count in candidates.most_common()]


def _find_credentials_in_memory(pid: int) -> tuple[str, str]:
    """Scan Warframe process memory and return the most likely credentials.

    Raises:
        InventoryFetchError: if credentials cannot be found.
    """
    candidates = _find_credential_candidates(pid)
    if candidates:
        return candidates[0]
    raise InventoryFetchError(
        "Could not find accountId/nonce in Warframe memory. Make sure you're in the Orbiter."
    )


def _find_credentials_with_retry(pid: int) -> list[tuple[str, str]]:
    """Find credentials, retrying briefly while the game finishes a sync."""
    for attempt in range(_CREDENTIAL_RETRY_ATTEMPTS):
        candidates = _find_credential_candidates(pid)
        if candidates:
            return candidates
        if attempt < _CREDENTIAL_RETRY_ATTEMPTS - 1:
            _log.info(
                "Credentials not found yet (attempt %s/%s); retrying...",
                attempt + 1,
                _CREDENTIAL_RETRY_ATTEMPTS,
            )
            time.sleep(_CREDENTIAL_RETRY_DELAY)
    raise InventoryFetchError(
        "Could not find accountId/nonce in Warframe memory. "
        "Make sure you're in the Orbiter and try refresh again."
    )


class _NonceRejectedError(RuntimeError):
    """Internal signal that the API rejected a candidate nonce."""


def _fetch_inventory_with(account_id: str, nonce: str) -> dict:
    """Fetch the live inventory with one credential pair."""
    inv_url = INVENTORY_URL.format(account_id, nonce)
    req = urllib.request.Request(
        inv_url,
        headers={"User-Agent": USER_AGENT_DE},
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise _NonceRejectedError(str(exc)) from exc
        raise InventoryFetchError(f"HTTP {exc.code} fetching live inventory") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise InventoryFetchError(f"Could not fetch live inventory: {exc}") from exc
    try:
        return json.loads(data)
    except json.JSONDecodeError as exc:
        raise InventoryFetchError(f"Invalid JSON from live inventory: {exc}") from exc


def fetch_inventory() -> tuple[dict, str]:
    """Scan Warframe memory for credentials and fetch the live inventory.

    Candidate nonces are tried in frequency order so a stale heap copy does
    not fail the refresh when a newer valid nonce is also present.

    Returns:
        ``(inventory_dict, account_id)``.
    """
    _log.info("Scanning Warframe memory for credentials...")
    pid = find_warframe_pid()
    candidates = _find_credentials_with_retry(pid)
    _log.info("Found %s credential candidate(s); fetching live inventory...", len(candidates))

    rejected = 0
    for account_id, nonce in candidates:
        try:
            inventory = _fetch_inventory_with(account_id, nonce)
        except _NonceRejectedError:
            rejected += 1
            continue
        return inventory, account_id

    raise InventoryFetchError(
        f"Warframe rejected all {rejected} nonce candidate(s). "
        "Return to the Orbiter and refresh again."
    )


def load_inventory(path: str) -> dict:
    """Load a previously-cached inventory JSON file from disk.

    Used when a cached ``inventory.json`` exists and ``--refresh`` was
    not specified.
    """
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise InventoryFetchError(f"Could not load inventory cache {path}: {exc}") from exc


def fetch_de_profile(account_id: str) -> dict:
    """Fetch player profile data from Digital Extremes' profile viewer API.

    The profile API returns mastery rank, XP history (which items have been
    ranked up), and the player's display name.  This data is needed by the
    ``mastery`` sub-command and to determine which items are "mastered"
    even after being sold.

    Returns:
        The first result dict from the API response.
    """
    url = PROFILE_URL.format(account_id)
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


def inventory_summary(inv: dict) -> tuple[int, int, int]:
    """Return ``(misc_count, recipe_count, equipment_count)``.

    Used only for the one-line summary printed after a ``--refresh``.
    """
    misc = len(inv.get("MiscItems", []))
    rec = len(inv.get("Recipes", []))
    equip = sum(len(inv.get(s, [])) for s in EQUIPMENT_SECTIONS)
    return misc, rec, equip


def load_inventory_with_fallback(
    inv_path: str,
    refresh: bool = False,
) -> tuple[dict, str | None]:
    """Return inventory — either freshly fetched or from the local cache.

    When *refresh* is ``True`` (or the cache file is missing) the
    function fetches live data from the Warframe process, saves it, and
    prints a summary to stderr.  Otherwise the cached file is loaded.

    This is the main inventory loading entry point used by all presenters.

    Returns:
        ``(inventory_dict, account_id_or_None)``.
    """
    if refresh or not os.path.exists(inv_path):
        inv, account_id = fetch_inventory()
        try:
            atomic_write_json(inv_path, inv, indent=2)
        except OSError as exc:
            _log.warning("could not save inventory cache %s: %s", inv_path, exc)
        misc, rec, equip = inventory_summary(inv)
        _log.info(
            "Inventory: %s items, %s recipes, %s equipment (account: %s)",
            misc,
            rec,
            equip,
            account_id,
        )
        return inv, account_id
    return load_inventory(inv_path), None


def load_data(
    items_cache: str,
    refresh_items: bool = False,
    inventory_path: str | None = None,
    refresh: bool = False,
) -> tuple:
    """Load the item database and inventory as the shared data objects.

    This is the standard "init" pattern for most sub-commands:
    1. Create :class:`ItemData` and load the item database.
    2. Load inventory (live or cached) and wrap it in :class:`Inventory`.
    3. Return both plus the live account id (``None`` for cached data).

    Raises:
        ItemCacheNotFoundError: if the item database cannot be loaded.
        InventoryFetchError: if the inventory cannot be fetched or read.
        WarframeNotRunningError: if a live scan was requested and the game
            is not running.

    Returns:
        ``(ItemData, Inventory, account_id_or_None)``.
    """
    # Imported lazily: data.py depends on this module for ExportDB and the
    # inventory section constants.
    from warframe_profile import DATA_DIR
    from warframe_profile.model.data import Inventory, ItemData

    item_data = ItemData.from_path(items_cache, refresh=refresh_items)

    inv_path = inventory_path or os.path.join(DATA_DIR, "inventory.json")
    inv, account_id = load_inventory_with_fallback(
        inv_path,
        refresh=refresh or (not inventory_path and not os.path.exists(inv_path)),
    )

    return item_data, Inventory(inv, item_data), account_id
