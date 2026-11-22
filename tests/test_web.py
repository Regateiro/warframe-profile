"""Tests for web-server helpers and HTML escaping."""

from types import SimpleNamespace

from warframe_profile.model.analysis import (
    AnalysisResult,
    ItemResult,
    NeededPart,
    PartAnalysis,
)
from warframe_profile.model.data import Inventory, ItemData
from warframe_profile.model.mastery import (
    MasteryReport,
    MasteryRow,
    MasterySection,
)
from warframe_profile.presenter.web_server import (
    _display_host,
    _is_loopback,
    _is_same_origin_request,
    _parse_host,
    _with_notice,
)
from warframe_profile.view.web.templates import (
    _esc,
    relic_category_tabs,
    render_cleanup_rebuy,
    render_craft_result,
    render_ducats,
    render_home,
    render_mastery_missing_parts,
    render_mastery_ready,
    render_primes,
    render_relics_needed,
    render_relics_safe,
)


def test_parse_host_defaults_to_loopback(monkeypatch):
    monkeypatch.delenv("WARFRAME_WEB_HOST", raising=False)
    assert _parse_host() == "127.0.0.1"


def test_parse_host_honours_env_override(monkeypatch):
    monkeypatch.setenv("WARFRAME_WEB_HOST", "0.0.0.0")
    assert _parse_host() == "0.0.0.0"


def test_display_host_maps_wildcards_to_localhost():
    assert _display_host("0.0.0.0") == "localhost"
    assert _display_host("::") == "localhost"
    assert _display_host("192.168.1.10") == "192.168.1.10"


def test_is_loopback():
    assert _is_loopback("127.0.0.1")
    assert _is_loopback("localhost")
    assert _is_loopback("::1")
    assert not _is_loopback("0.0.0.0")
    assert not _is_loopback("192.168.1.10")


def test_same_origin_allows_missing_headers():
    # Scripted clients (curl) send neither Origin nor Referer.
    assert _is_same_origin_request("", "", "127.0.0.1:8090")


def test_same_origin_accepts_matching_origin_and_referer():
    assert _is_same_origin_request("http://127.0.0.1:8090", "", "127.0.0.1:8090")
    assert _is_same_origin_request("", "http://127.0.0.1:8090/ducats", "127.0.0.1:8090")


def test_same_origin_rejects_cross_origin_requests():
    assert not _is_same_origin_request("http://evil.example", "", "127.0.0.1:8090")
    assert not _is_same_origin_request("", "http://evil.example/x", "127.0.0.1:8090")
    assert not _is_same_origin_request("null", "", "127.0.0.1:8090")


def test_with_notice_marks_errors():
    url = _with_notice("/ducats", "boom")

    assert "refresh_error=boom" in url
    assert "notice=" not in url


def test_with_notice_marks_informational_messages():
    url = _with_notice("/ducats", "done", error=False)

    assert "notice=done" in url
    assert "refresh_error" not in url


def test_home_page_has_both_refresh_buttons():
    page = render_home()

    assert 'action="/refresh"' in page
    assert 'action="/refresh-items"' in page
    assert "Refresh Inventory" in page
    assert "Refresh Item DB" in page


def test_notifications_are_anchored_to_lower_right():
    page = render_home()

    assert ".notice-stack { position: fixed; bottom: 16px; right: 16px;" in page


def test_notifications_dismiss_automatically_after_a_few_seconds():
    page = render_home()

    assert "}, 5000);" in page


def test_refresh_buttons_show_progress_feedback():
    page = render_home()

    assert "Refreshing data - this can take a few seconds" in page
    assert 'button.textContent = "Refreshing...";' in page


def _bare_handler(web_server):
    """Build a WarframeHandler without opening a socket."""
    handler = object.__new__(web_server.WarframeHandler)
    handler._return_path = lambda: "/ducats"
    return handler


def test_refresh_items_runs_update_then_reloads(monkeypatch):
    from warframe_profile.presenter import web_server
    from warframe_profile.scripts import update_export_db

    calls = []
    handler = _bare_handler(web_server)
    handler._redirect = lambda path: calls.append(("redirect", path))

    monkeypatch.setattr(web_server._cache, "items_cache", "/tmp/export_db.json")
    monkeypatch.setattr(web_server, "load_db", lambda path: calls.append(("load_db", path)))
    monkeypatch.setattr(web_server, "rebuild_derived_state", lambda: calls.append("rebuild"))
    monkeypatch.setattr(
        update_export_db, "main", lambda args: calls.append(("update", args.items_cache))
    )

    web_server.WarframeHandler._handle_refresh_items(handler)

    assert ("update", "/tmp/export_db.json") in calls
    assert ("load_db", "/tmp/export_db.json") in calls
    assert "rebuild" in calls
    assert calls[-1][0] == "redirect"
    assert "notice=" in calls[-1][1]


