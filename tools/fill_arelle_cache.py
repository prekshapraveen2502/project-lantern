"""One-time: fill a project-local Arelle taxonomy cache from the 16 ingested filings, then check the guard offline.

Usage: python tools/fill_arelle_cache.py <repo root> <cache dir> online|offline
  online   Arelle may download taxonomy files into <cache dir> (network on)
  offline  Arelle is set to workOffline and may only read <cache dir> (no requests)
For every filing (data/edgar_comps/{T}/{10-K,10-Q}/{accn}/unpacked/{doc}) it prints the number of numeric facts
Arelle loaded and the number of ix:nonFraction tags in the HTML. The guard passes when the two are equal.
Writes only into <cache dir>. Exits 1 if any filing fails the guard.
"""
import json
import re
import sys
from pathlib import Path

from arelle import Cntlr

root, cache, mode = Path(sys.argv[1]), Path(sys.argv[2]).resolve(), sys.argv[3]
assert mode in ("online", "offline")
cache.mkdir(parents=True, exist_ok=True)
failed = 0
for fj in sorted((root / "data" / "edgar_comps").glob("*/*/*/filing.json")):
    f = json.loads(fj.read_text())
    htm = fj.parent / "unpacked" / f["doc"]
    tags = len(re.findall(r"<ix:nonFraction\b", htm.read_text(encoding="utf-8", errors="ignore"), re.I))
    c = Cntlr.Cntlr(logFileName="logToBuffer")
    c.webCache.cacheDir = str(cache)
    c.webCache.workOffline = (mode == "offline")
    model = c.modelManager.load(str(htm))
    loaded = sum(1 for x in model.facts if getattr(x, "isNumeric", False))
    ok = loaded == tags
    failed += not ok
    print(f"{f['ticker']:5} {f['form']:5} {f['accn']}  numeric facts {loaded:5}  ix:nonFraction {tags:5}  "
          f"{'ok' if ok else 'GUARD FAILS'}")
    c.close()
print(f"{mode}: {failed} filing(s) fail the guard")
sys.exit(1 if failed else 0)
