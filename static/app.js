/* DuTemps UI. Vanilla JS, no build step. All DOM is built with h(), which
   sets text via textContent, so stored descriptions can never inject markup. */
"use strict";

// ---------------------------------------------------------------- utilities
const $ = (sel, root = document) => root.querySelector(sel);

function h(tag, attrs, ...kids) {
  const el = tag === "svg" || SVG_TAGS.has(tag)
    ? document.createElementNS("http://www.w3.org/2000/svg", tag)
    : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style") Object.assign(el.style, v);
    else if (k === "class") el.setAttribute("class", v);
    else if (k === "value" && "value" in el && !(el instanceof SVGElement)) el.value = v;
    else if (k === "checked" || k === "selected" || k === "disabled") el[k] = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const SVG_TAGS = new Set(["g", "rect", "path", "circle", "line", "text", "title", "polyline"]);
// Small stroke icons on a 24px grid, drawn for DuTemps.
const ICONS = {
  x: [["path", { d: "M18 6 6 18M6 6l12 12" }]],
  more: [["circle", { cx: 12, cy: 5, r: 1 }], ["circle", { cx: 12, cy: 12, r: 1 }], ["circle", { cx: 12, cy: 19, r: 1 }]],
  play: [["path", { d: "M7 4.5v15l12-7.5z", fill: "currentColor" }]],
  left: [["path", { d: "m15 18-6-6 6-6" }]],
  right: [["path", { d: "m9 18 6-6-6-6" }]],
  up: [["path", { d: "M12 19V5M6 11l6-6 6 6" }]],
  down: [["path", { d: "M12 5v14M18 13l-6 6-6-6" }]],
  clock: [["circle", { cx: 12, cy: 12, r: 9 }], ["path", { d: "M12 7v5l3 2" }]],
  list: [["path", { d: "M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01" }]],
  table: [["rect", { x: 3, y: 4, width: 18, height: 16, rx: 2 }], ["path", { d: "M3 10h18M9 10v10" }]],
  calendar: [["rect", { x: 3, y: 5, width: 18, height: 16, rx: 2 }], ["path", { d: "M8 3v4M16 3v4M3 11h18" }]],
  layout: [["rect", { x: 3, y: 3, width: 7, height: 9, rx: 1.5 }], ["rect", { x: 14, y: 3, width: 7, height: 5, rx: 1.5 }],
    ["rect", { x: 14, y: 12, width: 7, height: 9, rx: 1.5 }], ["rect", { x: 3, y: 16, width: 7, height: 5, rx: 1.5 }]],
  chart: [["path", { d: "M3 21h18M7 17v-5M12 17V6M17 17v-8" }]],
  file: [["path", { d: "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" }], ["path", { d: "M14 3v5h5M9 13h6M9 17h4" }]],
  folder: [["path", { d: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" }]],
  users: [["circle", { cx: 9, cy: 8, r: 4 }], ["path", { d: "M2 21a7 7 0 0 1 14 0M16 4.2a4 4 0 0 1 0 7.6M22 21a7 7 0 0 0-4-6.3" }]],
  tag: [["path", { d: "M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8z" }], ["circle", { cx: 7.5, cy: 7.5, r: 1.5 }]],
  sliders: [["path", { d: "M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1" }],
    ["circle", { cx: 15, cy: 6, r: 2 }], ["circle", { cx: 9, cy: 12, r: 2 }], ["circle", { cx: 17, cy: 18, r: 2 }]],
  mark: [["circle", { cx: 12, cy: 12, r: 8 }], ["path", { d: "M12 12V4a8 8 0 0 1 8 8z", fill: "currentColor" }]],
};
const ico = (name) => h("svg", { class: "i", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "2",
  "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" }, (ICONS[name] || []).map(([tag, a]) => h(tag, a)));
const tagIcon = () => ico("tag");
// append/replaceChildren flatten arrays and skip null/false like h() does, so `cond ? el : null` works everywhere
for (const name of ["append", "replaceChildren"]) {
  const orig = Element.prototype[name];
  Element.prototype[name] = function (...kids) {
    return orig.apply(this, kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false));
  };
}

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json", "X-DuTemps": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}
const GET = (p, q) => api("GET", q ? `${p}?${qs(q)}` : p);
function qs(o) {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(o)) {
    if (v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length)) continue;
    u.set(k, Array.isArray(v) ? v.join(",") : v);
  }
  return u.toString();
}

function toast(msg, opts = {}) {
  const t = h("div", { class: "toast" + (opts.err ? " err" : "") }, h("span", {}, msg),
    opts.action ? h("button", { onclick: () => { opts.action.fn(); t.remove(); } }, opts.action.label) : null);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), opts.ms || (opts.action ? 8000 : 3500));
}
const fail = (e) => toast(e.message || String(e), { err: true });

// ---------------------------------------------------------------- time
const S = { meta: null, running: null };
const tz = () => S.meta.settings.timezone;
const pad = (n) => String(n).padStart(2, "0");

const _fmtCache = {};
function parts(utc) {
  const d = typeof utc === "string" ? new Date(utc) : utc;
  const key = tz();
  const f = _fmtCache[key] || (_fmtCache[key] = new Intl.DateTimeFormat("en-CA", {
    timeZone: key, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }));
  const p = Object.fromEntries(f.formatToParts(d).map((x) => [x.type, x.value]));
  return { date: `${p.year}-${p.month}-${p.day}`, h: +p.hour, m: +p.minute, s: +p.second };
}
const localDate = (utc) => parts(utc).date;
const todayLocal = () => parts(new Date()).date;

function localToUtc(date, h, m, s = 0) {
  // find the UTC instant whose local wall clock in tz() is date h:m:s
  const [Y, M, D] = date.split("-").map(Number);
  const want = Date.UTC(Y, M - 1, D, h, m, s);
  let guess = want;
  for (let i = 0; i < 3; i++) {
    const p = parts(new Date(guess));
    const [y2, m2, d2] = p.date.split("-").map(Number);
    const got = Date.UTC(y2, m2 - 1, d2, p.h, p.m, p.s);
    guess += want - got;
  }
  return isoUtc(new Date(guess));
}
const isoUtc = (d) => d.toISOString().replace(/\.\d{3}Z$/, "Z");
const nowUtc = () => isoUtc(new Date());

