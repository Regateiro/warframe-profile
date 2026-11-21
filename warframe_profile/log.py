"""Shared logging configuration for diagnostics.

User-facing report output stays on stdout via ``print``; operational
messages (startup, refresh progress, cache warnings) go through logging so
they can be filtered and redirected consistently.
"""

import logging
import os
import sys

_CONFIGURED = False


def configure(level: str | None = None) -> None:
    """Configure stderr logging once, honouring ``WARFRAME_LOG_LEVEL``."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    resolved = (level or os.environ.get("WARFRAME_LOG_LEVEL") or "INFO").upper()
    logging.basicConfig(
        stream=sys.stderr,
        level=getattr(logging, resolved, logging.INFO),
        format="[%(levelname)s] %(message)s",
    )
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the ``warframe_profile`` namespace."""
    configure()
    return logging.getLogger(f"warframe_profile.{name}")
