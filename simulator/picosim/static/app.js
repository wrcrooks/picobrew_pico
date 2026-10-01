'use strict';

const $ = (sel) => document.querySelector(sel);
const state = { devices: [], selected: null, snap: null, history: [], lastT: null, paksKey: null };

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  for (const c of children) if (c != null) node.append(c);
  return node;
}

async function api(method, url, body) {
  const resp = await fetch(url, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (resp.status === 204) return null;
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
  return data;
}

let toastTimer;
function toast(msg, isError = true) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast' + (isError ? ' error' : '');
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 5000);
}

async function command(cmd, extra = {}) {
  if (!state.selected) return;
  document.body.classList.add('busy');
  try {
    applySnapshot(await api('POST', `/api/devices/${state.selected}/command`, { command: cmd, ...extra }));
  } catch (e) {
    toast(e.message);
    refreshDevice();
  } finally {
    document.body.classList.remove('busy');
  }
}

// ---------- device list ----------
async function refreshList() {
  try {
    state.devices = await api('GET', '/api/devices');
  } catch (e) {
    return;
  }
  const list = $('#device-list');
  list.replaceChildren(...state.devices.map((d) => {
    const li = el('li', { className: 'device-item' + (d.id === state.selected ? ' active' : '') },
      el('span', { className: 'dot ' + (d.powered ? 'on' : 'off') }),
      el('div', {},
        el('div', { className: 'name', textContent: d.name }),
        el('div', { className: 'muted small', textContent: d.program || `${d.model_label} · ${d.powered ? 'idle' : 'off'}` })));
    li.addEventListener('click', () => select(d.id));
    return li;
  }));
  $('#no-devices').hidden = state.devices.length > 0;
  if (!state.selected && state.devices.length) select(state.devices[0].id);
  if (state.selected && !state.devices.some((d) => d.id === state.selected)) select(null);
}

function select(id) {
  state.selected = id;
  state.snap = null;
  state.history = [];
  state.lastT = null;
  state.paksKey = null;
  $('#device-view').hidden = !id;
  $('#empty-view').hidden = !!id;
  refreshList();
  if (id) refreshDevice(true);
}

// ---------- device view ----------
async function refreshDevice(populateSettings = false) {
  const id = state.selected;
  if (!id) return;
  const qs = state.lastT != null ? `?history_since=${state.lastT}` : '';
  try {
    const snap = await api('GET', `/api/devices/${id}${qs}`);
    if (id !== state.selected) return;
    applySnapshot(snap, populateSettings);
  } catch (e) {
    /* transient; next poll retries */
  }
}

function applySnapshot(snap, populateSettings = false) {
  if (state.lastT != null && snap.history.length && snap.history[0].t <= state.lastT) {
    state.history = [];  // full (non-incremental) history, e.g. a command response
  }
  if (snap.sim_clock < (state.lastT || 0)) state.history = [];  // simulator restarted
  state.history.push(...snap.history);
  if (state.history.length > 4000) state.history.splice(0, state.history.length - 4000);
  if (state.history.length) state.lastT = state.history[state.history.length - 1].t;
  state.snap = snap;
  render(snap);
  if (populateSettings) {
    const f = $('#settings');
    f.name.value = snap.name;
    f.firmware.value = snap.firmware;
    f.server_url.value = snap.server_url;
  }
}

function fmtDuration(sec) {
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return (h ? `${h}:${String(m).padStart(2, '0')}` : `${m}`) + `:${String(s).padStart(2, '0')}`;
}

function badge(text, cls) { return el('span', { className: `badge ${cls}`, textContent: text }); }

function render(s) {
  $('#d-name').textContent = s.name;
  $('#d-model').textContent = s.model_label;
  $('#d-uid').textContent = s.uid;
  $('#d-fw').textContent = s.firmware;

  const badges = [badge(s.powered ? 'Powered on' : 'Powered off', s.powered ? 'ok' : 'off')];
  if (s.powered && s.registered) badges.push(badge('Registered', 'ok'));
  if (s.firmware_update_available) badges.push(badge('Firmware update available', 'warn'));
  if (s.needs_cleaning) badges.push(badge('Deep clean required', 'warn'));
  if (s.error_code) badges.push(badge(`Error ${s.error_code}`, 'bad'));
  if (s.last_comm_error) badges.push(badge('Server unreachable', 'bad'));
  if (s.program && s.program.user_paused) badges.push(badge('Paused', 'warn'));
  if (s.program && s.program.phase === 'waiting') badges.push(badge('Waiting at Pause step', 'warn'));
  $('#d-badges').replaceChildren(...badges);

  $('#btn-power').textContent = s.powered ? 'Power off' : 'Power on';
  const running = !!s.program;
  document.querySelectorAll('[data-needs-power]').forEach((b) => { b.disabled = !s.powered; });
  document.querySelectorAll('[data-needs-idle]').forEach((b) => { b.disabled = !s.powered || running; });
  document.querySelectorAll('[data-needs-run]').forEach((b) => { b.disabled = !running; });
  $('#btn-error').disabled = !s.powered;
  const speedSel = $('#speed');
  if (document.activeElement !== speedSel) speedSel.value = String(s.speed);

  $('#t-wort').textContent = s.powered ? `${s.wort.toFixed(1)}°F` : '—';
  $('#t-therm').textContent = s.powered ? `${s.therm.toFixed(1)}°F` : '—';
  $('#t-left').textContent = running ? fmtDuration(s.program.time_left) : '—';

  renderPaks(s);
  renderSteps(s);
  renderOled(s);
  renderChart();
  renderEvents(s);
  renderTraffic(s);
}

