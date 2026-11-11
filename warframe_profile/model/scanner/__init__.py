"""Platform-dispatching process scanner.

Exposes the same two functions on every supported platform:

* :func:`find_warframe_pid` — locate the running Warframe client.
* :func:`find_in_memory` — search its address space for an ASCII pattern.

Linux (including Wine/Proton) reads ``/proc``; Windows uses the Win32 API
through :mod:`ctypes`.
"""

import sys

from warframe_profile.model.scanner._common import WARFRAME_PROCESS_NAME

if sys.platform == "win32":
    from warframe_profile.model.scanner.windows import find_in_memory, find_warframe_pid
else:
    from warframe_profile.model.scanner.linux import find_in_memory, find_warframe_pid

__all__ = ["WARFRAME_PROCESS_NAME", "find_in_memory", "find_warframe_pid"]