function addDays(date, n) {
  const [Y, M, D] = date.split("-").map(Number);
  const d = new Date(Date.UTC(Y, M - 1, D + n));
  return d.toISOString().slice(0, 10);
}
const isoDow = (date) => { const d = new Date(date + "T00:00:00Z").getUTCDay(); return d === 0 ? 7 : d; };
function weekStartOf(date) {
  const ws = +S.meta.settings.week_start;
  return addDays(date, -((isoDow(date) - ws + 7) % 7));
}
function monthEnd(date) {
  const [Y, M] = date.split("-").map(Number);
  return new Date(Date.UTC(Y, M, 0)).toISOString().slice(0, 10);
}
const DOW = ["", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function dayLabel(date, long = true) {
  const t = todayLocal();
  if (long && date === t) return "Today";
  if (long && date === addDays(t, -1)) return "Yesterday";
  const [Y, M, D] = date.split("-").map(Number);
  return `${DOW[isoDow(date)]}, ${MON[M - 1]} ${D}${Y !== +t.slice(0, 4) ? ", " + Y : ""}`;
}
function clock(h, m) {
  if (S.meta.settings.time_format === "24") return `${pad(h)}:${pad(m)}`;
  const ap = h < 12 ? "AM" : "PM";
  return `${h % 12 || 12}:${pad(m)} ${ap}`;
}
const timeOf = (utc) => { const p = parts(utc); return clock(p.h, p.m); };
function hms(sec) {
  sec = Math.max(0, Math.round(sec));
  return `${Math.floor(sec / 3600)}:${pad(Math.floor(sec / 60) % 60)}:${pad(sec % 60)}`;
}
const dec = (sec) => (sec / 3600).toFixed(2);
const dur = (sec) => (S.meta.settings.duration_format === "decimal" ? dec(sec) : hms(sec));
const money = (x) => "$" + (x || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const secsBetween = (a, b) => (new Date(b) - new Date(a)) / 1000;

/** "9", "930", "9:30", "9:30pm", "21:30", "9p" -> {h, m} or null */
function parseClock(s) {
  s = (s || "").trim().toLowerCase().replace(/\s+/g, "");
  const m = s.match(/^(\d{1,2})(?::?(\d{2}))?(?::(\d{2}))?(a|am|p|pm)?$/);
  if (!m) return null;
  let hh = +m[1], mm = +(m[2] || 0);
  if (m[4]) { if (hh > 12 || hh === 0) return null; hh = (hh % 12) + (m[4][0] === "p" ? 12 : 0); }
  if (hh > 23 || mm > 59) return null;
  return { h: hh, m: mm };
}
/** "1:30", "1:30:00", "1.5", "90m", "1h30m", "2h" -> seconds or null */
function parseDur(s) {
  s = (s || "").trim().toLowerCase();
  if (!s) return null;
  let m;
  if ((m = s.match(/^(\d+):(\d{1,2})(?::(\d{1,2}))?$/))) return +m[1] * 3600 + +m[2] * 60 + +(m[3] || 0);
  if ((m = s.match(/^(\d*\.?\d+)$/))) return Math.round(parseFloat(m[1]) * 3600);
  if ((m = s.match(/^(?:(\d+(?:\.\d+)?)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?$/)) && (m[1] || m[2] || m[3]))
    return Math.round((+(m[1] || 0)) * 3600) + +(m[2] || 0) * 60 + +(m[3] || 0);
  return null;
}

// ---------------------------------------------------------------- lookups
const proj = (id) => S.meta.projects.find((p) => p.id === id);
const task = (id) => S.meta.tasks.find((t) => t.id === id);
const client = (id) => S.meta.clients.find((c) => c.id === id);
async function reloadMeta() {
  const m = await GET("/api/meta");
  S.meta = m;
  S.running = m.running;
  renderSideRunning();
}

// ---------------------------------------------------------------- popovers
let openPop = null;
function closePop() {
  if (!openPop) return;
  const el = openPop;
  openPop = null;
  el.remove();
  el._onClose && el._onClose();
}
document.addEventListener("mousedown", (e) => { if (openPop && !openPop.contains(e.target) && !e.target.closest("[data-popper]")) closePop(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && openPop) { e.preventDefault(); const a = openPop._anchor; closePop(); a && a.focus(); } });

function popAt(anchor, el) {
  closePop();
  el.classList.add("pop");
  el._anchor = anchor;
  // a modal dialog is in the top layer and makes the rest of the page inert
  (anchor.closest("dialog[open]") || document.body).append(el);
  const r = anchor.getBoundingClientRect();
  const w = el.offsetWidth, hgt = el.offsetHeight;
  el.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + "px";
  el.style.top = (r.bottom + hgt + 8 > innerHeight ? Math.max(8, r.top - hgt - 4) : r.bottom + 4) + "px";
  openPop = el;
  return el;
}

/** Searchable list with arrow-key navigation. items: [{label, cls, pick, color}] */
function listPop(anchor, build, placeholder = "Search") {
  const input = h("input", { class: "search", placeholder, "aria-label": placeholder });
  const ul = h("ul", { role: "listbox" });
  const el = h("div", {}, input, ul);
  let items = [], active = -1;
  const paint = () => {
    ul.replaceChildren();
    items = build(input.value.trim().toLowerCase());
    const pickable = items.filter((i) => i.pick);
    active = Math.min(Math.max(active, 0), pickable.length - 1);
    let n = -1;
    for (const it of items) {
      const li = h("li", { class: it.cls || "", role: it.pick ? "option" : "presentation" },
        it.color ? h("span", { class: "dot", style: { background: it.color } }) : null, it.label);
      if (it.pick) {
        n++;
        const idx = n;
        if (idx === active) li.classList.add("active");
        li.addEventListener("mousedown", (e) => { e.preventDefault(); closePop(); it.pick(); });
        li.addEventListener("mousemove", () => { if (active !== idx) { active = idx; mark(); } });
      }
      ul.append(li);
    }
  };
  const mark = () => {
    const lis = [...ul.querySelectorAll('li[role="option"]')];
    lis.forEach((li, i) => li.classList.toggle("active", i === active));
    lis[active] && lis[active].scrollIntoView({ block: "nearest" });
  };
  input.addEventListener("input", () => {
    // highlight the first option whose own label matches, not its parent row
    const q = input.value.trim().toLowerCase();
    const pickable = build(q).filter((i) => i.pick);
    active = Math.max(0, q ? pickable.findIndex((i) => i.label.toLowerCase().includes(q)) : 0);
    paint();
  });
  input.addEventListener("keydown", (e) => {
    const pickable = items.filter((i) => i.pick);
    if (e.key === "ArrowDown") { active = Math.min(active + 1, pickable.length - 1); mark(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { active = Math.max(active - 1, 0); mark(); e.preventDefault(); }
    else if (e.key === "Enter") { e.preventDefault(); const it = pickable[active]; if (it) { closePop(); it.pick(); anchor.focus(); } }
  });
  paint();
  popAt(anchor, el);
  input.focus();
}

/** Project/task picker. cur = {project_id, task_id}; onPick({project_id, task_id}) */
function pickProject(anchor, cur, onPick) {
  listPop(anchor, (q) => {
    const out = !q || "no project".includes(q)
      ? [{ label: "No project", cls: "act", pick: () => onPick({ project_id: null, task_id: null }) }] : [];
    const byClient = new Map();
    for (const p of S.meta.projects) {
      if (p.archived && p.id !== cur.project_id) continue;
      const tasks = S.meta.tasks.filter((t) => t.project_id === p.id && (!t.done || t.id === cur.task_id));
      const pm = p.name.toLowerCase().includes(q) || (p.client || "").toLowerCase().includes(q);
      const tm = tasks.filter((t) => t.name.toLowerCase().includes(q));
      if (q && !pm && !tm.length) continue;
      const k = p.client || "No client";
      if (!byClient.has(k)) byClient.set(k, []);
      byClient.get(k).push({ p, tasks: q && !pm ? tm : tasks });
    }
    for (const [cname, ps] of [...byClient].sort((a, b) => a[0].localeCompare(b[0]))) {
      out.push({ label: cname, cls: "client" });
      for (const { p, tasks } of ps) {
        out.push({ label: p.name, color: p.color, pick: () => onPick({ project_id: p.id, task_id: null }) });
        for (const t of tasks) out.push({ label: t.name, cls: "task", pick: () => onPick({ project_id: p.id, task_id: t.id }) });
      }
    }
    return out;
  }, "Find project or task…");
}

function projectChip(pid, tid, onPick) {
  const p = proj(pid), t = task(tid);
  const b = h("button", { class: "proj" + (p ? "" : " none"), type: "button", "data-popper": "1",
    title: p ? `${p.name}${t ? ": " + t.name : ""}${p.client ? " — " + p.client : ""}` : "Select project" },
    p ? [h("span", { class: "dot", style: { background: p.color } }),
         h("span", { style: { color: p.color } }, p.name), t ? h("span", { class: "t" }, ": " + t.name) : null]
      : "+ Project");
  b.addEventListener("click", () => pickProject(b, { project_id: pid, task_id: tid }, onPick));
  return b;
}

/** Tag checklist; onChange(newIds) fires when the popover closes after edits. */
function pickTags(anchor, ids, onChange) {
  const sel = new Set(ids);
  const input = h("input", { class: "search", placeholder: "Add/find tags", "aria-label": "Find or create tag" });
  const box = h("div", { style: { overflow: "auto" } });
  const el = h("div", {}, input, box);
  const paint = () => {
    const q = input.value.trim().toLowerCase();
    box.replaceChildren();
    const shown = S.meta.tags.filter((t) => (!t.archived || sel.has(t.id)) && t.name.toLowerCase().includes(q));
    for (const t of shown) {
      const cb = h("input", { type: "checkbox", checked: sel.has(t.id),
        onchange: () => { cb.checked ? sel.add(t.id) : sel.delete(t.id); } });
      box.append(h("label", {}, cb, t.name));
    }
    if (q && !S.meta.tags.some((t) => t.name.toLowerCase() === q))
      box.append(h("label", { class: "act", onclick: create }, `+ Create tag "${input.value.trim()}"`));
    if (!shown.length && !q) box.append(h("div", { class: "muted pad" }, "No tags yet. Type to create one."));
  };
  const create = async () => {
    try {
      const t = await api("POST", "/api/tags", { name: input.value.trim() });
      S.meta.tags.push(t);
      sel.add(t.id);
      input.value = "";
      paint();
    } catch (e) { fail(e); }
  };
  input.addEventListener("input", paint);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); if (input.value.trim()) create(); } });
  paint();
  popAt(anchor, el);
  input.focus();
  const before = [...sel].sort().join();
  el._onClose = () => { if ([...sel].sort().join() !== before) onChange([...sel]); };
}

function tagButton(tags, onChange) {
  const ids = tags.map((t) => t.id);
  const b = h("button", { class: "tagbtn" + (tags.length ? " has" : ""), type: "button", "data-popper": "1",
    title: tags.length ? tags.map((t) => t.name).join(", ") : "Tags", "aria-label": "Tags" },
    tags.length ? tags.map((t) => t.name).join(", ") : tagIcon());
  b.addEventListener("click", () => pickTags(b, ids, onChange));
  return b;
}

function dollar(on, onToggle) {
  return h("button", { class: "dollar" + (on ? " on" : ""), type: "button", "aria-pressed": on ? "true" : "false",
    title: on ? "Billable" : "Non-billable", onclick: () => onToggle(!on) }, "$");
}

function menu(anchor, items) {
  listPop(anchor, () => items.map((i) => ({ label: i.label, pick: i.fn })), "Action");
  openPop.classList.add("menu");
}

// ---------------------------------------------------------------- side timer
function renderSideRunning() {
  const el = $("#side-running");
  const r = S.running;
  el.replaceChildren(r ? h("div", {}, h("div", { class: "run" }, h("span", { class: "live-dot" }), "Running",
    h("b", { id: "side-clock" }, hms(secsBetween(r.start_utc, nowUtc())))),
    h("div", { class: "muted" }, r.description || r.project || "(no description)")) : "");
}
setInterval(() => {
  if (!S.running) { document.title = "DuTemps"; return; }
  const t = hms(secsBetween(S.running.start_utc, nowUtc()));
  document.title = `${t} · ${S.running.description || "DuTemps"}`;
  const a = $("#side-clock"); if (a) a.textContent = t;
  const b = $("#bar-clock"); if (b) b.textContent = t;
}, 1000);

// ---------------------------------------------------------------- entry dialog
/** Create (e = defaults without id) or edit (e.id) an entry. Resolves true on save. */
function entryDialog(e, onSaved) {
  const isNew = !e.id;
  const st = e.start_utc ? parts(e.start_utc) : null;
  const en = e.end_utc ? parts(e.end_utc) : null;
  const running = !isNew && !e.end_utc;
  let pid = e.project_id || null, tid = e.task_id || null, tagIds = (e.tags || []).map((t) => t.id);
  let billable = e.billable === undefined ? 1 : e.billable;
  const f = {
    desc: h("input", { value: e.description || "", placeholder: "What did you work on?" }),
    date: h("input", { type: "date", value: st ? st.date : todayLocal() }),
    start: h("input", { class: "time", value: st ? clock(st.h, st.m) : "" }),
    end: h("input", { class: "time", value: en ? clock(en.h, en.m) : "", disabled: running }),
    dur: h("input", { class: "dur", value: e.start_utc && e.end_utc ? hms(secsBetween(e.start_utc, e.end_utc)) : "" }),
    rate: h("input", { type: "number", min: "0", step: "0.01", value: e.hourly_rate ?? "", placeholder: e.rate ? `${e.rate} (inherited)` : "inherited" }),
  };
  const chipBox = h("span"), tagBox = h("span"), billBox = h("span");
  const err = h("div", { class: "err", role: "alert" });
  const paintChips = () => {
    chipBox.replaceChildren(projectChip(pid, tid, (v) => { pid = v.project_id; tid = v.task_id;
      if (v.project_id && isNew) billable = proj(v.project_id).billable; paintChips(); }));
    tagBox.replaceChildren(tagButton(S.meta.tags.filter((t) => tagIds.includes(t.id)), (ids) => { tagIds = ids; paintChips(); }));
    billBox.replaceChildren(dollar(billable, (v) => { billable = v ? 1 : 0; paintChips(); }));
  };
  paintChips();
  const resolve = () => {
    const sc = parseClock(f.start.value);
    if (!sc) throw new Error("Start time is not valid (e.g. 9:30 AM or 21:30)");
    const start = localToUtc(f.date.value, sc.h, sc.m, st && f.start.value === clock(st.h, st.m) ? st.s : 0);
    if (running) return { start, end: null };
    let end;
    const ec = parseClock(f.end.value);
    const d = parseDur(f.dur.value);
    if (ec && (!en || f.end.value !== clock(en.h, en.m) || d === null || f.dur.value === hms(secsBetween(e.start_utc, e.end_utc)))) {
      end = localToUtc(f.date.value, ec.h, ec.m, en && f.end.value === clock(en.h, en.m) ? en.s : 0);
      if (end < start) end = localToUtc(addDays(f.date.value, 1), ec.h, ec.m);
    } else if (d !== null) {
      end = isoUtc(new Date(new Date(start).getTime() + d * 1000));
    } else throw new Error("Give an end time or a duration");
    return { start, end };
  };
  // keep end/duration consistent while typing
  f.dur.addEventListener("change", () => {
    const sc = parseClock(f.start.value), d = parseDur(f.dur.value);
    if (sc && d !== null) {
      const end = new Date(new Date(localToUtc(f.date.value, sc.h, sc.m)).getTime() + d * 1000);
      const p = parts(end); f.end.value = clock(p.h, p.m); f.dur.value = hms(d);
    }
  });
  const syncDur = () => { try { const r = resolve(); if (r.end) f.dur.value = hms(secsBetween(r.start, r.end)); } catch { /* typing */ } };
  f.end.addEventListener("change", syncDur);
  f.start.addEventListener("change", syncDur);

  const dlg = h("dialog", { "aria-label": isNew ? "Add entry" : "Edit entry" });
  const save = async (ev) => {
    ev.preventDefault();
    try {
      const { start, end } = resolve();
      const body = { description: f.desc.value, project_id: pid, task_id: tid, tag_ids: tagIds,
        billable, hourly_rate: f.rate.value === "" ? null : +f.rate.value, start_utc: start };
      if (!running) body.end_utc = end;
      const out = isNew ? await api("POST", "/api/entries", body) : await api("PATCH", `/api/entries/${e.id}`, body);
      dlg.close();
      toast(isNew ? "Entry added" : "Entry saved");
      onSaved && onSaved(out);
    } catch (x) { err.textContent = x.message; }
  };
  const del = async () => {
    dlg.close();
    await deleteEntries([e.id]);
    onSaved && onSaved(null);
  };
  dlg.append(h("form", { onsubmit: save },
    h("h2", {}, isNew ? "Add time entry" : running ? "Edit running entry" : "Edit time entry"),
    h("label", { class: "f" }, "Description", f.desc),
    h("div", { class: "row" }, chipBox, tagBox, billBox),
    h("div", { class: "grid" },
      h("label", { class: "f" }, "Date", f.date),
      h("label", { class: "f" }, "Start", f.start),
      h("label", { class: "f" }, "End", f.end),
      h("label", { class: "f" }, "Duration", f.dur),
      h("label", { class: "f wide" }, "Hourly rate override (USD)", f.rate),
      !isNew ? h("div", { class: "f wide muted" }, `Source: ${e.source}${e.source_ref ? " · " + e.source_ref.slice(0, 40) : ""}`) : null),
    err,
    h("div", { class: "actions" },
      !isNew ? h("button", { type: "button", class: "danger", onclick: del }, "Delete") : null,
      h("span", { class: "grow" }),
      h("button", { type: "button", onclick: () => dlg.close() }, "Cancel"),
      h("button", { type: "submit", class: "primary" }, isNew ? "Add" : "Save"))));
  dlg.addEventListener("close", () => dlg.remove());
  document.body.append(dlg);
  dlg.showModal();
  (isNew && !e.description ? f.desc : f.desc).focus();
}

async function deleteEntries(ids) {
  try {
    const { audit_ids: audits } = await api("POST", "/api/entries/delete", { ids });
    toast(ids.length === 1 ? "Entry deleted" : `${ids.length} entries deleted`, { action: { label: "UNDO", fn: async () => {
      try { for (const a of audits) await api("POST", `/api/restore/${a}`); toast("Restored"); await reloadMeta(); route(); } catch (e) { fail(e); }
    } } });
    if (S.running && ids.includes(S.running.id)) await reloadMeta();
  } catch (e) { fail(e); }
}

// ---------------------------------------------------------------- timer bar
function timerBar(onChange) {
  const r = S.running;
  let manual = false;
  const bar = h("div", { class: "panel timerbar" });
  const paint = () => {
    bar.replaceChildren();
    const cur = S.running;
    const st = { description: cur ? cur.description : bar._desc || "", project_id: cur ? cur.project_id : bar._pid || null,
      task_id: cur ? cur.task_id : bar._tid || null, tags: cur ? cur.tags : (bar._tags || []),
      billable: cur ? cur.billable : (bar._bill ?? 1) };
    const desc = h("input", { class: "desc", value: st.description, placeholder: "What are you working on?",
      "aria-label": "Description", autocomplete: "off" });
    const patchRunning = async (body) => {
      try { S.running = await api("PATCH", `/api/entries/${cur.id}`, body); renderSideRunning(); paint(); } catch (e) { fail(e); }
    };
    desc.addEventListener("change", () => { if (cur) patchRunning({ description: desc.value }); else bar._desc = desc.value; });
    desc.addEventListener("keydown", (e) => { if (e.key === "Enter" && !cur && !manual) { bar._desc = desc.value; start(); } });
    desc.addEventListener("input", () => { bar._desc = desc.value; if (!cur) suggest(desc); });
    const chip = projectChip(st.project_id, st.task_id, (v) => {
      if (cur) patchRunning(v);
      else { bar._pid = v.project_id; bar._tid = v.task_id; if (v.project_id) bar._bill = proj(v.project_id).billable; paint(); }
    });
    const tags = tagButton(st.tags, (ids) => {
      if (cur) patchRunning({ tag_ids: ids });
      else { bar._tags = S.meta.tags.filter((t) => ids.includes(t.id)); paint(); }
    });
    const bill = dollar(st.billable, (v) => { if (cur) patchRunning({ billable: v }); else { bar._bill = v ? 1 : 0; paint(); } });
    bar.append(desc, chip, tags, bill, h("div", { class: "sep" }));
    if (manual && !cur) {
      const d = h("input", { type: "date", value: bar._date || todayLocal(), "aria-label": "Date" });
      const s = h("input", { class: "time", placeholder: "Start", "aria-label": "Start time" });
      const e = h("input", { class: "time", placeholder: "End", "aria-label": "End time" });
      const add = async () => {
        const sc = parseClock(s.value), ec = parseClock(e.value);
        if (!sc || !ec) return toast("Enter start and end times (e.g. 9:00 AM, 10:30 AM)", { err: true });
        const su = localToUtc(d.value, sc.h, sc.m);
        let eu = localToUtc(d.value, ec.h, ec.m);
        if (eu < su) eu = localToUtc(addDays(d.value, 1), ec.h, ec.m);
        try {
          await api("POST", "/api/entries", { description: desc.value, project_id: bar._pid || null, task_id: bar._tid || null,
            tag_ids: (bar._tags || []).map((t) => t.id), billable: bar._bill ?? 1, start_utc: su, end_utc: eu });
          bar._desc = ""; bar._date = d.value; toast("Entry added"); paint(); onChange();
        } catch (x) { fail(x); }
      };
      e.addEventListener("keydown", (ev) => { if (ev.key === "Enter") add(); });
      bar.append(s, h("span", { class: "muted" }, "–"), e, d, h("button", { class: "primary go", onclick: add }, "Add"));
    } else {
      const startIn = cur ? h("input", { class: "time bare", value: timeOf(cur.start_utc), title: "Start time", "aria-label": "Start time" }) : null;
      if (startIn) startIn.addEventListener("change", () => {
        const c = parseClock(startIn.value);
        if (!c) return fail(new Error("Not a valid time"));
        let su = localToUtc(localDate(cur.start_utc), c.h, c.m);
        if (su > nowUtc()) su = localToUtc(addDays(localDate(cur.start_utc), -1), c.h, c.m);
        patchRunning({ start_utc: su });
      });
      bar.append(startIn, h("span", { class: "clock", id: "bar-clock" }, cur ? hms(secsBetween(cur.start_utc, nowUtc())) : "0:00:00"),
        h("button", { class: "primary go" + (cur ? " stop" : ""), onclick: cur ? stop : start }, cur ? "Stop" : "Start"),
        cur ? h("button", { class: "icon", title: "Discard running timer", "aria-label": "Discard running timer", onclick: discard }, ico("x")) : null);
    }
    if (!cur) bar.append(h("button", { class: "icon", title: manual ? "Timer mode" : "Manual mode", "aria-label": manual ? "Switch to timer mode" : "Switch to manual mode",
      onclick: () => { manual = !manual; paint(); } }, manual ? ico("clock") : ico("list")));
  };
  const start = async () => {
    try {
      S.running = await api("POST", "/api/timer/start", { description: bar._desc || "", project_id: bar._pid || null,
        task_id: bar._tid || null, tag_ids: (bar._tags || []).map((t) => t.id), billable: bar._bill ?? 1 });
      bar._desc = ""; bar._pid = bar._tid = null; bar._tags = []; bar._bill = 1;
      renderSideRunning(); paint(); onChange();
    } catch (e) { fail(e); }
  };
  const stop = async () => {
    try { await api("POST", "/api/timer/stop"); S.running = null; renderSideRunning(); paint(); onChange(); } catch (e) { fail(e); }
  };
  const discard = async () => {
    try { await api("POST", "/api/timer/discard"); S.running = null; renderSideRunning(); paint(); toast("Timer discarded"); onChange(); } catch (e) { fail(e); }
  };
  const suggest = async (input) => {
    const q = input.value.trim();
    if (q.length < 2) { closePop(); return; }
    const rows = await GET("/api/suggest", { q }).catch(() => []);
    if (!rows.length || document.activeElement !== input) { closePop(); return; }
    const ul = h("ul", {});
    for (const s of rows) {
      const p = proj(s.project_id), t = task(s.task_id);
      ul.append(h("li", { onmousedown: (e) => { e.preventDefault(); closePop();
        bar._desc = s.description; bar._pid = s.project_id; bar._tid = s.task_id; if (p) bar._bill = p.billable; paint(); } },
        s.description, p ? h("span", { class: "muted" }, ` · ${p.name}${t ? ": " + t.name : ""}`) : null));
    }
    popAt(input, h("div", {}, ul));
  };
  bar.refresh = paint;
  paint();
  return bar;
}

async function startFrom(e) {
  try { S.running = await api("POST", `/api/entries/${e.id}/continue`); renderSideRunning(); route(); } catch (x) { fail(x); }
}

// ---------------------------------------------------------------- tracker
async function pageTracker(root) {
  let entries = [], count = 0, totals = { days: {}, weeks: {} };
  const selected = new Set();
  const list = h("div");
  const bulk = h("div");
  const bar = timerBar(() => load(true));
  root.append(h("h1", {}, "Time Tracker"), bar, bulk, list);

  async function load(reset) {
    const off = reset ? 0 : entries.length;
    const r = await GET("/api/entries", { limit: 60, offset: off, totals: 1 });
    entries = reset ? r.entries : entries.concat(r.entries);
    count = r.count;
    totals = reset ? r.totals : { days: { ...totals.days, ...r.totals.days }, weeks: { ...totals.weeks, ...r.totals.weeks } };
    paint();
  }
  function paintBulk() {
    bulk.replaceChildren(selected.size ? h("div", { class: "bulkbar" }, `${selected.size} selected`,
      h("button", { class: "danger", onclick: async () => { await deleteEntries([...selected]); selected.clear(); load(true); } }, "Delete"),
      h("button", { onclick: () => { selected.clear(); paint(); } }, "Clear")) : "");
  }
  function paint() {
    paintBulk();
    list.replaceChildren();
    const done = entries.filter((e) => e.end_utc);
    if (!done.length) { list.append(h("div", { class: "panel empty" }, "No time entries yet. Start the timer, or use the list icon beside Start to add time by hand.")); return; }
    const weeks = new Map();
    for (const e of done) {
      const d = localDate(e.start_utc), w = weekStartOf(d);
      if (!weeks.has(w)) weeks.set(w, new Map());
      const days = weeks.get(w);
      if (!days.has(d)) days.set(d, []);
      days.get(d).push(e);
    }
    const tw = weekStartOf(todayLocal());
    for (const [w, days] of weeks) {
      const tot = totals.weeks[w] || 0;
      const label = w === tw ? "This week" : w === addDays(tw, -7) ? "Last week" : `${dayLabel(w, false)} – ${dayLabel(addDays(w, 6), false)}`;
      list.append(h("div", { class: "week-head" }, h("span", {}, label), h("span", {}, "Week total: ", h("b", {}, dur(tot)))));
      for (const [d, es] of days) {
        const dtot = totals.days[d] || 0;
        const all = h("input", { type: "checkbox", "aria-label": `Select all on ${d}`, checked: es.every((e) => selected.has(e.id)),
          onchange: () => { es.forEach((e) => all.checked ? selected.add(e.id) : selected.delete(e.id)); paint(); } });
        list.append(h("div", { class: "panel day" },
          h("div", { class: "day-head" }, all, h("span", {}, dayLabel(d)), h("span", { class: "tot" }, "Total: " + dur(dtot))),
          es.map((e) => entryRow(e))));
      }
    }
    if (entries.length < count) list.append(h("button", { class: "more", onclick: () => load(false) }, `Load more (${count - entries.length} older)`));
  }
  function entryRow(e) {
    const patch = async (body) => {
      try { await api("PATCH", `/api/entries/${e.id}`, body); load(true); } catch (x) { fail(x); load(true); }
    };
    const cb = h("input", { type: "checkbox", "aria-label": "Select entry", checked: selected.has(e.id),
      onchange: () => { cb.checked ? selected.add(e.id) : selected.delete(e.id); paint(); } });
    const desc = h("input", { class: "bare desc", value: e.description, placeholder: "Add description", "aria-label": "Description" });
    desc.addEventListener("change", () => patch({ description: desc.value }));
    desc.addEventListener("keydown", (ev) => { if (ev.key === "Enter") desc.blur(); });
    const sp = parts(e.start_utc), ep = parts(e.end_utc);
    const sIn = h("input", { class: "bare time", value: clock(sp.h, sp.m), "aria-label": "Start time" });
    const eIn = h("input", { class: "bare time", value: clock(ep.h, ep.m), "aria-label": "End time" });
    const dIn = h("input", { class: "bare dur", value: dur(e.seconds), "aria-label": "Duration" });
    const dateIn = h("input", { type: "date", class: "bare", value: sp.date, "aria-label": "Date", style: { width: "34px" }, title: sp.date });
    sIn.addEventListener("change", () => {
      const c = parseClock(sIn.value); if (!c) return fail(new Error("Not a valid time"));
      let su = localToUtc(sp.date, c.h, c.m);
      if (su > e.end_utc) su = localToUtc(addDays(sp.date, -1), c.h, c.m);
      patch({ start_utc: su });
    });
    eIn.addEventListener("change", () => {
      const c = parseClock(eIn.value); if (!c) return fail(new Error("Not a valid time"));
      let eu = localToUtc(sp.date, c.h, c.m);
      if (eu < e.start_utc) eu = localToUtc(addDays(sp.date, 1), c.h, c.m);
      patch({ end_utc: eu });
    });
    dIn.addEventListener("change", () => {
      const d = parseDur(dIn.value); if (d === null) return fail(new Error("Not a valid duration"));
      patch({ end_utc: isoUtc(new Date(new Date(e.start_utc).getTime() + d * 1000)) });
    });
    dateIn.addEventListener("change", () => {
      const shift = (new Date(dateIn.value) - new Date(sp.date)) ;
      patch({ start_utc: isoUtc(new Date(new Date(e.start_utc).getTime() + shift)),
              end_utc: isoUtc(new Date(new Date(e.end_utc).getTime() + shift)) });
    });
    for (const i of [sIn, eIn, dIn]) i.addEventListener("keydown", (ev) => { if (ev.key === "Enter") i.blur(); });
    const more = h("button", { class: "icon", title: "More actions", "aria-label": "More actions", "data-popper": "1" }, ico("more"));
    more.addEventListener("click", () => menu(more, [
      { label: "Edit…", fn: () => entryDialog(e, () => load(true)) },
      { label: "Duplicate", fn: async () => { try { await api("POST", `/api/entries/${e.id}/duplicate`); toast("Duplicated"); load(true); } catch (x) { fail(x); } } },
      { label: "Delete", fn: async () => { await deleteEntries([e.id]); load(true); } },
    ]));
    return h("div", { class: "entry" + (selected.has(e.id) ? " sel" : "") }, cb, desc,
      projectChip(e.project_id, e.task_id, (v) => patch(v)),
      tagButton(e.tags, (ids) => patch({ tag_ids: ids })),
      dollar(e.billable, (v) => patch({ billable: v })),
      h("div", { class: "sep" }),
      h("div", { class: "times" }, sIn, h("span", { class: "muted" }, "–"), eIn, dateIn),
      h("div", { class: "sep" }), dIn,
      h("button", { class: "icon", title: "Continue this entry", "aria-label": "Continue this entry", onclick: () => startFrom(e) }, ico("play")),
      more);
  }
  await load(true);
}

// ---------------------------------------------------------------- timesheet
async function pageTimesheet(root, params) {
  let week = params.week || weekStartOf(todayLocal());
  const box = h("div");
  const extraRows = [];
  root.append(h("h1", {}, "Timesheet"), box);
  async function load() {
    const days = [...Array(7)].map((_, i) => addDays(week, i));
    const r = await GET("/api/entries", { from: days[0], to: days[6], limit: 5000 });
    const es = r.entries.filter((e) => e.end_utc);
    const rows = new Map();
    const key = (p, t) => `${p || 0}:${t || 0}`;
    for (const e of es) {
      const k = key(e.project_id, e.task_id);
      if (!rows.has(k)) rows.set(k, { pid: e.project_id, tid: e.task_id, cells: days.map(() => []) });
      rows.get(k).cells[days.indexOf(localDate(e.start_utc))].push(e);
    }
    for (const x of extraRows) if (!rows.has(key(x.pid, x.tid))) rows.set(key(x.pid, x.tid), { pid: x.pid, tid: x.tid, cells: days.map(() => []) });
    const sorted = [...rows.values()].sort((a, b) => ((proj(a.pid) || {}).name || "~").localeCompare((proj(b.pid) || {}).name || "~"));
    const t = todayLocal();
    const colTot = days.map((_, i) => sorted.reduce((a, r) => a + r.cells[i].reduce((x, e) => x + e.seconds, 0), 0));
    const cell = (row, i) => {
      const es = row.cells[i], sec = es.reduce((a, e) => a + e.seconds, 0);
      const inp = h("input", { value: sec ? dur(sec) : "", "aria-label": `${(proj(row.pid) || {}).name || "No project"} ${days[i]}`, placeholder: "" });
      inp.addEventListener("keydown", (ev) => { if (ev.key === "Enter") inp.blur(); });
      inp.addEventListener("change", async () => {
        const want = inp.value.trim() === "" ? 0 : parseDur(inp.value);
        if (want === null) { fail(new Error("Use h:mm or decimal hours")); inp.value = sec ? dur(sec) : ""; return; }
        try {
          if (want > sec) {
            const last = es.reduce((a, e) => (e.end_utc > a ? e.end_utc : a), "");
            const dayEnds = r.entries.filter((e) => e.end_utc && localDate(e.start_utc) === days[i]).map((e) => e.end_utc).sort();
            const start = last || dayEnds.pop() || localToUtc(days[i], 9, 0);
            await api("POST", "/api/entries", { project_id: row.pid, task_id: row.tid, start_utc: start,
              end_utc: isoUtc(new Date(new Date(start).getTime() + (want - sec) * 1000)), description: "",
              billable: row.pid ? proj(row.pid).billable : 1 });
          } else if (want < sec) {
            let cut = sec - want;
            const ordered = [...es].sort((a, b) => (a.start_utc < b.start_utc ? 1 : -1));
            const toDelete = [];
            for (const e of ordered) {
              if (cut <= 0) break;
              if (e.seconds <= cut) { toDelete.push(e.id); cut -= e.seconds; }
              else { await api("PATCH", `/api/entries/${e.id}`, { end_utc: isoUtc(new Date(new Date(e.end_utc).getTime() - cut * 1000)) }); cut = 0; }
            }
            if (toDelete.length) await api("POST", "/api/entries/delete", { ids: toDelete });
          }
          load();
        } catch (x) { fail(x); load(); }
      });
      return h("td", {}, inp);
    };
    box.replaceChildren(
      h("div", { class: "row", style: { marginBottom: "12px" } },
        h("button", { class: "sq", onclick: () => { week = addDays(week, -7); load(); }, "aria-label": "Previous week" }, ico("left")),
        h("b", {}, `${dayLabel(days[0], false)} – ${dayLabel(days[6], false)}`),
        h("button", { class: "sq", onclick: () => { week = addDays(week, 7); load(); }, "aria-label": "Next week" }, ico("right")),
        h("button", { onclick: () => { week = weekStartOf(todayLocal()); load(); } }, "This week"),
        h("span", { class: "grow" }),
        h("span", { class: "muted" }, "Type h:mm or decimal hours. More than the cell adds an entry; less trims the latest ones.")),
      h("div", { class: "panel" }, h("table", { class: "t ts" },
        h("thead", {}, h("tr", {}, h("th", {}, "Project / task"),
          days.map((d) => h("th", { class: d === t ? "today" : "" }, dayLabel(d, false).split(",").slice(0, 2).join(","))), h("th", {}, "Total"))),
        h("tbody", {}, sorted.map((row) => {
          const p = proj(row.pid), tk = task(row.tid);
          const tot = row.cells.flat().reduce((a, e) => a + e.seconds, 0);
          return h("tr", {}, h("td", {}, p ? [h("span", { class: "dot", style: { background: p.color } }), p.name, tk ? h("span", { class: "muted" }, ": " + tk.name) : null] : h("span", { class: "muted" }, "No project")),
            days.map((_, i) => cell(row, i)), h("td", { class: "num" }, h("b", {}, dur(tot))));
        }),
          h("tr", {}, h("td", { colspan: 9 }, (() => {
            const b = h("button", { class: "link", "data-popper": "1" }, "+ Add row");
            b.addEventListener("click", () => pickProject(b, {}, (v) => { extraRows.push({ pid: v.project_id, tid: v.task_id }); load(); }));
            return b;
          })()))),
        h("tfoot", {}, h("tr", {}, h("td", {}, "Total"), colTot.map((s) => h("td", { class: "num" }, s ? dur(s) : "")),
          h("td", { class: "num" }, dur(colTot.reduce((a, b) => a + b, 0))))))));
  }
  await load();
}

// ---------------------------------------------------------------- calendar
async function pageCalendar(root, params) {
  let week = params.week || weekStartOf(todayLocal());
  const box = h("div");
  root.append(h("h1", {}, "Calendar"), box);
  const PX = 48; // px per hour
  async function load() {
    const days = [...Array(7)].map((_, i) => addDays(week, i));
    const r = await GET("/api/entries", { from: addDays(days[0], -1), to: days[6], limit: 5000 });
    const t = todayLocal();
    const scroll = h("div", { class: "cal-scroll panel" });
    const grid = h("div", { class: "cal" });
    grid.append(h("div", { class: "cal-head" }), days.map((d) => h("div", { class: "cal-head" + (d === t ? " today" : "") },
      dayLabel(d, false).split(",")[0] + " " + d.slice(8), h("b", {}, dur(r.entries.filter((e) => localDate(e.start_utc) === d).reduce((a, e) => a + e.seconds, 0))))));
    const hours = h("div", { class: "cal-hours" }, [...Array(24)].map((_, i) => h("div", { class: "cal-hour" }, clock(i, 0))));
    grid.append(hours);
    for (const d of days) {
      const col = h("div", { class: "cal-col", "data-date": d }, [...Array(24)].map(() => h("div", { class: "slot" })));
      const dayStart = localToUtc(d, 0, 0), dayEnd = localToUtc(addDays(d, 1), 0, 0);
      const segs = r.entries.map((e) => ({ e, s: e.start_utc < dayStart ? dayStart : e.start_utc,
        en: (e.end_utc || nowUtc()) > dayEnd ? dayEnd : (e.end_utc || nowUtc()) })).filter((x) => x.s < x.en && x.s < dayEnd && x.en > dayStart)
        .sort((a, b) => (a.s < b.s ? -1 : 1));
      // lanes for overlapping blocks
      const lanes = [];
      for (const x of segs) { let i = lanes.findIndex((end) => end <= x.s); if (i < 0) { i = lanes.length; lanes.push(""); } lanes[i] = x.en; x.lane = i; }
      const n = Math.max(1, lanes.length);
      for (const x of segs) {
        const top = secsBetween(dayStart, x.s) / 3600 * PX, hgt = Math.max(14, secsBetween(x.s, x.en) / 3600 * PX);
        const p = proj(x.e.project_id);
        const blk = h("div", { class: "blk" + (x.e.end_utc ? "" : " run") + (hgt < 34 ? " short" : hgt >= 52 ? " tall" : ""), tabindex: "0", role: "button",
          style: { top: top + "px", height: hgt + "px", background: p ? p.color : "#a1a1aa",
            left: `calc(${(x.lane / n) * 100}% + 2px)`, width: `calc(${100 / n}% - 4px)`, right: "auto" },
          title: `${x.e.description || "(no description)"}\n${p ? p.name : "No project"}${x.e.task ? ": " + x.e.task : ""}\n${timeOf(x.e.start_utc)} – ${x.e.end_utc ? timeOf(x.e.end_utc) : "running"} (${hms(x.e.seconds)})` },
          h("b", {}, x.e.description || "(no description)"), h("div", {}, p ? p.name : ""));
        const open = (ev) => { ev.stopPropagation(); entryDialog(x.e, () => load()); };
        blk.addEventListener("mousedown", (ev) => ev.stopPropagation());
        blk.addEventListener("click", open);
        blk.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); open(ev); } });
        col.append(blk);
      }
      // drag to create (15-minute snap)
      col.addEventListener("mousedown", (ev) => {
        if (ev.button !== 0) return;
        const rect = col.getBoundingClientRect();
        const slot = (y) => Math.max(0, Math.min(96, Math.round((y - rect.top) / (PX / 4))));
        const a = slot(ev.clientY);
        const ghost = h("div", { class: "blk ghost" });
        col.append(ghost);
        let b = a + 2;
        const draw = () => { const lo = Math.min(a, b), hi = Math.max(a, b, lo + 1); ghost.style.top = lo * PX / 4 + "px"; ghost.style.height = (hi - lo) * PX / 4 + "px"; };
        draw();
        const move = (e2) => { b = slot(e2.clientY); if (b === a) b = a + 1; draw(); };
        const up = () => {
          removeEventListener("mousemove", move); removeEventListener("mouseup", up); ghost.remove();
          const lo = Math.min(a, b), hi = Math.max(a, b, lo + 1);
          const s = localToUtc(d, Math.floor(lo / 4), (lo % 4) * 15);
          const e = hi >= 96 ? localToUtc(addDays(d, 1), 0, 0) : localToUtc(d, Math.floor(hi / 4), (hi % 4) * 15);
          entryDialog({ start_utc: s, end_utc: e }, () => load());
        };
        addEventListener("mousemove", move); addEventListener("mouseup", up);
        ev.preventDefault();
      });
      grid.append(col);
    }
    scroll.append(grid);
    box.replaceChildren(
      h("div", { class: "row", style: { marginBottom: "12px" } },
        h("button", { class: "sq", onclick: () => { week = addDays(week, -7); load(); }, "aria-label": "Previous week" }, ico("left")),
        h("b", {}, `${dayLabel(days[0], false)} – ${dayLabel(days[6], false)}`),
        h("button", { class: "sq", onclick: () => { week = addDays(week, 7); load(); }, "aria-label": "Next week" }, ico("right")),
        h("button", { onclick: () => { week = weekStartOf(todayLocal()); load(); } }, "This week"),
        h("span", { class: "grow" }),
        h("span", { class: "muted" }, "Drag on empty time to add an entry. Click a block to edit."),
        h("button", { onclick: () => entryDialog({}, () => load()) }, "+ Add entry")),
      scroll);
    // after the page is attached: scroll to the earliest work this week, or 7 AM
    const first = Math.min(7, ...r.entries.filter((e) => days.includes(localDate(e.start_utc))).map((e) => parts(e.start_utc).h));
    setTimeout(() => { scroll.scrollTop = Math.max(0, first * PX - 6); }, 0);
  }
  await load();
}