def test_refresh_items_reports_failure(monkeypatch):
    from warframe_profile.presenter import web_server
    from warframe_profile.scripts import update_export_db

    calls = []
    handler = _bare_handler(web_server)
    handler._redirect = lambda path: calls.append(("redirect", path))

    monkeypatch.setattr(web_server._cache, "items_cache", "/tmp/export_db.json")

    def fail(_args):
        raise OSError("network down")

    monkeypatch.setattr(update_export_db, "main", fail)

    web_server.WarframeHandler._handle_refresh_items(handler)

    assert calls[-1][0] == "redirect"
    assert "refresh_error=" in calls[-1][1]


def test_refresh_redirects_with_success_notice(monkeypatch):
    from warframe_profile.presenter import web_server

    calls = []
    handler = _bare_handler(web_server)
    handler.path = "/refresh"
    handler._redirect = lambda path: calls.append(("redirect", path))
    handler._is_same_origin = lambda: True

    monkeypatch.setattr(web_server._cache, "inventory_path", "/tmp/inventory.json")
    monkeypatch.setattr(
        web_server,
        "reload_inventory",
        lambda path, refresh: calls.append(("reload", path, refresh)),
    )
    monkeypatch.setattr(web_server, "invalidate_analysis", lambda: calls.append("invalidate"))

    web_server.WarframeHandler.do_POST(handler)

    assert ("reload", "/tmp/inventory.json", True) in calls
    assert "invalidate" in calls
    assert calls[-1][0] == "redirect"
    assert "notice=Inventory+refreshed" in calls[-1][1]


def test_esc_escapes_markup():
    assert _esc("<script>&") == "&lt;script&gt;&amp;"


def _sellable_item(
    name: str = "Akmagnus Prime",
    part: str = "Akmagnus Prime Link",
    surplus: int = 2,
    ducats: int = 45,
) -> ItemResult:
    """Minimal prime item with one surplus ducat-valued part."""
    return ItemResult(
        name=name,
        category="Secondary",
        status="partial",
        needed_builds=1,
        extra_builds=0,
        mastery_req=14,
        has_copy=False,
        parts=[
            PartAnalysis(
                name=part,
                owned=surplus,
                needed=1,
                surplus=surplus,
                missing=0,
                ducats=ducats,
            )
        ],
        can_build=False,
        missing_parts=0,
    )


def test_render_ducats_escapes_part_names():
    data = AnalysisResult(
        items=[_sellable_item(name="<script>Lato Prime</script>", part="Barrel <b>x</b>")],
        total_ducats_safe=30,
    )

    page = render_ducats(data)

    assert "<script>Lato Prime</script>" not in page
    assert "&lt;script&gt;Lato Prime&lt;/script&gt;" in page
    assert "<b>x</b>" not in page
    assert "&lt;b&gt;x&lt;/b&gt;" in page
    assert "Total: 30 ducats" in page


def test_ducats_page_has_no_subtabs():
    page = render_ducats(AnalysisResult(items=[_sellable_item()], total_ducats_safe=90))

    assert "Sell for Ducats" in page
    assert 'class="tab-bar"' not in page
    assert "/ducats/excess" not in page
    assert "/ducats/sell" not in page
    assert "Ready to Build" not in page


def test_render_ducats_shows_empty_state():
    page = render_ducats(AnalysisResult())

    assert "Nothing to sell for ducats" in page
    assert 'class="tab-bar"' not in page


def _mastery_report(ready_rows=None) -> MasteryReport:
    """Minimal mastery report for subtab rendering tests."""
    return MasteryReport(
        total=10,
        mastered=4,
        sections=[
            MasterySection(
                header="UNMASTERED OWNED ITEMS",
                desc="Owned or partially leveled",
                group_by_tier=False,
            ),
            MasterySection(
                header="READY TO BUILD",
                desc="All blueprints and materials owned",
                rows=ready_rows or [],
                group_by_tier=False,
                link_craft=True,
            ),
            MasterySection(
                header="MISSING PARTS",
                desc="Missing blueprints or materials",
                group_by_tier=False,
                link_craft=True,
            ),
        ],
        remaining=6,
        total_mr=60_000,
    )


