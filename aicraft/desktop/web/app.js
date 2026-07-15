'use strict';

/* ============ Bridge & stato ============ */
const state = {
  meta: null, profiles: [], activeProfileId: null, currentPlan: null, planWeekStart: null, view: 'oggi',
  backlogFilter: 'aperto', backlogSearch: '',
  libFilter: { status: '', category: '', search: '', page: 0 },
  expandedPieceId: null, showMonthly: false, showAllocPreview: false, showDryRun: false,
};

async function call(method, ...args) {
  try {
    const res = await window.pywebview.api[method](...args);
    return res || { ok: false, error: 'nessuna risposta' };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

/* ============ Etichette ============ */
const CT_LABELS = {
  video_talking: 'Video Talking', video_balletti: 'Video Balletti',
  video_caption: 'Video Caption', carosello: 'Caroselli', stories: 'Stories',
};
const CT_COLORS = { video_talking: '#8fe23a', video_balletti: '#ff6b5e', video_caption: '#4c8bf5', carosello: '#7ee787', stories: '#e8b23d' };
const DAY_LABELS = { lun: 'Lun', mar: 'Mar', mer: 'Mer', gio: 'Gio', ven: 'Ven', sab: 'Sab', dom: 'Dom' };
const TIPO_LABELS = { solo_talking: 'Solo talking', solo_balletti: 'Solo balletti', misto: 'Misto' };
const BACKLOG_STATUS_LABELS = { aperto: 'Aperto', fatto: 'Fatto', scartato: 'Scartato', tutti: 'Tutti' };
const BACKLOG_STATUS_BADGE = { aperto: 'amber', fatto: 'green', scartato: 'gray' };
const PIECE_STATUS_LABELS = {
  reference_ready: 'Da produrre', image_regen: 'Foto in corso', video_regen: 'Video in corso',
  qa: 'QA in corso', caption_hashtag: 'Caption in corso', delivered: 'Consegnato',
  error: 'Errore', blocked_nsfw: 'Bloccato (NSFW)', too_long: 'Video troppo lungo',
  content_refused: 'Rifiutato da Claude',
};
const PIECE_STATUS_BADGE = {
  reference_ready: 'gray', image_regen: 'amber', video_regen: 'amber', qa: 'amber',
  caption_hashtag: 'amber', delivered: 'green', error: 'red', blocked_nsfw: 'red', too_long: 'red',
  content_refused: 'red',
};
const REF_STATUS_BADGE = {
  ready: 'green', pending: 'gray', downloading: 'amber', transcribing: 'amber',
  error: 'red', download_error: 'red', private: 'red', unavailable: 'red', transcription_error: 'red',
};
const REF_RETRYABLE_STATUSES = ['error', 'download_error', 'private', 'unavailable', 'transcription_error'];
const PIECE_FAILURE_STATUSES = ['error', 'blocked_nsfw', 'too_long', 'content_refused'];

/* ============ Utility ============ */
const $ = (sel, root = document) => root.querySelector(sel);
const fmt = (n) => (n == null ? '—' : Number(n).toFixed(2));
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function toast(msg, kind = 'ok') {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast show ' + kind;
  setTimeout(() => { t.className = 'toast ' + kind; }, 2600);
}

function isoDate(d) { return d.toISOString().slice(0, 10); }
function currentWeek() {
  const now = new Date();
  const dow = (now.getDay() + 6) % 7;
  const mon = new Date(now); mon.setDate(now.getDate() - dow);
  const sun = new Date(mon); sun.setDate(mon.getDate() + 6);
  return [isoDate(mon), isoDate(sun)];
}
function prettyDate(iso) { const [y, m, d] = iso.split('-'); return `${d}-${m}-${y}`; }
function addDays(iso, n) { const d = new Date(iso + 'T00:00:00'); d.setDate(d.getDate() + n); return isoDate(d); }
function dayNum(iso) { return iso.split('-')[2]; }

/* ============ Balance pill (topbar) ============ */
async function refreshBalance() {
  const r = await call('budget_status');
  if (!r.ok) return;
  const pill = $('#balancePill');
  pill.className = 'balance-pill ' + (r.balance < 0 ? 'neg' : r.balance < 50 ? 'low' : '');
  $('#balancePillValue').textContent = fmt(r.balance) + ' CR';
}

async function refreshTopProfileSwitch() {
  const sel = $('#topProfileSwitch');
  if (!state.profiles.length) {
    const r = await call('list_profiles');
    if (r.ok) state.profiles = r.profiles;
  }
  const active = state.profiles.find((p) => p.is_active);
  sel.innerHTML = state.profiles.length
    ? state.profiles.map((p) => `<option value="${p.id}" ${active && p.id === active.id ? 'selected' : ''}>${esc(p.nome)}</option>`).join('')
    : '<option value="">— nessun profilo —</option>';
}

/* ============ Router ============ */
const VIEWS = {};
async function setView(name) {
  // Se si ri-renderizza la STESSA vista (es. dopo un'azione come retry/refresh),
  // mantiene la posizione di scroll invece di tornare in cima — il placeholder
  // "Carico…" sotto e' piu' corto del contenuto reale e altrimenti farebbe
  // collassare temporaneamente l'altezza scrollabile, resettando lo scroll.
  // Cambiando invece tab (vista diversa) si riparte dall'alto, comportamento atteso.
  const sameView = name === state.view;
  const mainEl = document.querySelector('.main');
  const scrollTop = sameView && mainEl ? mainEl.scrollTop : 0;

  state.view = name;
  document.querySelectorAll('.tab').forEach((a) => a.classList.toggle('active', a.dataset.view === name));
  const root = $('#view');
  root.innerHTML = '<div class="loading">Carico…</div>';
  try {
    root.innerHTML = await VIEWS[name]();
  } catch (e) {
    root.innerHTML = `<div class="empty">Errore nel caricamento: ${esc(e)}</div>`;
  }
  if (mainEl) mainEl.scrollTop = scrollTop;
}

function head(title, sub, actions = '') {
  return `<div class="page-head">
    <div><h1 class="page-title">${title}</h1><div class="page-sub">${sub}</div></div>
    <div class="spacer"></div>${actions}</div>`;
}

function chipStrip(items) {
  // items: [{label, value, color}]
  return `<div class="chip-strip">${items.map((i) =>
    `<div class="chip"><span class="c-dot" style="background:${i.color || '#4c8bf5'}"></span>${esc(i.label)}
      <span class="c-val num">${i.value}</span></div>`).join('')}</div>`;
}

/* ============ Vista: Oggi ============ */
VIEWS.oggi = async () => {
  const [r, agenda, health, events] = await Promise.all([
    call('overview'), call('today_agenda'), call('health_check'), call('today_events'),
  ]);
  if (!r.ok) return `<div class="empty">${esc(r.error)}</div>`;
  const o = r.overview;
  const cp = o.content_per_stato || {};
  const totContent = Object.values(cp).reduce((a, b) => a + b, 0);
  const delivered = cp['delivered'] || 0;
  const bal = o.saldo_crediti;

  const warnings = [];
  if (o.budget_alert) warnings.push(`Saldo sotto la soglia di ${fmt(o.budget_alert_threshold)} CR.`);
  if (health.ok && !health.all_ok) {
    const missing = [];
    if (!health.higgsfield_cli) missing.push('CLI Higgsfield');
    if (!health.claude_cli) missing.push('CLI Claude');
    if (!health.google_sheet_credentials) missing.push('credenziali Google Sheet');
    warnings.push(`Configurazione incompleta: ${missing.join(', ')} — vedi tab Sistema.`);
  }
  const warnBanner = warnings.length
    ? `<div class="warn-list" style="margin-bottom:16px">${warnings.map((w) => `<div class="warn">${esc(w)}</div>`).join('')}</div>`
    : '';

  const eventsHtml = events.ok && events.events.length
    ? `<div class="plist">${events.events.map((e) => `
        <div class="row" style="padding:5px 0;font-size:11.5px">
          <span class="badge ${e.status === 'completed' ? 'green' : e.status === 'failed' ? 'red' : 'amber'}" style="min-width:78px;text-align:center">${esc(e.status)}</span>
          <span class="muted" style="flex:1">${CT_LABELS[e.content_type] || esc(e.content_type || '—')} #${e.piece_id} · ${esc(e.stage)} · ${esc(e.profile_nome || '—')}</span>
          <span class="faint">${esc((e.timestamp || '').replace('T', ' ').slice(11, 19))}</span>
        </div>`).join('')}</div>`
    : '<div class="empty">Nessuna attività registrata oggi.</div>';

  return head('Oggi', 'Panoramica rapida del tuo lavoro') + warnBanner +
    chipStrip([
      { label: 'Saldo', value: fmt(bal) + ' CR', color: bal < 0 ? '#ff6b5e' : '#8fe23a' },
      { label: 'Profili', value: o.profili.length, color: '#4c8bf5' },
      { label: 'Contenuti', value: totContent + ' (' + delivered + ' consegnati)' },
      { label: 'Reference', value: Object.values(o.reference_per_stato || {}).reduce((a, b) => a + b, 0), color: '#e8b23d' },
    ]) +
    `<div class="section-title">Agenda di oggi (${DAY_LABELS[agenda.giorno] || agenda.giorno || ''})</div>` +
    agendaSection(agenda) + `
    <div class="section-title">Attività di oggi (tutti i profili)</div>` +
    eventsHtml + `
    <div class="section-title">Profili</div>
    ${o.profili.length ? '<div class="plist">' + o.profili.map((p) => `
      <div class="prow ${p.attivo ? '' : ''}">
        <div class="p-avatar">${esc((p.nome[0] || '?').toUpperCase())}</div>
        <div><div class="p-name">${esc(p.nome)}</div><div class="faint">${TIPO_LABELS[p.tipo_contenuto] || p.tipo_contenuto}</div></div>
        <div class="spacer"></div>
        <span class="badge ${p.attivo ? 'green' : 'gray'}">${p.attivo ? 'attivo' : 'disabilitato'}</span>
      </div>`).join('') + '</div>' : '<div class="empty">Nessun profilo. Vai su <b>Creator</b> per crearne uno.</div>'}
  `;
};

function agendaSection(agenda) {
  if (!agenda || !agenda.ok) {
    return `<div class="empty">${esc(agenda ? agenda.error : 'Impossibile leggere l\'agenda')}</div>`;
  }
  if (!agenda.has_profile) {
    return '<div class="empty">Nessun profilo attivo. Selezionane uno per vedere l\'agenda del giorno.</div>';
  }
  if (!agenda.plan) {
    return `<div class="empty">Nessun piano copre la settimana corrente per <b>${esc(agenda.profile_nome)}</b>.
      <button class="btn sm" data-action="goto-view" data-view="piano" style="margin-left:8px">Vai al Piano</button></div>`;
  }
  const pieces = agenda.pieces || [];
  const missingRef = pieces.filter((p) => !p.has_reference).length;
  const actions = [];
  if (agenda.plan.status === 'bozza') {
    actions.push('Il piano di questa settimana e\' ancora in bozza — approvalo per metterlo in produzione.');
  }
  if (missingRef) {
    actions.push(`${missingRef} pezzo/i di oggi senza reference assegnata — aggiorna la Libreria o assegna reference nel Piano.`);
  }
  const actionsHtml = actions.length
    ? `<div class="warn-list" style="margin-bottom:12px">${actions.map((a) => `<div class="warn">${esc(a)}</div>`).join('')}</div>`
    : '';
  if (!pieces.length) {
    return actionsHtml + '<div class="empty">Nessun contenuto pianificato per oggi.</div>';
  }
  const rows = pieces.map((p) => `
    <div class="prow">
      <span class="dc-dot" style="background:${CT_COLORS[p.content_type] || '#4c8bf5'};width:9px;height:9px;border-radius:50%;flex-shrink:0"></span>
      <div><div class="p-name">${CT_LABELS[p.content_type] || esc(p.content_type)}</div>
        <div class="faint">${p.has_reference ? 'reference assegnata' : 'reference mancante'}</div></div>
      <div class="spacer"></div>
      <span class="badge ${PIECE_STATUS_BADGE[p.status] || 'gray'}">${PIECE_STATUS_LABELS[p.status] || esc(p.status)}</span>
    </div>`).join('');
  return actionsHtml + `<div class="plist">${rows}</div>`;
}

/* ============ Vista: Creator ============ */
VIEWS.creator = async () => {
  const r = await call('list_profiles');
  if (!r.ok) return `<div class="empty">${esc(r.error)}</div>`;
  state.profiles = r.profiles;
  const creatorsOpts = r.creators.map((c) => `<option value="${c.id}">${esc(c.nome)}</option>`).join('');
  const tipiOpts = (state.meta?.tipi_profilo || []).map((t) => `<option value="${t}">${TIPO_LABELS[t] || t}</option>`).join('');
  return head('Creator', 'Profili gestiti e loro stato') +
    chipStrip([
      { label: 'Profili', value: r.profiles.length, color: '#8fe23a' },
      { label: 'Attivi', value: r.profiles.filter((p) => p.attivo).length },
      { label: 'Creator', value: r.creators.length, color: '#4c8bf5' },
    ]) + `
    <div class="section-title">Profili gestiti</div>
    ${r.profiles.length ? '<div class="plist">' + r.profiles.map((p) => {
      const st = p.content_stats || { total: 0, delivered: 0, cost_actual: 0 };
      return `
      <div class="prow ${p.is_active ? 'active' : ''}">
        <div class="p-avatar">${esc((p.nome[0] || '?').toUpperCase())}</div>
        <div><div class="p-name">${esc(p.nome)} ${p.is_active ? '<span class="badge green">selezionato</span>' : ''}</div>
          <div class="faint">${TIPO_LABELS[p.tipo_contenuto] || p.tipo_contenuto} · ${esc(p.creator || '')}
            ${st.total ? ` · ${st.delivered}/${st.total} consegnati · ${fmt(st.cost_actual)} CR spesi` : ' · nessun contenuto ancora'}</div></div>
        <div class="spacer"></div>
        ${p.is_active ? '' : `<button class="btn sm" data-action="profile-activate" data-id="${p.id}">Rendi attivo</button>`}
        <button class="btn sm danger" data-action="profile-delete" data-id="${p.id}" data-name="${esc(p.nome)}">Elimina</button>
      </div>`;
    }).join('') + '</div>' : '<div class="empty">Ancora nessun profilo.</div>'}

    <div class="section-title">Aggiungi</div>
    <div class="grid cols-2">
      <div class="card">
        <div class="muted" style="margin-bottom:10px;font-weight:700">Nuovo creator</div>
        <div class="row"><input id="newCreatorName" placeholder="Nome creator" style="flex:1" />
          <button class="btn blue" data-action="create-creator">Crea</button></div>
      </div>
      <div class="card">
        <div class="muted" style="margin-bottom:10px;font-weight:700">Nuovo profilo</div>
        <div class="row wrap">
          <select id="newProfileCreator" ${r.creators.length ? '' : 'disabled'}>${creatorsOpts || '<option>— crea prima un creator —</option>'}</select>
          <input id="newProfileName" placeholder="Nome profilo" style="flex:1" />
          <select id="newProfileTipo">${tipiOpts}</select>
          <button class="btn blue" data-action="create-profile" ${r.creators.length ? '' : 'disabled'}>Crea</button>
        </div>
      </div>
    </div>`;
};

/* ============ Vista: Piano ============ */
async function ensurePlan() {
  if (!state.profiles.length) {
    const r = await call('list_profiles');
    if (r.ok) state.profiles = r.profiles;
  }
  const active = state.profiles.find((p) => p.is_active) || state.profiles[0];
  if (!active) return null;
  state.activeProfileId = active.id;
  const lp = await call('list_plans', active.id);
  const plans = lp.ok ? lp.plans : [];
  if (!state.planWeekStart && plans.length) state.planWeekStart = plans[0].week_start; // piu' recente per default
  const match = plans.find((p) => p.week_start === state.planWeekStart);
  if (match) {
    const g = await call('get_plan', match.id);
    if (g.ok) { state.currentPlan = g.plan; return active; }
  }
  state.currentPlan = null;
  return active;
}

function weekNavHtml() {
  return `<div class="row" style="margin-bottom:12px">
    <button class="btn sm" data-action="plan-week-prev">‹ Settimana prec.</button>
    <button class="btn sm" data-action="plan-week-next">Settimana succ. ›</button>
  </div>`;
}

VIEWS.piano = async () => {
  const active = await ensurePlan();
  if (!active) {
    return head('Piano', 'Calendario editoriale') +
      '<div class="empty">Nessun profilo. Crea prima un profilo in <b>Creator</b>.</div>';
  }

  if (!state.currentPlan) {
    const ws = state.planWeekStart || currentWeek()[0];
    const we = addDays(ws, 6);
    return head('Piano', 'Calendario editoriale · ' + esc(active.nome)) +
      weekNavHtml() + `
      <div class="card hero"><div class="hs-title" style="font-size:19px">Nessun piano per la settimana ${prettyDate(ws)} → ${prettyDate(we)}</div>
      <div class="muted" style="margin:8px 0 16px">Crea il piano della settimana per iniziare a distribuire i contenuti.</div>
      <div class="row"><span class="faint">Settimana</span><input id="planWs" type="date" value="${ws}" />
        <span class="faint">→</span><input id="planWe" type="date" value="${we}" />
        <button class="btn primary" data-action="plan-create" data-profile="${active.id}">Crea piano</button></div></div>`;
  }

  const pl = state.currentPlan;
  const statusBadge = pl.status === 'approvato' ? '<span class="badge green">Approvato</span>' : '<span class="badge amber">Bozza</span>';
  const days = state.meta.giorni;
  const cts = state.meta.content_types;
  const todayIso = isoDate(new Date());

  const cards = days.map((d, idx) => {
    const dateIso = addDays(pl.week_start, idx);
    const isToday = dateIso === todayIso;
    const rows = cts.map((ct) => {
      const n = pl.grid[ct][d];
      return `<div class="dc-row">
        <span class="dc-dot" style="background:${CT_COLORS[ct]}"></span>
        <span class="dc-label">${CT_LABELS[ct]}</span>
        <span class="dc-count num">${n}</span>
        <span class="dc-steppers">
          <button data-action="cell-dec" data-ct="${ct}" data-day="${d}">−</button>
          <button data-action="cell-inc" data-ct="${ct}" data-day="${d}">+</button>
        </span></div>`;
    }).join('');
    const dayTotal = pl.totals_by_day[d];
    return `<div class="daycard ${isToday ? 'today' : ''}">
      <div class="dc-head"><span class="dc-day">${DAY_LABELS[d]}</span><span class="dc-date num">${dayNum(dateIso)}</span></div>
      <div class="dc-rows">${rows}</div>
      <div class="dc-foot"><span>totale</span><span class="num">${dayTotal}</span></div>
    </div>`;
  }).join('');

  const actions = `<div class="row wrap">
    <span class="badge gray">v${pl.version}</span>${statusBadge}
    <span class="badge ${pl.missing_references ? 'amber' : 'green'}">${pl.assigned_references}/${pl.total} ref</span>
    <button class="btn sm blue" data-action="plan-assign-refs">Assegna reference</button>
    <button class="btn sm" data-action="plan-toggle-alloc-preview">${state.showAllocPreview ? 'Nascondi' : 'Anteprima'} assegnazione</button>
    <button class="btn sm" data-action="plan-duplicate" data-profile="${active.id}">Duplica come prossima settimana</button>
    <button class="btn sm" data-action="plan-toggle-monthly">Vista mensile</button>
    <button class="btn danger sm" data-action="plan-reset">Azzera</button>
    <button class="btn primary" data-action="plan-approve">Approva piano →</button></div>`;

  const updatedNote = pl.updated_at
    ? `<div class="faint" style="margin:-10px 0 16px">Ultima modifica: ${esc(pl.updated_at.replace('T', ' ').slice(0, 19))}</div>` : '';

  let monthlyHtml = '';
  if (state.showMonthly) {
    const [year, month] = pl.week_start.split('-').map(Number);
    const m = await call('monthly_summary', active.id, year, month);
    monthlyHtml = m.ok ? monthlySummarySection(m) : `<div class="empty">${esc(m.error)}</div>`;
  }

  let allocPreviewHtml = '';
  if (state.showAllocPreview) {
    const ap = await call('plan_allocation_preview', pl.id);
    allocPreviewHtml = ap.ok ? allocationPreviewSection(ap) : `<div class="empty">${esc(ap.error)}</div>`;
  }

  return head('Piano', 'Calendario editoriale · ' + prettyDate(pl.week_start) + ' → ' + prettyDate(pl.week_end), actions) +
    updatedNote + weekNavHtml() +
    chipStrip(cts.map((ct) => ({ label: CT_LABELS[ct], value: pl.totals_by_type[ct], color: CT_COLORS[ct] }))
      .concat([{ label: 'Totale', value: pl.total, color: '#4c8bf5' }])) +
    `<div class="week-strip">${cards}</div>` +
    monthlyHtml + allocPreviewHtml;
};

function allocationPreviewSection(ap) {
  // Simula assign_references_to_plan SENZA assegnare nulla: mostra quali
  // reference verrebbero scelte se si assegnasse ora — richiesto
  // dall'utente per vedere il mix prima di premere Approva.
  const rows = ap.pieces.length ? ap.pieces.map((p) => `
    <div class="row" style="padding:5px 0">
      <span class="dc-dot" style="background:${CT_COLORS[p.content_type] || '#4c8bf5'};width:8px;height:8px;border-radius:50%"></span>
      <span class="muted" style="flex:1;margin-left:8px">${CT_LABELS[p.content_type] || esc(p.content_type)} #${p.piece_id}</span>
      ${p.would_assign
        ? `<span class="faint" style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.reference_category || '')}${p.reference_week ? ' · ' + esc(p.reference_week) : ''}${p.reference_caption ? ' · "' + esc(p.reference_caption) + '"' : ''}</span>`
        : '<span class="badge amber">nessuna reference disponibile</span>'}
    </div>`).join('') : '<div class="empty">Tutte le reference sono gia\' assegnate.</div>';
  return `<div class="section-title">Anteprima assegnazione (${ap.would_assign} assegnabili, ${ap.would_miss} mancanti — nessuna modifica salvata)</div>
    <div class="card">${rows}</div>`;
}

