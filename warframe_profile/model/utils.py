"""Shared utility functions used across model modules.

``normalize_path`` is imported by every other model module to ensure
inventory and recipe keys are compared consistently.

``atomic_write_json`` is the single place that persists JSON to disk, so a
crash or Ctrl+C during a write can never leave a truncated cache file.
"""

import contextlib
import json
import os
from typing import Any


def normalize_path(path: str) -> str:
    """Normalise a ``ItemType`` path for consistent dictionary lookups.

    The DE inventory API uses mixed case and backslashes in paths like
    ``/Lotus/Weapons/Tenno/Pistols/LatoPrime``.  This lowercases and
    normalises separators so lookups against dictionary keys always match.
    """
    return path.replace("\\", "/").strip().lower()


def atomic_write_json(path: str, data: Any, **dump_kwargs) -> None:
    """Serialise *data* to *path* atomically.

    The JSON is written to a temporary file in the same directory, flushed,
    and then moved into place with :func:`os.replace`.  Readers therefore see
    either the previous file or the complete new one, never a partial write.

    Args:
        path: Destination file path.
        data: JSON-serialisable payload.
        **dump_kwargs: Extra keyword arguments for :func:`json.dump`.

    Raises:
        OSError: if the directory cannot be created or the write fails.
    """
    directory = os.path.dirname(os.path.abspath(path))
    tmp_path = f"{path}.tmp-{os.getpid()}"
    try:
        os.makedirs(directory, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, **dump_kwargs)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp_path)
