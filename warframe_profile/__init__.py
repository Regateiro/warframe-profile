"""Warframe prime part inventory analyzer.

Packages the analysis, CLI, and data-fetching modules under a single
namespace.  The only top-level export is DATA_DIR, which points at the
``data/`` directory relative to this package root.
"""

import os

#: Absolute path to the project-level ``data/`` directory where cached
#: inventory JSON, export-database dumps, and similar files live.
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
