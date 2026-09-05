#!/usr/bin/env python3
"""LedgerLock — run the pipeline end to end and write the reports.

    make demo

Deterministic: same seed, same numbers, every time.

This file is the command-line front door and nothing else. The pipeline itself
lives in `src/pipeline.py`, so the web viewer runs exactly the same code rather
than a parallel copy of it.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import pipeline                               # noqa: E402
import report                                 # noqa: E402
from analyst.client import PRESETS            # noqa: E402


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    ap.add_argument("--runs", type=Path, default=ROOT / "runs")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=None,
                    help="where results.md and exceptions.md are written. "
                         "Defaults beside --data, so pointing at another dataset "
                         "cannot overwrite this repository's report")
    ap.add_argument("--analyst", choices=["auto", "off", "replay", "live"], default="auto",
                    help="auto replays cached responses when present, else stays off; "
                         "live calls the API and records what comes back")
    ap.add_argument("--analyst-provider", choices=sorted(PRESETS), default=None,
                    help="preset base URL, wire protocol and key variable. "
                         "Defaults to whatever the cached responses were recorded "
                         "with, so `make demo` reproduces them")
    ap.add_argument("--analyst-model", default=None)
    ap.add_argument("--analyst-url", default=None, help="override the base URL")
    ap.add_argument("--analyst-protocol", choices=["anthropic", "openai"], default=None,
                    help="override the wire protocol")
    ap.add_argument("--analyst-key-env", default=None,
                    help="name of the environment variable holding the API key. "
                         "The key itself is never taken as a flag")
    ap.add_argument("--analyst-price", default=None, metavar="IN,OUT",
                    help="USD per 1M input,output tokens, so cost can be reported "
                         "for a model whose rates are not built in")
    return ap.parse_args()


def _price(text):
    if not text:
        return None
    try:
        a, b = (float(x) for x in text.split(","))
    except ValueError:
        raise SystemExit("--analyst-price wants two numbers, as IN,OUT") from None
    return [a, b]


def summarise(r, runs_dir):
    """The terminal summary. Every figure here also appears in results.md."""
    m, meta, analyst = r.metrics, r.meta, r.analyst
    _, rt_passed, rt_total = r.redteam
    split, _ = meta["evidence"]

    print(f"run {r.run_id}  tiers {'+'.join(meta['tiers'])}  {r.runtime:.3f}s")
    print(f"  auto-matched      {m['auto_matched']}/{m['linkable']} "
          f"({100 * m['auto_match_rate']:.1f}%) of genuine settlement lines")
    print(f"  FALSE MATCHES     {m['false_matches']}")
    print(f"  false refusals    {m['false_refusals']}")
    print(f"  unresolved        {m['unresolved']}")
    print(f"  refused (proved)  {m['correct_refusals']}")
    print(f"  evidence          {split['reference+amount']} reference-backed, "
          f"{split['amount+date']} amount+date only")
    print(f"  analyst           {analyst.mode} [{analyst.model or 'no model'}]"
          + (f" ({analyst.unavailable_reason})" if analyst.unavailable_reason else "")
          + f" — {analyst.proposals} proposal(s), {analyst.accepted} verified, "
            f"{analyst.rejected} rejected, {analyst.declined} declined")
    print(f"  verifier red team {rt_passed}/{rt_total} adversarial proposals "
          f"adjudicated correctly")
    print(f"  foreign lines     {m['foreign']} "
          f"({m['correct_refusals']} refused, {m['foreign_unresolved']} passively skipped)")
    print(f"  audit trail       {runs_dir / pipeline.LOG_NAME} ({meta['audit_events']} events)")


def main():
    args = parse_args()
    out_dir = args.out or args.data.resolve().parent
    provider = pipeline.resolve_provider(
        args.runs,
        preset=args.analyst_provider,
        base_url=args.analyst_url,
        model=args.analyst_model,
        protocol=args.analyst_protocol,
        key_env=args.analyst_key_env,
        price=_price(args.analyst_price),
    )

    r = pipeline.execute(args.data, args.runs, seed=args.seed,
                         analyst_mode=args.analyst, provider=provider)

    if not r.scored:
        print("no ground_truth.csv in the data directory; cannot score this run")
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    report.write_results(out_dir / "results.md", r.score, r.metrics, r.meta,
                         r.state.decisions)
    report.write_exceptions(out_dir / "exceptions.md", r.score, r.state.decisions,
                            r.bank_by_id, r.groups, r.meta)

    summarise(r, args.runs)

    problems = pipeline.verify_trail(r.log_path, r.state.decisions)
    if problems:
        print(f"  AUDIT TRAIL INCOMPLETE ({len(problems)} discrepancies):")
        for p in problems[:5]:
            print(f"    {p}")
        return 1
    print(f"  trail replay      OK — all {len(r.state.decisions)} verdicts reproduced "
          f"from the log alone")
    print(f"  wrote {out_dir / 'results.md'}, {out_dir / 'exceptions.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