// ---------------------------------------------------------------- charts
// Drawn at the box's real size, so it fills its card without stretching text.
function barChart(buckets, values, colors, opts = {}) {
  const box = h("div", { class: "chart" });
  let last = "";
  new ResizeObserver(() => {
    const W = box.clientWidth, H = box.clientHeight;
    if (!W || !H || last === W + "x" + H) return;
    last = W + "x" + H;
    box.replaceChildren(barSvg(buckets, values, colors, opts, W, H));
  }).observe(box);
  return box;
}

function barSvg(buckets, values, colors, opts, W, H) {
  const L = 40, B = 26, T = 12;
  const totals = buckets.map((b) => Object.values(values[b] || {}).reduce((a, x) => a + x, 0));
  const maxH = Math.max(1, ...totals.map((s) => s / 3600));
  const step = maxH <= 4 ? 1 : maxH <= 10 ? 2 : maxH <= 24 ? 4 : Math.ceil(maxH / 6 / 5) * 5;
  const top = Math.ceil(maxH / step) * step;
  const bw = (W - L - 10) / buckets.length;
  const y = (hrs) => T + (H - T - B) * (1 - hrs / top);
  const svg = h("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": opts.label || "Hours chart" });
  for (let v = 0; v <= top; v += step) {
    svg.append(h("line", { x1: L, x2: W - 6, y1: y(v), y2: y(v), stroke: "#f0f0f1" }),
      h("text", { x: L - 6, y: y(v) + 4, "text-anchor": "end" }, v + "h"));
  }
  const every = Math.ceil(buckets.length / Math.max(2, Math.floor((W - L) / 56)));
  buckets.forEach((b, i) => {
    let acc = 0;
    const x = L + i * bw + bw * 0.15, w = bw * 0.7;
    const g = h("g", {}, h("title", {}, `${b}: ${hms(totals[i])}`));
    for (const [name, sec] of Object.entries(values[b] || {}).sort()) {
      const hh = sec / 3600;
      g.append(h("rect", { x, width: w, y: y(acc + hh), height: Math.max(0, y(acc) - y(acc + hh)), fill: colors[name] || "#a1a1aa" }));
      acc += hh;
    }
    svg.append(g);
    if (i % every === 0) svg.append(h("text", { x: x + w / 2, y: H - 6, "text-anchor": "middle" }, opts.fmt ? opts.fmt(b) : b.slice(5)));
  });
  if (!totals.some((t) => t > 0)) svg.append(h("text", { x: (L + W) / 2, y: (T + H - B) / 2 + 5, "text-anchor": "middle", style: { fontSize: "14px" } }, "No time in this period"));
  return svg;
}