function monthlySummarySection(m) {
  const MONTH_NAMES = ['', 'Gennaio', 'Febbraio', 'Marzo', 'Aprile', 'Maggio', 'Giugno', 'Luglio', 'Agosto', 'Settembre', 'Ottobre', 'Novembre', 'Dicembre'];
  const weekRows = m.weeks.length
    ? m.weeks.map((w) => `<div class="row" style="padding:5px 0">
        <span class="muted" style="flex:1">${prettyDate(w.week_start)} → ${prettyDate(w.week_end)}</span>
        <span class="badge ${w.status === 'approvato' ? 'green' : 'amber'}">${w.status}</span>
        <span class="num" style="margin-left:10px">${w.total}</span>
      </div>`).join('')
    : '<div class="empty">Nessun piano in questo mese.</div>';
  const byTypeRows = Object.keys(m.totals_by_type).length
    ? Object.entries(m.totals_by_type).map(([ct, n]) => `<div class="row" style="padding:5px 0">
        <span class="dc-dot" style="background:${CT_COLORS[ct] || '#4c8bf5'};width:8px;height:8px;border-radius:50%"></span>
        <span class="muted" style="flex:1;margin-left:8px">${CT_LABELS[ct] || esc(ct)}</span>
        <span class="num">${n}</span>
      </div>`).join('')
    : '<div class="faint">nessun contenuto</div>';
  return `
    <div class="section-title">Riepilogo mensile — ${MONTH_NAMES[m.month]} ${m.year} (${m.total_pieces} contenuti)</div>
    <div class="grid cols-2">
      <div class="card"><div class="muted" style="font-weight:700;margin-bottom:10px">Per settimana</div>${weekRows}</div>
      <div class="card"><div class="muted" style="font-weight:700;margin-bottom:10px">Per tipo</div>${byTypeRows}</div>
    </div>`;
}

