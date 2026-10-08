// LeadForge UI. Every value from the store is provider text: it is only ever set as
// textContent (through h()), never parsed as HTML.
"use strict";

// Tabs a host page adds before this script runs: window.leadforgeTabs = [{ key, label,
// view }]. They come first and the first is the default tab; a view is called as
// view({ state, setTab, reload }) on every render and returns the node to show.
const extraTabs = window.leadforgeTabs || [];
const wantedTab = new URLSearchParams(location.search).get("tab");

const state = {
  store: localStorage.getItem("lf.store") || "demo",
  tab: wantedTab || (extraTabs[0] && extraTabs[0].key) || localStorage.getItem("lf.tab") || "overview",
  reveal: false,
  leads: null, leadsError: null,
  runs: null, runsError: null,
  score: null, scoreError: null,
  sources: null,
  job: null,
  filter: { q: "", status: "", flag: "", scenario: "" },
  sort: { key: "name", dir: 1 },
  scenario: null,
  faults: false, fresh: true,
};

// ------------------------------------------------------------------ helpers

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style") el.setAttribute("style", v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const $ = (sel) => document.querySelector(sel);
const text = (v) => (v && typeof v === "object" && "value" in v ? v.value : v);
const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString() : "-");
const pct = (a, b) => (b ? Math.round((100 * a) / b) : 0);

class ApiError extends Error {
  constructor(status, detail) { super(detail); this.status = status; }
}

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, body.detail || res.statusText);
  return body;
}

function badge(label, kind) { return h("span", { class: `badge b-${kind}` }, label); }

function emailBadge(status) {
  const kind = { verified: "good", invalid: "bad", unverified: "warn", accept_all: "warn" }[status] || "muted";
  return badge(status, kind);
}

function currentJob(lead) {
  const emp = lead.employments || [];
  return emp.find((e) => e.is_current === true) || emp[0] || null;
}

function bars(rows, total) {
  const max = Math.max(1, ...rows.map((r) => r[1]));
  return h("div", { class: "bars" }, rows.map(([label, n]) =>
    h("div", { class: "row" },
      h("div", { title: label }, label),
      h("div", { class: "track" }, h("div", { class: "fill", style: `width:${(100 * n) / max}%` })),
      h("div", { class: "n" }, total ? `${n} · ${pct(n, total)}%` : n))));
}

function tile(value, label) {
  return h("div", { class: "tile" }, h("div", { class: "v" }, value), h("div", { class: "k" }, label));
}

function countBy(items, fn) {
  const m = new Map();
  for (const it of items) for (const k of [].concat(fn(it))) if (k) m.set(k, (m.get(k) || 0) + 1);
  return [...m.entries()].sort((a, b) => b[1] - a[1]);
}

// ------------------------------------------------------------------ data

async function loadAll() {
  state.leads = state.runs = state.score = null;
  state.leadsError = state.runsError = state.scoreError = null;
  render();
  const q = `reveal=${state.reveal}&include_retired=true`;
  const tasks = [
    api(`/api/${state.store}/leads?${q}`).then((b) => { state.leads = b.leads; },
      (e) => { state.leadsError = e; state.leads = []; }),
    api(`/api/${state.store}/runs`).then((b) => { state.runs = b.runs; },
      (e) => { state.runsError = e; state.runs = []; }),
  ];
  if (state.store === "demo") {
    tasks.push(api(`/api/demo/scorecard?reveal=${state.reveal}`).then((b) => { state.score = b; },
      (e) => { state.scoreError = e; }));
  }
  if (!state.sources) {
    tasks.push(api("/api/sources").then((b) => { state.sources = b.sources; }, () => { state.sources = []; }));
  }
  await Promise.all(tasks);
  render();
}

async function pollJob() {
  try {
    const { job } = await api("/api/jobs/current");
    const was = state.job && state.job.state;
    state.job = job;
    renderJob();
    renderActions();
    if (job && job.state === "running") setTimeout(pollJob, 800);
    else if (was === "running") loadAll();
  } catch (e) {
    setTimeout(pollJob, 3000);
  }
}

