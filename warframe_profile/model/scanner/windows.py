"""Windows process scanning via the Win32 API (through :mod:`ctypes`).

Warframe stores the ``accountId`` and ``nonce`` used by the DE inventory
API in its process heap.  On Linux those values are found by walking
``/proc``; on Windows the equivalent is:

1. Enumerate processes with ``CreateToolhelp32Snapshot`` to find
   ``Warframe.x64.exe``.
2. Open the process with ``OpenProcess``.
3. Walk its address space with ``VirtualQueryEx`` and search each
   readable region with ``ReadProcessMemory``.

The public interface is re-exported by
:mod:`warframe_profile.model.scanner`.

Notes:
    * A 64-bit Python interpreter is required because Warframe is a
      64-bit process; a 32-bit interpreter cannot address its memory.
    * Reading another process may require running the tool with the same
      privileges as the game (typically the same user, or Administrator
      when the game was launched elevated).
"""

from __future__ import annotations

import ctypes
from typing import Any

from warframe_profile.model.errors import WarframeNotRunningError
from warframe_profile.model.scanner._common import (
    DEFAULT_MAX_REGION,
    MAX_IMAGE_REGION,
    WARFRAME_PROCESS_NAME,
    scan_buffer,
)

# ---------------------------------------------------------------------------
# Win32 types
# ---------------------------------------------------------------------------
# ``ctypes.wintypes`` maps several types (notably ``DWORD``) to C ``long``,
# whose width differs between Windows and Linux.  Declaring the fixed-width
# aliases explicitly keeps the structure layout identical everywhere and
# makes it verifiable on non-Windows hosts.

WORD = ctypes.c_uint16
DWORD = ctypes.c_uint32
LONG = ctypes.c_int32
BOOL = ctypes.c_int32
HANDLE = ctypes.c_void_p
SIZE_T = ctypes.c_size_t
ULONG_PTR = ctypes.c_size_t
WCHAR = ctypes.c_uint16

# ---------------------------------------------------------------------------
# Win32 constants
# ---------------------------------------------------------------------------

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010

TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000

PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100

#: Base page protections that allow reading (PAGE_READONLY .. PAGE_EXECUTE_WRITECOPY).
READABLE_PROTECTIONS = frozenset({0x02, 0x04, 0x08, 0x20, 0x40, 0x80})

ERROR_ACCESS_DENIED = 5

MAX_PATH = 260


# ---------------------------------------------------------------------------
# Win32 structures
# ---------------------------------------------------------------------------
# ``_fields_`` names intentionally mirror the Win32 structs for clarity.
# Ruff does not inspect names embedded in ctypes field tuples.


class ProcessEntry32W(ctypes.Structure):
    """``PROCESSENTRY32W`` — one entry from a process snapshot."""

    _fields_ = [
        ("dwSize", DWORD),
        ("cntUsage", DWORD),
        ("th32ProcessID", DWORD),
        ("th32DefaultHeapID", ULONG_PTR),
        ("th32ModuleID", DWORD),
        ("cntThreads", DWORD),
        ("th32ParentProcessID", DWORD),
        ("pcPriClassBase", LONG),
        ("dwFlags", DWORD),
        ("szExeFile", WCHAR * MAX_PATH),
    ]


class MemoryBasicInformation(ctypes.Structure):
    """``MEMORY_BASIC_INFORMATION`` — one virtual-memory region.

    ``PartitionId`` and the padding after it are part of the native layout;
    they are declared explicitly so the structure matches the size the Win32
    API expects on both 32-bit and 64-bit interpreters.
    """

    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", DWORD),
        ("PartitionId", WORD),
        ("_padding1", WORD),
        ("RegionSize", SIZE_T),
        ("State", DWORD),
        ("Protect", DWORD),
        ("Type", DWORD),
    ]


# ---------------------------------------------------------------------------
# Lazy kernel32 binding
# ---------------------------------------------------------------------------

_KERNEL32: Any = None


def _last_error() -> int:
    """Return the calling thread's last Win32 error (0 outside Windows)."""
    getter = getattr(ctypes, "get_last_error", None)
    return int(getter()) if getter is not None else 0


