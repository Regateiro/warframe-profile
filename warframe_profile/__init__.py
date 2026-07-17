"""Warframe prime part inventory analyzer.

Packages the analysis, CLI, and data-fetching modules under a single
namespace.  The only top-level export is DATA_DIR, which points at the
``data/`` directory relative to this package root.
"""

import os

#: Absolute path to the project-level ``data/`` directory where cached
#: inventory JSON, export-database dumps, and similar files live.
#: Resolved relative to this package root so it works regardless of CWD.
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

#: Default DE account ID used for profile display fallback.
DEFAULT_ACCOUNT_ID = "57c71e613ade7fa2a211997b"

#: HTTP request timeout in seconds (used by inventory, drop data, and update scripts).
HTTP_TIMEOUT = 30

#: User-Agent header for DE API requests.
USER_AGENT_DE = "Mozilla/5.0 (compatible; calamity-inc/Soup)"

#: User-Agent header for community data downloads (WFCD).
USER_AGENT_COMMUNITY = "warframe-profile/1.0"