/* ============ Vista: Produzione ============ */
function timelineRows(events) {
  if (!events || !events.length) return '<div class="faint">Nessun evento ancora.</div>';
  return events.map((e) => `
    <div class="row" style="padding:3px 0;font-size:11.5px">
      <span class="badge ${e.status === 'completed' ? 'green' : e.status === 'failed' ? 'red' : 'amber'}" style="min-width:78px;text-align:center">${esc(e.status)}</span>
      <span style="flex:1">${esc(e.stage)}</span>
      ${e.duration_seconds != null ? `<span class="num faint">${e.duration_seconds.toFixed(1)}s</span>` : ''}
      <span class="faint" style="margin-left:8px">${esc((e.timestamp || '').replace('T', ' ').slice(0, 19))}</span>
    </div>
    ${e.detail ? `<div class="faint" style="padding-left:86px;font-size:10.5px;color:var(--red)">${esc(e.detail.slice(0, 200))}</div>` : ''}
  `).join('');
}

VIEWS.produzione = async () => {
  const [r, piecesRes, stageStats] = await Promise.all([
    call('production_preview'), call('list_content_pieces', null, null, 20), call('stage_duration_stats'),
  ]);
  const meta = state.meta;
  const capCards = meta.content_types.map((ct) => `<div class="card">
    <div class="row"><b>${CT_LABELS[ct]}</b><div class="spacer"></div><span class="badge green">Pronto</span></div>
    <div class="faint" style="margin-top:8px">${meta.pipeline[ct].join(' → ')} → qa → delivery</div></div>`).join('');
  let hero;
  if (!r.ok) {
    hero = `<div class="card hero bad"><div class="hs-title">${esc(r.error)}</div></div>`;
  } else {
    const covers = r.covers;
    hero = `<div class="card hero ${covers ? 'ok' : 'bad'}"><div class="hero-status"><div>
      <div class="hs-kicker">${covers ? 'Situazione' : 'Attenzione'}</div>
      <div class="hs-title">${r.ready_count ? (covers ? 'Produzione pronta' : 'Budget insufficiente') : 'Nessun contenuto in coda'}</div>
      <div class="muted">${r.ready_count} contenuti pronti da piani approvati · stima ${fmt(r.estimated_cost)} crediti su ${fmt(r.balance)} disponibili.</div>
      <div class="row" style="margin-top:16px">
        <button class="btn primary" data-action="prod-preview">Avvia una prova senza costi</button>
        ${r.ready_count ? `<button class="btn" data-action="prod-toggle-dry-run">${state.showDryRun ? 'Nascondi' : 'Vedi'} dettaglio pezzi</button>` : ''}
        <button class="btn danger" data-action="prod-run" ${(!r.ready_count || !covers) ? 'disabled' : ''}>Produci davvero</button>
      </div>
    </div></div></div>`;
  }

  let dryRunHtml = '';
  if (r.ok && state.showDryRun && (r.pieces || []).length) {
    dryRunHtml = `<div class="section-title">Dettaglio (nessun credito speso)</div>
      <div class="card"><div class="plist">${r.pieces.map((p) => `
        <div class="row" style="padding:5px 0">
          <span class="dc-dot" style="background:${CT_COLORS[p.content_type] || '#4c8bf5'};width:8px;height:8px;border-radius:50%"></span>
          <span class="muted" style="flex:1;margin-left:8px">${CT_LABELS[p.content_type] || esc(p.content_type)} #${p.id}${p.reference_category ? ' · ' + esc(p.reference_category) : ''}</span>
          <span class="num">${fmt(p.estimated_cost)} CR</span>
        </div>`).join('')}</div></div>`;
  }

  let piecesHtml = '<div class="empty">Nessun pezzo prodotto finora.</div>';
  if (piecesRes.ok && piecesRes.pieces.length) {
    const rows = await Promise.all(piecesRes.pieces.map(async (p) => {
      const expanded = state.expandedPieceId === p.id;
      let timelineHtml = '';
      if (expanded) {
        const t = await call('piece_timeline', p.id);
        timelineHtml = t.ok ? timelineRows(t.events) : `<div class="empty">${esc(t.error)}</div>`;
      }
      const qualityOpts = [0, 1, 2, 3, 4, 5].map((n) =>
        `<option value="${n}" ${p.quality_rating === n || (!p.quality_rating && n === 0) ? 'selected' : ''}>${n === 0 ? 'voto…' : '★'.repeat(n)}</option>`).join('');
      return `<div class="card" style="margin-bottom:8px">
        <div class="row" data-action="piece-toggle-timeline" data-id="${p.id}" style="cursor:pointer">
          ${thumbBox(p.thumbnail_url, (CT_LABELS[p.content_type] || '?')[0], 36, p.preview_kind, p.preview_url)}
          <div><div class="p-name">${CT_LABELS[p.content_type] || esc(p.content_type)} · #${p.id}</div>
            <div class="faint">${esc(p.profile_nome || '—')}</div></div>
          <div class="spacer"></div>
          ${p.cost_credits_actual != null ? `<span class="faint num" style="margin-right:10px">${fmt(p.cost_credits_actual)} CR</span>` : ''}
          <span class="badge ${PIECE_STATUS_BADGE[p.status] || 'gray'}">${PIECE_STATUS_LABELS[p.status] || esc(p.status)}</span>
        </div>
        <div class="row wrap" style="margin-top:8px;gap:8px">
          ${PIECE_FAILURE_STATUSES.includes(p.status) ? `<button class="btn sm" data-action="piece-retry" data-id="${p.id}">Riprova${p.was_refused ? ' (prompt più prudente)' : ''}</button>` : ''}
          ${p.status === 'reference_ready' ? `<button class="btn sm" data-action="piece-bump-priority" data-id="${p.id}">Metti in cima alla coda</button>` : ''}
          ${p.status === 'delivered' ? `<select data-change="piece-quality" data-id="${p.id}" style="width:90px">${qualityOpts}</select>` : ''}
          ${p.has_output ? `<button class="btn sm" data-action="piece-open-folder" data-id="${p.id}">Apri cartella</button>` : ''}
        </div>
        ${expanded ? `<div style="margin-top:10px;padding-top:10px;border-top:1px solid var(--border-soft)">${timelineHtml}</div>` : ''}
      </div>`;
    }));
    piecesHtml = rows.join('');
  }

  const stageStatsHtml = stageStats.ok && stageStats.stages.length
    ? `<div class="section-title">Durata media per stadio</div>
      <div class="card"><div class="grid cols-3">${stageStats.stages.map((s) => `
        <div class="tile"><div class="t-label">${esc(s.stage)}</div><div class="t-value num">${s.avg_seconds.toFixed(1)}s</div><div class="t-sub">${s.count} completati</div></div>
      `).join('')}</div></div>`
    : '';

  return head('Produzione', 'Centro operativo della produzione') + hero + dryRunHtml + `
    <div class="section-title">Capacità di produzione</div>
    <div class="grid cols-3">${capCards}</div>
    ${stageStatsHtml}
    <div class="section-title">Pezzi recenti (clicca per la timeline a checkpoint)</div>
    ${piecesHtml}`;
};

