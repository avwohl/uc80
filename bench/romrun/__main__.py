"""python3 -m romrun, or python3 bench/romrun."""

import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from romrun.cli import main
else:
    from .cli import main

sys.exit(main())