async function startJob(kind, extra = {}) {
  try {
    const { job } = await api("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-LeadForge": "1" },
      body: JSON.stringify({ kind, ...extra }),
    });
    state.job = job;
    renderJob();
    renderActions();
    pollJob();
  } catch (e) {
    alert(e.message);
  }
}

function confirmIngest() {
  const live = (state.sources || []).filter((s) => s.enabled && s.resolved_mode === "live");
  const lines = (state.sources || []).filter((s) => s.enabled)
    .map((s) => `  ${s.name}: ${s.resolved_mode}${s.missing_env.length ? ` (missing ${s.missing_env.join(", ")})` : ""}`);
  const warn = live.length
    ? `\n\n${live.length} source(s) run LIVE and may spend provider credits.`
    : "\n\nEvery source runs synthetic: no credits are spent.";
  return confirm(`Run ingestion into your store?\n\n${lines.join("\n")}${warn}`);
}

// ------------------------------------------------------------------ chrome

function renderActions() {
  const busy = state.job && state.job.state === "running";
  const box = $("#actions");
  box.replaceChildren();
  if (state.store === "demo") {
    box.append(
      h("label", { title: "Delete .leadforge/demo.db first" },
        h("input", { type: "checkbox", checked: state.fresh, onchange: (e) => { state.fresh = e.target.checked; } }), "fresh"),
      h("label", { title: "Also serve a scripted SerpApi failure" },
        h("input", { type: "checkbox", checked: state.faults, onchange: (e) => { state.faults = e.target.checked; } }), "faults"),
      h("button", { class: "btn primary", disabled: busy, onclick: () => startJob("demo_run", { fresh: state.fresh, faults: state.faults }) }, "Run demo"),
      h("button", { class: "btn", disabled: busy, title: "Rewrite the provider tables and answer key from the fixed seed",
        onclick: () => startJob("demo_generate") }, "Regenerate data"));
  } else {
    box.append(h("button", { class: "btn primary", disabled: busy,
      onclick: () => { if (confirmIngest()) startJob("ingest"); } }, "Run ingestion"));
  }
}

function renderJob() {
  const box = $("#job");
  const job = state.job;
  if (!job) { box.classList.add("hidden"); return; }
  box.className = `job ${job.state}`;
  const title = { ingest: "Ingestion", demo_run: "Demo run", demo_generate: "Generate demo data" }[job.kind];
  const head = h("div", {},
    job.state === "running" ? h("span", { class: "spinner" }) : null,
    h("strong", {}, `${title}: ${job.state}`),
    job.exit_code !== null ? h("span", { class: "muted" }, ` · exit ${job.exit_code}`) : null,
    h("span", { class: "muted" }, ` · started ${fmtTime(job.started_at)}`),
    job.state !== "running" ? h("button", { class: "btn", style: "float:right", onclick: () => box.classList.add("hidden") }, "Dismiss") : null);
  const parts = [head];
  if (job.error) parts.push(h("div", { class: "error" }, job.error));
  if (job.summary) parts.push(h("pre", { class: "mono" }, job.summary));
  if (job.report) parts.push(h("details", {}, h("summary", {}, "Run report"), h("pre", { class: "mono" }, job.report)));
  if (job.files && job.files.length) parts.push(h("pre", { class: "mono" }, job.files.join("\n")));
  box.replaceChildren(...parts);
}

function renderChrome() {
  for (const b of document.querySelectorAll("#store-switch button")) b.classList.toggle("on", b.dataset.store === state.store);
  for (const b of document.querySelectorAll("#tabs button")) {
    b.classList.toggle("on", b.dataset.tab === state.tab);
    b.disabled = b.dataset.tab === "scorecard" && state.store !== "demo";
  }
  $("#reveal").checked = state.reveal;
  renderActions();
}

function setTab(key) {
  state.tab = key;
  localStorage.setItem("lf.tab", key);
  render();
}

function render() {
  if (state.tab === "scorecard" && state.store !== "demo") state.tab = "overview";
  renderChrome();
  const view = $("#view");
  const extra = extraTabs.find((t) => t.key === state.tab);
  const fn = extra
    ? () => extra.view({ state, setTab, reload: loadAll })
    : { overview: viewOverview, leads: viewLeads, runs: viewRuns, scorecard: viewScorecard }[state.tab] || viewOverview;
  view.replaceChildren(fn());
}

