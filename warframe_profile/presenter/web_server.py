"""Web server presenter — loads data and serves the web UI.

The ``serve`` sub-command starts a lightweight HTTP server (stdlib
http.server + ThreadingMixIn) that serves the same analysis views as the
CLI, rendered as HTML via :mod:`warframe_profile.view.web.templates`.

Architecture:
    - DataCache: singleton that holds all loaded data and lazy-computed
      analysis results.
    - WarframeHandler: routes HTTP GET requests to the appropriate
      ``render_*`` function based on URL path.
    - POST /refresh: re-fetches inventory and clears analysis caches.

All data is loaded once at startup (in ``load_all``) and analysis results
are computed lazily when first requested.
"""

import html
import http.server
import os
import signal
import socketserver
import sys
import threading
import traceback
import urllib.parse

from warframe_profile import DATA_DIR
from warframe_profile.log import configure, get_logger
from warframe_profile.model.data import Inventory, ItemData
from warframe_profile.model.errors import InventoryFetchError, WarframeNotRunningError
from warframe_profile.model.inventory import load_inventory_with_fallback
from warframe_profile.view.web.templates import (
    RELIC_AGE_SLUGS,
    RELIC_RARITY_SLUGS,
    relic_category_tabs,
    render_cleanup_excess,
    render_cleanup_rebuy,
    render_cleanup_sell,
    render_craft,
    render_craft_result,
    render_ducats,
    render_home,
    render_mastery_missing_parts,
    render_mastery_owned_items,
    render_mastery_ready,
    render_mods,
    render_relics_age,
    render_relics_needed,
    render_relics_rarity,
    render_relics_safe,
    render_weapon_chains,
)


def _parse_port() -> int:
    """Parse the ``WARFRAME_WEB_PORT`` env var, falling back to 8090.

    8090 is used rather than the more common 8080 because Steam's
    ``steamwebhelper`` listens on 8080 on many players' machines.
    """
    try:
        return int(os.environ.get("WARFRAME_WEB_PORT", "8090"))
    except ValueError:
        return 8090


#: Bind address used when neither ``--host`` nor ``WARFRAME_WEB_HOST`` is set.
#: The UI has no authentication and can trigger a memory scan, so it must not
#: be reachable from the network by default.
DEFAULT_HOST = "127.0.0.1"


def _parse_host() -> str:
    """Return the default bind address (``WARFRAME_WEB_HOST`` or loopback)."""
    return os.environ.get("WARFRAME_WEB_HOST") or DEFAULT_HOST


def _display_host(host: str) -> str:
    """Return a URL-friendly host: wildcard binds are shown as ``localhost``."""
    if host in {"0.0.0.0", "::", ""}:
        return "localhost"
    return host


def _is_loopback(host: str) -> bool:
    """Return whether *host* is a loopback address (no network exposure)."""
    return host in {"127.0.0.1", "::1", "localhost"}


PORT = _parse_port()
HOST = _parse_host()


# ---------------------------------------------------------------------------
# Global data cache (loaded once at startup)
# ---------------------------------------------------------------------------
# The DataCache singleton holds everything in memory so that the web
# server can serve requests without re-loading data on every call.
# Analysis results are computed lazily — they are cached after the first
# request and only invalidated when the user clicks "Refresh Inventory".


class DataCache:
    """Singleton holding the item database and the current inventory snapshot.

    All derived state lives on :class:`Inventory` (owned counts, foundry
    results, mods, relics, analyses).  Replacing the inventory object therefore
    invalidates every derived analysis atomically.
    """

    def __init__(self) -> None:
        """Initialise the cache to its empty state."""
        self.item_data: ItemData | None = None
        self.inventory: Inventory | None = None
        self.items_cache: str = ""
        self.inventory_path: str = ""


_cache = DataCache()
# Guards cache swaps during refresh and request handling.
_cache_lock = threading.RLock()
# Serialises item-database refreshes.  Downloading + merging takes a while and
# must not run twice concurrently; the lock is only held by the refresh
# request, never by normal reads (those use ``_cache_lock`` for the swap).
_items_refresh_lock = threading.Lock()

_log = get_logger("web")


def _with_notice(path: str, message: str, *, error: bool = True) -> str:
    """Return *path* with a notification parameter added.

    Errors use ``refresh_error`` (rendered as an alert); informational
    messages use ``notice``.
    """
    parsed = urllib.parse.urlparse(path)
    params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    params.append(("refresh_error" if error else "notice", message))
    return urllib.parse.urlunparse(
        ("", "", parsed.path or "/", "", urllib.parse.urlencode(params), "")
    )