def test_render_mastery_ready_lists_craftable_items():
    item = MasteryRow(
        name="Riot-848",
        category="Secondary",
        mr=14,
        source="Market",
    )

    page = render_mastery_ready(_mastery_report(ready_rows=[item]))

    assert "Riot-848" in page
    assert 'href="/craft?q=Riot-848"' in page
    assert "/mastery/missing-parts" in page
    assert 'class="active">Ready to Build' in page
    assert "Nothing is ready to build" not in page


def test_render_mastery_ready_shows_empty_state():
    page = render_mastery_ready(_mastery_report())

    assert "Nothing is ready to build" in page
    assert "/mastery/missing-parts" in page


def test_ducats_rejects_removed_subpaths():
    from warframe_profile.presenter import web_server

    codes = []
    handler = _bare_handler(web_server)
    handler._html = lambda code, page: codes.append(code)

    web_server.WarframeHandler._handle_ducats(handler, "/buildable")
    web_server.WarframeHandler._handle_ducats(handler, "/sell")

    assert codes == [404, 404]


def test_render_relics_safe_escapes_relic_names():
    relic = SimpleNamespace(
        name="<script>x</script>", tier="Axi", count=1, vaulted=False, safe=True
    )

    page = render_relics_safe([], [relic])

    assert "<script>x</script>" not in page
    assert "&lt;script&gt;" in page


def _needed_part(
    part: str,
    item: str,
    category: str,
    drops: list[tuple[str, int, str, str]] | None = None,
) -> NeededPart:
    return NeededPart(
        part_name=part,
        item_name=item,
        part_type=part,
        category=category,
        missing=1,
        ducats=15,
        drops=drops or [],
    )


def test_render_primes_lists_missing_parts_and_owned_relic_drops():
    needed = [
        _needed_part(
            "Ash Prime Chassis",
            "Ash Prime",
            "Warframes",
            drops=[("Axi A5", 3, "Axi", "Rare")],
        ),
        _needed_part("Orthos Prime Blade", "Orthos Prime", "Melee"),
    ]

    page = render_primes(needed)

    assert "Missing Prime Parts" in page
    assert "2 missing Prime parts" in page
    assert "1 can drop from an owned relic" in page
    assert "Ash Prime Chassis" in page
    assert "Axi A5 x3" in page
    assert "Orthos Prime Blade" in page
    assert "<h3>Warframes</h3>" in page
    assert "<h3>Melee</h3>" in page
    assert 'class="active">Type' in page
    assert "/primes/age" in page
    assert "/primes/rarity" in page
    assert 'class="active">Primes' in page


def test_render_primes_escapes_part_and_relic_names():
    needed = [
        _needed_part(
            "<script>Ash Prime Chassis</script>",
            "<b>Ash Prime</b>",
            "Warframes",
            drops=[("<i>Axi A5</i>", 1, "Axi", "Rare")],
        )
    ]

    page = render_primes(needed)

    assert "<script>Ash Prime Chassis</script>" not in page
    assert "&lt;script&gt;Ash Prime Chassis&lt;/script&gt;" in page
    assert "<b>Ash Prime</b>" not in page
    assert "&lt;b&gt;Ash Prime&lt;/b&gt;" in page
    assert "<i>Axi A5</i>" not in page
    assert "&lt;i&gt;Axi A5&lt;/i&gt;" in page


def test_render_primes_groups_by_relic_age_and_filters_drops():
    needed = [
        _needed_part(
            "Ash Prime Chassis",
            "Ash Prime",
            "Warframes",
            drops=[
                ("Axi A5", 2, "Axi", "Rare"),
                ("Lith S1", 1, "Lith", "Common"),
            ],
        ),
        _needed_part(
            "Orthos Prime Blade",
            "Orthos Prime",
            "Melee",
            drops=[("Neo N1", 1, "Neo", "Uncommon")],
        ),
        _needed_part("Lato Prime Barrel", "Lato Prime", "Secondary"),
    ]

    page = render_primes(needed, "age")

    assert 'class="active">Relic Age' in page
    assert "<th>Type</th>" in page
    assert "<h3>Meso</h3>" not in page
    assert "No owned relic drops" not in page
    lith = page[page.index("<h3>Lith</h3>") : page.index("<h3>Neo</h3>")]
    assert "Lith S1 x1" in lith
    assert "Axi A5" not in lith
    assert "Lato Prime Barrel" in lith
    axi_start = page.index("<h3>Axi</h3>")
    unknown = page.find("<h3>Unknown age</h3>")
    axi = page[axi_start : unknown if unknown != -1 else len(page)]
    assert "Axi A5 x2" in axi
    assert "Lith S1" not in axi
    assert "Lato Prime Barrel" in axi