function emptyStore(err) {
  if (err && err.status === 409) {
    return h("div", { class: "empty" },
      h("h2", {}, "No data in this store yet"),
      h("p", {}, state.store === "demo"
        ? "Run the demo: every source runs synthetic on the 40-company, 250-person dataset."
        : "Run an ingestion. Sources without keys in .env run synthetic on sample data."),
      h("button", { class: "btn primary", onclick: () => state.store === "demo"
        ? startJob("demo_run", { fresh: state.fresh, faults: state.faults })
        : (confirmIngest() && startJob("ingest")) }, state.store === "demo" ? "Run demo" : "Run ingestion"));
  }
  return h("div", { class: "empty error" }, err ? err.message : "Failed to load");
}

const loading = () => h("div", { class: "empty" }, h("span", { class: "spinner" }), "Loading…");

// ------------------------------------------------------------------ overview

function viewOverview() {
  if (state.leads === null) return loading();
  if (state.leadsError) return emptyStore(state.leadsError);
  const all = state.leads;
  const active = all.filter((l) => !l.retired_at);
  const L = active.map((l) => l.lead);
  const n = active.length;
  const tiles = h("div", { class: "tiles" },
    tile(n, "active leads"),
    tile(all.length - n, "retired (merged away)"),
    tile(`${pct(L.filter((l) => l.email_status === "verified").length, n)}%`, "verified email"),
    tile(`${pct(L.filter((l) => l.email).length, n)}%`, "have an email"),
    tile(`${pct(L.filter((l) => l.linkedin_url).length, n)}%`, "have LinkedIn"),
    tile(L.filter((l) => l.opt_out).length, "opted out"),
    tile(L.filter((l) => l.suppressed).length, "suppressed"),
    tile(L.filter((l) => l.email_is_role_address).length, "role address only"),
    tile(active.filter((l) => l.primary_domain_flagged).length, "flagged domain ties"),
    tile(active.filter((l) => l.contributing_sources.length > 1).length, "multi-source leads"),
    state.score ? tile(`${pct(state.score.checks_passed, state.score.checks_passed + state.score.checks_failed)}%`, "scorecard checks passed") : null);

  const companies = countBy(active, (l) => { const j = currentJob(l.lead); return j && (j.company.name || j.company.domains[0]); }).slice(0, 12);
  const panels = h("div", { class: "grid g2" },
    h("div", { class: "panel" }, h("h3", {}, "Email status"), bars(countBy(L, (l) => l.email_status), n)),
    h("div", { class: "panel" }, h("h3", {}, "Sources behind each lead"), bars(countBy(active, (l) => l.contributing_sources), n)),
    h("div", { class: "panel" }, h("h3", {}, "Top companies"), bars(companies)),
    h("div", { class: "panel" }, h("h3", {}, "Tech signals"), bars(countBy(L, (l) => [...new Set((l.tech_signals || []).map((s) => s.label))]).slice(0, 12))),
    latestRunPanel(),
    sourcesPanel());
  return h("div", {}, tiles, panels);
}

function latestRunPanel() {
  const run = state.runs && state.runs[0];
  if (!run) return h("div", { class: "panel" }, h("h3", {}, "Latest run"), h("div", { class: "muted" }, "No runs yet."));
  return h("div", { class: "panel" },
    h("h3", {}, "Latest run ", statusBadge(run)),
    h("div", { class: "muted" }, `${fmtTime(run.started_at)} · ${run.leads_merged ?? "-"} leads merged · ${run.leads_retired ?? 0} retired`),
    sourceTable(run.sources, true));
}

