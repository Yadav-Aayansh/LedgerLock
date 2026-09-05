/* Wiring: state, stage navigation, and the four actions. Markup lives in render.js. */

import { api } from './api.js';
import * as R from './render.js';

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

const STAGES = ['ledgers', 'engine', 'analyst', 'accuracy'];
const LOCKABLE = ['engine', 'analyst', 'accuracy'];
const state = { sessionId: null, presets: {}, loaded: false, ran: false, openLine: null };

/* ── chrome ──────────────────────────────────────────────────── */

const NOTICE = { ledgers: '#notice-1', engine: '#notice-2', analyst: '#notice-3', accuracy: '#notice-4' };
let current = 'ledgers';

function notice(text, kind = 'err') {
  for (const sel of Object.values(NOTICE)) $(sel).innerHTML = '';
  if (text) $(NOTICE[current]).innerHTML = `<div class="notice ${kind}">${R.esc(text)}</div>`;
}

function go(step) {
  current = step;
  for (const s of STAGES) {
    $(`#panel-${s}`).classList.toggle('hidden', s !== step);
    $(`.nav-item[data-step="${s}"]`).setAttribute('aria-current', String(s === step));
  }
  window.scrollTo({ top: 0 });
}

function unlock(step, done = false) {
  const btn = $(`.nav-item[data-step="${step}"]`);
  btn.disabled = false;
  if (done) btn.querySelector('.ord').classList.add('done');
}

function relock(steps = LOCKABLE) {
  for (const step of steps) {
    const btn = $(`.nav-item[data-step="${step}"]`);
    btn.disabled = true;
    btn.querySelector('.ord').classList.remove('done');
  }
  $('#sealed').classList.remove('hidden');
  $('#run-hint').textContent = '';
  $('#analyst-hint').textContent = '';
  state.openLine = null;
}

function busy(btn, on, label) {
  btn.disabled = on;
  if (on) {
    btn.dataset.label = btn.innerHTML;
    btn.innerHTML = `<span class="spinner"></span>${label}`;
  } else if (btn.dataset.label) {
    btn.innerHTML = btn.dataset.label;
  }
}

function showDataset(d) {
  $('#btn-reveal').disabled = !d.has_answer_key;
  $('#sealed-copy').textContent = d.has_answer_key
    ? 'Sealed until you open it.'
    : 'No answer key in this dataset. Generate again, or upload a ground_truth.csv.';
}

/* ── stage 1 ─────────────────────────────────────────────────── */

async function loadLedgers() {
  const d = await api.ledgers(state.sessionId);
  $('#stats').innerHTML = R.stats(d.disagreement);
  $('#ledger-tables').innerHTML = ['orders', 'settlements', 'bank']
    .map((k) => R.ledgerTable(k, d.ledgers[k])).join('');
  $('#empty-1').classList.add('hidden');
  state.loaded = true;
  unlock('engine');
}

function clearLedgers() {
  state.loaded = false;
  state.ran = false;
  relock();
  $('#stats').innerHTML = '';
  $('#ledger-tables').innerHTML = '';
  $('#empty-1').classList.remove('hidden');
  for (const id of ['#engine-results', '#analyst-results', '#accuracy-results']) {
    $(id).innerHTML = '';
  }
}

async function resetTo(dataset) {
  clearLedgers();
  $('#upload-report').innerHTML = '';
  showDataset(dataset);
  await loadLedgers();
  go('ledgers');
}

async function generate() {
  const btn = $('#btn-generate');
  busy(btn, true, 'generating');
  try {
    await resetTo(await api.generate(state.sessionId, Number($('#seed').value)));
    notice('');
  } catch (e) { notice(e.message); } finally { busy(btn, false); }
}

