"""Tests for shared model utilities."""

import json

import pytest

from warframe_profile.model.utils import atomic_write_json, normalize_path


def test_normalize_path_lowercases_and_normalises_separators():
    assert normalize_path("/Lotus\\Weapons\\Tenno\\LatoPrime") == "/lotus/weapons/tenno/latoprime"


def test_normalize_path_strips_whitespace():
    assert normalize_path("  /Lotus/Weapons/Lato  ") == "/lotus/weapons/lato"


def test_atomic_write_json_creates_parent_directory(tmp_path):
    path = tmp_path / "nested" / "data.json"
    atomic_write_json(path, {"a": 1}, indent=2)

    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}
    assert not list(path.parent.glob("*.tmp-*"))


def test_atomic_write_json_replaces_existing_file(tmp_path):
    path = tmp_path / "data.json"
    path.write_text('{"old": true}', encoding="utf-8")

    atomic_write_json(path, {"new": True})

    assert json.loads(path.read_text(encoding="utf-8")) == {"new": True}
    assert not list(tmp_path.glob("*.tmp-*"))


def test_atomic_write_json_keeps_original_on_failure(tmp_path):
    path = tmp_path / "data.json"
    path.write_text('{"old": true}', encoding="utf-8")

    with pytest.raises(TypeError):
        atomic_write_json(path, {"bad": object()})

    # The previous file is untouched and no temporary files are left behind.
    assert json.loads(path.read_text(encoding="utf-8")) == {"old": True}
    assert not list(tmp_path.glob("*.tmp-*"))
