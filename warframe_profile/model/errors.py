"""Exception types shared by the data layer and platform scanners.

Kept in a standalone module so that the platform-specific scanner backends
can raise them without importing :mod:`warframe_profile.model.inventory`
(which would create an import cycle).
"""


class WarframeNotRunningError(RuntimeError):
    """Raised when Warframe is not running or its memory cannot be accessed."""


class InventoryFetchError(RuntimeError):
    """Raised when the DE inventory API returns an error or is unreachable."""


class ItemCacheNotFoundError(FileNotFoundError):
    """Raised when the local item export database does not exist."""


class ProfileNotFoundError(RuntimeError):
    """Raised when the DE profile viewing API returns no data."""
