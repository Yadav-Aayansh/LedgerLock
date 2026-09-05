#!/usr/bin/env python3
"""The verifier's adversarial suite, as a test.

The suite itself lives in src/verifier/redteam.py because results.md reports
it on every run -- a verifier that silently started accepting everything would
otherwise look identical to a model that had stopped making mistakes.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from verifier.redteam import run_suite  # noqa: E402


def main():
    results, passed, total = run_suite()
    for r in results:
        mark = "PASS" if r["ok"] else "FAIL"
        print(f"  {mark}  {r['name'].replace('_', ' ')}")
        if not r["ok"]:
            print(f"        expected {r['expected']}, got {r['actual']}")
    print(f"\n{passed}/{total} adversarial proposals adjudicated correctly")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
