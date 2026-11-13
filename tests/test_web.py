"""Tests for web-server helpers and HTML escaping."""

from types import SimpleNamespace

from warframe_profile.model.analysis import (
    AnalysisResult,
    ItemResult,
    MissingPrime,
    PartAnalysis,
)
from warframe_profile.presenter.mastery import (
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
    _item_row,
    render_cleanup_rebuy,
    render_craft_result,
    render_ducats_buildable,
    render_ducats_missing,
    render_home,
    render_mastery,
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


def test_esc_escapes_markup():
    assert _esc("<script>&") == "&lt;script&gt;&amp;"


def test_render_ducats_missing_escapes_names():
    data = AnalysisResult(
        missing=[
            MissingPrime(name="<script>alert(1)</script>", category="<b>Cat</b>", mastery_req=0)
        ]
    )

    page = render_ducats_missing(data)

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
    assert "&lt;b&gt;Cat&lt;/b&gt;" in page


def test_render_ducats_buildable_lists_ready_items():
    item = ItemResult(
        name="Akmagnus Prime",
        category="Secondary",
        status="buildable",
        needed_builds=1,
        extra_builds=0,
        mastery_req=14,
        has_copy=False,
        parts=[],
        can_build=True,
        missing_parts=0,
        total_parts=2,
    )

    page = render_ducats_buildable(AnalysisResult(items=[item]))

    assert "Akmagnus Prime" in page
    assert "/ducats/buildable" in page
    assert "Nothing is ready to build" not in page


def test_render_ducats_buildable_shows_empty_state():
    page = render_ducats_buildable(AnalysisResult())

    assert "Nothing is ready to build" in page
    assert "/ducats/buildable" in page


def test_item_row_escapes_item_and_part_names():
    item = ItemResult(
        name="Lato Prime <b>X</b>",
        category="Secondary",
        status="partial",
        needed_builds=1,
        extra_builds=0,
        mastery_req=0,
        has_copy=False,
        parts=[
            PartAnalysis(
                name="Lato Prime <i>Barrel</i>",
                owned=2,
                needed=1,
                surplus=1,
                missing=0,
                ducats=15,
            )
        ],
        can_build=False,
        missing_parts=0,
        total_parts=1,
    )

    row = _item_row(item)

    assert "Lato Prime &lt;b&gt;X&lt;/b&gt;" in row
    assert "Lato Prime &lt;i&gt;Barrel&lt;/i&gt;" in row
    assert "<i>Barrel</i>" not in row


def test_render_relics_safe_escapes_relic_names():
    relic = SimpleNamespace(
        name="<script>x</script>", tier="Axi", count=1, vaulted=False, safe=True
    )

    page = render_relics_safe([], [relic])

    assert "<script>x</script>" not in page
    assert "&lt;script&gt;" in page


def test_render_cleanup_rebuy_escapes_equipment_names():
    equipment = SimpleNamespace(
        name="<b>Braton</b>",
        category="Primary",
        buy_type="direct",
        rare_component=None,
    )

    page = render_cleanup_rebuy([equipment], [], {}, {})

    assert "<b>Braton</b>" not in page
    assert "&lt;b&gt;Braton&lt;/b&gt;" in page


def test_render_mastery_links_blueprint_rows_to_craft():
    blueprint = MasteryRow(
        name="Silva & Aegis Prime",
        category="Melee",
        mr=14,
        source="Market",
    )
    other = MasteryRow(name="Lato Prime", category="Secondary", mr=14, source="Void")
    report = MasteryReport(
        total=2,
        mastered=0,
        sections=[
            MasterySection(
                header="BLUEPRINT READY",
                desc="Blueprint owned",
                rows=[blueprint],
                group_by_tier=False,
                link_craft=True,
            ),
            MasterySection(
                header="NEVER TOUCHED",
                desc="No XP recorded",
                rows=[other],
                group_by_tier=False,
            ),
            MasterySection(header="IN PROGRESS", desc="Partial XP"),
        ],
    )

    page = render_mastery(report)

    assert 'href="/craft?q=Silva+%26+Aegis+Prime"' in page
    assert "Silva &amp; Aegis Prime" in page
    assert 'href="/craft?q=Lato+Prime"' not in page


def test_render_craft_result_opens_exact_match():
    item = {
        "uniqueName": "/Lotus/Weapons/Tenno/LatoPrime",
        "name": "Lato Prime",
        "category": "Secondary",
    }
    items_by_un = {item["uniqueName"].lower(): item}

    exact = render_craft_result("Lato Prime", [item], items_by_un, {}, {}, {})
    partial = render_craft_result("Lato", [item], items_by_un, {}, {}, {})

    assert "<details open>" in exact
    assert "<details open>" not in partial
