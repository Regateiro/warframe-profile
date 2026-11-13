"""Tests for the mod-drop cache location and failure handling."""

import os

from warframe_profile import DATA_DIR
from warframe_profile.model import drop_data


def test_mod_drop_cache_lives_in_the_per_user_data_dir():
    assert os.path.abspath(os.path.dirname(drop_data._CACHE_PATH)) == os.path.abspath(DATA_DIR)


def test_load_mod_drops_tolerates_corrupt_cache(tmp_path, monkeypatch):
    cache = tmp_path / "mod_drop_locations.json"
    cache.write_text("{not valid json", encoding="utf-8")

    monkeypatch.setattr(drop_data, "_CACHE_PATH", str(cache))
    monkeypatch.setattr(drop_data, "_memo", None)
    monkeypatch.setattr(drop_data, "_memo_value", None)

    def offline(*_args, **_kwargs):
        raise OSError("offline")

    monkeypatch.setattr(drop_data.urllib.request, "urlopen", offline)

    # Fetch fails and the stale cache cannot be decoded: return an empty
    # lookup instead of raising.
    assert drop_data.load_mod_drops(force_refresh=True) == {}
