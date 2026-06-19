"""Web server presenter — loads data and serves the web UI."""

import http.server
import json
import os
import socketserver
import sys
import urllib.parse

from warframe_profile import DATA_DIR
from warframe_profile.model.inventory import (
    ExportDB, load_inventory_with_fallback, build_owned,
)
from warframe_profile.model.craft_model import (
    build_items_by_un, build_recipes_by_result, find_items,
    build_weapon_chains, resolve_tree, resolve_name, build_lookup,
)
from warframe_profile.model.analysis import (
    build_prime_map, analyze, compute_sellable_equipment,
    build_item_index, build_regular_to_prime_map,
    build_relics_map, build_needed_drops, normalize_path,
    find_owned_item_uns, find_owned_prime_uns,
    find_excess_blueprints_and_components,
)
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
    _cache.owned = build_owned(inv)


def _ensure_analysis():
    if _cache.analysis_result is not None:
        return
    data = _cache.db.raw
    _cache.prime_map = build_prime_map(data.get("items", []))
    _cache.analysis_result = analyze(
        _cache.inventory, _cache.prime_map,
        ["Suits", "LongGuns", "Pistols", "Melee",
         "Sentinels", "SentinelWeapons", "SpaceSuits",
         "SpaceGuns", "SpaceMelee"],
    )
    items = _cache.db.items
    _cache.sellable = compute_sellable_equipment(
        _cache.db, _cache.inventory,
        ["Suits", "LongGuns", "Pistols", "Melee",
         "Sentinels", "SentinelWeapons", "SpaceSuits",
         "SpaceGuns", "SpaceMelee"],
    )
    item_index = build_item_index(items)
    reg_to_prime = build_regular_to_prime_map(items)
    _equip_sections = ["Suits", "LongGuns", "Pistols", "Melee",
                       "Sentinels", "SentinelWeapons", "SpaceSuits",
                       "SpaceGuns", "SpaceMelee"]
    owned_regular = find_owned_item_uns(_cache.inventory, item_index, _equip_sections)
    owned_primes = find_owned_prime_uns(_cache.inventory, item_index, _equip_sections)
    pairs = []
    for reg_un in owned_regular:
        pi = reg_to_prime.get(reg_un)
        if pi and pi["prime_un"] in owned_primes:
            item = _cache.items_by_un.get(reg_un, {})
            pairs.append((
                item.get("name", reg_un),
                item.get("category", ""),
                pi["prime_name"],
            ))
    _cache.owned_prime_pairs = pairs


def _ensure_excess():
    if _cache.excess_items is not None:
        return
    _equip_sections = ["Suits", "LongGuns", "Pistols", "Melee",
                       "Sentinels", "SentinelWeapons", "SpaceSuits",
                       "SpaceGuns", "SpaceMelee"]
    owned_finished: set[str] = {
        normalize_path(eq.get("ItemType", ""))
        for sect in _equip_sections
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
    data = _cache.db.raw
    items = _cache.db.items
    if _cache.prime_map is None:
        _cache.prime_map = build_prime_map(items)
    _cache.relic_info = build_relics_map(
        items, _cache.inventory, _cache.prime_map, _cache.owned,
        ["Suits", "LongGuns", "Pistols", "Melee",
         "Sentinels", "SentinelWeapons", "SpaceSuits",
         "SpaceGuns", "SpaceMelee"],
    )
    _cache.needed_drops = build_needed_drops(
        items, _cache.inventory, _cache.prime_map, _cache.owned,
        ["Suits", "LongGuns", "Pistols", "Melee",
         "Sentinels", "SentinelWeapons", "SpaceSuits",
         "SpaceGuns", "SpaceMelee"],
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

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        params = urllib.parse.parse_qs(parsed.query)

        try:
            if path == "" or path == "/":
                self._html(200, render_home())
            elif path == "/ducats":
                _ensure_analysis()
                self._html(200, render_ducats_missing(_cache.analysis_result))
            elif path == "/ducats/excess":
                _ensure_analysis()
                self._html(200, render_ducats_excess(_cache.analysis_result))
            elif path == "/ducats/sell":
                _ensure_analysis()
                self._html(200, render_ducats_sell(_cache.analysis_result))
            elif path == "/relics":
                _ensure_relics()
                self._html(200, render_relics_needed(_cache.needed_drops, _cache.relic_info))
            elif path == "/relics/safe":
                _ensure_relics()
                self._html(200, render_relics_safe(_cache.needed_drops, _cache.relic_info))
            elif path == "/craft":
                q = (params.get("q") or [""])[0].strip()
                if q:
                    by_name = build_lookup(_cache.items_by_un, _cache.loc_dict)
                    matches = find_items(q, by_name)
                    if matches:
                        self._html(200, render_craft_result(
                            q, matches[:5], _cache.items_by_un,
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
                            f'value="{q}"',
                        ).replace(
                            "</form>",
                            f'<p class="red">No results for &quot;{q}&quot;</p></form>',
                        ))
                else:
                    self._html(200, render_craft(
                        _cache.items_by_un, _cache.recipes_by_result,
                        _cache.owned, _cache.loc_dict,
                    ))
            elif path == "/weapon-chains":
                chains, req, craft, shown = build_weapon_chains(
                    _cache.items_by_un, _cache.recipes_by_result,
                    _cache.owned, _cache.loc_dict,
                )
                self._html(200, render_weapon_chains(
                    chains, req, craft, shown,
                    _cache.items_by_un, _cache.recipes_by_result,
                    _cache.owned, _cache.loc_dict,
                ))
            elif path == "/cleanup":
                _ensure_analysis()
                self._html(200, render_cleanup_rebuy(
                    _cache.sellable, _cache.owned_prime_pairs,
                    _cache.items_by_un, _cache.loc_dict,
                ))
            elif path == "/cleanup/sell":
                _ensure_analysis()
                self._html(200, render_cleanup_sell(
                    _cache.sellable, _cache.owned_prime_pairs,
                    _cache.items_by_un, _cache.loc_dict,
                ))
            elif path == "/cleanup/excess":
                _ensure_excess()
                self._html(200, render_cleanup_excess(
                    _cache.excess_items,
                ))
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
            data_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data")
            inv_path = os.path.join(data_dir, "inventory.json")
            items_cache = os.path.join(data_dir, "export_db.json")
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
    data_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data")
    items_cache = getattr(args, "items_cache", "") or os.path.join(data_dir, "export_db.json")
    inv_path = getattr(args, "inventory", "") or os.path.join(data_dir, "inventory.json")
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