function sourcesPanel() {
  if (state.store === "demo") {
    return h("div", { class: "panel" }, h("h3", {}, "Source modes"),
      h("p", { class: "muted" }, "The demo runs every source synthetic on the provider-shaped tables in demo/data/. Keys in .env are never used."));
  }
  const rows = (state.sources || []).map((s) => h("tr", {},
    h("td", {}, s.name),
    h("td", {}, s.enabled ? badge(s.resolved_mode, s.resolved_mode === "live" ? "warn" : "muted") : badge("disabled", "muted")),
    h("td", { class: "muted" }, s.missing_env.length ? `missing ${s.missing_env.join(", ")}` : "keys set")));
  return h("div", { class: "panel" }, h("h3", {}, "Source modes for the next ingestion"),
    h("table", {}, h("tbody", {}, rows)),
    h("p", { class: "muted" }, "A source goes live once all its keys are set in .env (or LEADFORGE_MODE forces a mode)."));
}

// ------------------------------------------------------------------ leads

function leadScenario() {
  const m = new Map();
  for (const p of (state.score && state.score.persons) || []) if (p.lead_id) m.set(p.lead_id, p);
  return m;
}

function filteredLeads() {
  const f = state.filter;
  const q = f.q.trim().toLowerCase();
  const scen = leadScenario();
  let rows = state.leads.filter((s) => {
    const l = s.lead;
    if (f.flag !== "retired" && s.retired_at) return false;
    if (f.status && l.email_status !== f.status) return false;
    if (f.flag === "opt_out" && !l.opt_out) return false;
    if (f.flag === "suppressed" && !l.suppressed) return false;
    if (f.flag === "role" && !l.email_is_role_address) return false;
    if (f.flag === "tie" && !s.primary_domain_flagged) return false;
    if (f.flag === "retired" && !s.retired_at) return false;
    if (f.flag === "no_email" && l.email) return false;
    if (f.flag === "multi_job" && (l.employments || []).filter((e) => e.is_current).length < 2) return false;
    if (f.scenario && (scen.get(s.lead_id) || {}).scenario !== f.scenario) return false;
    if (q) {
      const j = currentJob(l);
      const hay = [l.full_name, l.email, j && j.company.name, j && j.title, ...(j ? j.company.domains : [])].join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  const key = {
    name: (s) => (s.lead.full_name || "").toLowerCase(),
    company: (s) => { const j = currentJob(s.lead); return ((j && (j.company.name || j.company.domains[0])) || "").toLowerCase(); },
    status: (s) => s.lead.email_status,
    sources: (s) => s.contributing_sources.length,
    signals: (s) => (s.lead.tech_signals || []).length + (s.lead.intent_signals || []).length,
  }[state.sort.key];
  rows = rows.slice().sort((a, b) => (key(a) > key(b) ? 1 : key(a) < key(b) ? -1 : 0) * state.sort.dir);
  return rows;
}

function viewLeads() {
  if (state.leads === null) return loading();
  if (state.leadsError) return emptyStore(state.leadsError);
  const f = state.filter;
  const scen = leadScenario();
  const sel = (value, options, onchange) => h("select", { onchange },
    options.map(([v, label]) => h("option", { value: v, selected: v === value }, label)));
  const statuses = [...new Set(state.leads.map((s) => s.lead.email_status))].sort();
  const search = h("input", { type: "search", placeholder: "Search name, company, email, title", value: f.q });
  search.addEventListener("input", (e) => { f.q = e.target.value; refreshTable(); });
  const toolbar = h("div", { class: "toolbar" },
    search,
    sel(f.status, [["", "Any email status"], ...statuses.map((s) => [s, s])], (e) => { f.status = e.target.value; refreshTable(); }),
    sel(f.flag, [["", "Any flag"], ["opt_out", "Opted out"], ["suppressed", "Suppressed"], ["role", "Role address"],
      ["no_email", "No email"], ["tie", "Flagged domain tie"], ["multi_job", "Two current jobs"], ["retired", "Retired only"]],
    (e) => { f.flag = e.target.value; refreshTable(); }),
    state.score ? sel(f.scenario, [["", "Any scenario"], ...state.score.scenarios.map((s) => [s.name, s.name])],
      (e) => { f.scenario = e.target.value; refreshTable(); }) : null,
    h("span", { class: "muted", id: "lead-count" }),
    // The whole store's active leads (not just the filtered rows), masked as shown.
    h("a", { class: "btn", download: "", href: `/api/${state.store}/leads.xlsx?reveal=${state.reveal}`,
      title: "Every active lead in this store, as an Excel workbook" }, "Download Excel"));
  const wrap = h("div", { class: "panel" }, h("div", { id: "lead-table" }));
  const container = h("div", {}, toolbar, wrap);

  function th(label, key, cls) {
    const arrow = state.sort.key === key ? (state.sort.dir > 0 ? " ▲" : " ▼") : "";
    return h("th", { class: `sort ${cls || ""}`, onclick: () => {
      state.sort = { key, dir: state.sort.key === key ? -state.sort.dir : 1 }; refreshTable();
    } }, label + arrow);
  }

  function refreshTable() {
    const rows = filteredLeads();
    const shown = rows.slice(0, 500);
    const count = container.querySelector("#lead-count");
    count.textContent = `${rows.length} leads${rows.length > shown.length ? " (first 500 shown)" : ""}`;
    const table = h("table", {},
      h("thead", {}, h("tr", {}, th("Name", "name"), th("Company", "company"), h("th", {}, "Title"),
        h("th", {}, "Email"), th("Status", "status"), th("Sources", "sources"), th("Signals", "signals", "num"),
        h("th", {}, "Flags"), state.score ? h("th", {}, "Scenario") : null)),
      h("tbody", {}, shown.map((s) => {
        const l = s.lead;
        const j = currentJob(l);
        const p = scen.get(s.lead_id);
        const failed = p && p.checks.some((c) => c.outcome === false);
        return h("tr", { class: "click", onclick: () => openLead(s.lead_id) },
          h("td", {}, l.full_name || h("span", { class: "muted" }, "(no name)")),
          h("td", {}, j ? (j.company.name || j.company.domains[0] || "-") : "-"),
          h("td", { class: "muted" }, j && j.title ? j.title.slice(0, 60) : ""),
          h("td", { class: "mono" }, l.email || h("span", { class: "muted" }, "-")),
          h("td", {}, emailBadge(l.email_status)),
          h("td", {}, s.contributing_sources.map((x) => h("span", { class: "chip" }, x))),
          h("td", { class: "num" }, (l.tech_signals || []).length + (l.intent_signals || []).length),
          h("td", {}, flagBadges(s)),
          state.score ? h("td", {}, p ? [h("span", { class: "chip" }, p.scenario), failed ? badge("check failed", "bad") : null] : h("span", { class: "muted" }, "-")) : null);
      })));
    container.querySelector("#lead-table").replaceChildren(table);
  }
  queueMicrotask(refreshTable);
  return container;
}

function flagBadges(s) {
  const l = s.lead;
  return [
    s.retired_at ? badge("retired", "muted") : null,
    l.opt_out ? badge("opt-out", "bad") : null,
    l.suppressed ? badge("suppressed", "bad") : null,
    l.email_is_role_address ? badge("role address", "warn") : null,
    s.primary_domain_flagged ? badge("domain tie", "warn") : null,
    s.stale ? badge("stale", "muted") : null,
  ];
}

// ------------------------------------------------------------------ lead drawer

async function openLead(id) {
  const drawer = $("#drawer");
  drawer.replaceChildren(loading());
  drawer.classList.remove("hidden");
  $("#scrim").classList.remove("hidden");
  try {
    const s = await api(`/api/${state.store}/leads/${id}?reveal=${state.reveal}`);
    drawer.replaceChildren(leadDetail(s));
  } catch (e) {
    drawer.replaceChildren(h("div", { class: "error" }, e.message));
  }
}

function closeDrawer() {
  $("#drawer").classList.add("hidden");
  $("#scrim").classList.add("hidden");
}

function strength(v) {
  return h("span", { class: "strength", title: v }, h("i", { style: `width:${Math.round(100 * Math.max(0, Math.min(1, v)))}%` }));
}

function leadDetail(s) {
  const l = s.lead;
  const kv = (rows) => h("div", { class: "kv" }, rows.filter(Boolean).flatMap(([k, v]) => [h("div", { class: "k" }, k), h("div", {}, v ?? "-")]));
  const person = leadScenario().get(s.lead_id);

  // Provenance state, as `leadforge leads show` prints it: first record of a path wins.
  const winners = new Set();
  const prov = s.provenance.map((p) => {
    let st;
    if (p.superseded) st = badge("superseded", "muted");
    else if (winners.has(p.canonical_path)) st = badge("agrees", "good");
    else { winners.add(p.canonical_path); st = badge(`winner · ${s.agreement[p.canonical_path] || 1} agree`, "good"); }
    return h("tr", {},
      h("td", { class: "mono" }, p.canonical_path),
      h("td", {}, p.source_name, " ", h("span", { class: "muted" }, p.data_mode)),
      h("td", {}, st),
      h("td", {}, p.confidence === null || p.confidence === undefined ? h("span", { class: "muted" }, "none") : [p.confidence, strength(p.confidence)]),
      h("td", { class: "muted" }, p.confidence_origin, p.untrusted ? " · untrusted" : ""));
  });

  return h("div", {},
    h("button", { class: "btn close", onclick: closeDrawer }, "Close"),
    h("h2", {}, l.full_name || "(no name)"),
    h("div", { class: "muted mono" }, s.lead_id),
    h("div", { style: "margin-top:8px" }, emailBadge(l.email_status), flagBadges(s)),

    person ? [h("h4", {}, "Answer key"), checksView(person)] : null,

    h("h4", {}, "Contact"),
    kv([
      ["Email", h("span", { class: "mono" }, l.email || "-")],
      ["Role address", l.email_is_role_address ? "yes (no personal address found)" : "no"],
      l.role_contact_emails.length ? ["Company contacts", h("span", { class: "mono" }, l.role_contact_emails.join(", "))] : null,
      ["LinkedIn", h("span", { class: "mono" }, l.linkedin_url || "-")],
      ["Opt-out / suppressed", `${l.opt_out ? "opted out" : "no"} / ${l.suppressed ? "suppressed" : "no"}`],
      s.retired_at ? ["Retired", `${fmtTime(s.retired_at)} → ${s.successor_ids.join(", ")}`] : null,
    ]),

    h("h4", {}, "Employment"),
    l.employments.length ? h("table", {}, h("tbody", {}, l.employments.map((e) => h("tr", {},
      h("td", {}, e.company.name || "-", h("div", { class: "muted mono" }, e.company.domains.join(", "))),
      h("td", {}, e.title || h("span", { class: "muted" }, "-")),
      h("td", {}, badge({ true: "current", false: "past" }[e.is_current] || "unknown", e.is_current ? "good" : "muted")))))) : h("div", { class: "muted" }, "none"),
    kv([["Primary domain", s.primary_domain ? [s.primary_domain, " ", h("span", { class: "muted" }, `[${s.primary_domain_source}]`), s.primary_domain_flagged ? badge("flagged tie", "warn") : null] : "-"]]),

    h("h4", {}, "Signals"),
    (l.tech_signals.length + l.intent_signals.length) ? h("div", {},
      l.tech_signals.map((x) => h("span", { class: "chip" }, "tech: ", x.label, strength(x.strength))),
      l.intent_signals.map((x) => h("span", { class: "chip" }, "intent: ", x.label, strength(x.strength)))) : h("div", { class: "muted" }, "none"),

    h("h4", {}, `Web evidence (${s.web_evidence.length})`),
    s.web_evidence.length ? s.web_evidence.map((w) => {
      const v = w.values;
      const title = text(v.title);
      const snippet = text(v.snippet);
      const link = text(v.link || v.url);
      return h("div", { class: "ev" },
        h("div", {}, badge(w.attachment.replaceAll("_", " "), w.attachment === "own_domain" ? "good" : "warn"),
          h("span", { class: "muted" }, `${w.source_name} · ${w.domains.join(", ")}`)),
        title ? h("div", { class: "title" }, title) : null,
        snippet ? h("div", { class: "muted" }, snippet) : null,
        link ? h("div", { class: "mono" }, link) : null);
    }) : h("div", { class: "muted" }, "none attached"),

    h("h4", {}, "Field provenance"),
    h("div", { class: "muted", style: "margin-bottom:6px" },
      `Sources: ${s.contributing_sources.join(", ") || "-"} · projection v${s.projection_version} · computed ${fmtTime(s.computed_at)}`),
    h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Field"), h("th", {}, "Source"), h("th", {}, "State"), h("th", {}, "Confidence"), h("th", {}, "Origin"))),
      h("tbody", {}, prov)));
}

function checksView(p) {
  return h("div", {},
    h("div", {}, h("span", { class: "chip" }, p.scenario), " ", h("span", { class: "muted" }, `${p.subject} · ${p.company}`)),
    h("div", { style: "margin-top:6px" }, p.checks.map((c) => h("span", {
      class: `chip ${c.outcome === true ? "pass" : c.outcome === false ? "fail" : "skip"}`,
      title: c.outcome === null ? "skipped: the run could not reach this check" : "",
    }, c.outcome === true ? "✓ " : c.outcome === false ? "✗ " : "– ", c.check))));
}

// ------------------------------------------------------------------ runs

function statusBadge(run) {
  const kind = run.status === "completed" && run.exit_code === 0 ? "good" : run.status === "running" ? "warn" : "bad";
  return badge(`${run.status}${run.exit_code !== null ? ` · exit ${run.exit_code}` : ""}`, kind);
}

function sourceTable(sources, compact) {
  return h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, "Source"), h("th", {}, "Mode"),
      h("th", { class: "num" }, "Calls ok/fail"), h("th", { class: "num" }, "Leads"),
      compact ? null : [h("th", { class: "num" }, "Contribs"), h("th", { class: "num" }, "Retries"), h("th", { class: "num" }, "429s"), h("th", { class: "num" }, "Credits")],
      h("th", {}, "Failure"))),
    h("tbody", {}, sources.map((s) => h("tr", {},
      h("td", {}, s.source_name),
      h("td", { title: s.mode_reason || "" }, badge(s.mode, s.mode === "live" ? "warn" : "muted")),
      h("td", { class: "num" }, `${s.succeeded ?? "-"}/${s.failed ?? "-"}`),
      h("td", { class: "num" }, s.leads_found),
      compact ? null : [h("td", { class: "num" }, s.contributions_written), h("td", { class: "num" }, s.retries),
        h("td", { class: "num" }, s.http_429_count), h("td", { class: "num" }, s.credits_consumed ?? "-")],
      h("td", {}, s.failure_class ? badge(s.failure_class, "bad") : h("span", { class: "muted" }, "-"))))));
}