function donut(groups, opts = {}) {
  const R = 70, r = 44, C = 90;
  const total = groups.reduce((a, g) => a + g.seconds, 0);
  const svg = h("svg", { viewBox: "0 0 180 180", width: opts.size || 180, role: "img", "aria-label": "Share by group" });
  if (!total) { svg.append(h("circle", { cx: C, cy: C, r: (R + r) / 2, fill: "none", stroke: "#f0f0f1", "stroke-width": R - r })); return svg; }
  let a0 = -Math.PI / 2;
  for (const g of groups) {
    const frac = g.seconds / total;
    if (frac <= 0) continue;
    const a1 = a0 + frac * 2 * Math.PI - (frac === 1 ? 0.0001 : 0);
    const large = a1 - a0 > Math.PI ? 1 : 0;
    const P = (rad, a) => `${C + rad * Math.cos(a)} ${C + rad * Math.sin(a)}`;
    svg.append(h("path", { d: `M ${P(R, a0)} A ${R} ${R} 0 ${large} 1 ${P(R, a1)} L ${P(r, a1)} A ${r} ${r} 0 ${large} 0 ${P(r, a0)} Z`,
      fill: g.color || PALETTE[groups.indexOf(g) % PALETTE.length] }, h("title", {}, `${g.name}: ${hms(g.seconds)} (${(frac * 100).toFixed(1)}%)`)));
    a0 = a1;
  }
  svg.append(h("text", { x: C, y: C + 5, "text-anchor": "middle", style: { fontSize: "15px", fontWeight: "600", fill: "#18181b" } }, hms(total).replace(/:\d\d$/, "")));
  return svg;
}
const PALETTE = ["#03A9F4", "#E91E63", "#4CAF50", "#FF9800", "#9C27B0", "#009688", "#795548", "#3F51B5", "#CDDC39", "#607D8B", "#F44336", "#00BCD4"];

function legend(groups, total) {
  return h("ul", { class: "legend" }, groups.map((g, i) => h("li", {},
    h("span", { class: "dot", style: { background: g.color || PALETTE[i % PALETTE.length], marginTop: "5px" } }),
    h("span", { class: "grow", title: g.name }, g.name),
    h("span", { class: "num" }, hms(g.seconds)),
    h("span", { class: "num muted", style: { width: "48px" } }, total ? ((g.seconds / total) * 100).toFixed(1) + "%" : ""))));
}

// ---------------------------------------------------------------- dashboard
async function pageDashboard(root) {
  const d = await GET("/api/dashboard", { today: todayLocal() });
  const card = (k, v, sub) => h("div", { class: "panel card" }, h("div", { class: "k" }, k), h("div", { class: "v" }, v), sub ? h("div", { class: "muted" }, sub) : null);
  const colors = { ...d.week_series.projects, ...d.last30.projects };
  root.append(h("h1", {}, "Dashboard"),
    h("div", { class: "cards" },
      card("Today", dur(d.today.seconds)),
      card("This week", dur(d.week.seconds), `${dec(d.week.billable_seconds)} h billable`),
      card("This month", dur(d.month.seconds), `${dec(d.month.billable_seconds)} h billable`),
      card("Billable amount, month", money(d.month.amount)),
      card("Entries this month", d.month.entries)),
    h("div", { class: "grid2 mt" },
      h("div", { class: "panel pad" }, h("h2", {}, "This week by day"),
        barChart(d.week_series.buckets, d.week_series.values, colors, { fmt: (b) => DOW[isoDow(b)] + " " + b.slice(8), label: "Hours this week by day" })),
      h("div", { class: "panel pad" }, h("h2", {}, "This week by project"),
        h("div", { class: "row" }, donut(d.week_projects), h("div", { class: "grow" }, legend(d.week_projects.slice(0, 8), d.week.seconds))))),
    h("div", { class: "grid2 mt" },
      h("div", { class: "panel pad" }, h("h2", {}, "Last 30 days"),
        barChart(d.last30.buckets, d.last30.values, colors, { label: "Hours in the last 30 days" })),
      h("div", { class: "panel pad" }, h("h2", {}, "This month by project"),
        d.month_projects.length ? d.month_projects.slice(0, 10).map((g) => h("div", { style: { margin: "8px 0" } },
          h("div", { class: "row" }, h("span", { class: "dot", style: { background: g.color || "#a1a1aa" } }), h("span", { class: "grow" }, g.name), h("span", { class: "num" }, dec(g.seconds) + " h")),
          h("div", { class: "bar-track" }, h("div", { class: "bar-fill", style: { width: (g.seconds / d.month.seconds * 100) + "%", background: g.color || "#a1a1aa" } }))))
          : h("div", { class: "muted" }, "Nothing tracked this month yet."))),
    h("div", { class: "panel mt" }, h("div", { class: "pad" }, h("h2", { style: { margin: 0 } }, "Recent activity")),
      h("table", { class: "t" }, h("tbody", {}, d.recent.map((e) => h("tr", { class: "click", onclick: () => entryDialog(e, () => route()) },
        h("td", { class: "wrap" }, e.description || h("span", { class: "muted" }, "(no description)")),
        h("td", {}, e.project ? [h("span", { class: "dot", style: { background: e.project_color } }), e.project, e.task ? h("span", { class: "muted" }, ": " + e.task) : null] : ""),
        h("td", { class: "muted" }, dayLabel(localDate(e.start_utc))),
        h("td", { class: "num" }, e.end_utc ? dur(e.seconds) : "running")))))));
}

// ---------------------------------------------------------------- reports
function presetRange(name) {
  const t = todayLocal(), w = weekStartOf(t), m = t.slice(0, 8) + "01", y = t.slice(0, 4);
  const lm = addDays(m, -1).slice(0, 8) + "01";
  return {
    today: [t, t], yesterday: [addDays(t, -1), addDays(t, -1)], week: [w, addDays(w, 6)],
    lastweek: [addDays(w, -7), addDays(w, -1)], month: [m, monthEnd(t)], lastmonth: [lm, monthEnd(lm)],
    year: [`${y}-01-01`, `${y}-12-31`], lastyear: [`${+y - 1}-01-01`, `${+y - 1}-12-31`], all: ["2000-01-01", "2099-12-31"],
  }[name];
}

function checklist(label, options, sel, onChange) {
  const b = h("button", { class: "chk", "data-popper": "1", type: "button" });
  const paint = () => { b.textContent = sel.size ? `${label}: ${sel.size}` : `${label}: all`; };
  b.addEventListener("click", () => {
    const input = h("input", { class: "search", placeholder: `Find ${label.toLowerCase()}` });
    const box = h("div", { style: { overflow: "auto" } });
    const draw = () => {
      box.replaceChildren(h("label", { class: "act" }, h("input", { type: "checkbox", checked: !sel.size, onchange: () => { sel.clear(); draw(); paint(); onChange(); } }), "All"));
      for (const o of options().filter((o) => o.name.toLowerCase().includes(input.value.toLowerCase()))) {
        const cb = h("input", { type: "checkbox", checked: sel.has(o.id), onchange: () => { cb.checked ? sel.add(o.id) : sel.delete(o.id); paint(); onChange(); draw(); } });
        box.append(h("label", {}, cb, o.color ? h("span", { class: "dot", style: { background: o.color } }) : null, o.name));
      }
    };
    input.addEventListener("input", draw);
    draw();
    popAt(b, h("div", {}, input, box));
    input.focus();
  });
  paint();
  return b;
}

