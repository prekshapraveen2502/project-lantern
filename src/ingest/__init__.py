"""Case Study 2, Part 0: ingestion stages.

CS1 modules in src/ import each other by bare name (e.g. `from evaluate import norm_label`
in xbrl.py). Putting src/ on sys.path once here lets src.ingest reuse them unchanged.
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
