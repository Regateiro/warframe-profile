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

import functools
import html
import http.server
import json
import os
import signal
import socketserver
import sys
import threading
import traceback
import urllib.parse

from warframe_profile import DATA_DIR
from warframe_profile.model.analysis import (
    AnalysisResult,
    ExcessItem,
    NeededPart,
    RelicInfo,
    SellableEquipment,
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
from warframe_profile.model.errors import InventoryFetchError, WarframeNotRunningError
from warframe_profile.model.foundry_export import (
    build_foundry_export,
    steel_settings_from_xp,
)
from warframe_profile.model.inventory import (
    EQUIPMENT_SECTIONS,
    ExportDB,
    build_mastered_set,
    build_owned,
    load_inventory_with_fallback,
)
from warframe_profile.model.utils import normalize_path
from warframe_profile.presenter.mastery import MasteryReport, build_mastery_report
from warframe_profile.presenter.missing_mods import ModsReport, build_mods_report
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
    render_mastery,
    render_mods,
    render_relics_needed,
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
    """Singleton holding all loaded data and lazy-computed analysis caches.

    Attributes are populated by ``load_all`` (called at startup) and the
    ``_ensure_*`` methods (called on first request).  Every derived value is
    reset by :meth:`reset` when the inventory is refreshed.
    """

    def __init__(self) -> None:
        """Initialise the cache to its empty state."""
        self.db: ExportDB | None = None
        self.inventory: dict = {}
        self.owned: dict = {}
        self.mastered: set[str] = set()
        self.items_by_un: dict = {}
        self.recipes_by_result: dict = {}
        self.loc_dict: dict = {}
        self.by_name: dict = {}
        self.reset()

    def reset(self) -> None:
        """Drop every lazily-computed result derived from the inventory."""
        # Analysis caches (lazy-computed via _ensure_* methods)
        self.prime_map: dict[str, dict] | None = None
        self.owned_finished: set[str] | None = None
        self.extra_builds: dict[str, int] | None = None
        self.analysis_result: AnalysisResult | None = None
        self.relic_info: list[RelicInfo] | None = None
        self.needed_drops: list[NeededPart] | None = None
        self.sellable: list[SellableEquipment] | None = None
        self.owned_prime_pairs: list[tuple[str, str, str]] | None = None
        self.excess_items: list[ExcessItem] | None = None
        self.weapon_chains: tuple[list[list[str]], dict, dict, int] | None = None
        self.foundry_export: dict | None = None
        self.foundry_settings: tuple | None = None
        self.mastery_report: MasteryReport | None = None
        self.mods_report: ModsReport | None = None


_cache = DataCache()
# Guards the lazy ``_ensure_*`` population and the /refresh reload.  The
# server is threaded, so unlocked check-then-act would double-compute (and
# potentially expose torn state) on concurrent first requests.  Re-entrant
# because the ``_ensure_*`` helpers nest (e.g. analysis → prime map).
_cache_lock = threading.RLock()


def _with_notice(path: str, message: str) -> str:
    """Return *path* with a ``refresh_error`` notification parameter added."""
    parsed = urllib.parse.urlparse(path)
    params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    params.append(("refresh_error", message))
    return urllib.parse.urlunparse(
        ("", "", parsed.path or "/", "", urllib.parse.urlencode(params), "")
    )


def _require_db() -> ExportDB:
    """Return the loaded item database, or raise if startup loading hasn't run."""
    db = _cache.db
    if db is None:
        raise RuntimeError("Item database not loaded; call load_all() at startup first")
    return db


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

    The item database is *not* touched — it only changes on ``update``.
    """
    inv, _ = load_inventory_with_fallback(inventory_path, refresh)
    _cache.inventory = inv
    _cache.mastered = build_mastered_set(inv)
    _cache.owned = build_owned(inv, _require_db().raw.get("recipes", {}))


def invalidate_analysis() -> None:
    """Drop every lazily-computed result derived from the inventory."""
    _cache.reset()


def _lazy(attr: str):
    """Return a decorator that computes a cache attribute at most once.

    The server is threaded, so an unlocked check-then-act would double-compute
    on concurrent first requests.  The attribute is re-checked under the cache
    lock; the decorated function is expected to write ``_cache.<attr>``.
    """

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if getattr(_cache, attr) is not None:
                return
            with _cache_lock:
                if getattr(_cache, attr) is None:
                    fn(*args, **kwargs)

        return wrapper

    return decorator


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


@_lazy("prime_map")
def _ensure_prime_map():
    """Build the prime map once (shared by the ducat and relic analyses)."""
    _cache.prime_map = build_prime_map(_require_db().raw.get("items", []))


@_lazy("owned_finished")
def _ensure_owned_state():
    """Build ``owned_finished`` / ``extra_builds`` once per inventory load."""
    _ensure_prime_map()
    assert _cache.prime_map is not None  # guaranteed by _ensure_prime_map
    _cache.owned_finished = compute_owned_finished(
        _cache.inventory, EQUIPMENT_SECTIONS, _cache.mastered
    )
    _cache.extra_builds = compute_extra_builds(
        _cache.prime_map, _cache.owned, _cache.owned_finished
    )


@_lazy("analysis_result")
def _ensure_analysis():
    """Lazy-load ducat analysis: Prime map, analysis result, sellable equipment,
    and owned prime pairs."""
    items = _require_db().items
    _ensure_prime_map()
    _ensure_owned_state()
    assert _cache.prime_map is not None  # guaranteed by _ensure_prime_map
    prime_map = _cache.prime_map
    _cache.analysis_result = analyze(
        _cache.inventory,
        prime_map,
        EQUIPMENT_SECTIONS,
        mastered=_cache.mastered,
        recipes=_require_db().recipes,
        owned=_cache.owned,
        owned_finished=_cache.owned_finished,
        extra_builds=_cache.extra_builds,
    )
    _cache.sellable = compute_sellable_equipment(
        _require_db(),
        _cache.inventory,
        EQUIPMENT_SECTIONS,
    )
    # CLI parity: print_report reads sellable off the analysis result.
    _cache.analysis_result.sellable = _cache.sellable
    item_index = build_item_index(items)
    reg_to_prime = build_regular_to_prime_map(items)
    owned_regular = find_owned_item_uns(_cache.inventory, item_index, EQUIPMENT_SECTIONS)
    owned_primes = find_owned_prime_uns(_cache.inventory, prime_map, EQUIPMENT_SECTIONS)
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


@_lazy("excess_items")
def _ensure_excess():
    """Lazy-load excess blueprints and components analysis."""
    _ensure_owned_state()
    assert _cache.owned_finished is not None  # guaranteed by _ensure_owned_state
    _cache.excess_items = find_excess_blueprints_and_components(
        _cache.inventory,
        _cache.items_by_un,
        _require_db().recipes,
        _cache.owned,
        _cache.owned_finished,
        _cache.loc_dict,
        mastered=_cache.mastered,
    )


@_lazy("relic_info")
def _ensure_relics():
    """Lazy-load relic analysis: safe-to-spam relics and needed drops."""
    items = _require_db().items
    _ensure_owned_state()
    assert _cache.prime_map is not None  # guaranteed by _ensure_prime_map
    _cache.relic_info = build_relics_map(
        items,
        _cache.inventory,
        _cache.prime_map,
        _cache.owned,
        EQUIPMENT_SECTIONS,
        mastered=_cache.mastered,
        owned_finished=_cache.owned_finished,
        extra_builds=_cache.extra_builds,
    )
    _cache.needed_drops = build_needed_drops(
        items,
        _cache.inventory,
        _cache.prime_map,
        _cache.owned,
        EQUIPMENT_SECTIONS,
        mastered=_cache.mastered,
        owned_finished=_cache.owned_finished,
        extra_builds=_cache.extra_builds,
    )


@_lazy("weapon_chains")
def _ensure_weapon_chains():
    """Lazy-load weapon upgrade chains."""
    _cache.weapon_chains = build_weapon_chains(
        _cache.items_by_un,
        _cache.recipes_by_result,
        _cache.owned,
        _cache.loc_dict,
        mastered=_cache.mastered,
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
            _require_db(), _cache.inventory, settings=settings
        )
        _cache.foundry_settings = key
        return _cache.foundry_export


@_lazy("mastery_report")
def _ensure_mastery():
    """Lazy-load the mastery progression report."""
    _cache.mastery_report = build_mastery_report(_require_db(), _cache.inventory)


@_lazy("mods_report")
def _ensure_mods():
    """Lazy-load the missing-mods report."""
    _cache.mods_report = build_mods_report(_require_db(), _cache.inventory)


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
        assert _cache.weapon_chains is not None  # guaranteed by _ensure_weapon_chains
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

    def _handle_mastery(self):
        """Serve the mastery progression view."""
        _ensure_mastery()
        assert _cache.mastery_report is not None  # guaranteed by _ensure_mastery
        self._html(200, render_mastery(_cache.mastery_report))

    def _handle_mods(self):
        """Serve the missing-mods view."""
        _ensure_mods()
        assert _cache.mods_report is not None  # guaranteed by _ensure_mods
        self._html(200, render_mods(_cache.mods_report))

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
            /mastery        → Mastery progression (blueprint ready, gaps)
            /mods           → Missing mods by in-game category
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
            elif path == "/mastery":
                self._handle_mastery()
            elif path == "/mods":
                self._handle_mods()
            elif path.startswith("/cleanup"):
                self._handle_cleanup(path.removeprefix("/cleanup"))
            elif path == "/foundry-export":
                self._handle_foundry_export(params)
            else:
                self._html(404, "<h1>404 Not Found</h1>")
        except Exception as e:
            # Keep the full traceback in the console; never send internals to
            # the browser (the server can be bound to a LAN address).
            traceback.print_exc()
            message = f"{type(e).__name__}: {e}"[:300]
            self._html(500, f"<h1>500 Error</h1><pre>{html.escape(message)}</pre>")

    def do_POST(self):
        """Handle POST requests — currently only /refresh."""
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/refresh":
            if not self._is_same_origin():
                self._html(403, "<h1>403 Forbidden</h1>")
                return
            inv_path = os.path.join(DATA_DIR, "inventory.json")
            return_path = self._return_path()
            try:
                with _cache_lock:
                    reload_inventory(inv_path, refresh=True)
                    invalidate_analysis()
            except (WarframeNotRunningError, InventoryFetchError) as exc:
                # Surface the failure as an in-page notification instead of a
                # bare error page; cached data stays untouched.
                self._redirect(_with_notice(return_path, str(exc)))
                return
            self._redirect(return_path)
        else:
            self._html(404, "<h1>404 Not Found</h1>")


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

    print("[web] Loading data…", file=sys.stderr)
    load_all(items_cache, inv_path, refresh)
    print(
        f"[web] Data loaded. Starting server on http://{display_host}:{port}",
        file=sys.stderr,
    )

    class ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
        allow_reuse_address = True
        daemon_threads = True

    try:
        server = ThreadedServer((host, port), WarframeHandler)
    except OSError as exc:
        print(
            f"[web] Could not bind {host}:{port}: {exc}. "
            "Use --host/--port or set WARFRAME_WEB_HOST/WARFRAME_WEB_PORT.",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    if not _is_loopback(host):
        print(
            f"[web] warning: bound to {host}, which is reachable from the network. "
            "This UI has no authentication; only expose it on trusted networks.",
            file=sys.stderr,
        )

    with server as httpd:
        print(
            f"[web] Serving at http://{display_host}:{port} (bound to {host}:{port})",
            file=sys.stderr,
        )

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
            print("\n[web] Shutting down.", file=sys.stderr)
        finally:
            # After an interrupt SIGINT stays ignored until the process exits,
            # so a late forwarded Ctrl+C cannot break teardown.
            if not interrupted and previous_handler is not None:
                signal.signal(signal.SIGINT, previous_handler)