async function pageReports(root, params) {
  const F = { tab: params.tab || "summary", from: params.from, to: params.to, preset: params.from ? "custom" : "month",
    project: new Set(), client: new Set(), task: new Set(), tag: new Set(), billable: "", q: "",
    group1: "project", group2: "description", week: weekStartOf(todayLocal()), offset: 0 };
  if (!F.from) [F.from, F.to] = presetRange("month");
  const out = h("div");
  const tabs = h("div", { class: "tabs", role: "tablist" });
  const filters = h("div", { class: "panel filters" });
  root.append(h("h1", {}, "Reports"), tabs, filters, out);
  const fq = () => ({ from: F.from, to: F.to, project: [...F.project], client: [...F.client], task: [...F.task], tag: [...F.tag], billable: F.billable, q: F.q });

  const paintTabs = () => tabs.replaceChildren(...[["summary", "Summary"], ["detailed", "Detailed"], ["weekly", "Weekly"], ["invoice", "Invoice lines"]].map(([k, l]) =>
    h("button", { class: F.tab === k ? "on" : "", role: "tab", "aria-selected": F.tab === k ? "true" : "false", onclick: () => { F.tab = k; F.offset = 0; paintTabs(); paintFilters(); load(); } }, l)));

  function paintFilters() {
    const from = h("input", { type: "date", value: F.from, "aria-label": "From" });
    const to = h("input", { type: "date", value: F.to, "aria-label": "To" });
    const preset = h("select", { "aria-label": "Date range" }, [["custom", "Custom"], ["today", "Today"], ["yesterday", "Yesterday"], ["week", "This week"], ["lastweek", "Last week"],
      ["month", "This month"], ["lastmonth", "Last month"], ["year", "This year"], ["lastyear", "Last year"], ["all", "All time"]].map(([v, l]) => h("option", { value: v, selected: F.preset === v }, l)));
    preset.addEventListener("change", () => { if (preset.value !== "custom") { F.preset = preset.value; [F.from, F.to] = presetRange(preset.value); F.offset = 0; paintFilters(); load(); } });
    const shift = (n) => {
      const span = (new Date(F.to) - new Date(F.from)) / 86400000 + 1;
      if (["month", "lastmonth"].includes(F.preset) || (F.from.endsWith("-01") && F.to === monthEnd(F.from))) {
        const nf = n > 0 ? addDays(monthEnd(F.from), 1) : addDays(F.from, -1).slice(0, 8) + "01";
        F.from = nf; F.to = monthEnd(nf);
      } else { F.from = addDays(F.from, n * span); F.to = addDays(F.to, n * span); }
      F.preset = "custom"; F.offset = 0; paintFilters(); load();
    };
    for (const [i, k] of [[from, "from"], [to, "to"]]) i.addEventListener("change", () => { F[k] = i.value; F.preset = "custom"; F.offset = 0; paintFilters(); load(); });
    const reload = () => { F.offset = 0; load(); };
    const bill = h("select", { "aria-label": "Billable" }, [["", "Billable and non-billable"], ["true", "Billable"], ["false", "Non-billable"]].map(([v, l]) => h("option", { value: v, selected: F.billable === v }, l)));
    bill.addEventListener("change", () => { F.billable = bill.value; reload(); });
    const q = h("input", { placeholder: "Description contains…", value: F.q, "aria-label": "Description contains" });
    q.addEventListener("change", () => { F.q = q.value; reload(); });
    const isWeekly = F.tab === "weekly";
    filters.replaceChildren(
      isWeekly ? h("div", { class: "row" },
        h("button", { class: "sq", onclick: () => { F.week = addDays(F.week, -7); load(); paintFilters(); }, "aria-label": "Previous week" }, ico("left")),
        h("b", {}, `${dayLabel(F.week, false)} – ${dayLabel(addDays(F.week, 6), false)}`),
        h("button", { class: "sq", onclick: () => { F.week = addDays(F.week, 7); load(); paintFilters(); }, "aria-label": "Next week" }, ico("right")))
        : h("div", { class: "row" }, h("button", { class: "sq", onclick: () => shift(-1), "aria-label": "Previous period" }, ico("left")), preset, from, to,
          h("button", { class: "sq", onclick: () => shift(1), "aria-label": "Next period" }, ico("right"))),
      checklist("Client", () => S.meta.clients.map((c) => ({ id: c.id, name: c.name })), F.client, reload),
      checklist("Project", () => S.meta.projects.map((p) => ({ id: p.id, name: p.name + (p.client ? ` (${p.client})` : ""), color: p.color })), F.project, reload),
      checklist("Task", () => S.meta.tasks.filter((t) => !F.project.size || F.project.has(t.project_id)).map((t) => ({ id: t.id, name: `${(proj(t.project_id) || {}).name}: ${t.name}` })), F.task, reload),
      checklist("Tag", () => S.meta.tags.map((t) => ({ id: t.id, name: t.name })), F.tag, reload),
      bill, q);
  }

  const exportBtns = (kind, extra = {}) => h("div", { class: "row" }, ["csv", "xlsx"].map((fmt) =>
    h("a", { class: "btn", href: `/api/export?${qs({ kind, format: fmt, ...fq(), ...extra })}`, download: "" }, "Export " + fmt.toUpperCase())));

  async function load() {
    try {
      if (F.tab === "summary") await summary();
      else if (F.tab === "detailed") await detailed();
      else if (F.tab === "weekly") await weekly();
      else await invoice();
    } catch (e) { fail(e); }
  }

  async function summary() {
    const GROUPS = [["project", "Project"], ["client", "Client"], ["task", "Task"], ["tag", "Tag"], ["description", "Description"], ["day", "Day"], ["week", "Week"], ["month", "Month"], ["billable", "Billable"]];
    const days = (new Date(F.to) - new Date(F.from)) / 86400000 + 1;
    const by = days > 62 ? "week" : "day";
    const [r, s] = await Promise.all([GET("/api/reports/summary", { ...fq(), group1: F.group1, group2: F.group2 }),
      days <= 800 ? GET("/api/reports/series", { ...fq(), by }) : Promise.resolve(null)]);
    const g1 = h("select", { "aria-label": "Group by" }, GROUPS.map(([v, l]) => h("option", { value: v, selected: F.group1 === v }, l)));
    const g2 = h("select", { "aria-label": "Then by" }, [h("option", { value: "" }, "(none)"), GROUPS.map(([v, l]) => h("option", { value: v, selected: F.group2 === v }, l))]);
    g1.addEventListener("change", () => { F.group1 = g1.value; load(); });
    g2.addEventListener("change", () => { F.group2 = g2.value; load(); });
    const open = new Set();
    const tbody = h("tbody");
    const paintRows = () => {
      tbody.replaceChildren();
      r.groups.forEach((g, i) => {
        const has = F.group2 && g.children.length;
        tbody.append(h("tr", { class: has ? "click" : "", onclick: () => { if (!has) return; open.has(i) ? open.delete(i) : open.add(i); paintRows(); } },
          h("td", { class: "wrap" }, has ? (open.has(i) ? "▾ " : "▸ ") : "", g.color ? h("span", { class: "dot", style: { background: g.color } }) : null, g.name),
          h("td", { class: "num" }, hms(g.seconds)), h("td", { class: "num" }, g.hours.toFixed(2)), h("td", { class: "num" }, money(g.amount)),
          h("td", { class: "num muted" }, r.total.seconds ? (g.seconds / r.total.seconds * 100).toFixed(1) + "%" : "")));
        if (open.has(i)) for (const c of g.children) tbody.append(h("tr", { class: "sub" }, h("td", { class: "wrap" }, c.name),
          h("td", { class: "num" }, hms(c.seconds)), h("td", { class: "num" }, c.hours.toFixed(2)), h("td", { class: "num" }, money(c.amount)), h("td")));
      });
    };
    paintRows();
    out.replaceChildren(
      h("div", { class: "cards" },
        h("div", { class: "panel card" }, h("div", { class: "k" }, "Total"), h("div", { class: "v" }, hms(r.total.seconds)), h("div", { class: "muted" }, r.total.hours.toFixed(2) + " h")),
        h("div", { class: "panel card" }, h("div", { class: "k" }, "Billable"), h("div", { class: "v" }, hms(r.total.billable_seconds)), h("div", { class: "muted" }, r.total.billable_hours.toFixed(2) + " h")),
        h("div", { class: "panel card" }, h("div", { class: "k" }, "Amount"), h("div", { class: "v" }, money(r.total.amount))),
        h("div", { class: "panel card" }, h("div", { class: "k" }, "Entries"), h("div", { class: "v" }, r.total.entries))),
      s ? h("div", { class: "grid2 mt" },
        h("div", { class: "panel pad" }, barChart(s.buckets, s.values, s.projects, { label: `Hours by ${by}`, fmt: by === "day" ? (b) => b.slice(5) : (b) => b.slice(5) })),
        h("div", { class: "panel pad" }, h("div", { class: "row" }, donut(r.groups.slice(0, 12)), h("div", { class: "grow" }, legend(r.groups.slice(0, 8), r.total.seconds))))) : null,
      h("div", { class: "row mt" }, "Group by", g1, "then", g2, h("span", { class: "grow" }), exportBtns("summary", { group1: F.group1, group2: F.group2 })),
      h("div", { class: "panel mt" }, h("table", { class: "t" },
        h("thead", {}, h("tr", {}, h("th", {}, GROUPS.find((x) => x[0] === F.group1)[1]), h("th", { class: "num" }, "Duration"), h("th", { class: "num" }, "Hours"), h("th", { class: "num" }, "Amount"), h("th", { class: "num" }, "Share"))),
        tbody,
        h("tfoot", {}, h("tr", {}, h("td", {}, "Total"), h("td", { class: "num" }, hms(r.total.seconds)), h("td", { class: "num" }, r.total.hours.toFixed(2)), h("td", { class: "num" }, money(r.total.amount)), h("td"))))));
  }

  async function detailed() {
    const PAGE = 100;
    const [r, sum] = await Promise.all([GET("/api/entries", { ...fq(), limit: PAGE, offset: F.offset }), GET("/api/reports/summary", { ...fq(), group1: "billable" })]);
    const sel = new Set();
    const bulk = h("div");
    const paintBulk = () => bulk.replaceChildren(sel.size ? h("div", { class: "bulkbar" }, `${sel.size} selected`,
      h("button", { class: "danger", onclick: async () => { await deleteEntries([...sel]); load(); } }, "Delete")) : "");
    out.replaceChildren(
      h("div", { class: "row" }, h("b", {}, `${r.count} entries`), h("span", { class: "muted" }, `· ${hms(sum.total.seconds)} · ${sum.total.hours.toFixed(2)} h · ${money(sum.total.amount)}`),
        h("span", { class: "grow" }), exportBtns("detailed")),
      bulk,
      h("div", { class: "panel mt" }, h("table", { class: "t" },
        h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", {}, "Description"), h("th", {}, "Project / task"), h("th", {}, "Tags"), h("th", {}, "Date"), h("th", {}, "Time"), h("th", { class: "num" }, "Duration"), h("th", { class: "num" }, "Amount"))),
        h("tbody", {}, r.entries.map((e) => {
          const cb = h("input", { type: "checkbox", "aria-label": "Select entry", onclick: (ev) => ev.stopPropagation(), onchange: () => { cb.checked ? sel.add(e.id) : sel.delete(e.id); paintBulk(); } });
          return h("tr", { class: "click", onclick: () => entryDialog(e, () => load()) },
            h("td", {}, cb),
            h("td", { class: "wrap" }, e.description || h("span", { class: "muted" }, "(no description)")),
            h("td", {}, e.project ? [h("span", { class: "dot", style: { background: e.project_color } }), e.project, e.task ? h("span", { class: "muted" }, ": " + e.task) : null] : h("span", { class: "muted" }, "No project")),
            h("td", { class: "muted" }, e.tags.map((t) => t.name).join(", ")),
            h("td", { style: { whiteSpace: "nowrap" } }, localDate(e.start_utc)),
            h("td", { class: "muted", style: { whiteSpace: "nowrap" } }, `${timeOf(e.start_utc)} – ${e.end_utc ? timeOf(e.end_utc) : "…"}`),
            h("td", { class: "num" }, dur(e.seconds)),
            h("td", { class: "num" }, e.billable ? money(e.amount) : h("span", { class: "muted" }, "non-billable")));
        })))),
      r.count > PAGE ? h("div", { class: "row mt" },
        h("button", { disabled: F.offset === 0, onclick: () => { F.offset = Math.max(0, F.offset - PAGE); load(); } }, ico("left"), h("span", {}, "Previous")),
        h("span", { class: "muted" }, `${F.offset + 1}–${Math.min(r.count, F.offset + PAGE)} of ${r.count}`),
        h("button", { disabled: F.offset + PAGE >= r.count, onclick: () => { F.offset += PAGE; load(); } }, h("span", {}, "Next"), ico("right"))) : null);
  }

  async function weekly() {
    const w = await GET("/api/reports/weekly", { ...fq(), week: F.week });
    out.replaceChildren(h("div", { class: "row" }, h("span", { class: "grow" }), exportBtns("weekly", { week: F.week })),
      h("div", { class: "panel mt" }, h("table", { class: "t ts" },
        h("thead", {}, h("tr", {}, h("th", {}, "Project / task"), w.days.map((d) => h("th", {}, dayLabel(d, false).split(",").slice(0, 2).join(","))), h("th", {}, "Total"))),
        h("tbody", {}, w.rows.map((r) => h("tr", {}, h("td", {}, h("span", { class: "dot", style: { background: r.color } }), r.project, r.task ? h("span", { class: "muted" }, ": " + r.task) : null),
          r.days.map((s) => h("td", { class: "num" }, s ? dur(s) : "")), h("td", { class: "num" }, h("b", {}, dur(r.total)))))),
        h("tfoot", {}, h("tr", {}, h("td", {}, "Total"), w.totals.map((s) => h("td", { class: "num" }, s ? dur(s) : "")), h("td", { class: "num" }, dur(w.total)))))));
  }

  async function invoice() {
    const r = await GET("/api/reports/invoice", fq());
    out.replaceChildren(
      h("div", { class: "row" }, h("b", {}, `${r.lines.length} lines · ${r.hours.toFixed(2)} h billed · ${money(r.amount)}`),
        h("span", { class: "muted" }, `(raw ${r.raw_hours.toFixed(4)} h; each date × project × task rounded to 0.25 h, 0.25 h minimum)`),
        h("span", { class: "grow" }), exportBtns("invoice")),
      h("div", { class: "panel mt" }, h("table", { class: "t" },
        h("thead", {}, h("tr", {}, ["Date", "Project", "Task", "Description", "Raw h", "Billed h", "Rate", "Amount"].map((x, i) => h("th", { class: i >= 4 ? "num" : "" }, x)))),
        h("tbody", {}, r.lines.map((l) => h("tr", {}, h("td", { style: { whiteSpace: "nowrap" } }, l.date), h("td", {}, l.project), h("td", {}, l.task), h("td", { class: "wrap" }, l.description),
          h("td", { class: "num muted" }, l.raw_hours.toFixed(4)), h("td", { class: "num" }, l.hours.toFixed(2)), h("td", { class: "num" }, l.rate ?? ""), h("td", { class: "num" }, money(l.amount))))),
        h("tfoot", {}, h("tr", {}, h("td", { colspan: 4 }, "Total"), h("td", { class: "num" }, r.raw_hours.toFixed(4)), h("td", { class: "num" }, r.hours.toFixed(2)), h("td"), h("td", { class: "num" }, money(r.amount)))))));
  }

  paintTabs();
  paintFilters();
  await load();
}