/* ============ Vista: Libreria ============ */
function weeklyTrendRows(trend) {
  if (!trend || !trend.ok || !trend.weeks.length) return '<div class="empty">Nessun dato per settimana.</div>';
  const legend = `<div class="row wrap" style="margin-bottom:16px;gap:16px">
    <span class="row" style="gap:6px"><span style="width:9px;height:9px;border-radius:50%;background:var(--green);display:inline-block"></span><span class="faint">Pronte</span></span>
    <span class="row" style="gap:6px"><span style="width:9px;height:9px;border-radius:50%;background:var(--red);display:inline-block"></span><span class="faint">Errore</span></span>
    <span class="row" style="gap:6px"><span style="width:9px;height:9px;border-radius:50%;background:var(--amber);display:inline-block"></span><span class="faint">In attesa</span></span>
  </div>`;
  const rows = trend.weeks.map((w) => {
    const total = Math.max(1, w.total);
    const pct = (n) => (n / total * 100).toFixed(1) + '%';
    return `<div style="margin-bottom:16px">
      <div class="row" style="margin-bottom:6px">
        <span class="muted" style="font-weight:700">Sett. ${esc(prettyDate(w.week_start))}</span>
        <div class="spacer"></div>
        <span class="num" style="color:var(--green)">${w.ready} pronte</span>
        <span class="faint" style="margin:0 6px">·</span>
        <span class="num" style="color:var(--red)">${w.error} errore</span>
        <span class="faint" style="margin:0 6px">·</span>
        <span class="num" style="color:var(--amber)">${w.pending} attesa</span>
        <span class="faint" style="margin-left:10px">(${w.total} totali)</span>
      </div>
      <div style="display:flex;height:10px;border-radius:5px;overflow:hidden;background:var(--bg-card-2)">
        <div style="width:${pct(w.ready)};background:var(--green)"></div>
        <div style="width:${pct(w.error)};background:var(--red)"></div>
        <div style="width:${pct(w.pending)};background:var(--amber)"></div>
      </div>
    </div>`;
  }).join('');
  return legend + rows;
}

function thumbBox(url, fallbackLetter, size, previewKind, previewUrl) {
  size = size || 52;
  const img = url
    ? `<img src="${esc(url)}" style="width:${size}px;height:${size}px;border-radius:9px;object-fit:cover;flex-shrink:0;background:var(--bg-card-2)" onerror="this.style.display='none'" />`
    : `<div class="p-avatar" style="width:${size}px;height:${size}px;flex-shrink:0">${esc(fallbackLetter || '?')}</div>`;
  if (!previewKind || !previewUrl) return img;
  return `<div data-action="open-lightbox" data-kind="${esc(previewKind)}" data-url="${esc(previewUrl)}" style="cursor:zoom-in;flex-shrink:0">${img}</div>`;
}

/* ============ Ricerca globale (topbar) ============ */
async function runGlobalSearch() {
  const q = ($('#globalSearchInput')?.value || '').trim();
  const panel = $('#globalSearchPanel');
  if (!panel) return;
  if (!q) { panel.innerHTML = ''; return; }
  const r = await call('global_search', q);
  if (!r.ok) { panel.innerHTML = `<div class="empty">${esc(r.error)}</div>`; return; }
  const sections = [
    ['Reference', r.references, (x) => `${esc(x.category || '—')} · ${esc(x.status)}${x.caption ? ' — "' + esc(x.caption.slice(0, 60)) + '"' : ''}`, 'libreria'],
    ['Contenuti generati', r.pieces, (x) => `${CT_LABELS[x.content_type] || esc(x.content_type)} #${x.id} · ${esc(x.status)}`, 'libreria'],
    ['Da migliorare', r.backlog, (x) => `${esc(x.title)} · ${esc(x.category)}`, 'backlog'],
  ];
  let html = '';
  for (const [title, items, render, view] of sections) {
    if (!items.length) continue;
    html += `<div class="gsearch-section-title">${title}</div>` +
      items.map((x) => `<div class="gsearch-item" data-action="gsearch-goto" data-view="${view}">${render(x)}</div>`).join('');
  }
  panel.innerHTML = html || '<div class="empty">Nessun risultato.</div>';
}

/* ============ Lightbox QA (foto/video a piena risoluzione) ============ */
function openLightbox(kind, url) {
  closeLightbox();
  const backdrop = document.createElement('div');
  backdrop.className = 'lightbox-backdrop';
  backdrop.id = 'lightboxBackdrop';
  const media = kind === 'video'
    ? `<video src="${esc(url)}" controls autoplay></video>`
    : `<img src="${esc(url)}" />`;
  backdrop.innerHTML = `<button class="lightbox-close" data-action="close-lightbox">✕</button>
    <div class="lightbox-content">${media}</div>`;
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) closeLightbox(); });
  document.body.appendChild(backdrop);
}
function closeLightbox() {
  const el = document.getElementById('lightboxBackdrop');
  if (el) el.remove();
}

const LIB_PAGE_SIZE = 50;