function renderPaks(s) {
  const key = JSON.stringify(s.paks);
  if (key === state.paksKey) return;
  state.paksKey = key;
  const sel = $('#pak-select');
  const opts = [el('option', { value: '', textContent: s.paks.length ? 'Choose a PicoPak…' : 'No paks from server' })];
  for (const p of s.paks) opts.push(el('option', { value: p.rfid, textContent: `${p.name} (${p.rfid})` }));
  sel.replaceChildren(...opts);
}

function renderSteps(s) {
  const steps = s.program ? s.program.steps : (s.recipe ? s.recipe.steps : []);
  $('#program-title').textContent = s.program ? s.program.label
    : s.recipe ? `Recipe: ${s.recipe.name} (ABV ${s.recipe.abv}%, IBU ${s.recipe.ibu})` : 'Recipe';
  $('#steps-empty').hidden = steps.length > 0;
  $('#steps-body').replaceChildren(...steps.map((st, i) => {
    let cls = '';
    let phase = '';
    if (s.program) {
      if (i < s.program.step_index) cls = 'done';
      else if (i === s.program.step_index) { cls = 'current'; phase = ` · ${s.program.phase}`; }
    }
    return el('tr', { className: cls },
      el('td', { textContent: i + 1 }),
      el('td', { textContent: st.name + phase }),
      el('td', { textContent: st.location }),
      el('td', { textContent: st.temperature || '—' }),
      el('td', { textContent: st.step_time }),
      el('td', { textContent: st.drain_time }));
  }));
}

// ---------- OLED (128x64, 1 bit) ----------
function renderOled(s) {
  const c = $('#oled');
  const ctx = c.getContext('2d');
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, 128, 64);
  if (!s.powered) return;

  if (!s.program && s.recipe && /^[0-9a-fA-F]{2048}$/.test(s.recipe.image)) {
    drawOledImage(ctx, s.recipe.image);
    return;
  }
  const lines = [];
  if (s.program) {
    const st = s.program.steps[s.program.step_index];
    lines.push(s.program.label, st ? st.name : '', `W ${Math.round(s.wort)}F  T ${Math.round(s.therm)}F`,
      s.program.phase === 'waiting' ? 'Press Resume' : `Left ${fmtDuration(s.program.time_left)}`);
  } else if (s.recipe) {
    lines.push(s.recipe.name, `${s.recipe.steps.length} steps`, 'Ready to brew');
  } else {
    lines.push(s.needs_cleaning ? 'Deep clean needed' : 'Ready', 'Insert PicoPak', `W ${Math.round(s.wort)}F`);
  }
  ctx.fillStyle = '#fff';
  ctx.font = '10px monospace';
  ctx.textBaseline = 'top';
  lines.forEach((line, i) => ctx.fillText(String(line).slice(0, 21), 2, 2 + i * 15));
}