async function upload(files) {
  if (!files.length) return;
  try {
    const res = await api.upload(state.sessionId, files);
    const ok = res.accepted.map((a) => `${a.name} read as ${a.recognised_as}`).join(', ');
    const bad = res.rejected.map((r) => `${r.name} (${r.why})`).join(', ');
    const html = [
      ok && `<div class="notice info">Accepted ${R.esc(ok)}</div>`,
      bad && `<div class="notice err">Ignored ${R.esc(bad)}</div>`,
      res.dropped && `<div class="notice warn">Dropped the previous ${R.esc(res.dropped)}. `
        + `It belonged to the old bank statement, so it cannot grade this one. `
        + `Upload an answer key for this data to unlock Accuracy.</div>`,
    ].filter(Boolean).join('');
    if (res.dataset.ready) { await resetTo(res.dataset); }
    else { notice(`Still missing ${res.dataset.missing.join(', ')}`, 'warn'); }
    $('#upload-report').innerHTML = html;
  } catch (e) { notice(e.message); }
}

/* ── stages 2 and 3 ──────────────────────────────────────────── */

function paintRun(payload) {
  $('#engine-results').innerHTML =
    R.engineSummary(payload.run) + R.decisionTable(payload.run);

  const byId = Object.fromEntries(payload.run.decisions.map((d) => [d.bank_txn_id, d]));
  $$('#engine-results tr[data-line]').forEach((tr) => {
    tr.addEventListener('click', () => {
      const id = tr.dataset.line;
      const closing = state.openLine === id;
      state.openLine = closing ? null : id;
      $$('#engine-results tr[data-line]')
        .forEach((r) => r.classList.toggle('on', !closing && r.dataset.line === id));
      $('#decision-detail').innerHTML = closing ? '' : R.decisionDetail(byId[id]);
    });
  });

  showDataset(payload.dataset);
  state.ran = true;
  unlock('analyst', true);
  if (payload.answer_key_available) unlock('accuracy');
}

async function runEngine() {
  if (!state.loaded) {
    notice('Load a dataset first.', 'warn');
    return;
  }
  const btn = $('#btn-run');
  busy(btn, true, 'running');
  try {
    const payload = await api.run(state.sessionId, { mode: 'off' });
    paintRun(payload);
    $('#run-hint').textContent = `${payload.run.runtime_ms} ms`;
    notice('');
  } catch (e) { notice(e.message); } finally { busy(btn, false); }
}

async function runAnalyst(mode) {
  if (!state.ran) {
    notice('Run the engine first. The model only sees what it left open.', 'warn');
    return;
  }
  const btn = mode === 'live' ? $('#btn-analyst-live') : $('#btn-analyst-replay');
  busy(btn, true, mode === 'live' ? 'calling' : 'replaying');
  try {
    const payload = await api.run(state.sessionId, {
      mode,
      provider: $('#provider').value,
      base_url: $('#base-url').value.trim() || null,
      model: $('#model').value.trim() || null,
      api_key: $('#api-key').value || null,
    });
    saveConnection();
    paintRun(payload);
    $('#analyst-results').innerHTML =
      R.analystPanel(payload.analyst) + R.redteamPanel(payload.redteam);
    $('#analyst-hint').textContent = payload.analyst.unavailable_reason
      ? 'fell back' : payload.analyst.mode;
    notice('');
  } catch (e) { notice(e.message); } finally { busy(btn, false); }
}

/* ── stage 4 ─────────────────────────────────────────────────── */

async function reveal() {
  const btn = $('#btn-reveal');
  busy(btn, true, 'grading');
  try {
    $('#accuracy-results').innerHTML = R.accuracyPanel(await api.score(state.sessionId));
    $('#sealed').classList.add('hidden');
    notice('');
  } catch (e) { notice(e.message); } finally { busy(btn, false); }
}

/* ── connection form ─────────────────────────────────────────── */

/* Settings live in this browser only. The key is kept per provider and only
   while "Remember" is on, so switching provider recalls the right one and
   turning it off erases every key immediately. Every access is guarded:
   storage throws in private windows and when site data is blocked. */
const STORE = 'ledgerlock.connection.v1';
const KEYS = 'ledgerlock.keys.v1';

const read = (k, fallback) => {
  try { return JSON.parse(localStorage.getItem(k)) ?? fallback; } catch { return fallback; }
};
const write = (k, v) => {
  try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch { return false; }
};
const drop = (k) => { try { localStorage.removeItem(k); } catch { /* nothing to do */ } };

