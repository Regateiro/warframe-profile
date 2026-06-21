#!/usr/bin/env python3
"""Warframe Profile — unified project entrypoint.

Thin wrapper that delegates to :func:`warframe_profile.__main__.main`.
All four sub-commands are accessed through this script:

.. code-block:: text

    python warframe.py --craft    [args...]
    python warframe.py --ducats   [args...]
    python warframe.py --cleanup  [args...]
    python warframe.py --update   (downloads latest export data)
"""

import sys

from warframe_profile.__main__ import main

# Entry point: parse CLI args, dispatch to sub-command, return exit code.
sys.exit(main())