def test_render_primes_grouped_without_any_owned_relics_shows_single_table():
    needed = [
        _needed_part("Ash Prime Chassis", "Ash Prime", "Warframes"),
        _needed_part("Orthos Prime Blade", "Orthos Prime", "Melee"),
    ]

    page = render_primes(needed, "age")

    assert "No owned relic drops" not in page
    assert "<h3>" not in page
    assert "Ash Prime Chassis" in page
    assert "Orthos Prime Blade" in page


def test_render_primes_groups_by_rarity_and_filters_drops():
    needed = [
        _needed_part(
            "Ash Prime Chassis",
            "Ash Prime",
            "Warframes",
            drops=[
                ("Axi A5", 2, "Axi", "Rare"),
                ("Lith S1", 1, "Lith", "Common"),
            ],
        ),
        _needed_part(
            "Orthos Prime Blade",
            "Orthos Prime",
            "Melee",
            drops=[("Neo N1", 1, "Neo", "Uncommon")],
        ),
        _needed_part(
            "Lato Prime Barrel",
            "Lato Prime",
            "Secondary",
            drops=[("Meso M1", 1, "Meso", "")],
        ),
        _needed_part("Boltor Prime Stock", "Boltor Prime", "Primary"),
    ]

    page = render_primes(needed, "rarity")

    assert 'class="active">Rarity' in page
    assert "No owned relic drops" not in page
    assert page.index("<h3>Common</h3>") < page.index("<h3>Uncommon</h3>")
    assert page.index("<h3>Uncommon</h3>") < page.index("<h3>Rare</h3>")
    assert page.index("<h3>Rare</h3>") < page.index("<h3>Unknown rarity</h3>")
    common = page[page.index("<h3>Common</h3>") : page.index("<h3>Uncommon</h3>")]
    assert "Lith S1 x1" in common
    assert "Axi A5" not in common
    assert "Boltor Prime Stock" in common
    rare = page[page.index("<h3>Rare</h3>") : page.index("<h3>Unknown rarity</h3>")]
    assert "Axi A5 x2" in rare
    assert "Lith S1" not in rare
    assert "Boltor Prime Stock" in rare
    unknown = page[page.index("<h3>Unknown rarity</h3>") :]
    assert "Lato Prime Barrel" in unknown
    assert "Meso M1 x1" in unknown
    assert "Boltor Prime Stock" in unknown


def test_render_primes_shows_empty_state():
    page = render_primes([])

    assert "No Prime parts are missing" in page
    assert 'class="active">Primes' in page


def test_primes_route_accepts_views_and_rejects_subpaths(monkeypatch):
    from warframe_profile.presenter import web_server

    needed = [_needed_part("Ash Prime Chassis", "Ash Prime", "Warframes")]
    monkeypatch.setattr(
        web_server._cache,
        "inventory",
        SimpleNamespace(needed_drops=needed),
    )
    monkeypatch.setattr(
        web_server,
        "render_primes",
        lambda n, view="type": f"primes-{view}",
    )

    calls = []
    handler = _bare_handler(web_server)
    handler._html = lambda code, page: calls.append((code, page))

    web_server.WarframeHandler._handle_primes(handler, "")
    web_server.WarframeHandler._handle_primes(handler, "/age")
    web_server.WarframeHandler._handle_primes(handler, "/rarity")
    web_server.WarframeHandler._handle_primes(handler, "/type")
    web_server.WarframeHandler._handle_primes(handler, "/unknown")

    not_found = (404, "<h1>404 Not Found</h1>")
    assert calls == [
        (200, "primes-type"),
        (200, "primes-age"),
        (200, "primes-rarity"),
        not_found,
        not_found,
    ]


def test_relic_category_tabs_put_safe_first_and_order_categories():
    needed = [
        _needed_part("Orthos Prime Blade", "Orthos Prime", "Melee"),
        _needed_part("Ash Prime Chassis", "Ash Prime", "Warframes"),
        _needed_part("Odonata Prime Wings", "<ARCHWING> Odonata Prime", "Archwing"),
        _needed_part("Corvas Prime Barrel", "<ARCHWING> Corvas Prime", "Arch-Gun"),
    ]

    tabs = relic_category_tabs(needed)

    assert tabs[0] == ("safe", "Safe to Spam", "/relics")
    assert [key for key, _, _ in tabs[1:]] == [
        "warframes",
        "melee",
        "archwing",
        "arch-gun",
    ]