// ---------------------------------------------------------------- projects
const COLORS = ["#03A9F4", "#2196F3", "#3F51B5", "#673AB7", "#9C27B0", "#E91E63", "#F44336", "#FF5722", "#FF9800", "#FFC107", "#CDDC39", "#8BC34A", "#4CAF50", "#009688", "#00BCD4", "#795548", "#A1887F", "#607D8B"];
function colorPicker(cur, onPick) {
  const box = h("div", { class: "swatches", role: "radiogroup", "aria-label": "Colour" });
  const paint = (c) => box.replaceChildren(...COLORS.map((x) => h("button", { type: "button", class: "swatch" + (x.toLowerCase() === (c || "").toLowerCase() ? " on" : ""),
    style: { background: x }, role: "radio", "aria-checked": x === c ? "true" : "false", "aria-label": x, onclick: () => { paint(x); onPick(x); } })));
  paint(cur);
  return box;
}

async function pageProjects(root, params) {
  if (params.id) return projectDetail(root, +params.id);
  const all = await GET("/api/reports/summary", { from: "2000-01-01", to: "2099-12-31", group1: "project" });
  const tracked = Object.fromEntries(all.groups.map((g) => [g.name, g]));
  let showArchived = false, q = "";
  const box = h("div");
  const form = () => {
    let color = COLORS[Math.floor(Math.random() * COLORS.length)];
    const name = h("input", { placeholder: "Project name", required: true, "aria-label": "Project name" });
    const cl = h("select", { "aria-label": "Client" }, h("option", { value: "" }, "No client"), S.meta.clients.filter((c) => !c.archived).map((c) => h("option", { value: c.id }, c.name)));
    return h("form", { class: "panel pad row", onsubmit: async (e) => {
      e.preventDefault();
      try { await api("POST", "/api/projects", { name: name.value, client_id: cl.value || null, color, billable: 1 }); await reloadMeta(); toast("Project created"); paint(); } catch (x) { fail(x); }
    } }, name, cl, colorPicker(color, (c) => { color = c; }), h("button", { class: "primary", type: "submit" }, "Create project"));
  };
  const paint = () => {
    const search = h("input", { placeholder: "Search projects", value: q, "aria-label": "Search projects" });
    search.addEventListener("input", () => { q = search.value; paintTable(); });
    const tableBox = h("div", { class: "panel mt" });
    const paintTable = () => {
      const rows = S.meta.projects.filter((p) => (showArchived || !p.archived) && (p.name + " " + (p.client || "")).toLowerCase().includes(q.toLowerCase()));
      tableBox.replaceChildren(h("table", { class: "t" },
        h("thead", {}, h("tr", {}, ["Name", "Client", "Tracked", "Amount", "Progress", "Rate", "Billable", ""].map((x, i) => h("th", { class: i >= 2 && i <= 5 ? "num" : "" }, x)))),
        h("tbody", {}, rows.map((p) => {
          const t = tracked[p.name] || { seconds: 0, amount: 0 };
          const est = p.estimate_hours ? Math.min(100, t.seconds / 3600 / p.estimate_hours * 100) : null;
          return h("tr", { class: "click" + (p.archived ? " archived" : ""), onclick: () => { location.hash = `#/projects/${p.id}`; } },
            h("td", {}, h("span", { class: "dot", style: { background: p.color } }), p.name, p.archived ? h("span", { class: "muted" }, " (archived)") : null),
            h("td", {}, p.client || h("span", { class: "muted" }, "—")),
            h("td", { class: "num" }, (t.seconds / 3600).toFixed(2) + " h"),
            h("td", { class: "num" }, money(t.amount)),
            h("td", { class: "num" }, est === null ? h("span", { class: "muted" }, "—") : h("div", { class: "bar-track", title: `${est.toFixed(0)}% of ${p.estimate_hours} h` }, h("div", { class: "bar-fill", style: { width: est + "%", background: est >= 100 ? "#e53935" : p.color } }))),
            h("td", { class: "num" }, p.hourly_rate != null ? money(p.hourly_rate) : h("span", { class: "muted" }, "default")),
            h("td", {}, p.billable ? "Yes" : "No"),
            h("td", {}, h("button", { class: "link", onclick: (e) => { e.stopPropagation(); location.hash = `#/projects/${p.id}`; } }, "Edit")));
        }))));
    };
    box.replaceChildren(form(), h("div", { class: "row mt" }, search,
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: showArchived, onchange: (e) => { showArchived = e.target.checked; paintTable(); } }), "Show archived")), tableBox);
    paintTable();
  };
  root.append(h("h1", {}, "Projects"), box);
  paint();
}

async function projectDetail(root, id) {
  const p = proj(id);
  if (!p) { root.append(h("div", { class: "empty" }, "Project not found.")); return; }
  const v = { name: p.name, client_id: p.client_id || "", color: p.color, billable: p.billable, hourly_rate: p.hourly_rate ?? "", estimate_hours: p.estimate_hours ?? "", note: p.note || "", archived: p.archived };
  const inp = (k, attrs = {}) => { const i = h("input", { value: v[k], ...attrs }); i.addEventListener("input", () => { v[k] = i.value; }); return i; };
  const cl = h("select", {}, h("option", { value: "" }, "No client"), S.meta.clients.map((c) => h("option", { value: c.id, selected: c.id === p.client_id }, c.name)));
  cl.addEventListener("change", () => { v.client_id = cl.value; });
  const save = async (e) => {
    e.preventDefault();
    try {
      await api("PATCH", `/api/projects/${id}`, { ...v, client_id: v.client_id || null, hourly_rate: v.hourly_rate === "" ? null : v.hourly_rate, estimate_hours: v.estimate_hours === "" ? null : v.estimate_hours });
      await reloadMeta(); toast("Project saved"); route();
    } catch (x) { fail(x); }
  };
  const tasksBox = h("div", { class: "panel mt" });
  const paintTasks = () => {
    const ts = S.meta.tasks.filter((t) => t.project_id === id);
    const name = h("input", { placeholder: "New task name", "aria-label": "New task name" });
    tasksBox.replaceChildren(h("form", { class: "pad row", onsubmit: async (e) => {
      e.preventDefault();
      try { await api("POST", "/api/tasks", { project_id: id, name: name.value }); await reloadMeta(); paintTasks(); } catch (x) { fail(x); }
    } }, name, h("button", { class: "primary", type: "submit" }, "Add task")),
    h("table", { class: "t" }, h("thead", {}, h("tr", {}, h("th", {}, "Task"), h("th", { class: "num" }, "Rate override"), h("th", { class: "num" }, "Estimate (h)"), h("th", {}, "Done"), h("th", {}, ""))),
      h("tbody", {}, ts.map((t) => {
        const patch = async (body) => { try { await api("PATCH", `/api/tasks/${t.id}`, body); await reloadMeta(); paintTasks(); } catch (x) { fail(x); } };
        const n = h("input", { class: "bare", value: t.name, "aria-label": "Task name" }); n.addEventListener("change", () => patch({ name: n.value }));
        const r = h("input", { class: "bare num", type: "number", step: "0.01", value: t.hourly_rate ?? "", placeholder: "inherit", "aria-label": "Rate", style: { width: "100px" } }); r.addEventListener("change", () => patch({ hourly_rate: r.value }));
        const es = h("input", { class: "bare num", type: "number", step: "0.25", value: t.estimate_hours ?? "", placeholder: "—", "aria-label": "Estimate", style: { width: "90px" } }); es.addEventListener("change", () => patch({ estimate_hours: es.value }));
        return h("tr", { class: t.done ? "archived" : "" }, h("td", {}, n), h("td", { class: "num" }, r), h("td", { class: "num" }, es),
          h("td", {}, h("input", { type: "checkbox", checked: t.done, "aria-label": "Done", onchange: (e) => patch({ done: e.target.checked }) })),
          h("td", {}, h("button", { class: "link", onclick: async () => { try { const o = await api("DELETE", `/api/tasks/${t.id}`); toast(o.archived ? "Task has entries; marked done instead" : "Task deleted"); await reloadMeta(); paintTasks(); } catch (x) { fail(x); } } }, "Delete")));
      }))));
  };
  root.append(h("p", { class: "back" }, h("a", { href: "#/projects" }, ico("left"), "Projects")), h("h1", {}, h("span", { class: "dot", style: { background: p.color } }), p.name),
    h("form", { class: "panel pad", onsubmit: save, style: { display: "grid", gap: "12px", maxWidth: "760px" } },
      h("div", { class: "row" }, h("label", { class: "f grow" }, "Name", inp("name")), h("label", { class: "f" }, "Client", cl)),
      h("div", { class: "f" }, "Colour", colorPicker(v.color, (c) => { v.color = c; })),
      h("div", { class: "row" },
        h("label", { class: "f" }, "Hourly rate (blank = client/default)", inp("hourly_rate", { type: "number", step: "0.01", min: "0" })),
        h("label", { class: "f" }, "Estimate (hours)", inp("estimate_hours", { type: "number", step: "0.25", min: "0" })),
        h("label", { class: "check" }, h("input", { type: "checkbox", checked: v.billable, onchange: (e) => { v.billable = e.target.checked; } }), "Billable by default"),
        h("label", { class: "check" }, h("input", { type: "checkbox", checked: v.archived, onchange: (e) => { v.archived = e.target.checked; } }), "Archived")),
      h("label", { class: "f" }, "Note", (() => { const t = h("textarea", { rows: 2 }); t.value = v.note; t.addEventListener("input", () => { v.note = t.value; }); return t; })()),
      h("div", { class: "row" }, h("button", { class: "primary", type: "submit" }, "Save"),
        h("button", { type: "button", onclick: async () => { try { const o = await api("DELETE", `/api/projects/${id}`); toast(o.archived ? "Project has entries; archived instead" : "Project deleted"); await reloadMeta(); location.hash = "#/projects"; } catch (x) { fail(x); } } }, "Delete"),
        h("a", { class: "btn", href: `#/reports?tab=summary` }, "Reports"))),
    h("h2", {}, "Tasks"), tasksBox);
  paintTasks();
}

// ---------------------------------------------------------------- clients & tags
async function simpleList(root, table, title, cols) {
  let showArchived = false;
  const box = h("div");
  root.append(h("h1", {}, title), box);
  const paint = () => {
    const name = h("input", { placeholder: `New ${title.slice(0, -1).toLowerCase()} name`, "aria-label": "Name" });
    const rows = S.meta[table].filter((x) => showArchived || !x.archived);
    box.replaceChildren(
      h("form", { class: "panel pad row", onsubmit: async (e) => { e.preventDefault(); try { await api("POST", `/api/${table}`, { name: name.value }); await reloadMeta(); paint(); } catch (x) { fail(x); } } },
        name, h("button", { class: "primary", type: "submit" }, "Add"), h("span", { class: "grow" }),
        h("label", { class: "check" }, h("input", { type: "checkbox", checked: showArchived, onchange: (e) => { showArchived = e.target.checked; paint(); } }), "Show archived")),
      h("div", { class: "panel mt" }, h("table", { class: "t" },
        h("thead", {}, h("tr", {}, h("th", {}, "Name"), cols.map((c) => h("th", {}, c.label)), h("th", {}, "Archived"), h("th", {}, ""))),
        h("tbody", {}, rows.length ? null : h("tr", {}, h("td", { colspan: cols.length + 3, class: "muted", style: { textAlign: "center", padding: "24px" } },
          `No ${title.toLowerCase()} yet. Add one above.`)), rows.map((x) => {
          const patch = async (body) => { try { await api("PATCH", `/api/${table}/${x.id}`, body); await reloadMeta(); paint(); } catch (e) { fail(e); } };
          const n = h("input", { class: "bare", value: x.name, "aria-label": "Name" }); n.addEventListener("change", () => patch({ name: n.value }));
          return h("tr", { class: x.archived ? "archived" : "" }, h("td", {}, n), cols.map((c) => h("td", {}, c.cell(x, patch))),
            h("td", {}, h("input", { type: "checkbox", checked: x.archived, "aria-label": "Archived", onchange: (e) => patch({ archived: e.target.checked }) })),
            h("td", {}, h("button", { class: "link", onclick: async () => { try { const o = await api("DELETE", `/api/${table}/${x.id}`); toast(o.archived ? "In use; archived instead" : "Deleted"); await reloadMeta(); paint(); } catch (e) { fail(e); } } }, "Delete")));
        })))));
  };
  paint();
}
const pageClients = (root) => simpleList(root, "clients", "Clients", [
  { label: "Projects", cell: (c) => S.meta.projects.filter((p) => p.client_id === c.id).length },
  { label: "Hourly rate (blank = default)", cell: (c, patch) => { const i = h("input", { class: "bare", type: "number", step: "0.01", value: c.hourly_rate ?? "", placeholder: "default", "aria-label": "Rate", style: { width: "110px" } }); i.addEventListener("change", () => patch({ hourly_rate: i.value })); return i; } },
]);
const pageTags = (root) => simpleList(root, "tags", "Tags", []);

// ---------------------------------------------------------------- invoices
// A draft is only its parameters; the server rebuilds it from the entries each
// time, so the totals here are always the entries' totals. Issuing freezes the
// invoice and its PDF in the database for good; after that the only change is
// Void.
const isoNice = (d) => { if (!d) return ""; const [Y, M, D] = d.split("-").map(Number); return `${MON[M - 1]} ${D}, ${Y}`; };
const smoney = (x) => (x < 0 ? "−" : "") + money(Math.abs(x));
const hrs = (x) => (x || 0).toFixed(2) + " h";
const statusChip = (s) => h("span", { class: `chip ${s}` }, s[0].toUpperCase() + s.slice(1));
const pdfUrl = (id, download) => `/api/invoices/${id}/pdf${download ? "?download=1" : ""}`;

/** The app's own confirm (no browser dialogs). Resolves the reason text
    (or "") when confirmed, null when cancelled. */
function ask(title, text, okLabel, { danger = false, reason = null } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const input = reason !== null ? h("input", { placeholder: reason, "aria-label": reason }) : null;
    const dlg = h("dialog", { "aria-label": title });
    const finish = (v) => { if (done) return; done = true; dlg.close(); dlg.remove(); resolve(v); };
    dlg.append(h("form", { method: "dialog", onsubmit: (e) => { e.preventDefault(); finish(input ? input.value : ""); } },
      h("h2", {}, title), h("p", { class: "muted", style: { margin: 0 } }, text), input,
      h("div", { class: "actions" },
        h("button", { type: "button", onclick: () => finish(null) }, "Cancel"),
        h("button", { type: "submit", class: danger ? "danger" : "primary" }, okLabel))));
    dlg.addEventListener("close", () => finish(null));
    document.body.append(dlg);
    dlg.showModal();
    (input || dlg.querySelector("button[type=submit]")).focus();
  });
}