function drawOledImage(ctx, hex) {
  const img = ctx.createImageData(128, 64);
  for (let b = 0; b < 1024; b++) {
    const byte = parseInt(hex.substr(b * 2, 2), 16);
    for (let bit = 0; bit < 8; bit++) {
      const v = (byte >> (7 - bit)) & 1 ? 255 : 0;
      const p = (b * 8 + bit) * 4;
      img.data[p] = img.data[p + 1] = img.data[p + 2] = v;
      img.data[p + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
}

// ---------- chart ----------
function renderChart() {
  const c = $('#chart');
  const w = c.clientWidth;
  const h = c.height;
  if (c.width !== w) c.width = w;
  const ctx = c.getContext('2d');
  ctx.clearRect(0, 0, w, h);
  const pts = state.history;
  if (pts.length < 2) return;

  const t0 = pts[0].t, t1 = pts[pts.length - 1].t;
  const temps = pts.flatMap((p) => [p.wort, p.therm]);
  const lo = Math.min(60, ...temps), hi = Math.max(220, ...temps);
  const padL = 34, padB = 18;
  const x = (t) => padL + ((t - t0) / Math.max(1, t1 - t0)) * (w - padL - 6);
  const y = (v) => (h - padB) - ((v - lo) / (hi - lo)) * (h - padB - 6);

  ctx.font = '11px system-ui, sans-serif';
  ctx.fillStyle = getComputedStyle(document.body).getPropertyValue('--muted');
  ctx.strokeStyle = getComputedStyle(document.body).getPropertyValue('--grid');
  ctx.lineWidth = 1;
  for (let v = Math.ceil(lo / 40) * 40; v <= hi; v += 40) {
    ctx.beginPath(); ctx.moveTo(padL, y(v)); ctx.lineTo(w, y(v)); ctx.stroke();
    ctx.fillText(`${v}`, 4, y(v) - 6);
  }
  ctx.fillText(`${fmtDuration(t1 - t0)} sim`, w - 80, h - 14);

  // step boundaries
  let prev = null;
  ctx.strokeStyle = getComputedStyle(document.body).getPropertyValue('--target');
  ctx.setLineDash([3, 3]);
  for (const p of pts) {
    if (p.step && p.step !== prev) { ctx.beginPath(); ctx.moveTo(x(p.t), 6); ctx.lineTo(x(p.t), h - padB); ctx.stroke(); }
    prev = p.step;
  }
  ctx.setLineDash([]);

  for (const [key, color] of [['therm', '--therm'], ['wort', '--wort']]) {
    ctx.strokeStyle = getComputedStyle(document.body).getPropertyValue(color);
    ctx.lineWidth = 2;
    ctx.beginPath();
    pts.forEach((p, i) => (i ? ctx.lineTo(x(p.t), y(p[key])) : ctx.moveTo(x(p.t), y(p[key]))));
    ctx.stroke();
  }
}

// ---------- logs ----------
function clock(ts) { return new Date(ts * 1000).toLocaleTimeString(); }

function renderEvents(s) {
  $('#events').replaceChildren(...s.events.map((e) =>
    el('li', { className: e.level }, el('span', { className: 'muted small', textContent: clock(e.time) + ' ' }), e.text)));
}

function renderTraffic(s) {
  const rows = s.traffic.slice().reverse().map((t) => el('tr', { className: t.status && t.status < 400 ? '' : 'bad' },
    el('td', { className: 'small', textContent: clock(t.time) }),
    el('td', { className: 'mono', textContent: `${t.method} ${t.path}` }),
    el('td', { textContent: t.status ?? 'ERR' }),
    el('td', { textContent: t.ms }),
    el('td', { className: 'mono', textContent: (t.error || t.response || '').replace(/\r?\n/g, '⏎').slice(0, 160) })));
  $('#traffic-body').replaceChildren(...rows);
}

// ---------- wiring ----------
document.querySelectorAll('[data-cmd]').forEach((b) => b.addEventListener('click', () => command(b.dataset.cmd)));

$('#btn-power').addEventListener('click', () => command(state.snap && state.snap.powered ? 'power_off' : 'power_on'));

$('#btn-insert').addEventListener('click', () => {
  const rfid = $('#rfid-input').value.trim() || $('#pak-select').value;
  if (!rfid) return toast('Choose a PicoPak or type an RFID');
  command('insert_pak', { rfid });
});

$('#btn-sous-vide').addEventListener('click', () =>
  command('start_sous_vide', { temperature: $('#sv-temp').value, minutes: $('#sv-min').value }));

$('#btn-error').addEventListener('click', () => command('report_error', { code: $('#err-code').value }));

$('#speed').addEventListener('change', async (e) => {
  try {
    applySnapshot(await api('PATCH', `/api/devices/${state.selected}`, { speed: Number(e.target.value) }));
  } catch (err) { toast(err.message); }
});

$('#btn-delete').addEventListener('click', async () => {
  if (!confirm(`Delete simulated device "${state.snap.name}"?`)) return;
  try {
    await api('DELETE', `/api/devices/${state.selected}`);
    select(null);
  } catch (e) { toast(e.message); }
});

$('#settings').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = e.target;
  try {
    applySnapshot(await api('PATCH', `/api/devices/${state.selected}`,
      { name: f.name.value, firmware: f.firmware.value, server_url: f.server_url.value }), true);
    refreshList();
    toast('Saved', false);
  } catch (err) { toast(err.message); }
});

$('#add-device').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = Object.fromEntries(new FormData(f).entries());
  try {
    const d = await api('POST', '/api/devices', body);
    f.reset();
    await refreshList();
    select(d.id);
  } catch (err) { toast(err.message); }
});

window.addEventListener('resize', renderChart);
refreshList();
setInterval(() => refreshDevice(), 1000);
setInterval(refreshList, 3000);
