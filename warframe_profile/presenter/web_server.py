"""Web server presenter — loads data and serves the web UI.

The ``--serve`` sub-command starts a lightweight HTTP server (stdlib
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

import http.server
import json
import os
import socketserver
import sys
import threading
import urllib.parse

from warframe_profile import DATA_DIR
from warframe_profile.model.analysis import (
    analyze,
    build_item_index,
    build_needed_drops,
    build_prime_map,
    build_regular_to_prime_map,
    build_relics_map,
    compute_extra_builds,
    compute_owned_finished,
    compute_sellable_equipment,
    find_excess_blueprints_and_components,
    find_owned_item_uns,
    find_owned_prime_uns,
)
from warframe_profile.model.craft_model import (
    build_items_by_un,
    build_lookup,
    build_recipes_by_result,
    build_weapon_chains,
    find_items,
)
from warframe_profile.model.foundry_export import (
    build_foundry_export,
    steel_settings_from_xp,
)
from warframe_profile.model.inventory import (
    EQUIPMENT_SECTIONS,
    ExportDB,
    build_owned,
    load_inventory_with_fallback,
)
from warframe_profile.model.utils import normalize_path
from warframe_profile.view.web.templates import (
    render_cleanup_excess,
    render_cleanup_rebuy,
    render_cleanup_sell,
    render_craft,
    render_craft_result,
    render_ducats_excess,
    render_ducats_missing,
    render_ducats_sell,
    render_home,
    render_relics_needed,
    render_relics_safe,
    render_weapon_chains,
)

PORT = int(os.environ.get("WARFRAME_WEB_PORT", "8080"))


# ---------------------------------------------------------------------------
# Global data cache (loaded once at startup)
# ---------------------------------------------------------------------------
# The DataCache singleton holds everything in memory so that the web
# server can serve requests without re-loading data on every call.
# Analysis results are computed lazily — they are cached after the first
# request and only invalidated when the user clicks "Refresh Inventory".


class DataCache:
    """Singleton holding all loaded data and lazy-computed analysis caches.

    Attributes are populated by ``load_all`` (called at startup) and the
    ``_ensure_*`` methods (called on first request).
    """

    db: ExportDB | None = None
    inventory: dict = {}
    owned: dict = {}
    items_by_un: dict = {}
    recipes_by_result: dict = {}
    loc_dict: dict = {}
    by_name: dict | None = None
    # Analysis caches (lazy-computed via _ensure_* methods)
    prime_map: dict | None = None
    owned_finished: set | None = None
    extra_builds: dict | None = None
    analysis_result = None
    relic_info = None
    needed_drops = None
    sellable = None
    owned_prime_pairs = None
    excess_items = None
    weapon_chains = None
    foundry_export = None
    foundry_settings: tuple | None = None


_cache = DataCache()
# Guards the lazy ``_ensure_*`` population and the /refresh reload.  The
# server is threaded, so unlocked check-then-act would double-compute (and
# potentially expose torn state) on concurrent first requests.  Re-entrant
# because the ``_ensure_*`` helpers nest (e.g. analysis → prime map).
_cache_lock = threading.RLock()


def load_db(items_cache: str) -> None:
    """Load the item database and build its static indices."""
    _cache.db = ExportDB(items_cache)
    _cache.db.load()
    data = _cache.db.raw
    _cache.items_by_un = build_items_by_un(data)
    _cache.recipes_by_result = build_recipes_by_result(data)
    _cache.loc_dict = data.get("dict", {})
    _cache.by_name = build_lookup(_cache.items_by_un, _cache.loc_dict)


def reload_inventory(inventory_path: str, refresh: bool = False) -> None:
    """(Re)load the player inventory and derived ownership data.

    The item database is *not* touched — it only changes on ``--update``.
    """
    inv, _ = load_inventory_with_fallback(inventory_path, refresh)
    _cache.inventory = inv
    _cache.owned = build_owned(inv, _cache.db.raw.get("recipes", {}))


def invalidate_analysis() -> None:
    """Drop every lazily-computed result derived from the inventory."""
    _cache.prime_map = None
    _cache.owned_finished = None
    _cache.extra_builds = None
    _cache.analysis_result = None
    _cache.relic_info = None
    _cache.needed_drops = None
    _cache.sellable = None
    _cache.owned_prime_pairs = None
    _cache.excess_items = None
    _cache.weapon_chains = None
    _cache.foundry_export = None
    _cache.foundry_settings = None


def load_all(items_cache: str, inventory_path: str, refresh: bool = False) -> None:
    """Load the item database and inventory (called once at server startup)."""
    load_db(items_cache)
    reload_inventory(inventory_path, refresh)


def _ensure_prime_map():
    """Build the prime map once (shared by the ducat and relic analyses)."""
    if _cache.prime_map is None:
        _cache.prime_map = build_prime_map(_cache.db.raw.get("items", []))


def _ensure_owned_state():
    """Build ``owned_finished`` / ``extra_builds`` once per inventory load."""
    if _cache.owned_finished is None or _cache.extra_builds is None:
        _ensure_prime_map()
        _cache.owned_finished = compute_owned_finished(
            _cache.inventory, EQUIPMENT_SECTIONS, None
        )
        _cache.extra_builds = compute_extra_builds(
            _cache.prime_map, _cache.owned, _cache.owned_finished
        )


def _ensure_analysis():
    """Lazy-load ducat analysis: Prime map, analysis result, sellable equipment,
    and owned prime pairs."""
    if _cache.analysis_result is not None:
        return
    with _cache_lock:
        if _cache.analysis_result is not None:
            return
        items = _cache.db.items
        _ensure_prime_map()
        _ensure_owned_state()
        _cache.analysis_result = analyze(
            _cache.inventory,
            _cache.prime_map,
            EQUIPMENT_SECTIONS,
            recipes=_cache.db.recipes,
            owned=_cache.owned,
            owned_finished=_cache.owned_finished,
            extra_builds=_cache.extra_builds,
        )
        _cache.sellable = compute_sellable_equipment(
            _cache.db,
            _cache.inventory,
            EQUIPMENT_SECTIONS,
        )
        item_index = build_item_index(items)
        reg_to_prime = build_regular_to_prime_map(items)
        owned_regular = find_owned_item_uns(_cache.inventory, item_index, EQUIPMENT_SECTIONS)
        owned_primes = find_owned_prime_uns(_cache.inventory, item_index, EQUIPMENT_SECTIONS)
        _cache.owned_prime_pairs = []
        for reg_un in owned_regular:
            pi = reg_to_prime.get(reg_un)
            if pi and pi["prime_un"] in owned_primes:
                item = _cache.items_by_un.get(normalize_path(reg_un), {})
                _cache.owned_prime_pairs.append(
                    (
                        item.get("name", reg_un),
                        item.get("category", ""),
                        pi["prime_name"],
                    )
                )


def _ensure_excess():
    """Lazy-load excess blueprints and components analysis."""
    if _cache.excess_items is not None:
        return
    with _cache_lock:
        if _cache.excess_items is not None:
            return
        _ensure_owned_state()
        _cache.excess_items = find_excess_blueprints_and_components(
            _cache.inventory,
            _cache.items_by_un,
            _cache.db.recipes,
            _cache.owned,
            _cache.owned_finished,
            _cache.loc_dict,
        )


def _ensure_relics():
    """Lazy-load relic analysis: safe-to-spam relics and needed drops."""
    if _cache.relic_info is not None:
        return
    with _cache_lock:
        if _cache.relic_info is not None:
            return
        items = _cache.db.items
        _ensure_owned_state()
        _cache.relic_info = build_relics_map(
            items,
            _cache.inventory,
            _cache.prime_map,
            _cache.owned,
            EQUIPMENT_SECTIONS,
            owned_finished=_cache.owned_finished,
            extra_builds=_cache.extra_builds,
        )
        _cache.needed_drops = build_needed_drops(
            items,
            _cache.inventory,
            _cache.prime_map,
            _cache.owned,
            EQUIPMENT_SECTIONS,
            owned_finished=_cache.owned_finished,
            extra_builds=_cache.extra_builds,
        )


def _ensure_weapon_chains():
    """Lazy-load weapon upgrade chains."""
    if _cache.weapon_chains is not None:
        return
    with _cache_lock:
        if _cache.weapon_chains is not None:
            return
        _cache.weapon_chains = build_weapon_chains(
            _cache.items_by_un,
            _cache.recipes_by_result,
            _cache.owned,
            _cache.loc_dict,
        )


def _ensure_foundry_export(settings: dict | None):
    """Lazy-load (and cache per settings) the Foundry export payload."""
    key = tuple(sorted(settings.items())) if settings else None
    if _cache.foundry_export is not None and _cache.foundry_settings == key:
        return _cache.foundry_export
    with _cache_lock:
        if _cache.foundry_export is not None and _cache.foundry_settings == key:
            return _cache.foundry_export
        _cache.foundry_export = build_foundry_export(
            _cache.db, _cache.inventory, settings=settings
        )
        _cache.foundry_settings = key
        return _cache.foundry_export


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class WarframeHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler — routes paths to handler methods."""

    def log_message(self, fmt, *args):
        """Log one request line to stderr in a compact format."""
        sys.stderr.write(f"[web] {args[0]} {args[1]} {args[2]}\n")

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

    def _handle_home(self):
        """Serve the home page."""
        self._html(200, render_home())

    def _handle_ducats(self, subpath: str):
        """Serve ducat analysis sub-views (missing / excess / sell)."""
        _ensure_analysis()
        if subpath == "/excess":
            self._html(200, render_ducats_excess(_cache.analysis_result))
        elif subpath == "/sell":
            self._html(200, render_ducats_sell(_cache.analysis_result))
        else:
            self._html(200, render_ducats_missing(_cache.analysis_result))

    def _handle_relics(self, subpath: str):
        """Serve relic analysis sub-views (needed drops / safe-to-spam)."""
        _ensure_relics()
        if subpath == "/safe":
            self._html(200, render_relics_safe(_cache.needed_drops, _cache.relic_info))
        else:
            self._html(200, render_relics_needed(_cache.needed_drops, _cache.relic_info))

    def _handle_craft(self, query: str):
        """Serve craft search results or the empty search form."""
        if not query:
            self._html(200, render_craft())
            return
        matches = find_items(query, _cache.by_name)
        if matches:
            self._html(
                200,
                render_craft_result(
                    query,
                    matches[:5],
                    _cache.items_by_un,
                    _cache.recipes_by_result,
                    _cache.owned,
                    _cache.loc_dict,
                ),
            )
        else:
            self._html(
                200,
                render_craft(query, error=f'No results for "{query}"'),
            )

    def _handle_weapon_chains(self):
        """Serve the weapon upgrade chains view."""
        _ensure_weapon_chains()
        chains, req, craft, shown = _cache.weapon_chains
        self._html(
            200,
            render_weapon_chains(
                chains,
                req,
                craft,
                shown,
                _cache.items_by_un,
                _cache.recipes_by_result,
                _cache.owned,
                _cache.loc_dict,
            ),
        )

    def _handle_foundry_export(self, params: dict):
        """Serve the Warframe Foundry import file as a JSON download.

        Supports an optional ``?steel_xp=<points>`` query parameter for the
        player's total Steel Path mastery gained (see
        :func:`steel_settings_from_xp`).
        """
        settings = None
        steel_xp = (params.get("steel_xp") or [""])[0].strip()
        if steel_xp:
            try:
                settings = steel_settings_from_xp(int(steel_xp))
            except ValueError:
                self._html(400, "<h1>400 Bad Request</h1><p>steel_xp must be an integer</p>")
                return
        export = _ensure_foundry_export(settings)
        body = json.dumps(export, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header(
            "Content-Disposition",
            'attachment; filename="warframe-foundry.json"',
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_cleanup(self, subpath: str):
        """Serve cleanup sub-views (rebuy / sell / excess)."""
        if subpath == "/sell":
            _ensure_analysis()
            self._html(
                200,
                render_cleanup_sell(
                    _cache.sellable,
                    _cache.owned_prime_pairs,
                    _cache.items_by_un,
                    _cache.loc_dict,
                ),
            )
        elif subpath == "/excess":
            _ensure_excess()
            self._html(200, render_cleanup_excess(_cache.excess_items))
        else:
            _ensure_analysis()
            self._html(
                200,
                render_cleanup_rebuy(
                    _cache.sellable,
                    _cache.owned_prime_pairs,
                    _cache.items_by_un,
                    _cache.loc_dict,
                ),
            )

    def do_GET(self):
        """Route GET requests to the appropriate handler based on URL path.

        Routes:
            /               → Home
            /ducats[/...]   → Ducat analysis (missing / excess / sell)
            /relics[/...]   → Relic analysis (needed drops / safe-to-spam)
            /craft          → Crafting tree browser (with ?q= search)
            /weapon-chains  → Weapon upgrade chains
            /cleanup[/...]  → Inventory cleanup (rebuy / sell / excess)
            /foundry-export → Download Warframe Foundry import file (JSON)
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
            elif path.startswith("/cleanup"):
                self._handle_cleanup(path.removeprefix("/cleanup"))
            elif path == "/foundry-export":
                self._handle_foundry_export(params)
            else:
                self._html(404, "<h1>404 Not Found</h1>")
        except Exception as e:
            import traceback

            tb = traceback.format_exc()
            self._html(500, f"<h1>500 Error</h1><pre>{e}\n{tb}</pre>")

    def do_POST(self):
        """Handle POST requests — currently only /refresh."""
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/refresh":
            inv_path = os.path.join(DATA_DIR, "inventory.json")
            with _cache_lock:
                reload_inventory(inv_path, refresh=True)
                invalidate_analysis()
            self._redirect("/")
        else:
            self._html(404, "<h1>404 Not Found</h1>")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(args) -> None:
    """Entry point for the ``--serve`` sub-command."""
    items_cache = getattr(args, "items_cache", "") or os.path.join(DATA_DIR, "export_db.json")
    inv_path = getattr(args, "inventory", "") or os.path.join(DATA_DIR, "inventory.json")
    refresh = getattr(args, "refresh", False)
    port = getattr(args, "port", PORT)

    print("[web] Loading data…", file=sys.stderr)
    load_all(items_cache, inv_path, refresh)
    print(f"[web] Data loaded. Starting server on http://0.0.0.0:{port}", file=sys.stderr)

    class ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with ThreadedServer(("0.0.0.0", port), WarframeHandler) as httpd:
        print(f"[web] Serving at http://0.0.0.0:{port}", file=sys.stderr)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[web] Shutting down.", file=sys.stderr)
            httpd.shutdown()
