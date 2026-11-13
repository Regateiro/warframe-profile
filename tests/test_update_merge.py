"""Tests for the ``update`` merge pipeline and download retries."""

import pytest

from warframe_profile.scripts import update_export_db as update


def _export() -> dict:
    return {
        "dict": {
            "/Lotus/Language/Weapons/LatoPrime": "Lato Prime",
            "/Lotus/Language/Weapons/LatoPrimeBarrel": "Lato Prime Barrel",
        },
        "weapons": {
            "/Lotus/Weapons/Tenno/LatoPrime": {
                "name": "/Lotus/Language/Weapons/LatoPrime",
                "masteryReq": 4,
                "productCategory": "Pistols",
            },
            "/Lotus/Weapons/Tenno/LatoPrimeBarrel": {
                "name": "/Lotus/Language/Weapons/LatoPrimeBarrel",
                "tradable": True,
                "primeSellingPrice": 15,
            },
        },
        "recipes": {
            "/Lotus/Recipes/LatoPrimeBlueprint": {"resultType": "/Lotus/Weapons/Tenno/LatoPrime"}
        },
    }


def _wfcd() -> list[dict]:
    return [
        {
            "uniqueName": "/Lotus/Weapons/Tenno/LatoPrime",
            "name": "Lato Prime",
            "isPrime": True,
            "category": "Secondary",
            "components": [
                {
                    "uniqueName": "/Lotus/Weapons/Tenno/LatoPrimeBarrel",
                    "itemCount": 1,
                    "ducats": 15,
                    "dropData": {"secret": True},
                }
            ],
        }
    ]


def test_merge_resolves_names_and_enriches_components():
    merged = update.merge(_export(), _wfcd())

    lato = next(
        item for item in merged["items"] if item["uniqueName"] == "/Lotus/Weapons/Tenno/LatoPrime"
    )
    assert lato["name"] == "Lato Prime"
    assert lato["isPrime"] is True
    assert lato["category"] == "Secondary"
    assert lato["masteryReq"] == 4

    component = lato["components"][0]
    assert component["name"] == "Lato Prime Barrel"
    assert component["tradable"] is True
    assert component["primeSellingPrice"] == 15
    assert "dropData" not in component  # bulky data is stripped


def test_merge_keeps_recipes_and_locale():
    export = _export()
    merged = update.merge(export, _wfcd())

    assert merged["recipes"] == export["recipes"]
    assert merged["dict"] == export["dict"]


def test_merge_includes_wfcd_only_prime_items():
    merged = update.merge(
        {"dict": {}, "weapons": {}},
        [
            {
                "uniqueName": "/Lotus/Weapons/Tenno/NewPrime",
                "name": "New Prime",
                "isPrime": True,
            }
        ],
    )

    assert any(item["name"] == "New Prime" for item in merged["items"])


def test_download_json_with_retry_recovers(monkeypatch):
    attempts: list[str] = []

    def fake_download(url: str):
        attempts.append(url)
        if len(attempts) < 3:
            raise OSError("temporary failure")
        return {"ok": True}

    monkeypatch.setattr(update, "download_json", fake_download)
    monkeypatch.setattr(update.time, "sleep", lambda _seconds: None)

    assert update.download_json_with_retry("https://example.test/x") == {"ok": True}
    assert len(attempts) == 3


def test_download_json_with_retry_raises_after_attempts(monkeypatch):
    def always_fail(_url: str):
        raise OSError("nope")

    monkeypatch.setattr(update, "download_json", always_fail)
    monkeypatch.setattr(update.time, "sleep", lambda _seconds: None)

    with pytest.raises(OSError):
        update.download_json_with_retry("https://example.test/x", attempts=2)
