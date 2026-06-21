"""Web server presenter — loads data and serves the web UI."""

import http.server
import os
import socketserver
import sys
import urllib.parse

from warframe_profile import DATA_DIR
from warframe_profile.model.inventory import (
    ExportDB, EQUIPMENT_SECTIONS, load_inventory_with_fallback, build_owned,
)
from warframe_profile.model.craft_model import (
    build_items_by_un, build_recipes_by_result, find_items,
    build_weapon_chains, resolve_tree, resolve_name, build_lookup,
)
from warframe_profile.model.analysis import (
    build_prime_map, analyze, compute_sellable_equipment,
    build_item_index, build_regular_to_prime_map,
    build_relics_map, build_needed_drops,
    find_owned_item_uns, find_owned_prime_uns,
    find_excess_blueprints_and_components,
)
from warframe_profile.model.utils import normalize_path
from warframe_profile.view.web.templates import (
    render_home,
    render_ducats_missing, render_ducats_excess, render_ducats_sell,
    render_relics_needed, render_relics_safe,
    render_craft, render_craft_result, render_weapon_chains,
    render_cleanup_rebuy, render_cleanup_sell, render_cleanup_excess,
)

PORT = int(os.environ.get("WARFRAME_WEB_PORT", "8080"))


# ---------------------------------------------------------------------------
# Global data cache (loaded once at startup)
# ---------------------------------------------------------------------------

class DataCache:
    db: ExportDB | None = None
    inventory: dict = {}
    owned: dict = {}
    items_by_un: dict = {}
    recipes_by_result: dict = {}
    loc_dict: dict = {}
    # Analysis caches
    prime_map: dict | None = None
    analysis_result = None
    relic_info = None
    needed_drops = None
    sellable = None
    owned_prime_pairs = None
    excess_items = None


_cache = DataCache()


def load_all(items_cache: str, inventory_path: str, refresh: bool = False) -> None:
    """Load and cache all data needed by all modes."""
    _cache.db = ExportDB(items_cache)
    _cache.db.load()
    data = _cache.db.raw
    _cache.items_by_un = build_items_by_un(data)
    _cache.recipes_by_result = build_recipes_by_result(data)
    _cache.loc_dict = data.get("dict", {})

    inv, _ = load_inventory_with_fallback(inventory_path, refresh)
    _cache.inventory = inv
    _cache.owned = build_owned(inv, data.get("recipes", {}))


def _ensure_analysis():
    if _cache.analysis_result is not None:
        return
    data = _cache.db.raw
    items = _cache.db.items
    _cache.prime_map = build_prime_map(data.get("items", []))
    _cache.analysis_result = analyze(
        _cache.inventory, _cache.prime_map, EQUIPMENT_SECTIONS,
    )
    _cache.sellable = compute_sellable_equipment(
        _cache.db, _cache.inventory, EQUIPMENT_SECTIONS,
    )
    item_index = build_item_index(items)
    reg_to_prime = build_regular_to_prime_map(items)
    owned_regular = find_owned_item_uns(_cache.inventory, item_index, EQUIPMENT_SECTIONS)
    owned_primes = find_owned_prime_uns(_cache.inventory, item_index, EQUIPMENT_SECTIONS)
    _cache.owned_prime_pairs = []
    for reg_un in owned_regular:
        pi = reg_to_prime.get(reg_un)
        if pi and pi["prime_un"] in owned_primes:
            item = _cache.items_by_un.get(reg_un, {})
            _cache.owned_prime_pairs.append((
                item.get("name", reg_un),
                item.get("category", ""),
                pi["prime_name"],
            ))


def _ensure_excess():
    if _cache.excess_items is not None:
        return
    owned_finished: set[str] = {
        normalize_path(eq.get("ItemType", ""))
        for sect in EQUIPMENT_SECTIONS
        for eq in _cache.inventory.get(sect, [])
    }
    _cache.excess_items = find_excess_blueprints_and_components(
        _cache.inventory, _cache.items_by_un,
        _cache.db.recipes, _cache.owned,
        owned_finished, _cache.loc_dict,
    )


