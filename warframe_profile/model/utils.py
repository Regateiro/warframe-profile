"""Shared utility functions used across model modules.

The only function here, :func:`normalize_path`, is imported by every other
model module to ensure inventory and recipe keys are compared consistently.
"""


def normalize_path(path: str) -> str:
    """Normalise a ``ItemType`` path for consistent dictionary lookups.

    The DE inventory API uses mixed case and backslashes in paths like
    ``/Lotus/Weapons/Tenno/Pistols/LatoPrime``.  This lowercases and
    normalises separators so lookups against dictionary keys always match.
    """
    return path.replace("\\", "/").strip().lower()
