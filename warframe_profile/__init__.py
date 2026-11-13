"""Warframe prime part inventory analyzer.

Packages the analysis, CLI, and data-fetching modules under a single
namespace.  Top-level constants are the per-user :data:`DATA_DIR` (the
``~/.local/share`` directory on Linux, ``%LOCALAPPDATA%`` on Windows) plus
the shared HTTP timeout and user-agent strings.
"""

import os


def _default_data_dir() -> str:
    """Return the platform-appropriate per-user data directory."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Local"
        )
        return os.path.join(base, "warframe-profile")
    return os.path.expanduser(os.path.join("~", ".local", "share", "warframe-profile"))


#: Absolute path to the per-user data directory where cached inventory
#: JSON, export-database dumps, and similar files live.
#: Defaults to ``~/.local/share/warframe-profile`` on Linux/macOS and
#: ``%LOCALAPPDATA%\\warframe-profile`` on Windows; override with the
#: ``WARFRAME_DATA_DIR`` environment variable.
DATA_DIR = os.environ.get("WARFRAME_DATA_DIR") or _default_data_dir()

#: HTTP request timeout in seconds (used by inventory, drop data, and update scripts).
HTTP_TIMEOUT = 30

#: User-Agent header for DE API requests.
USER_AGENT_DE = "Mozilla/5.0 (compatible; calamity-inc/Soup)"

#: User-Agent header for community data downloads (WFCD).
USER_AGENT_COMMUNITY = "warframe-profile/1.0"
