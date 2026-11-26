"""Make the modules in eval/ importable when tests run from the repository root.

    python -m pytest eval/tests/<file>
"""

import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(EVAL_DIR))