// Invoice settings: the standing text and billing rules every invoice is
// built from (invoice_profile.json beside the database). Drafts pick up a save at once;
// issued invoices keep the settings they were issued with.
async function invoiceSettings(root) {
  const p = await GET("/api/invoice-profile");
  const example = p.example;
  delete p.example;
  for (const s of p.sections) {          // edit every section as a list of groups
    if (!s.groups) { s.groups = [{ label: "", subtotal: s.subtotal || "", pairs: s.pairs || [] }]; delete s.subtotal; delete s.pairs; }
  }
  p.retainer_label = p.retainer_label || "Less: Retainer Fee Paid on {date}";
  p.credit_note = p.credit_note || { head: "", text: "" };
  const saveBtn = h("button", { class: "primary", disabled: true }, "Save settings");
  const touch = () => { saveBtn.disabled = false; };

  const field = (obj, key, label, { grow, ...attrs } = {}) => {
    const i = h("input", { value: obj[key] ?? "", ...attrs });
    i.addEventListener("input", () => { obj[key] = i.value; touch(); });
    return h("label", { class: "f" + (grow ? " grow" : "") }, label, i);
  };
  const area = (obj, key, label, rows = 3, hint) => {
    const t = h("textarea", { rows });
    t.value = Array.isArray(obj[key]) ? obj[key].join("\n") : (obj[key] || "");
    t.addEventListener("input", () => { obj[key] = Array.isArray(obj[key]) ? t.value.split("\n") : t.value; touch(); });
    return h("label", { class: "f" }, label, t, hint ? h("span", { class: "hint" }, hint) : null);
  };
  const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "section";

  // ---- sections
  const secBox = h("div");
  const pairName = (pr) => `${pr[0]} / ${pr[1] || "(no task)"}`;
  const allPairs = () => {
    const out = [];
    for (const pj of [...S.meta.projects].sort((a, b) => a.name.localeCompare(b.name))) {
      out.push([pj.name, ""]);
      for (const t of S.meta.tasks.filter((t) => t.project_id === pj.id).sort((a, b) => a.name.localeCompare(b.name))) out.push([pj.name, t.name]);
    }
    return out;
  };
  const used = () => new Set(p.sections.flatMap((s) => s.groups.flatMap((g) => g.pairs.map((pr) => pr[0] + "\u0000" + (pr[1] || "")))));
  const move = (arr, i, d) => { const j = i + d; if (j < 0 || j >= arr.length) return; [arr[i], arr[j]] = [arr[j], arr[i]]; touch(); paintSections(); paintCredits(); };
  const paintSections = () => {
    const taken = used();
    secBox.replaceChildren(p.sections.map((s, si) => h("div", { class: "panel pad mt prof-sec" },
      h("div", { class: "row" },
        field(s, "heading", `Section ${si + 1} heading`, { grow: true }),
        h("button", { class: "icon", title: "Move up", "aria-label": "Move section up", disabled: si === 0, onclick: () => move(p.sections, si, -1) }, ico("up")),
        h("button", { class: "icon", title: "Move down", "aria-label": "Move section down", disabled: si === p.sections.length - 1, onclick: () => move(p.sections, si, 1) }, ico("down")),
        h("button", { class: "icon", title: "Remove section", "aria-label": "Remove section", onclick: () => { p.sections.splice(si, 1); touch(); paintSections(); paintCredits(); } }, ico("x"))),
      area(s, "intro", "Introduction (printed under the heading in the appendix; optional)", 3),
      s.groups.map((g, gi) => h("div", { class: "prof-group" },
        h("div", { class: "row" },
          field(g, "label", s.groups.length > 1 || g.label ? `Group ${gi + 1} label (e.g. Requested by: …)` : "Group label (optional)", { grow: true }),
          field(g, "subtotal", "Subtotal line", { grow: true }),
          s.groups.length > 1 ? h("button", { class: "icon", title: "Remove group", "aria-label": "Remove group", onclick: () => { s.groups.splice(gi, 1); touch(); paintSections(); } }, ico("x")) : null),
        h("div", { class: "pairs" },
          g.pairs.map((pr, pi) => h("span", { class: "pair" }, pairName(pr),
            h("button", { class: "icon", "aria-label": `Remove ${pairName(pr)}`, onclick: () => { g.pairs.splice(pi, 1); touch(); paintSections(); } }, ico("x")))),
          (() => {
            const sel = h("select", { "aria-label": "Add a project or task" }, h("option", { value: "" }, "+ Add project / task…"),
              allPairs().filter((pr) => !taken.has(pr[0] + "\u0000" + pr[1])).map((pr) => h("option", { value: JSON.stringify(pr) }, pairName(pr))));
            sel.addEventListener("change", () => { if (!sel.value) return; g.pairs.push(JSON.parse(sel.value)); touch(); paintSections(); });
            return sel;
          })()))),
      h("button", { class: "link", onclick: () => { s.groups.push({ label: "", subtotal: "Subtotal — ", pairs: [] }); touch(); paintSections(); } }, "+ Add group"))),
    h("button", { class: "mt", onclick: () => { p.sections.push({ heading: "NEW SECTION", intro: "", groups: [{ label: "", subtotal: "Subtotal — ", pairs: [] }] }); touch(); paintSections(); paintCredits(); } }, "+ Add section"));
  };

  // ---- credits
  const credBox = h("div");
  const paintCredits = () => {
    credBox.replaceChildren(p.credits.map((c, ci) => {
      const sel = h("select", { "aria-label": "Applies to" },
        h("optgroup", { label: "Project" }, S.meta.projects.map((pj) => h("option", { value: "p:" + pj.name, selected: c.scope.project === pj.name }, pj.name))),
        h("optgroup", { label: "Whole section" }, p.sections.map((s, si) => h("option", { value: "s:" + si, selected: !!s.key && c.scope.section === s.key }, s.heading))));
      sel.addEventListener("change", () => {
        if (sel.value.startsWith("p:")) c.scope = { project: sel.value.slice(2) };
        else {
          const s = p.sections[+sel.value.slice(2)];
          if (!s.key) { let k = slug(s.heading), n = 2; while (p.sections.some((x) => x !== s && x.key === k)) k = slug(s.heading) + "-" + n++; s.key = k; }
          c.scope = { section: s.key };
        }
        touch();
      });
      return h("div", { class: "panel pad mt prof-sec" },
        h("div", { class: "row" }, field(c, "label", `Credit ${ci + 1} wording`, { grow: true }),
          h("button", { class: "icon", title: "Remove credit", "aria-label": "Remove credit", onclick: () => { p.credits.splice(ci, 1); touch(); paintCredits(); } }, ico("x"))),
        h("div", { class: "row" },
          h("label", { class: "f" }, "Applies to", sel),
          field(c, "pct", "Default %", { type: "number", min: "0", max: "100", step: "1", style: { width: "90px" } }),
          h("span", { class: "hint grow" }, "{pct} in the wording becomes the percentage. Each invoice can change the percentage.")));
    }),
    p.credits.length < 4 ? h("button", { class: "mt", onclick: () => { p.credits.push({ label: "Less: Discount ({pct}%)", pct: 50, scope: { project: S.meta.projects[0] ? S.meta.projects[0].name : "" } }); touch(); paintCredits(); } }, "+ Add credit") : null);
  };

  const body = () => ({ ...p, rate: +p.rate, max_hours: +p.max_hours,
    credits: p.credits.map((c) => ({ ...c, pct: +c.pct })),
    remittance: [].concat(p.remittance), client: { ...p.client, address: [].concat(p.client.address) } });
  saveBtn.addEventListener("click", async () => {
    try {
      await api("PUT", "/api/invoice-profile", body());
      saveBtn.disabled = true;
      toast("Invoice settings saved. Drafts use them now; issued invoices keep their own.");
    } catch (e) { fail(e); }
  });
  const preview = async () => {
    try {
      const res = await fetch("/api/invoice-profile/preview", { method: "POST", headers: { "Content-Type": "application/json", "X-DuTemps": "1" }, body: JSON.stringify(body()) });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || `HTTP ${res.status}`);
      window.open(URL.createObjectURL(await res.blob()), "_blank");
    } catch (e) { fail(e); }
  };

  root.append(
    h("p", { class: "back" }, h("a", { href: "#/invoices" }, ico("left"), "Invoices")),
    h("h1", {}, "Invoice settings"),
    example ? h("div", { class: "panel pad warn", role: "note" }, "These are example settings for a made-up business. Replace them with your own and save; invoices can't be issued until you do.") : null,
    h("p", { class: "muted", style: { maxWidth: "760px" } }, "The text and rules every invoice is built from. Drafts use a change as soon as it is saved; an issued invoice keeps the settings it was issued with. Each save keeps a copy of the previous settings in backups/ beside your database."),
    h("div", { class: "prof-grid" },
      h("div", { class: "panel pad prof-form" }, h("h2", {}, "From"),
        field(p.issuer, "name", "Your name"),
        h("div", { class: "row" }, field(p.issuer, "phone", "Phone"), field(p.issuer, "email", "Email", { grow: true }))),
      h("div", { class: "panel pad prof-form" }, h("h2", {}, "Bill to"),
        (() => {
          const names = S.meta.clients.map((c) => c.name);
          const cur = p.client.name || "";
          const sel = h("select", {}, h("option", { value: "" }, "(time with no client)"),
            cur && !names.includes(cur) ? h("option", { value: cur }, `${cur} (not in your clients)`) : null,
            names.map((n) => h("option", { value: n }, n)));
          sel.value = cur;
          sel.addEventListener("change", () => { p.client.name = sel.value; touch(); });
          return h("label", { class: "f" }, "Client", sel, h("span", { class: "hint" }, "Only this client's time is billed. Other time is left for its own invoice."));
        })(),
        area(p.client, "address", "Address (first line is printed in bold)", 5, "Up to 6 lines."),
        h("div", { class: "row" }, field(p.client, "attn", "Attention", { grow: true }), field(p.client, "email", "Client email", { grow: true })))),
    h("div", { class: "panel pad prof-form mt" }, h("h2", {}, "Terms and rate"),
      h("div", { class: "row" }, field(p, "agreement", "Agreement", { grow: true }), field(p, "terms", "Terms", { grow: true })),
      h("div", { class: "row" },
        field(p, "rate", "Hourly rate (USD)", { type: "number", min: "0", step: "0.01", style: { width: "130px" } }),
        field(p, "max_hours", "Monthly maximum (hours)", { type: "number", min: "0", step: "0.5", style: { width: "130px" } }),
        field(p, "retainer_label", "Retainer line ({date} becomes e.g. 30-Sep-26)", { grow: true })),
      area(p, "intro", "Introduction (under the header, up to 3 printed lines)", 2),
      area(p, "remittance", "Remittance lines (one per line, printed under the billing summary)", 3,
        "These are plain text: update them too if your phone or email changes.")),
    h("h2", { class: "mt" }, "Sections"),
    h("p", { class: "muted" }, "Every project and task billed in a period must belong to exactly one group, or the invoice refuses to build and names what's missing."),
    secBox,
    h("h2", { class: "mt" }, "Credits and discounts"),
    credBox,
    h("div", { class: "panel pad prof-form mt" }, h("h2", {}, "Credit note"),
      field(p.credit_note, "head", "Heading"),
      area(p.credit_note, "text", "Text (printed after the remittance lines when an invoice has credits)", 4)),
    h("div", { class: "savebar" }, saveBtn,
      h("button", { onclick: preview, title: "Your newest draft (or latest invoice) with these settings, unsaved" }, "Preview PDF"),
      h("button", { onclick: () => route() }, "Discard changes")));
  paintSections();
  paintCredits();
}

async function pageInvoices(root, params) {
  if (params.id === "settings") return invoiceSettings(root);
  if (params.id) return invoiceEditor(root, params.id);
  const { invoices } = await GET("/api/invoices");
  root.append(h("div", { class: "row", style: { marginBottom: "16px" } },
    h("h1", { class: "grow", style: { margin: 0 } }, "Invoices"),
    h("a", { class: "btn", href: "#/invoices/settings" }, "Invoice settings"),
    h("button", { class: "primary", onclick: () => { location.hash = "#/invoices/new"; } }, "New invoice")));
  if (!invoices.length) {
    root.append(h("div", { class: "panel empty" }, "No invoices yet. Start one with New invoice; it stays a draft, rebuilt from your entries, until you issue it."));
    return;
  }
  root.append(h("div", { class: "panel" }, h("table", { class: "t" },
    h("thead", {}, h("tr", {}, ["Number", "Service period", "Invoice date", "Hours", "Total", "Net payable", "Status", ""]
      .map((x, i) => h("th", { class: i >= 3 && i <= 5 ? "num" : "" }, x)))),
    h("tbody", {}, invoices.map((x) => h("tr", { class: "click" + (x.status === "void" ? " archived" : ""), onclick: () => { location.hash = `#/invoices/${x.id}`; } },
      h("td", {}, h("b", {}, x.number)),
      h("td", {}, `${isoNice(x.period_from)} – ${isoNice(x.period_to)}`),
      h("td", {}, isoNice(x.issued)),
      x.error ? h("td", { colspan: 3, class: "err-text" }, x.error)
        : [h("td", { class: "num" }, hrs(x.hours)), h("td", { class: "num" }, money(x.total)), h("td", { class: "num" }, h("b", {}, money(x.net)))],
      h("td", {}, statusChip(x.status)),
      h("td", {}, h("a", { class: "btn", href: pdfUrl(x.id), target: "_blank", rel: "noopener", onclick: (e) => e.stopPropagation() }, "PDF"))))))));
}

