"""One orchestration of the pipeline, shared by every front door.

`run.py` (the CLI) and `web/server.py` (the viewer) both call `execute` and
neither one re-implements it. That is deliberate: the README promises that
`make demo` reproduces the numbers in results.md, and a second orchestration
that drifted from the first would quietly make that promise false.

Stages are §6's: load -> match -> analyst -> verify -> score. `execute` runs
them and returns everything; deciding what to *print*, *write* or *serve* is
the caller's job.
"""

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import scoring
from analyst.analyst import analyse
from analyst.client import PRESETS, USD_TO_INR, build_provider
from matcher import engine, records
from matcher.audit import Audit
from matcher.decision import MATCHED
from matcher.tiers import (DATE_WINDOW_DAYS, FINALIZERS, REFERENCE_BACKED,
                           TIER_ORDERS, TIERS, TOLERANCE_PAISA)
from verifier.redteam import run_suite

NOT_BUILT = ("All five stages of §6 are built. Where a figure below is zero, the "
             "section beneath it says whether that is because nothing ran or "
             "because nothing was wrong.")

SIDECAR_NAME = "analyst_provider.json"
CACHE_NAME = "analyst_cache.jsonl"
LOG_NAME = "run_log.jsonl"


# -- provider resolution ----------------------------------------------------

def resolve_provider(runs_dir, preset=None, base_url=None, model=None,
                     protocol=None, key_env=None, price=None, key_value=None):
    """Build a Provider, defaulting to whatever the cached responses were
    recorded under so a later run replays them with no flags and no network.

    `key_value` is an in-memory credential (the web viewer's key field). It is
    never written to the sidecar, the cache, the trail or the reports.
    """
    sidecar = Path(runs_dir) / SIDECAR_NAME
    recorded = json.loads(sidecar.read_text()) if sidecar.exists() else {}
    return build_provider(
        preset or recorded.get("provider", "anthropic"),
        base_url=base_url if base_url is not None else recorded.get("base_url"),
        model=model or recorded.get("model"),
        protocol=protocol or recorded.get("protocol"),
        key_env=key_env if key_env is not None else recorded.get("key_env"),
        price=price if price else recorded.get("price"),
        key_value=key_value,
    )


def record_provider(runs_dir, provider):
    """Leave the configuration beside the cache -- never the API key."""
    runs_dir = Path(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / SIDECAR_NAME).write_text(json.dumps({
        "provider": provider.name, "protocol": provider.protocol,
        "base_url": provider.base_url, "model": provider.model,
        "key_env": provider.key_env, "price": list(provider.price),
        "note": "recorded so cached responses replay without flags; never a key",
    }, indent=2) + "\n")


# -- integrity checks -------------------------------------------------------

def evidence_split(decisions):
    """How many links rest on a recovered reference versus on arithmetic alone."""
    split = {"reference+amount": 0, "amount+date": 0}
    per_tier = {}
    for d in decisions.values():
        if d.status != MATCHED:
            continue
        per_tier[d.tier] = per_tier.get(d.tier, 0) + 1
        split["reference+amount" if d.tier in REFERENCE_BACKED else "amount+date"] += 1
    return split, per_tier


def verify_trail(path, decisions):
    """Replay the audit trail and check it reproduces the engine's own verdicts.

    §12 calls run_log.jsonl a full audit trail. That is only true if a reader
    with the file and nothing else reaches the same conclusions -- so it is
    checked rather than asserted. An incomplete trail is a defect, not a
    cosmetic gap, and the CLI exits non-zero if it drifts.
    """
    replayed = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec["stage"] == "decision":
                replayed[rec["bank_txn_id"]] = rec

    problems = []
    if set(replayed) != set(decisions):
        problems.append(f"trail covers {len(replayed)} lines, engine decided {len(decisions)}")
    for bid, d in decisions.items():
        rec = replayed.get(bid)
        if rec is None:
            continue
        if rec["event"] != d.status or rec["tier"] != d.tier:
            problems.append(f"{bid}: trail says {rec['event']}/{rec['tier']}, "
                            f"engine says {d.status}/{d.tier}")
        if sorted(rec["settlement_ids"]) != sorted(d.settlement_ids):
            problems.append(f"{bid}: trail links {rec['settlement_ids']}, "
                            f"engine linked {d.settlement_ids}")
    return problems


