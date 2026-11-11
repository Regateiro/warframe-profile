"""Cross-platform process/terminal helpers.

This module keeps the small amount of platform-specific setup in one place:

* :func:`configure_stdio` forces UTF-8 output (Windows redirects otherwise
  use the legacy ANSI code page, which crashes on box-drawing characters).
* :func:`enable_windows_vt` turns on ANSI escape processing for classic
  Windows consoles (Windows Terminal already supports it).
* :func:`color_enabled` decides whether ANSI colour should be emitted.
"""

import os
import sys


def configure_stdio() -> None:
    """Configure stdout/stderr for portable UTF-8 output.

    On Windows, redirected output uses the locale code page by default
    (e.g. cp1252), which cannot encode the box-drawing and status glyphs
    used by the reports.  Reconfiguring both streams to UTF-8 keeps output
    identical on every platform.  ``errors="replace"`` guarantees a report
    is never lost to an unencodable character.
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass
    enable_windows_vt()


def color_enabled() -> bool:
    """Return whether ANSI colour should be written to stdout.

    ``NO_COLOR`` always wins, ``FORCE_COLOR`` opts in even when redirected,
    and otherwise colour is used only for an interactive terminal.
    """
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    try:
        return bool(sys.stdout and sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


def enable_windows_vt() -> None:
    """Enable ANSI escape processing on classic Windows consoles.

    No-op on non-Windows platforms and when stdout is not a console.  Any
    failure is ignored — colour is cosmetic and must never break the CLI.
    """
    if os.name != "nt" or not color_enabled():
        return

    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetConsoleMode.restype = wintypes.BOOL
        kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.SetConsoleMode.restype = wintypes.BOOL

        std_output_handle = -11
        enable_virtual_terminal_processing = 0x0004

        handle = kernel32.GetStdHandle(std_output_handle)
        mode = wintypes.DWORD()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | enable_virtual_terminal_processing)
    except (AttributeError, OSError, ValueError, TypeError):
        pass
