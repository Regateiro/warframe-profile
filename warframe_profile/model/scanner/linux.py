"""Linux process scanning via ``/proc``.

Used both for a native Linux build and for Warframe running under
Wine/Proton.  The public interface is re-exported by
:mod:`warframe_profile.model.scanner`.
"""

import os
from collections.abc import Generator

from warframe_profile.model.errors import WarframeNotRunningError
from warframe_profile.model.scanner._common import (
    DEFAULT_MAX_REGION,
    MAX_IMAGE_REGION,
    WARFRAME_PROCESS_NAME,
    scan_region_chunks,
)


def find_warframe_pid() -> int:
    """Return the PID of the running Warframe process.

    Scans ``/proc/<pid>/cmdline`` for ``Warframe.x64.exe``.
    Non-digit /proc entries are skipped (kernel threads, etc.).

    Raises:
        WarframeNotRunningError: if no matching process is found.
    """
    try:
        entries = os.listdir("/proc")
    except OSError:
        raise WarframeNotRunningError(
            "Warframe process not found. Live inventory needs Linux /proc; "
            "launch the game and reach the Orbiter."
        ) from None
    for pid_str in entries:
        if not pid_str.isdigit():
            continue
        try:
            with open(f"/proc/{pid_str}/cmdline", encoding="utf-8", errors="replace") as f:
                cmd = f.read()
            if WARFRAME_PROCESS_NAME in cmd:
                return int(pid_str)
        except OSError:
            pass
    raise WarframeNotRunningError(
        "Warframe process not found. Launch the game and reach the Orbiter."
    )


def iter_in_memory(
    pid: int,
    pattern: str,
    context: int = 256,
    max_per_region: int | None = DEFAULT_MAX_REGION,
) -> Generator[tuple[int, bytes], None, None]:
    """Yield every occurrence of an ASCII *pattern* in a process's memory.

    Works like :func:`find_in_memory` but keeps scanning after the first
    match, so callers can disambiguate stale or unrelated copies of a string.
    Large anonymous (heap) regions are clamped to *max_per_region*;
    file-backed regions are clamped to 8 MiB.  Pass ``None`` to scan whole
    regions in bounded chunks (the credential fallback).

    Args:
        pid: Target process ID.
        pattern: ASCII string to locate.
        context: Number of extra bytes to return around each match.
        max_per_region: Maximum bytes per region, or ``None`` for a full scan.

    Yields:
        ``(address, surrounding_bytes)`` for every match, in memory order.
    """
    needle = pattern.encode("ascii")
    try:
        with open(f"/proc/{pid}/maps", encoding="utf-8", errors="replace") as f:
            regions: list[list[str]] = [line.split() for line in f]
    except OSError:
        return  # /proc unavailable (no access) → no match

    for parts in regions:
        perms = parts[1]
        if "r" not in perms:
            continue
        start_str, end_str = parts[0].split("-")
        start = int(start_str, 16)
        end = int(end_str, 16)

        # Anonymous (heap) regions are scanned up to max_per_region;
        # file-backed regions are limited to 8 MiB.
        is_anon = len(parts) < 6 or parts[-1].startswith("[") or parts[-1] == ""
        limit = max_per_region if is_anon else MAX_IMAGE_REGION
        if limit is not None:
            limit = min(end - start, limit)

        try:
            with open(f"/proc/{pid}/mem", "rb") as mem:

                def read(offset: int, size: int) -> bytes:
                    mem.seek(offset)
                    return mem.read(size)

                yield from scan_region_chunks(read, start, end - start, needle, context, limit)
        except (OSError, PermissionError):
            pass


def find_in_memory(
    pid: int,
    pattern: str,
    context: int = 256,
    max_per_region: int | None = DEFAULT_MAX_REGION,
) -> tuple[int | None, bytes | None]:
    """Search a process's address space for an ASCII *pattern*.

    Returns only the first match.  Use :func:`iter_in_memory` when every
    occurrence is needed.

    Args:
        pid: Target process ID.
        pattern: ASCII string to locate.
        context: Number of extra bytes to return around the match.
        max_per_region: Maximum bytes per region, or ``None`` for a full scan.

    Returns:
        ``(address, surrounding_bytes)`` or ``(None, None)``.
    """
    matches = iter_in_memory(pid, pattern, context, max_per_region)
    try:
        return next(matches, (None, None))
    finally:
        matches.close()
