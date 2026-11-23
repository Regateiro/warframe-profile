"""End-to-end CLI and web smoke tests against the bundled data fixtures."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from warframe_profile.__main__ import main as cli_main
from warframe_profile.model import drop_data
from warframe_profile.presenter import web_server

_DATA = Path(__file__).resolve().parent.parent / "data"
_ITEMS = _DATA / "export_db.json"
_INVENTORY = _DATA / "inventory.json"


@pytest.fixture(autouse=True)
def _offline_mod_drops(tmp_path, monkeypatch):
    cache = tmp_path / "mod_drop_locations.json"
    cache.write_text(json.dumps({"modLocations": []}), encoding="utf-8")
    monkeypatch.setattr(drop_data, "_CACHE_PATH", str(cache))


@pytest.mark.parametrize(
    "command",
    [
        ["ducats"],
        ["relics"],
        ["cleanup"],
        ["mastery"],
        ["mods"],
        ["craft", "Cedo", "--select", "0"],
    ],
)
def test_cli_commands_run_end_to_end(monkeypatch, command, capsys):
    monkeypatch.setattr(
        "sys.argv",
        [
            "warframe-profile",
            "--items-cache",
            str(_ITEMS),
            "--inventory",
            str(_INVENTORY),
            *command,
        ],
    )

    assert cli_main() == 0


def test_version_flag(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["warframe-profile", "--version"])

    with pytest.raises(SystemExit) as exc:
        cli_main()

    assert exc.value.code == 0
    assert "1.0" in capsys.readouterr().out


def _serve():
    web_server.load_all(str(_ITEMS), str(_INVENTORY))
    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.WarframeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_web_routes_render_end_to_end():
    server, thread = _serve()
    host, port = server.server_address[:2]
    assert web_server._cache.inventory is not None
    needed = web_server._cache.inventory.needed_drops
    from warframe_profile.view.web.templates import relic_category_tabs

    routes = [
        "/",
        "/relics",
        "/relics/age",
        "/relics/age/lith",
        "/relics/age/meso",
        "/relics/age/neo",
        "/relics/age/axi",
        "/relics/rarity",
        "/relics/rarity/common",
        "/relics/rarity/uncommon",
        "/relics/rarity/rare",
        "/ducats",
        "/craft?q=Cedo",
        "/weapon-chains",
        "/mastery",
        "/mastery/ready",
        "/mastery/missing-parts",
        "/mods",
        "/cleanup",
        "/cleanup/sell",
        "/cleanup/excess",
    ]
    routes.extend(url for _key, _label, url in relic_category_tabs(needed)[1:2])
    try:
        for route in routes:
            with urllib.request.urlopen(f"http://{host}:{port}{route}", timeout=30) as response:
                assert response.status == 200, route
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_cross_origin_refresh_is_rejected():
    server, thread = _serve()
    host, port = server.server_address[:2]
    request = urllib.request.Request(
        f"http://{host}:{port}/refresh",
        data=b"",
        method="POST",
        headers={"Origin": "http://evil.example"},
    )
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(request, timeout=30)
        assert exc.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
