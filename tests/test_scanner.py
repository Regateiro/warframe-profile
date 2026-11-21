"""Tests for shared memory-scan helpers."""

from warframe_profile.model.scanner._common import scan_buffer_all


def test_scan_buffer_all_returns_every_match_with_context():
    data = b"xxaccountId=1yyaccountId=2zz"

    matches = list(scan_buffer_all(data, b"accountId=", context=2, base_address=0x1000))

    assert [address for address, _ in matches] == [0x1002, 0x100F]
    assert matches[0][1] == b"xxac"
    assert matches[1][1] == b"yyac"


def _chunked_reader(data: bytes):
    def read(offset: int, size: int) -> bytes:
        return data[offset : offset + size]

    return read


def test_scan_region_chunks_scans_beyond_the_default_limit(monkeypatch):
    from warframe_profile.model.scanner import _common

    monkeypatch.setattr(_common, "SCAN_CHUNK_SIZE", 4)
    data = b"..." + b"nonce=abc" + b"..." * 10
    matches = list(
        _common.scan_region_chunks(_chunked_reader(data), 0, len(data), b"nonce=", 2, None)
    )

    assert len(matches) == 1
    assert matches[0][0] == 3


def test_scan_region_chunks_respects_max_bytes(monkeypatch):
    from warframe_profile.model.scanner import _common

    monkeypatch.setattr(_common, "SCAN_CHUNK_SIZE", 4)
    data = b"....nonce=late"
    matches = list(_common.scan_region_chunks(_chunked_reader(data), 0, len(data), b"nonce=", 2, 4))

    assert matches == []