VIEWS.libreria = async () => {
  const filter = state.libFilter || { status: '', category: '', search: '', page: 0 };
  const [r, listed, trend, generated] = await Promise.all([
    call('reference_stats'),
    call('list_references', filter.status || null, filter.category || null, filter.search || null, LIB_PAGE_SIZE, (filter.page || 0) * LIB_PAGE_SIZE),
    call('reference_weekly_trend', 8),
    call('list_content_pieces', null, null, 20),
  ]);
  if (!r.ok) return `<div class="empty">${esc(r.error)}</div>`;
  const statusRows = Object.keys(r.by_status || {}).length
    ? Object.entries(r.by_status).map(([s, n]) => `<div class="row" style="padding:5px 0"><span class="muted" style="flex:1">${esc(s)}</span><span class="num">${n}</span></div>`).join('')
    : '<div class="empty">Nessuna reference ancora importata.</div>';
  const weekRows = Object.keys(r.by_week || {}).length
    ? Object.entries(r.by_week).map(([s, n]) => `<div class="row" style="padding:5px 0"><span class="muted" style="flex:1">${esc(s)}</span><span class="num">${n}</span></div>`).join('')
    : '<div class="faint">nessuna settimana</div>';
  const categoryRows = Object.keys(r.by_category || {}).length
    ? Object.entries(r.by_category).map(([s, n]) => `<div class="row" style="padding:5px 0"><span class="muted" style="flex:1">${esc(s)}</span><span class="num">${n}</span></div>`).join('')
    : '<div class="faint">nessuna categoria</div>';

  const statusOpts = ['', ...Object.keys(r.by_status || {})].map((s) =>
    `<option value="${esc(s)}" ${filter.status === s ? 'selected' : ''}>${s ? esc(s) : 'Tutti gli stati'}</option>`).join('');
  const categoryNames = [...new Set(Object.keys(r.by_category || {}).map((k) => k.split(' / ').pop()))];
  const categoryOpts = ['', ...categoryNames].map((c) =>
    `<option value="${esc(c)}" ${filter.category === c ? 'selected' : ''}>${c ? esc(c) : 'Tutte le categorie'}</option>`).join('');

  const filteredRows = listed.ok && (listed.references || []).length
    ? listed.references.map((x) => `
      <div class="prow">
        ${thumbBox(x.thumbnail_url, (x.source_category || '?')[0], 52, x.preview_kind, x.preview_url)}
        <div style="min-width:0">
          <div class="p-name">${esc(x.source_category || '—')} <span class="badge ${REF_STATUS_BADGE[x.status] || 'gray'}">${esc(x.status)}</span>
            ${x.content_type_hint === 'video' ? '<span class="badge gray">video</span>' : ''}${x.has_transcript ? '<span class="badge gray">trascrizione</span>' : ''}</div>
          <div class="faint">${esc(x.week_start || 'senza settimana')} · ${esc(x.source_tab || '—')}${x.error_message ? ' · ' + esc(x.error_message) : ''}</div>
          ${x.original_caption ? `<div class="faint" style="margin-top:2px;max-width:520px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">"${esc(x.original_caption)}"</div>` : ''}
        </div>
        <div class="spacer"></div>
        ${x.retryable ? `<button class="btn sm" data-action="reference-retry" data-id="${x.id}">Riprova (${x.download_attempts}/${x.max_download_attempts})</button>`
          : REF_RETRYABLE_STATUSES.includes(x.status) ? `<span class="faint">Non disponibile · limite tentativi raggiunto</span>` : ''}
        ${x.has_local_media ? `<button class="btn sm" data-action="reference-open-folder" data-id="${x.id}">Apri cartella</button>` : ''}
      </div>`).join('')
    : '<div class="empty">Nessuna reference con questo filtro.</div>';

  const generatedRows = generated.ok && generated.pieces.length
    ? generated.pieces.map((p) => `
      <div class="prow">
        ${thumbBox(p.thumbnail_url, (CT_LABELS[p.content_type] || '?')[0], 52, p.preview_kind, p.preview_url)}
        <div style="min-width:0">
          <div class="p-name">${CT_LABELS[p.content_type] || esc(p.content_type)} · #${p.id}
            <span class="badge ${PIECE_STATUS_BADGE[p.status] || 'gray'}">${PIECE_STATUS_LABELS[p.status] || esc(p.status)}</span>
            ${p.quality_rating ? `<span class="badge amber">${'★'.repeat(p.quality_rating)}</span>` : ''}</div>
          <div class="faint">${esc(p.profile_nome || '—')}${p.cost_credits_actual != null ? ' · ' + fmt(p.cost_credits_actual) + ' CR' : ''}</div>
          ${p.caption ? `<div class="faint" style="margin-top:2px;max-width:520px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">"${esc(p.caption)}"</div>` : ''}
        </div>
        <div class="spacer"></div>
        ${p.has_output ? `<button class="btn sm" data-action="piece-open-folder" data-id="${p.id}">Apri cartella</button>` : ''}
      </div>`).join('')
    : '<div class="empty">Nessun contenuto generato ancora — vai in Produzione.</div>';

  const actions = `<button class="btn primary" data-action="references-sync">Aggiorna libreria</button>
    ${r.error_retryable ? `<button class="btn danger" data-action="reference-retry-all">Riprova tutti (${r.error_retryable})</button>` : ''}`;
  return head('Libreria', 'Magazzino operativo delle reference e dei contenuti generati', actions) +
    chipStrip([
      { label: 'Pronte', value: r.ready, color: '#8fe23a' },
      { label: 'Totali', value: r.total, color: '#4c8bf5' },
      { label: 'In attesa', value: r.pending, color: '#e8b23d' },
      { label: 'Errore', value: r.error, color: '#ff6b5e' },
      { label: 'Vecchie', value: r.too_old, color: '#a0a7b8' },
    ]) + `
    <div class="section-title">Stato del magazzino</div>
    <div class="grid cols-3">
      <div class="card"><div class="muted" style="font-weight:700;margin-bottom:10px">Per stato</div>${statusRows}</div>
      <div class="card"><div class="muted" style="font-weight:700;margin-bottom:10px">Per settimana</div>${weekRows}</div>
      <div class="card"><div class="muted" style="font-weight:700;margin-bottom:10px">Per categoria</div>${categoryRows}</div>
    </div>
    <div class="section-title">Andamento (ultime settimane)</div>
    <div class="card">${weeklyTrendRows(trend)}</div>
    <div class="section-title">Contenuti generati (ultimi 20)</div>
    <div class="plist">${generatedRows}</div>
    <div class="section-title">Aggiungi un link manualmente</div>
    <div class="card" style="margin-bottom:20px">
      <div class="row wrap">
        <input id="manualRefUrl" placeholder="URL Instagram" style="flex:2;min-width:220px" />
        <select id="manualRefTab">
          <option value="CAROSELLI">CAROSELLI</option>
          <option value="VIRAL GENERAL">VIRAL GENERAL</option>
        </select>
        <input id="manualRefCategory" placeholder="Categoria (es. BOOBS, TALKING)" style="width:170px" />
        <select id="manualRefType">
          <option value="carosello">Carosello</option>
          <option value="video">Video</option>
        </select>
        <button class="btn blue" data-action="reference-import-manual">Importa e scarica</button>
      </div>
      <div class="faint" style="margin-top:8px">Scarica subito, fuori dal normale giro dello sheet — utile per aggiungere un link al volo senza aspettare il prossimo sync.</div>
    </div>
    <div class="section-title">Reference scaricate (filtrabili, cercabili)</div>
    <div class="row wrap" style="margin-bottom:12px">
      <select id="libFilterStatus" data-change="lib-filter-status">${statusOpts}</select>
      <select id="libFilterCategory" data-change="lib-filter-category">${categoryOpts}</select>
      <input id="libSearchInput" placeholder="Cerca per caption o URL…" value="${esc(filter.search || '')}" style="flex:1;min-width:200px" data-keydown="lib-search-enter" />
      <button class="btn sm" data-action="lib-search">Cerca</button>
      ${filter.search ? '<button class="btn sm" data-action="lib-search-clear">Pulisci</button>' : ''}
    </div>
    <div class="plist">${filteredRows}</div>
    ${listedPagination(listed, filter)}
    <div class="faint" style="margin-top:12px">Finestra pesca: ultime ${r.selection_weeks} settimane · pulizia reference IG dopo ${r.retention_days} giorni.</div>`;
};

function listedPagination(listed, filter) {
  if (!listed.ok || !listed.total) return '';
  const page = filter.page || 0;
  const totalPages = Math.max(1, Math.ceil(listed.total / LIB_PAGE_SIZE));
  const from = listed.total === 0 ? 0 : page * LIB_PAGE_SIZE + 1;
  const to = Math.min(listed.total, (page + 1) * LIB_PAGE_SIZE);
  return `<div class="row" style="margin-top:12px;justify-content:center;gap:14px">
    <button class="btn sm" data-action="lib-page-prev" ${page <= 0 ? 'disabled' : ''}>‹ Precedente</button>
    <span class="faint num">${from}–${to} di ${listed.total}</span>
    <button class="btn sm" data-action="lib-page-next" ${page >= totalPages - 1 ? 'disabled' : ''}>Successiva ›</button>
  </div>`;
}