def test_render_relics_needed_filters_by_category():
    needed = [
        _needed_part("Ash Prime Chassis", "Ash Prime", "Warframes"),
        _needed_part("Orthos Prime Blade", "Orthos Prime", "Melee"),
    ]

    page = render_relics_needed(needed, "warframes")

    assert "Ash Prime Chassis" in page
    assert "Orthos Prime Blade" not in page
    assert "/relics/needed/melee" in page
    assert 'class="active">Warframes' in page


def test_relics_route_defaults_to_safe_and_rejects_unsupported_paths(monkeypatch):
    from warframe_profile.presenter import web_server

    needed = [_needed_part("Ash Prime Chassis", "Ash Prime", "Warframes")]
    monkeypatch.setattr(
        web_server._cache,
        "inventory",
        SimpleNamespace(needed_drops=needed, relic_info=[]),
    )
    monkeypatch.setattr(web_server, "render_relics_safe", lambda n, r: "safe-page")
    monkeypatch.setattr(
        web_server,
        "render_relics_needed",
        lambda n, slug: f"needed-{slug}",
    )

    calls = []
    handler = _bare_handler(web_server)
    handler._html = lambda code, page: calls.append((code, page))

    web_server.WarframeHandler._handle_relics(handler, "")
    web_server.WarframeHandler._handle_relics(handler, "/safe")
    web_server.WarframeHandler._handle_relics(handler, "/needed")
    web_server.WarframeHandler._handle_relics(handler, "/needed/unknown")
    web_server.WarframeHandler._handle_relics(handler, "/needed/warframes")

    not_found = (404, "<h1>404 Not Found</h1>")
    assert calls == [
        (200, "safe-page"),
        not_found,
        not_found,
        not_found,
        (200, "needed-warframes"),
    ]


def test_mastery_rejects_legacy_paths(monkeypatch):
    from warframe_profile.presenter import web_server

    monkeypatch.setattr(
        web_server._cache,
        "inventory",
        SimpleNamespace(mastery_report=object()),
    )

    codes = []
    handler = _bare_handler(web_server)
    handler._html = lambda code, page: codes.append(code)

    for path in (
        "/in-progress",
        "/blueprint-ready",
        "/never-touched",
        "/owned-blueprints",
        "/missing-blueprints",
        "/unknown",
    ):
        web_server.WarframeHandler._handle_mastery(handler, path)

    assert codes == [404, 404, 404, 404, 404, 404]


def test_render_cleanup_rebuy_escapes_equipment_names():
    equipment = SimpleNamespace(
        name="<b>Braton</b>",
        category="Primary",
        buy_type="direct",
        rare_component=None,
    )

    page = render_cleanup_rebuy([equipment])

    assert "<b>Braton</b>" not in page
    assert "&lt;b&gt;Braton&lt;/b&gt;" in page


def test_render_mastery_missing_parts_links_rows_to_craft():
    owned_blueprint = MasteryRow(
        name="Silva & Aegis Prime",
        category="Melee",
        mr=14,
        source="Market",
    )
    missing_blueprint = MasteryRow(name="Lato Prime", category="Secondary", mr=14, source="Void")
    report = MasteryReport(
        total=2,
        mastered=0,
        sections=[
            MasterySection(
                header="UNMASTERED OWNED ITEMS",
                desc="Owned or partially leveled",
                group_by_tier=False,
            ),
            MasterySection(
                header="READY TO BUILD",
                desc="All blueprints and materials owned",
                group_by_tier=False,
                link_craft=True,
            ),
            MasterySection(
                header="MISSING PARTS",
                desc="Missing blueprints or materials",
                rows=[owned_blueprint, missing_blueprint],
                group_by_tier=False,
                link_craft=True,
            ),
        ],
    )

    page = render_mastery_missing_parts(report)

    assert 'href="/craft?q=Silva+%26+Aegis+Prime"' in page
    assert "Silva &amp; Aegis Prime" in page
    assert 'href="/craft?q=Lato+Prime"' in page
    assert 'class="active">Missing Parts' in page
    assert "/mastery/missing-parts" in page


def test_render_craft_result_opens_exact_match():
    item = {
        "uniqueName": "/Lotus/Weapons/Tenno/LatoPrime",
        "name": "Lato Prime",
        "category": "Secondary",
    }
    raw = {"items": [item], "recipes": {}, "dict": {}}
    db = SimpleNamespace(items=[item], recipes={}, locale={}, raw=raw)
    item_data = ItemData(db)
    inventory = Inventory({}, item_data)

    plan = item_data.craft_plan(item["uniqueName"], inventory)
    exact = render_craft_result("Lato Prime", [plan])
    partial = render_craft_result("Lato", [plan])

    assert "<details open>" in exact
    assert "<details open>" not in partial
