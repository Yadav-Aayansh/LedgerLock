"""Run the analyst over the residue, then hand every proposal to the verifier.

The control flow is the architecture (§5): propose -> verify -> accept or
discard. There is no path from a proposal to a link that does not go through
`verifier.verify`, and `analyst.py` never touches `state.accept` itself.
"""

from dataclasses import dataclass, field

from analyst import packet as packet_builder
from analyst.client import USD_TO_INR, AnalystClient, build_provider, cost_usd
from analyst.schema import PROPOSAL_SCHEMA, SYSTEM
from verifier import verify


@dataclass
class AnalystOutcome:
    bank_txn_id: str
    status: str            # accepted | rejected | declined | not_run
    detail: str = ""
    hypothesis: str = ""
    confidence: float = 0.0
    checks: list = field(default_factory=list)


@dataclass
class AnalystReport:
    outcomes: list = field(default_factory=list)
    mode: str = "off"
    provider: object = None
    unavailable_reason: str = ""
    proposals: int = 0
    accepted: int = 0
    rejected: int = 0
    declined: int = 0
    not_run: int = 0
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})

    @property
    def model(self):
        return self.provider.model if self.provider else ""

    @property
    def cost_usd(self):
        """None when no rate is known for this model. Tokens are still
        recorded; the price is simply not asserted."""
        return cost_usd(self.usage, self.provider) if self.provider else None

    @property
    def cost_inr(self):
        usd = self.cost_usd
        return None if usd is None else usd * USD_TO_INR

    @property
    def rejection_rate(self):
        return (self.rejected / self.proposals) if self.proposals else None


def analyse(state, groups, rows_by_id, audit, cache_path, mode,
            window_days, indeterminate, provider=None):
    provider = provider or build_provider("anthropic")
    client = AnalystClient(mode, cache_path, SYSTEM, PROPOSAL_SCHEMA, provider=provider)
    report = AnalystReport(mode=client.mode, provider=provider,
                           unavailable_reason=client.unavailable_reason)

    residue = [t for t in state.open_txns()]
    audit.log("analyst", "start", mode=client.mode, provider=provider.name,
              protocol=provider.protocol, model=provider.model, residue=len(residue),
              reason=client.unavailable_reason or None)

    for txn in residue:
        decision = state.decisions[txn.bank_txn_id]
        events = [e for e in audit.recent(txn.bank_txn_id)]
        pkt = packet_builder.build(txn, decision, groups, state.claimed, events)

        proposal, source = client.propose(pkt)

        if proposal is None:
            report.not_run += 1
            report.outcomes.append(AnalystOutcome(txn.bank_txn_id, "not_run", source))
            audit.log("analyst", "not_run", bank_txn_id=txn.bank_txn_id, reason=source)
            continue

        link = proposal.get("proposed_link") or []
        hypothesis = proposal.get("hypothesis", "")
        try:
            confidence = float(proposal.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0

        if not link:
            # §10: a first-class answer, counted as such rather than as a miss.
            report.declined += 1
            report.outcomes.append(AnalystOutcome(
                txn.bank_txn_id, "declined",
                proposal.get("unresolvable_reason") or "no reason given",
                hypothesis, confidence))
            audit.log("analyst", "declined", bank_txn_id=txn.bank_txn_id,
                      source=source, hypothesis=hypothesis, confidence=confidence,
                      unresolvable_reason=proposal.get("unresolvable_reason"))
            continue

        report.proposals += 1
        # Tolerance zero: the tiers need slack because they compare against
        # reconstructed figures, but the verifier recomputes every component
        # itself, so it demands the paisa.
        verdict = verify(proposal, txn, rows_by_id, state.claimed_entities, indeterminate,
                         window_days, tolerance_paisa=0)

        audit.log("analyst", "proposal", bank_txn_id=txn.bank_txn_id, source=source,
                  hypothesis=hypothesis, arithmetic=proposal.get("arithmetic"),
                  proposed_link=link, confidence=confidence,
                  verdict="accepted" if verdict.accepted else "rejected",
                  checks=[{"name": c.name, "passed": c.passed, "detail": c.detail}
                          for c in verdict.checks])

        if not verdict.accepted:
            report.rejected += 1
            report.outcomes.append(AnalystOutcome(
                txn.bank_txn_id, "rejected", verdict.summary(), hypothesis, confidence,
                verdict.checks))
            state.mark_reason(txn, f"analyst proposed a link; verifier {verdict.summary()}",
                              kind=state.decisions[txn.bank_txn_id].residue_kind or "rejected")
            continue

        rows = [rows_by_id[e] for e in link]
        state.accept_verified(txn, rows, tier="LLM", confidence=confidence,
                              reason=f"analyst hypothesis, verified: {hypothesis}",
                              evidence={"arithmetic": proposal.get("arithmetic"),
                                        "checks": len(verdict.checks)})
        report.accepted += 1
        report.outcomes.append(AnalystOutcome(txn.bank_txn_id, "accepted",
                                              verdict.summary(), hypothesis, confidence,
                                              verdict.checks))

    report.usage = client.usage
    audit.log("analyst", "done", proposals=report.proposals, accepted=report.accepted,
              rejected=report.rejected, declined=report.declined, not_run=report.not_run,
              input_tokens=report.usage["input_tokens"],
              output_tokens=report.usage["output_tokens"])
    return report