function storeHint(text) { $('#store-hint').textContent = text; }

function saveConnection() {
  const provider = $('#provider').value;
  const remember = $('#remember').checked;
  const ok = write(STORE, {
    provider, remember,
    base_url: $('#base-url').value.trim(),
    model: $('#model').value.trim(),
  });
  if (remember) {
    const keys = read(KEYS, {});
    const key = $('#api-key').value;
    if (key) keys[provider] = key; else delete keys[provider];
    write(KEYS, keys);
    storeHint(ok ? 'Saved in this browser. The key never leaves this machine except to the endpoint above.'
      : 'This browser is blocking storage, so nothing was saved.');
  } else {
    drop(KEYS);
    storeHint(ok ? 'Saved without the key.' : 'This browser is blocking storage.');
  }
}

function forgetConnection() {
  drop(STORE); drop(KEYS);
  $('#api-key').value = '';
  $('#remember').checked = false;
  storeHint('Cleared from this browser.');
}

/* Suggestions, not validation: the field stays free text. */
function fillProvider({ keepValues = false } = {}) {
  const name = $('#provider').value;
  const p = state.presets[name];
  if (!p) return;

  $('#model-list').innerHTML = (p.models ?? [])
    .map((m) => `<option value="${R.esc(m)}">`).join('');

  if (!keepValues) {
    $('#base-url').value = p.base_url ?? '';
    $('#model').value = p.default_model ?? '';
    $('#api-key').value = $('#remember').checked ? (read(KEYS, {})[name] ?? '') : '';
  }
  $('#api-key').placeholder = p.key_env ? `or $${p.key_env}` : 'not required';
}

function restoreConnection() {
  const saved = read(STORE, null);
  if (!saved || !state.presets[saved.provider]) {
    $('#provider').value = 'local';
    fillProvider();
    return;
  }
  $('#provider').value = saved.provider;
  $('#remember').checked = saved.remember !== false;
  fillProvider();
  if (saved.base_url) $('#base-url').value = saved.base_url;
  if (saved.model) $('#model').value = saved.model;
  if (saved.remember !== false) {
    $('#api-key').value = read(KEYS, {})[saved.provider] ?? '';
    storeHint('Restored from this browser.');
  }
}

/* ── boot ────────────────────────────────────────────────────── */

async function boot() {
  try {
    const [{ presets }, session] = await Promise.all([api.providers(), api.session()]);
    state.presets = presets;
    state.sessionId = session.session_id;

    $('#provider').innerHTML = Object.entries(presets)
      .map(([n, p]) => `<option value="${R.esc(n)}">${R.esc(p.label ?? n)}</option>`).join('');
    restoreConnection();
    showDataset(session);
  } catch (e) {
    notice(`Could not start: ${e.message}`);
  }
}

$$('.nav-item').forEach((b) => b.addEventListener('click', () => go(b.dataset.step)));
$('#btn-generate').addEventListener('click', generate);
$('#btn-pick').addEventListener('click', () => $('#file-input').click());
$('#file-input').addEventListener('change', (e) => upload([...e.target.files]));
$('#btn-run').addEventListener('click', runEngine);
$('#btn-analyst-live').addEventListener('click', () => runAnalyst('live'));
$('#btn-analyst-replay').addEventListener('click', () => runAnalyst('replay'));
$('#btn-reveal').addEventListener('click', reveal);
$('#provider').addEventListener('change', () => { fillProvider(); saveConnection(); });
for (const id of ['#base-url', '#model', '#api-key']) {
  $(id).addEventListener('change', saveConnection);
}
$('#remember').addEventListener('change', saveConnection);
$('#btn-forget').addEventListener('click', forgetConnection);

const dz = $('#dropzone');
dz.addEventListener('click', () => $('#file-input').click());
dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('over'); });
dz.addEventListener('dragleave', () => dz.classList.remove('over'));
dz.addEventListener('drop', (e) => {
  e.preventDefault(); dz.classList.remove('over'); upload([...e.dataTransfer.files]);
});

boot();