/* ============ Vista: Costi ============ */
VIEWS.costi = async () => {
  await ensurePlan();
  const planId = state.currentPlan ? state.currentPlan.id : null;
  const [r, history, spendByType, projection, costCompare] = await Promise.all([
    call('budget_status', planId),
    call('ledger_history', 30),
    call('spend_by_content_type'),
    call('monthly_projection', 14),
    call('cost_estimate_vs_actual'),
  ]);
  if (!r.ok) return `<div class="empty">${esc(r.error)}</div>`;
  const alertBanner = r.budget_alert
    ? `<div class="warn-list" style="margin-bottom:16px"><div class="warn">Saldo sotto la soglia di ${fmt(r.budget_alert_threshold)} CR — valuta una ricarica prima di produrre altro.</div></div>`
    : '';
  let hero = '';
  if (r.plan_cost != null) {
    const covers = r.covers;
    hero = `<div class="card hero ${covers ? 'ok' : 'bad'}">
      <div class="hs-kicker">${covers ? 'Budget sufficiente' : 'Il budget non copre il piano'}</div>
      <div class="hs-title">${covers ? 'Puoi approvare' : 'Mancano ' + fmt(Math.abs(r.coverage)) + ' crediti'}</div>
      <div class="grid cols-3" style="margin-top:16px">
        <div class="tile accent-green"><div class="t-label">Disponibili</div><div class="t-value num">${fmt(r.balance)}</div></div>
        <div class="tile accent-amber"><div class="t-label">Costo piano</div><div class="t-value num">${fmt(r.plan_cost)}</div></div>
        <div class="tile accent-${covers ? 'blue' : 'red'}"><div class="t-label">Copertura</div><div class="t-value num">${fmt(r.coverage)}</div></div>
      </div></div>`;
  }

  const projTile = projection.ok
    ? `<div class="tile accent-blue"><div class="t-label">Proiezione 30gg</div><div class="t-value num">${fmt(projection.projected_30_days)}</div><div class="t-sub">~${fmt(projection.daily_avg)} CR/giorno, ultimi ${projection.window_days}gg</div></div>`
    : '';

  const spendTotal = spendByType.ok ? Object.values(spendByType.totals).reduce((a, b) => a + b, 0) : 0;
  const spendRows = spendByType.ok && Object.keys(spendByType.totals).length
    ? Object.entries(spendByType.totals).sort((a, b) => b[1] - a[1]).map(([ct, credits]) => {
        const pct = spendTotal ? (credits / spendTotal * 100) : 0;
        return `<div style="margin-bottom:8px">
          <div class="row" style="margin-bottom:3px"><span class="muted" style="flex:1">${CT_LABELS[ct] || esc(ct)}</span><span class="num">${fmt(credits)} CR</span></div>
          <div style="height:6px;border-radius:3px;background:var(--bg-card-2);overflow:hidden"><div style="width:${pct.toFixed(1)}%;height:100%;background:${CT_COLORS[ct] || '#4c8bf5'}"></div></div>
        </div>`;
      }).join('')
    : '<div class="empty">Nessuna spesa registrata ancora.</div>';

  const historyRows = history.ok && history.entries.length
    ? history.entries.map((e) => `<div class="row" style="padding:5px 0">
        <span class="faint" style="width:150px">${esc((e.timestamp || '').replace('T', ' ').slice(0, 19))}</span>
        <span class="muted" style="flex:1">${esc(e.motivo)}${e.content_type ? ' · ' + (CT_LABELS[e.content_type] || esc(e.content_type)) : ''}</span>
        <span class="num" style="color:${e.delta_credits < 0 ? 'var(--red)' : 'var(--green)'}">${e.delta_credits > 0 ? '+' : ''}${fmt(e.delta_credits)}</span>
      </div>`).join('')
    : '<div class="empty">Nessun movimento ancora.</div>';

  const compareRows = costCompare.ok && Object.keys(costCompare.by_content_type).length
    ? Object.entries(costCompare.by_content_type).map(([ct, b]) => `
        <div class="row" style="padding:5px 0">
          <span class="muted" style="flex:1">${CT_LABELS[ct] || esc(ct)} <span class="faint">(${b.count})</span></span>
          <span class="faint num" style="margin-right:10px">stima ${fmt(b.estimated)}</span>
          <span class="num" style="margin-right:10px">reale ${fmt(b.actual)}</span>
          <span class="num" style="color:${b.delta > 0 ? 'var(--red)' : 'var(--green)'}">${b.delta > 0 ? '+' : ''}${fmt(b.delta)}${b.delta_pct != null ? ' (' + b.delta_pct.toFixed(0) + '%)' : ''}</span>
        </div>`).join('')
    : '<div class="empty">Nessun pezzo con stima e costo reale entrambi disponibili ancora.</div>';

  return head('Costi', 'Budget e crediti (fonte: CreditLedger)') + alertBanner + hero + `
    <div class="grid cols-4" style="margin-top:16px">
      <div class="tile accent-${r.balance < 0 ? 'red' : 'green'}"><div class="t-label">Saldo attuale</div><div class="t-value num">${fmt(r.balance)}</div><div class="t-sub">crediti</div></div>
      ${projTile}
      <div class="card"><div class="muted" style="font-weight:700;margin-bottom:10px">Ricarica crediti</div>
        <div class="row"><input id="topupAmount" type="number" placeholder="es. 100" style="flex:1" />
          <button class="btn blue" data-action="budget-topup">Ricarica</button></div></div>
      <div class="card"><div class="muted" style="font-weight:700;margin-bottom:10px">Allinea a Higgsfield</div>
        <div class="row"><span class="faint" style="flex:1">Legge il saldo reale e registra la rettifica.</span>
          <button class="btn" data-action="budget-sync">Sincronizza</button></div></div>
    </div>
    <div class="section-title">Spesa per tipo contenuto</div>
    <div class="card">${spendRows}</div>
    <div class="section-title">Stima vs reale (per tenere aggiornate le previsioni di costo)</div>
    <div class="card">${compareRows}</div>
    <div class="section-title">Storico movimenti (ultimi 30)</div>
    <div class="card">${historyRows}</div>`;
};

/* ============ Vista: Sistema ============ */
VIEWS.sistema = async () => {
  const [r, health, sched, charHist] = await Promise.all([
    call('overview'), call('health_check'), call('scheduler_status'), call('character_history'),
  ]);
  if (!r.ok) return `<div class="empty">${esc(r.error)}</div>`;
  const o = r.overview;
  const block = (title, map) => `<div class="card"><div class="section-title" style="margin:0 0 12px">${title}</div>
    ${Object.keys(map).length ? Object.entries(map).map(([k, v]) =>
      `<div class="row" style="padding:4px 0"><span class="muted" style="flex:1">${esc(k)}</span><span class="num">${v}</span></div>`).join('') : '<div class="faint">nessuno</div>'}</div>`;

  const healthHtml = health.ok ? `<div class="card">
      <div class="muted" style="font-weight:700;margin-bottom:10px">Configurazione</div>
      <div class="row" style="padding:4px 0"><span class="muted" style="flex:1">CLI Higgsfield</span><span class="badge ${health.higgsfield_cli ? 'green' : 'red'}">${health.higgsfield_cli ? 'ok' : 'non trovato'}</span></div>
      <div class="row" style="padding:4px 0"><span class="muted" style="flex:1">CLI Claude</span><span class="badge ${health.claude_cli ? 'green' : 'red'}">${health.claude_cli ? 'ok' : 'non trovato'}</span></div>
      <div class="row" style="padding:4px 0"><span class="muted" style="flex:1">Credenziali Google Sheet</span><span class="badge ${health.google_sheet_credentials ? 'green' : 'red'}">${health.google_sheet_credentials ? 'ok' : 'mancanti'}</span></div>
    </div>` : `<div class="empty">${esc(health.error)}</div>`;

  const schedLine = (label, info) => info
    ? `<div class="row" style="padding:4px 0"><span class="muted" style="flex:1">${label}</span><span class="faint">${esc((info.last_modified || '').replace('T', ' ').slice(0, 19))}</span></div>`
    : `<div class="row" style="padding:4px 0"><span class="muted" style="flex:1">${label}</span><span class="faint">mai eseguito</span></div>`;
  const schedHtml = sched.ok ? `<div class="card">
      <div class="muted" style="font-weight:700;margin-bottom:10px">Scheduler settimanale</div>
      <div class="row" style="padding:4px 0"><span class="muted" style="flex:1">Installato</span><span class="badge ${sched.installed ? 'green' : 'gray'}">${sched.installed ? 'sì' : 'no'}</span></div>
      ${schedLine('Ultimo output', sched.out)}
      ${schedLine('Ultimo errore', sched.err)}
      ${sched.err && sched.err.tail ? `<div class="faint" style="margin-top:8px;white-space:pre-wrap;font-family:var(--mono);font-size:10.5px">${esc(sched.err.tail)}</div>` : ''}
    </div>` : `<div class="empty">${esc(sched.error)}</div>`;

  const charHtml = charHist.ok && charHist.versions.length
    ? `<div class="card"><div class="muted" style="font-weight:700;margin-bottom:10px">Storico personaggio (${charHist.versions[0].creator_nome})</div>
        ${charHist.versions.map((v, i) => `<div class="row" style="padding:5px 0;align-items:flex-start">
          <span class="badge ${i === 0 ? 'green' : 'gray'}">${i === 0 ? 'attuale' : 'v' + (charHist.versions.length - i)}</span>
          <span class="faint" style="flex:1">${esc((v.created_at || '').replace('T', ' ').slice(0, 19))} · ${esc(v.mandatory_additions)}</span>
        </div>`).join('')}</div>`
    : '<div class="empty">Nessuno storico personaggio ancora.</div>';

  return head('Sistema', 'Stato complessivo — legge lo stesso DB, nessuna logica duplicata',
    '<button class="btn" data-action="run-backup">Backup DB ora</button>') +
    chipStrip([
      { label: 'Saldo', value: fmt(o.saldo_crediti) + ' CR', color: o.saldo_crediti < 0 ? '#ff6b5e' : '#8fe23a' },
      { label: 'Profili', value: o.profili.length, color: '#4c8bf5' },
    ]) + `
    <div class="grid cols-3">
      ${block('Reference per stato', o.reference_per_stato)}
      ${block('Piani per stato', o.piani_per_stato)}
      ${block('Content per stato', o.content_per_stato)}</div>
    <div class="section-title">Configurazione e automazioni</div>
    <div class="grid cols-3">${healthHtml}${schedHtml}${charHtml}</div>`;
};