def _require_item_data() -> ItemData:
    """Return the loaded item database, or raise if startup loading hasn't run."""
    item_data = _cache.item_data
    if item_data is None:
        raise RuntimeError("Item database not loaded; call load_all() at startup first")
    return item_data


def _require_inventory() -> Inventory:
    """Return the current inventory snapshot, or raise if it is not loaded."""
    inventory = _cache.inventory
    if inventory is None:
        raise RuntimeError("Inventory not loaded; call load_all() at startup first")
    return inventory


def load_db(items_cache: str) -> None:
    """Load the item database and swap it in atomically."""
    item_data = ItemData.from_path(items_cache)

    with _cache_lock:
        _cache.item_data = item_data
        _cache.items_cache = items_cache
        if _cache.inventory is not None:
            _cache.inventory = _cache.inventory.rebind(item_data)


def rebuild_derived_state() -> None:
    """Rebuild the inventory snapshot after the item database changes."""
    with _cache_lock:
        if _cache.inventory is not None:
            _cache.inventory = _cache.inventory.rebind(_require_item_data())


def reload_inventory(inventory_path: str, refresh: bool = False) -> None:
    """(Re)load the player inventory and derived ownership data."""
    inv, _ = load_inventory_with_fallback(inventory_path, refresh)
    item_data = _require_item_data()
    with _cache_lock:
        _cache.inventory_path = inventory_path
        _cache.inventory = Inventory(inv, item_data)


def invalidate_analysis() -> None:
    """Drop every derived analysis by rebuilding the inventory snapshot."""
    with _cache_lock:
        if _cache.inventory is not None:
            _cache.inventory = _cache.inventory.rebind(_require_item_data())


def _is_same_origin_request(origin: str, referer: str, host: str) -> bool:
    """Return whether a request's ``Origin``/``Referer`` matches *host*.

    Browsers send ``Origin`` on cross-origin POSTs and ``Referer`` on
    same-origin form submissions.  A request with neither header (e.g.
    ``curl``) is allowed, so scripted refreshes keep working.
    """
    for raw in (origin, referer):
        if not raw:
            continue
        parsed = urllib.parse.urlparse(raw)
        return bool(parsed.netloc) and parsed.netloc == host
    return True