def _kernel32() -> Any:
    """Load ``kernel32`` once and declare every prototype used here."""
    global _KERNEL32
    if _KERNEL32 is not None:
        return _KERNEL32

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    kernel32.CreateToolhelp32Snapshot.argtypes = [DWORD, DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = HANDLE
    kernel32.Process32FirstW.argtypes = [HANDLE, ctypes.POINTER(ProcessEntry32W)]
    kernel32.Process32FirstW.restype = BOOL
    kernel32.Process32NextW.argtypes = [HANDLE, ctypes.POINTER(ProcessEntry32W)]
    kernel32.Process32NextW.restype = BOOL
    kernel32.CloseHandle.argtypes = [HANDLE]
    kernel32.CloseHandle.restype = BOOL

    kernel32.OpenProcess.argtypes = [DWORD, BOOL, DWORD]
    kernel32.OpenProcess.restype = HANDLE

    kernel32.VirtualQueryEx.argtypes = [
        HANDLE,
        ctypes.c_void_p,
        ctypes.POINTER(MemoryBasicInformation),
        SIZE_T,
    ]
    kernel32.VirtualQueryEx.restype = SIZE_T

    kernel32.ReadProcessMemory.argtypes = [
        HANDLE,
        ctypes.c_void_p,
        ctypes.c_void_p,
        SIZE_T,
        ctypes.POINTER(SIZE_T),
    ]
    kernel32.ReadProcessMemory.restype = BOOL

    _KERNEL32 = kernel32
    return kernel32


# ---------------------------------------------------------------------------
# Process discovery
# ---------------------------------------------------------------------------


def _decode_wchar_buffer(buffer: ctypes.Array) -> str:
    """Decode a NUL-terminated ``WCHAR`` array (UTF-16 code units)."""
    chars: list[str] = []
    for code_unit in buffer:
        if code_unit == 0:
            break
        chars.append(chr(code_unit))
    return "".join(chars)


def find_warframe_pid() -> int:
    """Return the PID of the running Warframe process.

    Enumerates processes with ``CreateToolhelp32Snapshot`` and matches
    ``Warframe.x64.exe`` case-insensitively.

    Raises:
        WarframeNotRunningError: if no matching process is found or the
            snapshot cannot be created.
    """
    kernel32 = _kernel32()
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == INVALID_HANDLE_VALUE:
        raise WarframeNotRunningError(
            f"Could not enumerate processes to find Warframe (Win32 error {_last_error()})."
        )

    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(ProcessEntry32W)
        found = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            exe_name = _decode_wchar_buffer(entry.szExeFile)
            if exe_name.casefold() == WARFRAME_PROCESS_NAME.casefold():
                return int(entry.th32ProcessID)
            found = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)

    raise WarframeNotRunningError(
        "Warframe process not found. Launch the game and reach the Orbiter."
    )


# ---------------------------------------------------------------------------
# Memory scanning
# ---------------------------------------------------------------------------


def _open_process(pid: int) -> int:
    """Open *pid* for reading, raising a helpful error on failure."""
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_INFORMATION | PROCESS_VM_READ,
        False,
        pid,
    )
    if not handle:
        error = _last_error()
        if error == ERROR_ACCESS_DENIED:
            raise WarframeNotRunningError(
                "Access denied while reading Warframe memory. Run this tool "
                "from the same user account as the game, or try running it "
                "as Administrator."
            )
        raise WarframeNotRunningError(f"Could not open the Warframe process (Win32 error {error}).")
    return handle


def _is_readable(info: MemoryBasicInformation) -> bool:
    """Return whether a committed region can be read without accessing guards."""
    if info.State != MEM_COMMIT:
        return False
    protection = info.Protect
    if protection & (PAGE_GUARD | PAGE_NOACCESS):
        return False
    return (protection & 0xFF) in READABLE_PROTECTIONS


def _read_region(handle: int, address: int, size: int) -> bytes:
    """Read up to *size* bytes at *address*, returning whatever was readable."""
    kernel32 = _kernel32()
    buffer = ctypes.create_string_buffer(size)
    bytes_read = ctypes.c_size_t(0)
    kernel32.ReadProcessMemory(
        handle,
        ctypes.c_void_p(address),
        ctypes.cast(buffer, ctypes.c_void_p),
        size,
        ctypes.byref(bytes_read),
    )
    if bytes_read.value == 0:
        return b""
    return buffer.raw[: bytes_read.value]


def find_in_memory(
    pid: int,
    pattern: str,
    context: int = 256,
    max_per_region: int = DEFAULT_MAX_REGION,
) -> tuple[int | None, bytes | None]:
    """Search a process's address space for an ASCII *pattern*.

    Walks the target's memory regions with ``VirtualQueryEx`` and scans
    each readable committed region with ``ReadProcessMemory``.  Private
    (heap) regions are clamped to *max_per_region*; image/mapped regions
    are clamped to 8 MiB, mirroring the Linux scanner.

    Args:
        pid: Target process ID.
        pattern: ASCII string to locate.
        context: Number of extra bytes to return around the match.
        max_per_region: Maximum bytes to scan per private region.

    Returns:
        ``(address, surrounding_bytes)`` or ``(None, None)``.

    Raises:
        WarframeNotRunningError: if the process cannot be opened or this is
            a 32-bit interpreter (Warframe is 64-bit).
    """
    if ctypes.sizeof(ctypes.c_void_p) < 8:
        raise WarframeNotRunningError(
            "Warframe is a 64-bit process and can only be read by 64-bit "
            "Python. Install the 64-bit build of Python and try again."
        )

    needle = pattern.encode("ascii")
    kernel32 = _kernel32()
    handle = _open_process(pid)

    try:
        address = 0
        info = MemoryBasicInformation()
        info_size = ctypes.sizeof(MemoryBasicInformation)

        while kernel32.VirtualQueryEx(
            handle,
            ctypes.c_void_p(address),
            ctypes.byref(info),
            info_size,
        ):
            base = int(info.BaseAddress or 0)
            region_size = int(info.RegionSize)
            if region_size <= 0:
                break

            if _is_readable(info):
                limit = (
                    max_per_region
                    if info.Type == MEM_PRIVATE
                    else min(region_size, MAX_IMAGE_REGION)
                )
                size = min(region_size, limit)
                if size > 0:
                    data = _read_region(handle, base, size)
                    match = scan_buffer(data, needle, context, base)
                    if match is not None:
                        return match

            next_address = base + region_size
            if next_address <= address:
                break  # Defensive: never loop on a bogus region size.
            address = next_address
    finally:
        kernel32.CloseHandle(handle)

    return None, None