/* ============ Vista: Da migliorare (backlog) ============ */
VIEWS.backlog = async () => {
  const filter = state.backlogFilter || 'aperto';
  const search = state.backlogSearch || '';
  const r = await call('list_backlog', filter, search || null);
  if (!r.ok) return `<div class="empty">${esc(r.error)}</div>`;
  const notes = r.notes;

  const filterBtns = ['aperto', 'fatto', 'scartato', 'tutti'].map((f) =>
    `<button class="btn sm ${filter === f ? 'primary' : ''}" data-action="backlog-filter" data-status="${f}">${BACKLOG_STATUS_LABELS[f]}</button>`
  ).join('');
  const searchBar = `<div class="row wrap" style="margin-bottom:16px">
    <input id="backlogSearchInput" placeholder="Cerca nel backlog…" value="${esc(search)}" style="flex:1;min-width:200px" data-keydown="backlog-search-enter" />
    <button class="btn sm" data-action="backlog-search">Cerca</button>
    ${search ? '<button class="btn sm" data-action="backlog-search-clear">Pulisci</button>' : ''}
  </div>`;

  const rows = notes.length ? notes.map((n) => `
    <div class="card" style="margin-bottom:10px">
      <div class="row">
        <span class="badge blue">${esc(n.category)}</span>
        <b style="flex:1">${esc(n.title)}</b>
        <span class="faint num">${esc(n.created_at.slice(0, 10))}</span>
      </div>
      ${n.description ? `<div class="muted" style="margin-top:8px">${esc(n.description)}</div>` : ''}
      <div class="row" style="margin-top:12px">
        <span class="badge ${BACKLOG_STATUS_BADGE[n.status] || 'gray'}">${BACKLOG_STATUS_LABELS[n.status] || esc(n.status)}</span>
        <div class="spacer"></div>
        ${n.status === 'aperto'
          ? `<button class="btn sm" data-action="backlog-status" data-id="${n.id}" data-status="fatto">Segna fatto</button>
             <button class="btn sm danger" data-action="backlog-status" data-id="${n.id}" data-status="scartato">Scarta</button>`
          : `<button class="btn sm" data-action="backlog-status" data-id="${n.id}" data-status="aperto">Riapri</button>`}
      </div>
    </div>`).join('') : '<div class="empty">Nessuna voce con questo filtro.</div>';

  return head('Da migliorare', 'Backlog di limiti noti e idee — cose annotate durante il lavoro da riprendere in futuro') +
    `<div class="row wrap" style="margin-bottom:16px">${filterBtns}</div>` +
    searchBar +
    `<div class="card" style="margin-bottom:20px">
      <div class="muted" style="font-weight:700;margin-bottom:10px">Aggiungi voce</div>
      <div class="row wrap">
        <input id="newBacklogCategory" placeholder="Categoria (es. qualita)" style="width:160px" />
        <input id="newBacklogTitle" placeholder="Titolo" style="flex:1;min-width:200px" />
        <input id="newBacklogDesc" placeholder="Descrizione (opzionale)" style="flex:2;min-width:240px" />
        <button class="btn blue" data-action="backlog-add">Aggiungi</button>
      </div>
    </div>
    ${rows}`;
};

/* ============ Azioni ============ */
async function reloadPlanView() { await setView('piano'); }