def load_all(items_cache: str, inventory_path: str, refresh: bool = False) -> None:
    """Load the item database and inventory (called once at server startup)."""
    load_db(items_cache)
    reload_inventory(inventory_path, refresh)


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class WarframeHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler — routes paths to handler methods."""

    def log_message(self, format: str, *args: object) -> None:
        """Log one request line to stderr in a compact format."""
        sys.stderr.write("[web] " + format % args + "\n")

    def _html(self, code: int, html: str):
        """Send an HTML response with *code* and *html* body."""
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, path: str):
        """Send a 302 redirect to *path*."""
        self.send_response(302)
        self.send_header("Location", path)
        self.end_headers()

    def _not_found(self):
        """Send the standard 404 response."""
        self._html(404, "<h1>404 Not Found</h1>")

    def _return_path(self) -> str:
        """Return a safe same-site path to return to after a refresh."""
        referer = self.headers.get("Referer", "")
        if not referer:
            return "/"
        parsed_ref = urllib.parse.urlparse(referer)
        host = self.headers.get("Host", "")
        if parsed_ref.netloc and parsed_ref.netloc != host:
            return "/"
        path = parsed_ref.path or "/"
        if not path.startswith("/") or path.startswith("//"):
            return "/"
        if parsed_ref.query:
            path += "?" + parsed_ref.query
        return path

    def _is_same_origin(self) -> bool:
        """Return ``False`` for cross-origin POSTs (basic CSRF protection)."""
        return _is_same_origin_request(
            self.headers.get("Origin", ""),
            self.headers.get("Referer", ""),
            self.headers.get("Host", ""),
        )

    def _handle_home(self):
        """Serve the home page."""
        self._html(200, render_home())

    def _handle_ducats(self, subpath: str):
        """Serve the single Ducats view (sell-for-ducats list)."""
        if subpath:
            self._not_found()
            return
        self._html(200, render_ducats(_require_inventory().analysis))

    def _handle_relics(self, subpath: str):
        """Serve relic views: Safe, age/rarity overviews, then item types."""
        inventory = _require_inventory()
        if not subpath:
            self._html(200, render_relics_safe(inventory.needed_drops, inventory.relic_info))
            return
        if subpath == "/age":
            self._html(200, render_relics_age(inventory.needed_drops))
            return
        if subpath.startswith("/age/"):
            slug = subpath.removeprefix("/age/")
            if slug in RELIC_AGE_SLUGS:
                self._html(200, render_relics_age(inventory.needed_drops, slug))
            else:
                self._not_found()
            return
        if subpath == "/rarity":
            self._html(200, render_relics_rarity(inventory.needed_drops))
            return
        if subpath.startswith("/rarity/"):
            slug = subpath.removeprefix("/rarity/")
            if slug in RELIC_RARITY_SLUGS:
                self._html(200, render_relics_rarity(inventory.needed_drops, slug))
            else:
                self._not_found()
            return
        if subpath.startswith("/needed/"):
            slug = subpath.removeprefix("/needed/")
            tabs = relic_category_tabs(inventory.needed_drops)
            if any(key == slug for key, _, _ in tabs):
                self._html(200, render_relics_needed(inventory.needed_drops, slug))
            else:
                self._not_found()
            return
        self._not_found()

    def _handle_craft(self, query: str):
        """Serve craft search results or the empty search form."""
        if not query:
            self._html(200, render_craft())
            return
        item_data = _require_item_data()
        inventory = _require_inventory()
        matches = item_data.find(query)
        if matches:
            plans = [
                item_data.craft_plan(match["uniqueName"], inventory, max_depth=3)
                for match in matches[:5]
            ]
            self._html(200, render_craft_result(query, plans))
        else:
            self._html(
                200,
                render_craft(query, error=f'No results for "{query}"'),
            )

    def _handle_weapon_chains(self):
        """Serve the weapon upgrade chains view."""
        item_data = _require_item_data()
        inventory = _require_inventory()
        chains, requirements, craftables, shown = inventory.weapon_chains
        chain_views = [
            (
                [item_data.weapon_name(un) for un in chain],
                item_data.craft_plan(chain[-1], inventory, max_depth=5),
            )
            for chain in chains
        ]
        self._html(
            200,
            render_weapon_chains(chain_views, requirements, craftables, shown),
        )

    def _handle_mastery(self, subpath: str):
        """Serve mastery sub-views (owned items, ready, missing parts)."""
        report = _require_inventory().mastery_report

        if subpath == "/ready":
            self._html(200, render_mastery_ready(report))
        elif subpath == "/missing-parts":
            self._html(200, render_mastery_missing_parts(report))
        elif subpath:
            self._not_found()
        else:
            self._html(200, render_mastery_owned_items(report))

    def _handle_mods(self):
        """Serve the missing-mods view."""
        self._html(200, render_mods(_require_inventory().mods_report))

    def _handle_refresh_items(self) -> None:
        """Re-download the merged item database and rebuild its indices."""
        return_path = self._return_path()
        if not _items_refresh_lock.acquire(blocking=False):
            self._redirect(
                _with_notice(
                    return_path,
                    "An item database refresh is already running.",
                    error=False,
                )
            )
            return

        try:
            items_cache = _cache.items_cache
            if not items_cache:
                self._redirect(_with_notice(return_path, "Item database path is unknown."))
                return

            # Imported lazily to keep startup light and avoid a circular import.
            from types import SimpleNamespace

            from warframe_profile.scripts.update_export_db import main as update_main

            _log.info("Refreshing item database...")
            update_main(
                SimpleNamespace(
                    items_cache=items_cache,
                    refresh_items=False,
                    refresh=False,
                )
            )
            load_db(items_cache)
            rebuild_derived_state()
        except (SystemExit, Exception) as exc:
            traceback.print_exc()
            self._redirect(
                _with_notice(
                    return_path,
                    f"Item database refresh failed: {exc}"[:300],
                )
            )
            return
        finally:
            _items_refresh_lock.release()

        self._redirect(_with_notice(return_path, "Item database refreshed.", error=False))

    def _handle_cleanup(self, subpath: str):
        """Serve cleanup sub-views (rebuy / sell / excess)."""
        inventory = _require_inventory()
        if subpath == "/sell":
            self._html(200, render_cleanup_sell(inventory.owned_prime_pairs))
        elif subpath == "/excess":
            self._html(200, render_cleanup_excess(inventory.excess_items))
        elif subpath:
            self._not_found()
        else:
            self._html(200, render_cleanup_rebuy(inventory.sellable_equipment))

    def do_GET(self):
        """Route GET requests to the appropriate handler based on URL path.

        Routes:
            /               → Home
            /ducats         → Ducat sell analysis
            /relics[/...]   → Safe-to-spam relics, needed parts by age/rarity,
                              and needed drops by item type
            /craft          → Crafting tree browser (with ?q= search)
            /weapon-chains  → Weapon upgrade chains
            /mastery[/...]  → Mastery (owned items / ready to build / missing parts)
            /mods           → Missing mods by in-game category
            /cleanup[/...]  → Inventory cleanup (rebuy / sell / excess)
        """
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        params = urllib.parse.parse_qs(parsed.query)

        try:
            if path in ("", "/"):
                self._handle_home()
            elif path.startswith("/ducats"):
                self._handle_ducats(path.removeprefix("/ducats"))
            elif path.startswith("/relics"):
                self._handle_relics(path.removeprefix("/relics"))
            elif path == "/craft":
                self._handle_craft((params.get("q") or [""])[0].strip())
            elif path == "/weapon-chains":
                self._handle_weapon_chains()
            elif path.startswith("/mastery"):
                self._handle_mastery(path.removeprefix("/mastery"))
            elif path == "/mods":
                self._handle_mods()
            elif path.startswith("/cleanup"):
                self._handle_cleanup(path.removeprefix("/cleanup"))
            else:
                self._not_found()
        except Exception as e:
            # Keep the full traceback in the console; never send internals to
            # the browser (the server can be bound to a LAN address).
            traceback.print_exc()
            message = f"{type(e).__name__}: {e}"[:300]
            self._html(500, f"<h1>500 Error</h1><pre>{html.escape(message)}</pre>")

    def do_POST(self):
        """Handle POST requests — ``/refresh`` and ``/refresh-items``."""
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/refresh":
            if not self._is_same_origin():
                self._html(403, "<h1>403 Forbidden</h1>")
                return
            inv_path = _cache.inventory_path or os.path.join(DATA_DIR, "inventory.json")
            return_path = self._return_path()
            try:
                reload_inventory(inv_path, refresh=True)
                invalidate_analysis()
            except (WarframeNotRunningError, InventoryFetchError) as exc:
                # Surface the failure as an in-page notification instead of a
                # bare error page; cached data stays untouched.
                self._redirect(_with_notice(return_path, str(exc)))
                return
            self._redirect(_with_notice(return_path, "Inventory refreshed.", error=False))
        elif path == "/refresh-items":
            if not self._is_same_origin():
                self._html(403, "<h1>403 Forbidden</h1>")
                return
            self._handle_refresh_items()
        else:
            self._not_found()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(args) -> None:
    """Entry point for the ``serve`` sub-command."""
    items_cache = getattr(args, "items_cache", "") or os.path.join(DATA_DIR, "export_db.json")
    inv_path = getattr(args, "inventory", "") or os.path.join(DATA_DIR, "inventory.json")
    refresh = getattr(args, "refresh", False)
    port = getattr(args, "port", None) or PORT
    host = getattr(args, "host", None) or HOST
    display_host = _display_host(host)

    configure()
    _log.info("Loading data...")
    load_all(items_cache, inv_path, refresh)
    _log.info("Data loaded. Starting server on http://%s:%s", display_host, port)

    class ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
        allow_reuse_address = True
        daemon_threads = True

    try:
        server = ThreadedServer((host, port), WarframeHandler)
    except OSError as exc:
        _log.error(
            "Could not bind %s:%s: %s. Use --host/--port or set "
            "WARFRAME_WEB_HOST/WARFRAME_WEB_PORT.",
            host,
            port,
            exc,
        )
        raise SystemExit(1) from exc

    if not _is_loopback(host):
        _log.warning(
            "Bound to %s, which is reachable from the network. This UI has no "
            "authentication; only expose it on trusted networks.",
            host,
        )

    with server as httpd:
        _log.info("Serving at http://%s:%s (bound to %s:%s)", display_host, port, host, port)

        def _handle_interrupt(signum, frame):
            """Turn the first Ctrl+C into a clean exit and ignore the rest.

            PyInstaller's bootloader forwards the terminal's SIGINT to the
            child process as well, so a second delivery can otherwise land in
            the shutdown path and produce a traceback.
            """
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            raise KeyboardInterrupt

        previous_handler = None
        try:
            previous_handler = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, _handle_interrupt)
        except ValueError:
            # Not the main thread (e.g. embedded use); keep the default handler.
            previous_handler = None

        interrupted = False
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            interrupted = True
            _log.info("Shutting down.")
        finally:
            # After an interrupt SIGINT stays ignored until the process exits,
            # so a late forwarded Ctrl+C cannot break teardown.
            if not interrupted and previous_handler is not None:
                signal.signal(signal.SIGINT, previous_handler)
