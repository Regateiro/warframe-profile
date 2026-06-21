"""Shared utility functions used across model modules."""


def normalize_path(path: str) -> str:
    """Normalise a ``ItemType`` path for consistent dictionary lookups."""
    return path.replace("\\", "/").strip().lower()