const ACTIONS = {
  'goto-view': (el) => setView(el.dataset.view),
  'profile-activate': async (el) => {
    const r = await call('set_active_profile', Number(el.dataset.id));
    if (r.ok) { toast('Profilo attivo aggiornato'); state.profiles = []; state.planWeekStart = null; await refreshTopProfileSwitch(); await setView('creator'); } else toast(r.error, 'err');
  },
  'profile-delete': async (el) => {
    const id = Number(el.dataset.id), nome = el.dataset.name;
    if (!confirm(`Eliminare il profilo "${nome}"? L'azione non è reversibile.`)) return;
    const r = await call('delete_profile', id);
    if (r.ok) { toast('Profilo eliminato'); state.profiles = []; state.currentPlan = null; await refreshTopProfileSwitch(); await setView('creator'); }
    else toast(r.error, 'err');
  },
  'create-creator': async () => {
    const nome = $('#newCreatorName').value.trim();
    if (!nome) return toast('Inserisci un nome', 'err');
    const r = await call('create_creator', nome);
    r.ok ? (toast('Creator creato'), setView('creator')) : toast(r.error, 'err');
  },
  'create-profile': async () => {
    const cid = $('#newProfileCreator').value, nome = $('#newProfileName').value.trim(), tipo = $('#newProfileTipo').value;
    if (!nome) return toast('Inserisci un nome profilo', 'err');
    const r = await call('create_profile', cid, nome, tipo);
    if (r.ok) { toast('Profilo creato'); state.profiles = []; await refreshTopProfileSwitch(); setView('creator'); } else toast(r.error, 'err');
  },
  'plan-create': async (el) => {
    const ws = $('#planWs').value, we = $('#planWe').value;
    const r = await call('create_plan', Number(el.dataset.profile), ws, we);
    if (r.ok) { state.currentPlan = r.plan; toast('Piano creato'); reloadPlanView(); } else toast(r.error, 'err');
  },
  'cell-inc': (el) => stepCell(el.dataset.ct, el.dataset.day, +1),
  'cell-dec': (el) => stepCell(el.dataset.ct, el.dataset.day, -1),
  'plan-reset': async () => {
    if (!state.currentPlan) return;
    const pl = state.currentPlan;
    for (const ct of state.meta.content_types)
      for (const d of state.meta.giorni)
        if (pl.grid[ct][d] > 0) await call('plan_set_cell', pl.id, ct, d, 0);
    toast('Piano azzerato'); state.currentPlan = null; reloadPlanView();
  },
  'plan-duplicate': async () => {
    if (!state.currentPlan) return;
    const pl = state.currentPlan;
    const newWs = addDays(pl.week_end, 1);
    const newWe = addDays(newWs, 6);
    const r = await call('duplicate_plan', pl.id, newWs, newWe);
    if (r.ok) {
      toast('Piano duplicato su ' + prettyDate(newWs) + ' → ' + prettyDate(newWe));
      state.currentPlan = null; state.planWeekStart = newWs; reloadPlanView();
    } else toast(r.error, 'err');
  },
  'plan-week-prev': () => {
    const ws = state.planWeekStart || (state.currentPlan ? state.currentPlan.week_start : currentWeek()[0]);
    state.planWeekStart = addDays(ws, -7);
    state.currentPlan = null;
    reloadPlanView();
  },
  'plan-week-next': () => {
    const ws = state.planWeekStart || (state.currentPlan ? state.currentPlan.week_start : currentWeek()[0]);
    state.planWeekStart = addDays(ws, 7);
    state.currentPlan = null;
    reloadPlanView();
  },
  'plan-toggle-monthly': () => { state.showMonthly = !state.showMonthly; reloadPlanView(); },
  'plan-approve': async () => {
    if (!state.currentPlan) return;
    const r = await call('approve_plan', state.currentPlan.id);
    if (r.ok) {
      state.currentPlan = r.plan;
      const a = r.reference_assignment || { assigned: 0, missing: r.plan.missing_references };
      const msg = a.missing
        ? 'Piano approvato · mancano ' + a.missing + ' reference: aggiorna Libreria'
        : 'Piano approvato · reference assegnate ' + a.assigned;
      toast(msg);
      refreshBalance();
      reloadPlanView();
    }
    else if (r.kind === 'budget') toast('Budget non copre il piano: mancano ' + fmt(r.needed - r.available) + ' CR', 'err');
    else toast(r.error, 'err');
  },
  'plan-assign-refs': async () => {
    if (!state.currentPlan) return;
    const r = await call('assign_plan_references', state.currentPlan.id);
    if (r.ok) {
      state.currentPlan = r.plan;
      toast('Reference assegnate: ' + r.assigned + (r.missing ? ' · mancanti ' + r.missing : ''));
      reloadPlanView();
    } else toast(r.error, 'err');
  },
  'budget-topup': async () => {
    const v = parseFloat($('#topupAmount').value);
    if (!v || v <= 0) return toast('Importo non valido', 'err');
    const r = await call('budget_topup', v);
    r.ok ? (toast('Ricarica registrata'), refreshBalance(), setView('costi')) : toast(r.error, 'err');
  },
  'budget-sync': async () => {
    toast('Sincronizzo con Higgsfield…');
    const r = await call('budget_sync');
    r.ok ? (toast('Saldo allineato: rettifica ' + fmt(r.adjustment) + ' CR'), refreshBalance(), setView('costi')) : toast(r.error, 'err');
  },
  'references-sync': async () => {
    toast('Aggiorno libreria…');
    const r = await call('references_sync_policy');
    if (!r.ok) return toast(r.error, 'err');
    const conflicts = r.sync?.category_conflicts || [];
    toast('Libreria aggiornata · processati ' + (r.sync?.processed ?? '—') + ' · pronte ' + r.ready
      + (conflicts.length ? ` · attenzione: ${conflicts.length} URL con tab/categoria cambiati` : ''));
    setView('libreria');
  },
  'reference-import-manual': async () => {
    const url = ($('#manualRefUrl')?.value || '').trim();
    const tab = $('#manualRefTab')?.value;
    const category = ($('#manualRefCategory')?.value || '').trim();
    const contentType = $('#manualRefType')?.value;
    if (!url) return toast('Inserisci un URL', 'err');
    if (!category) return toast('Inserisci una categoria', 'err');
    toast('Importo e scarico…');
    const r = await call('import_reference_url', url, tab, category, contentType);
    if (r.ok) { toast('Reference importata: stato ' + r.import.status); setView('libreria'); } else toast(r.error, 'err');
  },
  'reference-retry': async (el) => {
    const id = Number(el.dataset.id);
    toast('Riprovo…');
    const r = await call('retry_reference', id);
    if (r.ok) { toast('Nuovo stato: ' + r.retry.status); setView('libreria'); } else toast(r.error, 'err');
  },
  'reference-retry-all': async (el) => {
    if (retryAllBusy) return;
    const ok = confirm('Ritenta tutte le reference fallite: puo\' richiedere qualche minuto (stesso rate-limit dei download singoli). Continuare?');
    if (!ok) return;
    retryAllBusy = true;
    el.disabled = true;
    toast('Riprovo tutte le reference fallite…');
    const r = await call('retry_all_references');
    retryAllBusy = false;
    if (r.ok) {
      const s = r.retry_all;
      toast(`Fatto: ${s.ready}/${s.total} tornate pronte, ${s.still_failed} ancora fallite`);
      setView('libreria');
    } else toast(r.error, 'err');
  },
  'reference-open-folder': async (el) => {
    const r = await call('open_reference_folder', Number(el.dataset.id));
    r.ok ? toast('Cartella aperta') : toast(r.error, 'err');
  },
  'piece-open-folder': async (el) => {
    const r = await call('open_piece_folder', Number(el.dataset.id));
    r.ok ? toast('Cartella aperta') : toast(r.error, 'err');
  },
  'prod-preview': async () => {
    const r = await call('production_preview');
    r.ok ? toast(r.ready_count + ' pronti · stima ' + fmt(r.estimated_cost) + ' CR (nessun credito speso)') : toast(r.error, 'err');
  },
  'prod-run': async () => {
    const ok = confirm('Produzione reale: usera crediti Higgsfield/Claude sui contenuti pronti. Vuoi continuare?');
    if (!ok) return;
    toast('Produzione reale avviata…');
    const r = await call('production_run', null, 'PRODUCI');
    if (r.ok) {
      const p = r.production || {};
      toast('Produzione completata · consegnati ' + (p.delivered ?? 0) + ' · falliti ' + (p.failed ?? 0));
      refreshBalance();
      setView('produzione');
    } else toast(r.error, 'err');
  },
  'piece-toggle-timeline': (el) => {
    const id = Number(el.dataset.id);
    state.expandedPieceId = state.expandedPieceId === id ? null : id;
    setView('produzione');
  },
  'lib-search': () => {
    const value = ($('#libSearchInput')?.value || '').trim();
    state.libFilter.search = value;
    state.libFilter.page = 0;
    setView('libreria');
  },
  'lib-search-clear': () => {
    state.libFilter.search = '';
    state.libFilter.page = 0;
    setView('libreria');
  },
  'lib-page-prev': () => {
    if ((state.libFilter.page || 0) > 0) { state.libFilter.page -= 1; setView('libreria'); }
  },
  'lib-page-next': () => {
    state.libFilter.page = (state.libFilter.page || 0) + 1;
    setView('libreria');
  },
  'backlog-filter': (el) => { state.backlogFilter = el.dataset.status; setView('backlog'); },
  'backlog-add': async () => {
    const category = $('#newBacklogCategory').value.trim() || 'generico';
    const title = $('#newBacklogTitle').value.trim();
    const description = $('#newBacklogDesc').value.trim();
    if (!title) return toast('Inserisci un titolo', 'err');
    const r = await call('add_backlog_note', category, title, description);
    r.ok ? (toast('Voce aggiunta al backlog'), setView('backlog')) : toast(r.error, 'err');
  },
  'backlog-status': async (el) => {
    const r = await call('set_backlog_status', Number(el.dataset.id), el.dataset.status);
    r.ok ? (toast('Stato aggiornato'), setView('backlog')) : toast(r.error, 'err');
  },
  'backlog-search': () => {
    state.backlogSearch = ($('#backlogSearchInput')?.value || '').trim();
    setView('backlog');
  },
  'backlog-search-clear': () => { state.backlogSearch = ''; setView('backlog'); },
  'open-lightbox': (el) => openLightbox(el.dataset.kind, el.dataset.url),
  'close-lightbox': () => closeLightbox(),
  'gsearch-goto': (el) => {
    $('#globalSearchPanel').innerHTML = '';
    $('#globalSearchInput').value = '';
    setView(el.dataset.view);
  },
  'piece-retry': async (el) => {
    const id = Number(el.dataset.id);
    toast('Riprovo il pezzo…');
    const r = await call('retry_content_piece', id);
    if (r.ok) { toast('Nuovo stato: ' + r.retry.status); refreshBalance(); setView(state.view); } else toast(r.error, 'err');
  },
  'piece-bump-priority': async (el) => {
    const r = await call('bump_piece_priority', Number(el.dataset.id));
    r.ok ? (toast('Pezzo messo in cima alla coda'), setView(state.view)) : toast(r.error, 'err');
  },
  'plan-toggle-alloc-preview': () => { state.showAllocPreview = !state.showAllocPreview; reloadPlanView(); },
  'prod-toggle-dry-run': () => { state.showDryRun = !state.showDryRun; setView('produzione'); },
  'run-backup': async () => {
    toast('Backup in corso…');
    const r = await call('run_backup');
    r.ok ? toast('Backup salvato: ' + r.path) : toast('Backup non riuscito: ' + (r.reason || r.error || '—'), 'err');
  },
};

let stepBusy = false;
let retryAllBusy = false;
async function stepCell(ct, day, delta) {
  if (stepBusy || !state.currentPlan) return;
  stepBusy = true;
  const pl = state.currentPlan;
  const target = Math.max(0, pl.grid[ct][day] + delta);
  const r = await call('plan_set_cell', pl.id, ct, day, target);
  stepBusy = false;
  if (r.ok) { state.currentPlan = r.plan; reloadPlanView(); } else toast(r.error, 'err');
}

/* ============ Delegazione eventi ============ */
document.addEventListener('click', (e) => {
  if (!e.target.closest('.gsearch')) {
    const panel = document.getElementById('globalSearchPanel');
    if (panel && panel.innerHTML) panel.innerHTML = '';
  }
  const tab = e.target.closest('.tab');
  if (tab) { setView(tab.dataset.view); return; }
  const act = e.target.closest('[data-action]');
  if (act && ACTIONS[act.dataset.action]) { ACTIONS[act.dataset.action](act); }
});
document.addEventListener('change', async (e) => {
  const sw = e.target.closest('[data-change="switch-profile-top"]');
  if (sw && sw.value) {
    await call('set_active_profile', Number(sw.value));
    state.profiles = []; state.currentPlan = null; state.planWeekStart = null;
    if (state.view === 'piano' || state.view === 'costi') setView(state.view);
    return;
  }
  const libStatus = e.target.closest('[data-change="lib-filter-status"]');
  if (libStatus) { state.libFilter.status = libStatus.value; state.libFilter.page = 0; setView('libreria'); return; }
  const libCategory = e.target.closest('[data-change="lib-filter-category"]');
  if (libCategory) { state.libFilter.category = libCategory.value; state.libFilter.page = 0; setView('libreria'); return; }
  const quality = e.target.closest('[data-change="piece-quality"]');
  if (quality) {
    const rating = Number(quality.value);
    if (!rating) return;
    const r = await call('set_piece_quality', Number(quality.dataset.id), rating);
    r.ok ? toast('Voto salvato') : toast(r.error, 'err');
  }
});
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Enter') return;
  if (e.target.closest('[data-keydown="lib-search-enter"]')) { ACTIONS['lib-search'](); return; }
  if (e.target.closest('[data-keydown="backlog-search-enter"]')) { ACTIONS['backlog-search'](); return; }
  if (e.target.closest('[data-keydown="global-search-enter"]')) { runGlobalSearch(); }
});

/* ============ Avvio ============ */
async function init() {
  const m = await call('meta');
  if (m.ok) state.meta = m;
  await refreshBalance();
  await refreshTopProfileSwitch();
  await setView('oggi');
}
if (window.pywebview && window.pywebview.api) init();
else window.addEventListener('pywebviewready', init);