def _ensure_relics():
    if _cache.relic_info is not None:
        return
    items = _cache.db.items
    if _cache.prime_map is None:
        _cache.prime_map = build_prime_map(items)
    _cache.relic_info = build_relics_map(
        items, _cache.inventory, _cache.prime_map, _cache.owned,
        EQUIPMENT_SECTIONS,
    )
    _cache.needed_drops = build_needed_drops(
        items, _cache.inventory, _cache.prime_map, _cache.owned,
        EQUIPMENT_SECTIONS,
    )


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class WarframeHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler — routes paths to handler methods."""

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[web] {args[0]} {args[1]} {args[2]}\n")

    def _html(self, code: int, html: str):
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, path: str):
        self.send_response(302)
        self.send_header("Location", path)
        self.end_headers()

    def _handle_home(self):
        self._html(200, render_home())

    def _handle_ducats(self, subpath: str):
        _ensure_analysis()
        if subpath == "/excess":
            self._html(200, render_ducats_excess(_cache.analysis_result))
        elif subpath == "/sell":
            self._html(200, render_ducats_sell(_cache.analysis_result))
        else:
            self._html(200, render_ducats_missing(_cache.analysis_result))

    def _handle_relics(self, subpath: str):
        _ensure_relics()
        if subpath == "/safe":
            self._html(200, render_relics_safe(_cache.needed_drops, _cache.relic_info))
        else:
            self._html(200, render_relics_needed(_cache.needed_drops, _cache.relic_info))

    def _handle_craft(self, query: str):
        if not query:
            self._html(200, render_craft(
                _cache.items_by_un, _cache.recipes_by_result,
                _cache.owned, _cache.loc_dict,
            ))
            return
        by_name = build_lookup(_cache.items_by_un, _cache.loc_dict)
        matches = find_items(query, by_name)
        if matches:
            self._html(200, render_craft_result(
                query, matches[:5], _cache.items_by_un,
                _cache.recipes_by_result, _cache.owned,
                _cache.loc_dict,
            ))
        else:
            base = render_craft(
                _cache.items_by_un, _cache.recipes_by_result,
                _cache.owned, _cache.loc_dict,
            )
            self._html(200, base.replace(
                'value=""',
                f'value="{query}"',
            ).replace(
                "</form>",
                f'<p class="red">No results for &quot;{query}&quot;</p></form>',
            ))

    def _handle_weapon_chains(self):
        chains, req, craft, shown = build_weapon_chains(
            _cache.items_by_un, _cache.recipes_by_result,
            _cache.owned, _cache.loc_dict,
        )
        self._html(200, render_weapon_chains(
            chains, req, craft, shown,
            _cache.items_by_un, _cache.recipes_by_result,
            _cache.owned, _cache.loc_dict,
        ))

    def _handle_cleanup(self, subpath: str):
        if subpath == "/sell":
            _ensure_analysis()
            self._html(200, render_cleanup_sell(
                _cache.sellable, _cache.owned_prime_pairs,
                _cache.items_by_un, _cache.loc_dict,
            ))
        elif subpath == "/excess":
            _ensure_excess()
            self._html(200, render_cleanup_excess(_cache.excess_items))
        else:
            _ensure_analysis()
            self._html(200, render_cleanup_rebuy(
                _cache.sellable, _cache.owned_prime_pairs,
                _cache.items_by_un, _cache.loc_dict,
            ))

    def do_GET(self):
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
            else:
                self._html(404, "<h1>404 Not Found</h1>")
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self._html(500, f"<h1>500 Error</h1><pre>{e}\n{tb}</pre>")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/refresh":
            inv_path = os.path.join(DATA_DIR, "inventory.json")
            items_cache = os.path.join(DATA_DIR, "export_db.json")
            load_all(items_cache, inv_path, refresh=True)
            _cache.prime_map = None
            _cache.analysis_result = None
            _cache.relic_info = None
            _cache.needed_drops = None
            _cache.sellable = None
            _cache.owned_prime_pairs = None
            _cache.excess_items = None
            self._redirect("/")
        else:
            self._html(404, "<h1>404 Not Found</h1>")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args) -> None:
    items_cache = getattr(args, "items_cache", "") or os.path.join(DATA_DIR, "export_db.json")
    inv_path = getattr(args, "inventory", "") or os.path.join(DATA_DIR, "inventory.json")
    refresh = getattr(args, "refresh", False)
    port = getattr(args, "port", PORT)

    print(f"[web] Loading data…", file=sys.stderr)
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