function viewRuns() {
  if (state.runs === null) return loading();
  if (state.runsError) return emptyStore(state.runsError);
  if (!state.runs.length) return h("div", { class: "empty" }, "No runs yet.");
  return h("div", { class: "grid" }, state.runs.map((r) => {
    const dur = r.finished_at ? `${((new Date(r.finished_at) - new Date(r.started_at)) / 1000).toFixed(1)}s` : "-";
    return h("div", { class: "panel" },
      h("h3", {}, fmtTime(r.started_at), " ", statusBadge(r)),
      h("div", { class: "muted", style: "margin-bottom:8px" },
        `duration ${dur} · ${r.leads_merged ?? "-"} leads merged · ${r.leads_retired ?? 0} retired · ${r.primary_domain_ties_flagged ?? 0} flagged ties`,
        r.failure_reason ? h("span", { class: "error" }, ` · ${r.failure_reason}`) : null,
        h("span", { class: "mono" }, ` · ${r.run_id}`)),
      sourceTable(r.sources, false));
  }));
}

// ------------------------------------------------------------------ scorecard

function viewScorecard() {
  if (state.score === null && !state.scoreError) return loading();
  if (state.scoreError) return emptyStore(state.scoreError);
  const c = state.score;
  const total = c.checks_passed + c.checks_failed;
  const tiles = h("div", { class: "tiles" },
    tile(`${c.checks_passed}/${total}`, `checks passed (${pct(c.checks_passed, total)}%)`),
    tile(c.checks_failed, "checks failed"),
    tile(`${c.leads_active} / ${c.leads_expected}`, "leads active / expected"),
    tile(c.leads_without_subject, "leads matching no person"),
    tile(c.shared_address_leads, "leads sharing an address"),
    tile(`${c.companies_searched}/${c.companies}`, "companies web-searched"));

  const failures = Object.entries(c.check_failures).sort((a, b) => b[1] - a[1]);
  const reqs = Object.entries(c.requests);

  const scenarios = h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, "Scenario"), h("th", { class: "num" }, "People"), h("th", {}, "Pass rate"),
      h("th", { class: "num" }, "Passed"), h("th", { class: "num" }, "Failed"), h("th", { class: "num" }, "Skipped"), h("th", {}, "What it tests"))),
    h("tbody", {}, c.scenarios.map((s) => {
      const failed = Object.values(s.failed).reduce((a, b) => a + b, 0);
      const skipped = Object.values(s.skipped).reduce((a, b) => a + b, 0);
      return h("tr", { class: "click", onclick: () => { state.scenario = state.scenario === s.name ? null : s.name; render(); } },
        h("td", {}, h("strong", {}, s.name),
          failed ? h("div", { class: "fail" }, Object.entries(s.failed).map(([k, n]) => `${k} ×${n}`).join(", ")) : null),
        h("td", { class: "num" }, s.people),
        h("td", {}, h("div", { class: "progress" }, h("i", { style: `width:${pct(s.passed, s.passed + failed)}%` }))),
        h("td", { class: "num" }, s.passed),
        h("td", { class: `num ${failed ? "fail" : ""}` }, failed),
        h("td", { class: "num muted" }, skipped),
        h("td", { class: "muted" }, s.note));
    })));

  const people = state.scenario ? c.persons.filter((p) => p.scenario === state.scenario) : null;
  return h("div", {},
    tiles,
    h("div", { class: "grid g2", style: "margin-bottom:16px" },
      h("div", { class: "panel" }, h("h3", {}, "Failing checks"), failures.length ? bars(failures) : h("div", { class: "pass" }, "Every check passed.")),
      h("div", { class: "panel" }, h("h3", {}, "Requests served by the demo transport"),
        reqs.length ? bars(reqs) : h("div", { class: "muted" }, "None recorded (run the demo first)."))),
    h("div", { class: "panel" }, h("h3", {}, "Scenarios ", h("span", { class: "muted" }, "· click one to see its people")), scenarios),
    people ? h("div", { class: "panel", style: "margin-top:16px" },
      h("h3", {}, `${state.scenario}: ${people.length} people`),
      h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Person"), h("th", {}, "Company"), h("th", {}, "Expected"), h("th", {}, "Checks"))),
        h("tbody", {}, people.map((p) => h("tr", { class: p.lead_id ? "click" : "", onclick: p.lead_id ? () => openLead(p.lead_id) : null },
          h("td", {}, p.name || "-", h("div", { class: "muted mono" }, p.subject)),
          h("td", {}, p.company),
          h("td", { class: "muted" }, p.expect.lead === "absent" ? "no lead" :
            [h("span", { class: "mono" }, p.expect.email || "no email"), ` · ${p.expect.email_status}`,
              p.expect.opt_out ? " · opt-out" : "", p.expect.suppressed ? " · suppressed" : "",
              p.expect.same_lead_as ? ` · same lead as ${p.expect.same_lead_as}` : "",
              p.expect.different_lead_from ? ` · apart from ${p.expect.different_lead_from}` : ""]),
          h("td", {}, checksView(p).lastChild)))))) : null);
}

// ------------------------------------------------------------------ wiring

document.querySelectorAll("#store-switch button").forEach((b) => b.addEventListener("click", () => {
  state.store = b.dataset.store;
  localStorage.setItem("lf.store", state.store);
  state.filter.scenario = "";
  state.scenario = null;
  loadAll();
}));
for (const t of [...extraTabs].reverse()) $("#tabs").prepend(h("button", { "data-tab": t.key }, t.label));
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
$("#reveal").addEventListener("change", (e) => { state.reveal = e.target.checked; loadAll(); });
$("#scrim").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

loadAll();
pollJob();
