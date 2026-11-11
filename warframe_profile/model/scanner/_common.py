"""Constants and helpers shared by the platform memory scanners."""

#: Executable name of the Warframe client (the same name under Wine/Proton).
WARFRAME_PROCESS_NAME = "Warframe.x64.exe"

#: Maximum bytes to scan in a file-backed / image memory region.
MAX_IMAGE_REGION = 8 * 1024 * 1024

#: Default maximum bytes to scan in an anonymous (heap) memory region.
DEFAULT_MAX_REGION = 64 * 1024 * 1024


def scan_buffer(
    data: bytes,
    needle: bytes,
    context: int,
    base_address: int,
) -> tuple[int, bytes] | None:
    """Search *data* for *needle* and return ``(address, surrounding_bytes)``.

    The returned bytes are clamped to *context* bytes on either side of the
    match, mirroring the historical Linux scanner behaviour.  Returns
    ``None`` when the pattern is absent.
    """
    index = data.find(needle)
    if index == -1:
        return None
    low = max(0, index - context)
    high = min(len(data), index + context)
    return base_address + index, data[low:high]