async function invoiceEditor(root, idParam) {
  const isNew = idParam === "new";
  const [{ defaults: d }, rec] = await Promise.all([GET("/api/invoices"), isNew ? null : GET(`/api/invoices/${idParam}`)]);
  const id = rec ? rec.id : null;
  const editable = isNew || rec.status === "draft";
  const v = rec
    ? { number: rec.number, issued: rec.issued, period_from: rec.period_from, period_to: rec.period_to,
        retainer: rec.retainer || "", retainer_date: rec.retainer_date || "", credit_pcts: [...rec.credit_pcts], note: rec.note }
    : { number: "", issued: d.issued, period_from: d.period_from, period_to: d.period_to,
        retainer: d.retainer || "", retainer_date: d.retainer ? d.period_to : "", credit_pcts: [...d.credit_pcts], note: "" };
  let dirty = false;
  if (editable && v.credit_pcts.length !== d.credits.length) {
    v.credit_pcts = d.credits.map((c, i) => v.credit_pcts[i] ?? c.pct);
    dirty = !isNew;
  }
  const body = () => ({ ...v, id, retainer: v.retainer === "" ? 0 : +v.retainer, credit_pcts: v.credit_pcts.map(Number) });

  const previewBox = h("div");
  const paintPreview = (inv, error) => {
    if (error || !inv) { previewBox.replaceChildren(h("div", { class: "panel pad err-text" }, error || "Nothing to show.")); return; }
    const adj = inv.credits.reduce((a, c) => a + c.amount, 0) + (inv.retainer ? inv.retainer.amount : 0);
    const amt = (hours) => money(hours * inv.rate);
    previewBox.replaceChildren(
      h("div", { class: "cards two" },
        h("div", { class: "panel card" }, h("div", { class: "k" }, "Hours billed"), h("div", { class: "v" }, inv.hours.toFixed(2)), h("div", { class: "k" }, `${inv.days} days worked`)),
        h("div", { class: "panel card" }, h("div", { class: "k" }, "Services"), h("div", { class: "v" }, money(inv.total)), h("div", { class: "k" }, `at ${money(inv.rate)}/h`)),
        h("div", { class: "panel card" }, h("div", { class: "k" }, "Credits and retainer"), h("div", { class: "v" }, smoney(adj)), h("div", { class: "k" }, `${inv.credits.length} credit${inv.credits.length === 1 ? "" : "s"}${inv.retainer ? " + retainer" : ""}`)),
        h("div", { class: "panel card net" }, h("div", { class: "k" }, "Net payable"), h("div", { class: "v" }, money(inv.net)), h("div", { class: "k" }, v.number || "no number yet"))),
      inv.left_out && inv.left_out.length ? h("p", { class: "muted" }, "Not on this invoice, as it belongs to another client: " + inv.left_out.map(([c, x]) => `${c} ${hrs(x)}`).join(", ") + ".") : null,
      h("div", { class: "panel mt" }, h("table", { class: "t" },
        h("thead", {}, h("tr", {}, h("th", {}, "Billing summary"), h("th", { class: "num" }, "Lines"), h("th", { class: "num" }, "Hours"), h("th", { class: "num" }, "Amount"))),
        h("tbody", {},
          inv.sections.map((s) => [
            h("tr", { class: "sec" }, h("td", { colspan: 4 }, s.heading)),
            s.groups.map((g) => h("tr", { class: "sub" }, h("td", {}, g.label || g.subtotal), h("td", { class: "num" }, g.lines), h("td", { class: "num" }, hrs(g.hours)), h("td", { class: "num" }, amt(g.hours))))]),
          h("tr", { class: "strong" }, h("td", { colspan: 2 }, "Subtotal"), h("td", { class: "num" }, hrs(inv.hours)), h("td", { class: "num" }, money(inv.subtotal))),
          inv.credits.map((c) => h("tr", {}, h("td", { colspan: 2 }, c.label), h("td", { class: "num muted" }, hrs(c.hours)), h("td", { class: "num neg" }, smoney(c.amount)))),
          inv.retainer ? h("tr", {}, h("td", { colspan: 3 }, inv.retainer.label), h("td", { class: "num neg" }, smoney(inv.retainer.amount))) : null),
        h("tfoot", {}, h("tr", {}, h("td", { colspan: 3 }, "Net payable"), h("td", { class: "num" }, money(inv.net)))))));
  };

  let timer = 0, seq = 0;
  const refresh = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const n = ++seq;
      try { const inv = await api("POST", "/api/invoices/preview", { ...body(), number: v.number || "(draft)" }); if (n === seq) paintPreview(inv); }
      catch (e) { if (n === seq) paintPreview(null, e.message); }
    }, 300);
  };
  const changed = () => { dirty = true; saveBtn && (saveBtn.disabled = false); refresh(); };

  const inp = (k, attrs = {}) => {
    const i = h("input", { value: v[k], ...attrs, disabled: !editable });
    i.addEventListener("input", () => { v[k] = i.value; changed(); });
    return i;
  };
  const from = inp("period_from", { type: "date" }), to = inp("period_to", { type: "date" });
  const setPeriod = (a, b) => { v.period_from = from.value = a; v.period_to = to.value = b; changed(); };
  const lastStart = addDays(d.issued.slice(0, 8) + "01", -1).slice(0, 8) + "01";
  const thisStart = d.issued.slice(0, 8) + "01";

  const save = async () => {
    const out = isNew ? await api("POST", "/api/invoices", body()) : await api("PATCH", `/api/invoices/${id}`, body());
    dirty = false;
    return out;
  };
  const act = (fn) => async () => { try { await fn(); } catch (e) { fail(e); } };
  let saveBtn = null;
  const buttons = [];
  if (isNew) {
    buttons.push(h("button", { class: "primary", onclick: act(async () => { const o = await save(); toast(`Draft ${o.number} saved`); location.hash = `#/invoices/${o.id}`; }) }, "Save draft"));
  } else if (rec.status === "draft") {
    saveBtn = h("button", { disabled: !dirty, onclick: act(async () => { await save(); saveBtn.disabled = true; toast("Draft saved"); }) }, "Save draft");
    buttons.push(saveBtn,
      h("button", { onclick: act(async () => { if (dirty) { await save(); saveBtn.disabled = true; } window.open(pdfUrl(id), "_blank", "noopener"); }) }, "Open draft PDF"),
      h("button", { class: "primary", onclick: act(async () => {
        if (dirty) { await save(); saveBtn.disabled = true; }
        const cur = await GET(`/api/invoices/${id}`);
        if (cur.error) throw new Error(cur.error);
        const inv = cur.invoice;
        const ok = await ask(`Issue ${cur.number}?`,
          `${hrs(inv.hours)}, net payable ${money(inv.net)}. The invoice and its PDF are stored as they are now and can never be edited again; a mistake can only be voided.`, "Issue invoice");
        if (ok === null) return;
        await api("POST", `/api/invoices/${id}/issue`);
        toast(`${cur.number} issued`);
        route();
      }) }, "Issue…"),
      h("span", { style: { flexBasis: "100%" } }),
      h("button", { class: "link del", onclick: act(async () => {
        if (await ask(`Delete draft ${rec.number}?`, "Only the draft's settings are deleted; no time entries change.", "Delete draft", { danger: true }) === null) return;
        await api("DELETE", `/api/invoices/${id}`);
        toast("Draft deleted");
        location.hash = "#/invoices";
      }) }, "Delete draft"));
  } else {
    buttons.push(h("a", { class: "btn primary-link", href: pdfUrl(id), target: "_blank", rel: "noopener" }, "Open PDF"),
      h("a", { class: "btn", href: pdfUrl(id, true) }, "Download PDF"));
    if (rec.status === "issued") buttons.push(h("span", { class: "grow" }), h("button", { onclick: act(async () => {
      const reason = await ask(`Void ${rec.number}?`, "Voiding is final. The stored PDF stays available, marked void in the list, and the period can be invoiced again.", "Void invoice", { danger: true, reason: "Reason (kept in the audit log)" });
      if (reason === null) return;
      await api("POST", `/api/invoices/${id}/void`, { reason });
      toast(`${rec.number} voided`);
      route();
    }) }, "Void…"));
  }

  const notes = [];
  if (rec && rec.status !== "draft") {
    notes.push(h("p", { class: "muted" }, `Issued ${rec.issued_utc ? isoNice(localDate(rec.issued_utc)) + " " + timeOf(rec.issued_utc) : ""}${rec.status === "void" ? ` · voided ${isoNice(localDate(rec.voided_utc))} ${timeOf(rec.voided_utc)}` : ""}. Stored exactly as sent; the figures below are the snapshot, not today's entries.`));
    if (rec.live_hours !== undefined && Math.abs(rec.live_hours - rec.invoice.hours) > 0.001)
      notes.push(h("div", { class: "panel pad warn" }, `The entries for this period now add up to ${hrs(rec.live_hours)}, not the ${hrs(rec.invoice.hours)} invoiced. Entries were changed after issue; the invoice was not.`));
  }

  const pcts = d.credits.map((c, i) => {
    const el = h("input", { type: "number", min: "0", max: "100", step: "1", value: v.credit_pcts[i], disabled: !editable, style: { width: "80px" } });
    el.addEventListener("input", () => { v.credit_pcts[i] = el.value; changed(); });
    return h("label", { class: "f" }, `Credit on ${c.scope} (%)`, el);
  });
  const noteEl = h("textarea", { rows: 2, disabled: !editable, placeholder: "Internal; never printed" });
  noteEl.value = v.note;
  noteEl.addEventListener("input", () => { v.note = noteEl.value; changed(); });

  root.append(
    h("p", { class: "back" }, h("a", { href: "#/invoices" }, ico("left"), "Invoices")),
    h("div", { class: "row", style: { marginBottom: "12px" } },
      h("h1", { style: { margin: 0 } }, isNew ? "New invoice" : rec.number), rec ? statusChip(rec.status) : statusChip("draft")),
    notes,
    h("div", { class: "inv-grid" },
      h("div", { class: "panel pad inv-form" },
        h("label", { class: "f" }, "Invoice number", inp("number", { placeholder: "2026-001", required: true }),
          d.last_number && isNew ? h("span", { class: "hint" }, `Last: ${d.last_number}`) : null),
        h("label", { class: "f" }, "Invoice date", inp("issued", { type: "date" })),
        h("div", { class: "f" }, "Service period",
          h("div", { class: "row" }, from, h("span", { class: "muted" }, "to"), to),
          editable ? h("div", { class: "row" },
            h("button", { class: "link", type: "button", onclick: () => setPeriod(lastStart, monthEnd(lastStart)) }, "Last month"),
            h("button", { class: "link", type: "button", onclick: () => setPeriod(thisStart, monthEnd(thisStart)) }, "This month")) : null),
        h("div", { class: "row" },
          h("label", { class: "f" }, "Retainer paid (USD)", inp("retainer", { type: "number", min: "0", step: "0.01", placeholder: "0", style: { width: "120px" } })),
          h("label", { class: "f" }, "Paid on", inp("retainer_date", { type: "date" }))),
        h("div", { class: "row" }, pcts),
        h("label", { class: "f" }, "Note", noteEl),
        h("div", { class: "row mt" }, buttons)),
      h("div", {}, previewBox)));

  if (rec && rec.status !== "draft") paintPreview(rec.invoice);
  else if (rec && rec.error) { paintPreview(null, rec.error); }
  else if (rec) paintPreview(rec.invoice);
  else refresh();
}

// ---------------------------------------------------------------- settings
async function pageSettings(root) {
  const s = { ...S.meta.settings };
  const field = (k, label, el) => { el.value = s[k]; el.addEventListener("change", () => { s[k] = el.value; }); return h("label", { class: "f" }, label, el); };
  const deleted = await GET("/api/deleted");
  const opt = (pairs) => pairs.map(([v, l]) => h("option", { value: v }, l));
  root.classList.add("settings");
  root.append(h("h1", {}, "Settings"),
    h("form", { class: "panel pad settings-form", style: { display: "grid", gap: "12px" }, onsubmit: async (e) => {
      e.preventDefault();
      try { S.meta.settings = await api("PUT", "/api/settings", s); toast("Settings saved"); route(); } catch (x) { fail(x); }
    } },
      h("div", { class: "row" }, field("user_name", "Name", h("input", {})), field("user_email", "Email (used in exports)", h("input", { type: "email" }))),
      h("div", { class: "row" }, field("timezone", "Time zone", h("input", { list: "tzs" })), field("default_rate", "Default hourly rate (USD)", h("input", { type: "number", step: "0.01", min: "0" }))),
      h("datalist", { id: "tzs" }, (Intl.supportedValuesOf ? Intl.supportedValuesOf("timeZone") : []).map((z) => h("option", { value: z }))),
      h("div", { class: "row" },
        field("week_start", "Week starts on", h("select", {}, opt([["1", "Monday"], ["7", "Sunday"], ["6", "Saturday"]]))),
        field("time_format", "Time format", h("select", {}, opt([["12", "12-hour (1:30 PM)"], ["24", "24-hour (13:30)"]]))),
        field("duration_format", "Duration format", h("select", {}, opt([["hms", "h:mm:ss"], ["decimal", "Decimal hours"]])))),
      h("div", {}, h("button", { class: "primary", type: "submit" }, "Save settings"))),
    h("h2", {}, "Backup"),
    h("div", { class: "panel pad row" }, h("span", { class: "grow muted" }, "Copies the database to the backups/ folder beside it, using SQLite's backup API (safe while the app is running)."),
      h("button", { onclick: async () => { try { const r = await api("POST", "/api/backup"); toast("Saved " + r.path); } catch (x) { fail(x); } } }, "Back up now")),
    h("h2", {}, "Recently deleted entries"),
    h("div", { class: "panel" }, deleted.length ? h("table", { class: "t" }, h("tbody", {}, deleted.map((d) => h("tr", {},
      h("td", { class: "wrap" }, d.description || h("span", { class: "muted" }, "(no description)")),
      h("td", { class: "muted" }, localDate(d.start_utc)),
      h("td", { class: "num" }, d.end_utc ? hms(secsBetween(d.start_utc, d.end_utc)) : "running"),
      h("td", {}, h("button", { class: "link", onclick: async () => { try { await api("POST", `/api/restore/${d.audit_id}`); toast("Restored"); await reloadMeta(); route(); } catch (x) { fail(x); } } }, "Restore"))))))
      : h("div", { class: "pad muted" }, "Nothing deleted.")),
    h("h2", {}, "Importing"),
    h("div", { class: "panel pad muted" },
      h("p", {}, "From a CSV, one row per entry; the columns are listed in README.md (idempotent; re-running adds nothing):"),
      h("pre", { class: "cmd" }, "python -m dutemps import-csv entries.csv")));
}

// ---------------------------------------------------------------- router
const PAGES = { tracker: pageTracker, timesheet: pageTimesheet, calendar: pageCalendar, dashboard: pageDashboard,
  reports: pageReports, invoices: pageInvoices, projects: pageProjects, clients: pageClients, tags: pageTags, settings: pageSettings };

let lastPath = null;
async function route() {
  closePop();
  const [path, query] = (location.hash.slice(2) || "tracker").split("?");
  const moved = path !== lastPath;
  lastPath = path;
  const [name, id] = path.split("/");
  const params = Object.fromEntries(new URLSearchParams(query || ""));
  if (id) params.id = id;
  const page = PAGES[name] || pageTracker;
  document.querySelectorAll(".side nav a").forEach((a) => a.classList.toggle("on", a.dataset.page === (PAGES[name] ? name : "tracker")));
  const root = h("div");
  try {
    await page(root, params);
    $("#page").replaceChildren(root);
    if (moved) window.scrollTo(0, 0);
  } catch (e) {
    $("#page").replaceChildren(h("div", { class: "panel empty" }, "Could not load this page: " + e.message));
  }
}
window.addEventListener("hashchange", route);

const NAV_ICONS = { tracker: "clock", timesheet: "table", calendar: "calendar", dashboard: "layout", reports: "chart",
  invoices: "file", projects: "folder", clients: "users", tags: "tag", settings: "sliders" };
function decorateChrome() {
  document.querySelectorAll(".side nav a").forEach((a) => a.prepend(ico(NAV_ICONS[a.dataset.page])));
  $(".brand").prepend(h("span", { class: "mark", "aria-hidden": "true" }, ico("mark")));
}

(async function boot() {
  decorateChrome();
  try {
    await reloadMeta();
    await route();
  } catch (e) {
    document.body.replaceChildren(h("div", { class: "empty" }, "DuTemps server is not reachable: " + e.message));
  }
})();
