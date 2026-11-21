"""Tests for shared memory-scan helpers."""

from warframe_profile.model.scanner._common import scan_buffer_all


def test_scan_buffer_all_returns_every_match_with_context():
    data = b"xxaccountId=1yyaccountId=2zz"

    matches = list(scan_buffer_all(data, b"accountId=", context=2, base_address=0x1000))

    assert [address for address, _ in matches] == [0x1002, 0x100F]
    assert matches[0][1] == b"xxac"
    assert matches[1][1] == b"yyac"
