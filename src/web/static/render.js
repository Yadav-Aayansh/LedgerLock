/* Markup for each view. Pure functions: state in, HTML out, no fetching. */

export const esc = (v) => String(v ?? '')
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;');

/* Engine messages quote identifiers in backticks. */
const code = (v) => esc(v).replace(/`([^`]+)`/g, '<code>$1</code>');

const rs = (s) => `₹${Number(s).toLocaleString('en-IN', { minimumFractionDigits: 2 })}`;
const pct = (x) => `${(x * 100).toFixed(1)}%`;
const tag = (k, t) => `<span class="tag ${k}">${esc(t)}</span>`;

/* ── ledgers ─────────────────────────────────────────────────── */

export function stats(d) {
  const card = (cls, k, v, h) =>
    `<div class="stat ${cls}"><div class="k">${k}</div><div class="v">${v}</div><div class="h">${h}</div></div>`;
  return [
    card('', 'Sold', rs(d.sold), `${d.orders} orders`),
    card('lead', 'Paid out', rs(d.settled), `${d.settlement_rows} rows in ${d.settlement_batches} payouts`),
    card('', 'Reached bank', rs(d.banked), `${d.bank_lines} statement lines`),
    card('', 'Fees and GST', rs(d.fee_gap), 'deducted before payout'),
    card('warn', 'Unexplained by settlements', rs(d.bank_gap), 'must be refused, not matched'),
  ].join('');
}

const LEDGER = {
  orders: {
    title: 'Orders', note: 'the shop’s own system',
    head: ['order', 'amount', 'status'], widths: ['40%', '34%', '26%'],
    row: (r) => `
      <td class="mono">${esc(r.order_id)}<div class="sub2">${esc(String(r.created_at).slice(0, 10))}</div></td>
      <td class="r num">${esc(r.order_amount)}</td>
      <td class="mono mute">${esc(r.status)}</td>`,
  },
  settlements: {
    title: 'Settlements', note: 'what Razorpay reports',
    head: ['settlement', 'type', 'net'], widths: ['38%', '30%', '32%'],
    row: (r) => `
      <td class="mono">${esc(r.settlement_id)}<div class="sub2">${esc(r.payment_id)}</div></td>
      <td class="mono mute">${esc(r.type)}</td>
      <td class="r num">${esc(r.net_amount)}</td>`,
  },
  bank: {
    title: 'Bank statement', note: 'what arrived',
    head: ['line', 'narration', 'amount'], widths: ['28%', '42%', '30%'],
    row: (r) => `
      <td class="mono">${esc(r.bank_txn_id)}<div class="sub2">${esc(r.txn_date)}</div></td>
      <td class="mono trunc" title="${esc(r.narration)}">${esc(r.narration)}</td>
      <td class="r num">${r.credit ? esc(r.credit) : `−${esc(r.debit)}`}</td>`,
  },
};

export function ledgerTable(kind, rows) {
  const { title, note, head, widths, row } = LEDGER[kind];
  return `
    <div class="panel">
      <div class="head"><h3>${title}</h3><div class="note">${note} · ${rows.length} rows</div></div>
      <div class="scroll">
        <table class="fixed">
          <colgroup>${widths.map((w) => `<col style="width:${w}">`).join('')}</colgroup>
          <thead><tr>${head.map((h, i) =>
    `<th class="${i === head.length - 1 ? 'r' : ''}">${h}</th>`).join('')}</tr></thead>
          <tbody>${rows.map((r) => `<tr>${row(r)}</tr>`).join('')}</tbody>
        </table>
      </div>
    </div>`;
}

/* ── engine ──────────────────────────────────────────────────── */

export function engineSummary(run) {
  const c = run.counts;
  const cmp = run.tier_order_comparison;
  const s = (cls, k, v, h) =>
    `<div class="stat ${cls}"><div class="k">${k}</div><div class="v">${v}</div><div class="h">${h}</div></div>`;
  const tiers = Object.entries(run.per_tier).sort((a, b) => b[1] - a[1])
    .map(([t, n]) => `${t} ${n}`).join(' · ');
  return `
    <div class="stats">
      ${s('', 'Matched', c.matched, tiers)}
      ${s('', 'Refused', c.refused, 'proved not a settlement')}
      ${s('', 'Left open', c.unresolved, 'declined to guess')}
      ${s('', 'Runtime', `${run.runtime_ms} ms`, `${run.audit_events} audit events`)}
      ${s('', 'Evidence', run.evidence['reference+amount'], `reference-backed, ${run.evidence['amount+date']} on amount and date`)}
    </div>
    ${orderNote(cmp)}`;
}


function orderNote(cmp) {
  const a = cmp.spec_literal.evidence;
  const b = cmp.used.evidence;
  const total = (e) => e['reference+amount'] + e['amount+date'];
  const same = total(a) === total(b);
  const ref = b['reference+amount'] - a['reference+amount'];
  const counts = same
    ? `Same match count (${total(b)}).`
    : `Match count changes from ${total(a)} to ${total(b)}.`;
  const evidence = ref > 0
    ? `Reference-backed evidence rises from ${a['reference+amount']} to ${b['reference+amount']}.`
    : ref < 0
      ? `Reference-backed evidence falls from ${a['reference+amount']} to ${b['reference+amount']}.`
      : `Reference-backed evidence is unchanged at ${b['reference+amount']}.`;
  return `<div class="notice info">Salvage runs second, not last:
    ${esc(cmp.spec_literal.order.join(' '))} becomes ${esc(cmp.used.order.join(' '))}.
    ${counts} ${evidence}</div>`;
}

export function decisionTable(run) {
  const rows = run.decisions.map((d) => `
    <tr class="pick" data-line="${esc(d.bank_txn_id)}">
      <td class="mono">${esc(d.bank_txn_id)}</td>
      <td class="mono">${esc(d.date)}</td>
      <td class="mono trunc" title="${esc(d.narration)}">${esc(d.narration)}</td>
      <td class="r num">${rs(d.amount)}</td>
      <td>${tag(d.status, d.status === 'matched' ? d.tier : d.status)}</td>
      <td class="mono mute">${esc(d.settlement_ids.join(' ') || '—')}</td>
    </tr>`).join('');
  return `
    <div class="panel">
      <div class="head"><h3>Bank lines</h3>
        <div class="note">Select a row for the rule, the comparison and the audit trail.</div></div>
      <div class="scroll" style="max-height:520px">
        <table><thead><tr>
          <th>line</th><th>date</th><th>narration</th><th class="r">amount</th>
          <th>ruling</th><th>settlement</th>
        </tr></thead><tbody>${rows}</tbody></table>
      </div>
      <div id="decision-detail"></div>
    </div>`;
}

export function decisionDetail(d) {
  const linked = d.linked.map((g) => `
    <div class="subtable">
      <div class="cap">${esc(g.settlement_id)} · ${rs(g.payout)}${g.utr ? ` · ${esc(g.utr)}` : ''}${
  g.settled_at ? ` · ${esc(g.settled_at)}` : ''}</div>
      <table><thead><tr>
        <th>line</th><th>type</th><th>status</th>
        <th class="r">gross</th><th class="r">fee</th><th class="r">gst</th><th class="r">net</th>
      </tr></thead><tbody>${g.lines.map((l) => `
        <tr><td class="mono">${esc(l.entity_id)}</td><td class="mono">${esc(l.type)}</td>
        <td>${l.status === 'processed' ? `<span class="mono mute">${esc(l.status)}</span>`
    : tag('unresolved', l.status)}</td>
        <td class="r num">${rs(l.gross)}</td><td class="r num">${rs(l.fee)}</td>
        <td class="r num">${rs(l.gst_on_fee)}</td><td class="r num">${rs(l.net)}</td></tr>`).join('')}
      </tbody></table>
    </div>`).join('');

  const trail = d.trail.map((e) =>
    `<li><b>${esc(e.stage)}</b> ${esc(e.event)}${e.reason ? `: ${esc(e.reason)}` : ''}</li>`).join('');

  return `
    <div class="detail">
      <div class="id">${esc(d.bank_txn_id)} · ${rs(d.amount)} · ${esc(d.date)}</div>
      <div class="narr">${esc(d.narration)}</div>
      <div class="box">
        ${tag(d.status, d.status_label)}
        <div class="why">${code(d.reason)}</div>
        ${d.residue_label ? `<div class="why mute">${esc(d.residue_label)}</div>` : ''}
      </div>
      <dl class="facts">
        <dt>rule</dt><dd class="mono">${esc(d.tier ?? '—')}</dd>
        <dt>confidence</dt><dd class="num">${d.confidence ? d.confidence.toFixed(2) : '—'}</dd>
        <dt>lines</dt><dd class="num">${d.line_count || '—'}</dd>
      </dl>
      ${linked ? `<section><h4>linked settlements</h4>${linked}</section>` : ''}
      ${trail ? `<section><h4>audit trail</h4><ul class="trail">${trail}</ul></section>` : ''}
    </div>`;
}

/* ── model ───────────────────────────────────────────────────── */

export function analystPanel(a) {
  const cost = a.cost_inr != null ? `₹${a.cost_inr.toFixed(4)}`
    : `${a.usage.input_tokens + a.usage.output_tokens} tok`;
  const s = (k, v, h) =>
    `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div><div class="h">${h}</div></div>`;

  const cards = a.outcomes.map((o) => {
    const checks = o.checks.map((c) => `
      <li class="${c.passed ? 'pass' : 'fail'}">
        <span class="m">${c.passed ? '✓' : '✗'}</span>
        <span class="n">${esc(c.name)}</span>
        <span class="d">${code(c.detail)}</span></li>`).join('');
    return `
      <div class="proposal">
        <div class="top">
          <span class="mono">${esc(o.bank_txn_id)}</span>
          <span class="num">${o.amount ? rs(o.amount) : ''}</span>
          ${tag(o.status, o.status)}
          ${o.confidence ? `<span class="mono mute" style="font-size:10.5px">${o.confidence.toFixed(2)}</span>` : ''}
        </div>
        <div class="body">
          ${o.hypothesis ? `<div class="lbl">Model</div><p class="said">${esc(o.hypothesis)}</p>` : ''}
          <div class="lbl" style="margin-top:14px">Verifier</div>
          <div style="margin-top:4px;font-size:13px">${code(o.detail)}</div>
          ${checks ? `<ul class="checks">${checks}</ul>` : ''}
        </div>
      </div>`;
  }).join('');

  return `
    <div class="stats">
      ${s('Proposed', a.proposals, 'links the model asserted')}
      ${s('Verified', a.accepted, 'arithmetic balanced')}
      ${s('Rejected', a.rejected, 'thrown out by the verifier')}
      ${s('Declined', a.declined, 'model refused to guess')}
      ${s('Cost', cost, `${a.mode}${a.model ? ` · ${esc(a.model)}` : ''} · key ${esc(a.key_source)}`)}
    </div>
    ${a.unavailable_reason ? `<div class="notice warn">Fell back: ${esc(a.unavailable_reason)}</div>` : ''}
    <div>${cards || '<div class="panel"><div class="pad mute">Nothing was left open for the model.</div></div>'}</div>`;
}

export function redteamPanel(rt) {
  // One case is a genuinely correct proposal: the right outcome is to let it through.
  const rows = rt.cases.map((c) => `
    <li><span class="cid">${c.ok
    ? tag('matched', c.expected === 'accept' ? 'let through' : 'caught')
    : tag('refused', 'missed')}</span>
      <span class="cid">${esc(c.name.replace(/_/g, ' '))}</span>
      <span class="lab"><b>${esc(c.mistake)}</b>${esc(c.actual)}</span></li>`).join('');
  return `
    <div class="panel">
      <div class="head"><h3>Adversarial suite, ${rt.passed} of ${rt.total}</h3>
        <div class="note">Wrong proposals the verifier must reject, each on the correct check.</div></div>
      <div class="pad"><ul class="cases">${rows}</ul></div>
    </div>`;
}

/* ── accuracy ────────────────────────────────────────────────── */

const BUCKET = {
  correct_match: 'Matched, correct',
  false_match: 'Matched, wrong',
  correct_refusal: 'Refused, correct',
  false_refusal: 'Refused a real settlement',
  honest_miss: 'Left open, had an answer',
  foreign_unresolved: 'Left open, was foreign',
};
const VTAG = {
  handled: 'matched', partial: 'unresolved', declined: 'unresolved',
  unresolved: 'unresolved', failed: 'refused', deferred: 'neutral',
};

export function accuracyPanel(s) {
  if (!s.available) return `<div class="notice warn">${esc(s.why)}</div>`;
  const m = s.metrics;
  const partial = (s.unscored || []).length
    ? `<div class="notice warn">The answer key covers ${m.linkable + m.foreign} of
       ${m.linkable + m.foreign + s.unscored.length} bank lines.
       ${s.unscored.length} line(s) are excluded from every figure below:
       ${esc(s.unscored.join(', '))}.</div>`
    : '';

  const buckets = Object.entries(BUCKET).map(([k, label]) => {
    const n = s.counts[k] ?? 0;
    const bad = k === 'false_match' || k === 'false_refusal';
    return `<tr><td class="${n ? '' : 'mute'}">${esc(label)}</td>
      <td class="r num" style="color:${n && bad ? 'var(--refused)' : n ? 'var(--text)' : 'var(--text-mute)'}">${n}</td></tr>`;
  }).join('');

  const metrics = [
    ['Auto-match rate', pct(m.auto_match_rate)],
    ['Resolved by model', String(m.llm_assisted)],
    ['False-match rate', pct(m.false_match_rate)],
    ['Unresolved', String(m.unresolved)],
    ['Verifier rejection rate', m.verifier_rejection_rate == null ? '—' : pct(m.verifier_rejection_rate)],
    ['Cost', m.cost_inr == null ? '—' : `₹${m.cost_inr.toFixed(4)}`],
  ].map(([k, v]) => `<tr><td>${k}</td><td class="r num">${v}</td></tr>`).join('');

  const edges = s.edge_cases.map((e) => `
    <li><span class="cid">${esc(e.id)}</span>
      <span class="cid">${tag(VTAG[e.verdict] ?? 'neutral', e.verdict)}</span>
      <span class="lab"><b>${esc(e.label)}</b>${code(e.detail)}</span></li>`).join('');

  return `
    ${partial}
    <div class="headline">
      <div class="fig">${m.false_matches}<small>false matches in ${m.asserted_matches} links</small></div>
      <div class="side-stats">
        <div><div class="k">Auto-matched</div><div class="v">${m.auto_matched}<span class="mute">/${m.linkable}</span></div></div>
        <div><div class="k">Refused, correct</div><div class="v">${m.correct_refusals}</div></div>
        <div><div class="k">Refused, wrong</div><div class="v">${m.false_refusals}</div></div>
        <div><div class="k">Left open</div><div class="v">${m.unresolved}</div></div>
      </div>
    </div>

    <div class="grid2">
      <div class="panel">
        <div class="head"><h3>Outcomes</h3>
          <div class="note">Six buckets. A correct refusal is a result, not a gap.</div></div>
        <div class="scroll"><table><tbody>${buckets}</tbody></table></div>
      </div>
      <div class="panel">
        <div class="head"><h3>Metrics</h3><div class="note">As written to results.md.</div></div>
        <div class="scroll"><table><tbody>${metrics}</tbody></table></div>
      </div>
    </div>

    <div class="panel">
      <div class="head"><h3>Planted edge cases</h3>
        <div class="note">Eleven, each scored separately.</div></div>
      <div class="pad"><ul class="cases">${edges}</ul></div>
    </div>`;
}