# -- the run ----------------------------------------------------------------

@dataclass
class RunResult:
    """Everything one pass produced. Callers pick what they need."""
    run_id: str
    runtime: float
    orders: list
    settlements: list
    bank: list
    groups: dict
    rows_by_id: dict
    state: object
    residue: list
    unclaimed: list
    audit: object
    analyst: object
    redteam: tuple
    provider: object
    log_path: Path
    score: object = None
    metrics: dict = None
    meta: dict = field(default_factory=dict)

    @property
    def bank_by_id(self):
        return {t.bank_txn_id: t for t in self.bank}

    @property
    def scored(self):
        """False when the dataset shipped no answer key. Everything else in a
        run still works; only the accuracy table is unavailable."""
        return self.score is not None


def execute(data_dir, runs_dir, seed=42, analyst_mode="auto", provider=None,
            cache_path=None, log_path=None, score=True):
    """Run §6 end to end. Writes the audit trail; writes no reports."""
    data_dir, runs_dir = Path(data_dir), Path(runs_dir)
    started = time.perf_counter()
    run_id = uuid.uuid4().hex[:12]
    provider = provider or build_provider("anthropic")
    log_path = Path(log_path) if log_path else runs_dir / LOG_NAME
    cache_path = Path(cache_path) if cache_path else runs_dir / CACHE_NAME

    orders, settlements, bank, groups = records.load(data_dir)
    rows_by_id = {r.entity_id: r for r in settlements}

    with Audit(log_path, run_id) as audit:
        state, residue, unclaimed = engine.run(bank, groups, audit)

        # A line the engine proved indeterminate is off limits to the analyst:
        # on those, arithmetic cannot tell a correct proposal from a coin flip.
        indeterminate = {b for b, d in state.decisions.items()
                         if d.residue_kind == "ambiguous"}

        analyst = analyse(state, groups, rows_by_id, audit, cache_path,
                          "replay" if analyst_mode == "auto" else analyst_mode,
                          DATE_WINDOW_DAYS, indeterminate, provider=provider)

        unclaimed = sorted(g.settlement_id for g in state.free_groups())
        engine.log_decisions(state, bank, audit)

    # The same engine under §9's literal tier order, for comparison only. It
    # writes no audit trail and no report -- it exists so the ordering claim in
    # results.md is a measurement rather than an assertion.
    alt_name = "spec"
    alt_state, _, _ = engine.run(bank, groups, Audit(None, run_id),
                                 tiers=TIER_ORDERS[alt_name])

    runtime = time.perf_counter() - started
    if analyst.mode == "live":
        record_provider(runs_dir, provider)

    redteam, rt_passed, rt_total = run_suite()

    result = RunResult(
        run_id=run_id, runtime=runtime, orders=orders, settlements=settlements,
        bank=bank, groups=groups, rows_by_id=rows_by_id, state=state,
        residue=residue, unclaimed=unclaimed, audit=audit, analyst=analyst,
        redteam=(redteam, rt_passed, rt_total), provider=provider, log_path=log_path)

    if score and (data_dir / "ground_truth.csv").exists():
        truth = scoring.load_truth(data_dir)
        result.score = scoring.score(state.decisions, truth, unclaimed, redteam)
        result.metrics = scoring.metrics(
            result.score, state.decisions, runtime,
            llm_calls=analyst.proposals, llm_rejected=analyst.rejected,
            cost_inr=analyst.cost_inr)

    result.meta = {
        "seed": seed,
        "tiers": [name for name, _ in TIERS],
        "not_built": NOT_BUILT,
        "bank_lines": len(bank),
        "settlement_lines": len(settlements),
        "settlements": len(groups),
        "orders": len(orders),
        "finalizers": [name for name, _ in FINALIZERS],
        "window_days": DATE_WINDOW_DAYS,
        "tolerance_paisa": TOLERANCE_PAISA,
        "audit_events": audit.n,
        "evidence": evidence_split(state.decisions),
        "alt_order": ([n for n, _ in TIER_ORDERS[alt_name]],
                      evidence_split(alt_state.decisions)),
        "analyst": analyst,
        "redteam": (redteam, rt_passed, rt_total),
        "model": analyst.model,
        "providers": sorted(PRESETS),
        "usd_to_inr": USD_TO_INR,
    }
    return result
