from __future__ import annotations

import sys
from pathlib import Path

# When executed as `python path/to/port`, ensure imports resolve to this
# checkout rather than an installed `port` package from site-packages.
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from port.cli import main

main()
