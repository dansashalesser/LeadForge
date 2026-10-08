"use strict";
// Outreach page: all text from the API is set with textContent, never as HTML.

const state = { mode: "free_text", plan: null, search: null, catalog: { vendors: [] } };
const $ = (id) => document.getElementById(id);

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) node.append(child);
  return node;
}

async function api(path, options) {
  const response = await fetch(path, options);
  const text = await response.text();
  let body = null;
  try { body = JSON.parse(text); } catch { body = null; }
  if (!response.ok) throw new Error((body && body.detail) || text || response.statusText);
  return body;
}

function post(path, body) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-LeadForge": "1" },
    body: JSON.stringify(body),
  });
}

function setStatus(text, isError) {
  const node = $("status");
  node.textContent = text;
  node.className = isError ? "status error" : "status";
}

// ------------------------------------------------------------------ search panel

function setMode(mode) {
  state.mode = mode;
  state.plan = null;
  $("run").disabled = true;
  $("plan").classList.add("hidden");
  $("domains").classList.toggle("hidden", mode !== "workers");
  $("vendor").classList.toggle("hidden", mode !== "users");
  $("products").classList.toggle("hidden", mode !== "users");
  $("query").placeholder = {
    free_text: "Describe who you want to reach",
    workers: "Company name",
    users: "Optional note (pick vendor and products)",
  }[mode];
  for (const button of document.querySelectorAll("#mode button")) {
    button.classList.toggle("on", button.dataset.mode === mode);
  }
}

// Vendors and products come from GET /api/catalog; nothing about them is in this file.
function fillProducts() {
  const vendor = state.catalog.vendors.find((v) => v.key === $("vendor").value);
  const options = vendor ? [...vendor.products, ...vendor.ecosystem] : [];
  $("products").replaceChildren(...options.map((p) => el("option", { value: p.key, text: p.name })));
}

async function loadCatalog() {
  try {
    state.catalog = await api("/api/catalog");
  } catch (error) {
    setStatus(`Catalog unavailable: ${error.message}`, true);
    return;
  }
  $("vendor").replaceChildren(...state.catalog.vendors.map((v) => el("option", { value: v.key, text: v.name })));
  fillProducts();
}

function usersFilters() {
  if (state.mode !== "users") return {};
  const products = [...$("products").selectedOptions].map((o) => o.value);
  return { vendor: $("vendor").value || null, products, query: $("query").value || products.join(" ") };
}

async function preview() {
  setStatus("Compiling plan…");
  $("run").disabled = true;
  try {
    const domains = $("domains").value.split(",").map((d) => d.trim()).filter(Boolean);
    const answer = await post("/api/outreach/plan", {
      mode: state.mode, query: $("query").value, domains, ...usersFilters(),
    });
    state.plan = answer.plan;
    $("plan").textContent = JSON.stringify(answer.plan, null, 2);
    $("plan").classList.remove("hidden");
    $("run").disabled = false;
    setStatus(`Plan compiled (${answer.plan.compiler}). Nothing has been spent yet.`);
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function run() {
  if (!state.plan) return;
  $("run").disabled = true;
  $("preview").disabled = true;
  setStatus("Running the search…");
  try {
    const summary = await post("/api/outreach/searches", { plan: state.plan });
    setStatus(`Done: ${summary.gathered} gathered, ${summary.invited} invited. ${summary.notes.join("; ")}`);
    await loadSearches();
    await openSearch(summary.search_id);
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    $("preview").disabled = false;
  }
}

// -------------------------------------------------------------- searches and leads

async function loadSearches() {
  const { searches } = await api("/api/outreach/searches").catch(() => ({ searches: [] }));
  const box = $("searches");
  box.replaceChildren();
  if (!searches.length) {
    box.append(el("div", { class: "note", text: "No searches yet." }));
    return;
  }
  const table = el("table", {},
    el("thead", {}, el("tr", {}, ...["Mode", "Query", "Compiler", "Status", "Gathered", "Selected", "Invited"].map((h) => el("th", { text: h })))));
  const body = el("tbody");
  for (const s of searches) {
    const row = el("tr", { class: "lead" },
      ...[s.mode, s.query, s.compiler, s.status, s.funnel.gathered, s.funnel.selected, s.funnel.invited]
        .map((v) => el("td", { text: String(v) })));
    row.addEventListener("click", () => openSearch(s.search_id));
    body.append(row);
  }
  table.append(body);
  box.append(table);
}

async function openSearch(id) {
  state.search = id;
  const reveal = $("reveal").checked;
  const { report } = await api(`/api/outreach/searches/${id}?reveal=${reveal}`);
  $("detail").classList.remove("hidden");
  $("detail-title").textContent = `Leads: ${report.mode} / ${report.query}`;
  $("download").href = `/api/outreach/searches/${id}/report?format=md&reveal=${reveal}`;
  const funnel = $("funnel");
  funnel.replaceChildren(...Object.entries(report.funnel).map(([name, count]) =>
    el("div", {}, el("b", { text: String(count) }), name)));
  $("notes").textContent = report.notes.join(" · ");
  const table = el("table", {},
    el("thead", {}, el("tr", {}, ...["Lead", "Status", "Score", "Sequence", "Reasons"].map((h) => el("th", { text: h })))));
  const body = el("tbody");
  for (const lead of report.leads) {
    const row = el("tr", { class: "lead" },
      el("td", { text: lead.name || lead.email || lead.lead_id.slice(0, 8) }),
      el("td", {}, el("span", { class: `badge ${lead.status}`, text: lead.status })),
      el("td", { text: String(lead.score) }),
      el("td", { text: lead.sequence }),
      el("td", { text: lead.reasons.join(", ") }));
    const detail = el("tr", { class: "hidden" }, el("td", { colspan: "5" }, leadDetail(lead)));
    row.addEventListener("click", () => detail.classList.toggle("hidden"));
    body.append(row, detail);
  }
  table.append(body);
  $("leads").replaceChildren(table);
}

// Quotes are untrusted page text: textContent only. A URL is a link only if http(s).
function safeLink(url) {
  let parsed;
  try { parsed = new URL(url); } catch { return el("span", { class: "note", text: "no link" }); }
  if (!/^https?:$/.test(parsed.protocol)) return el("span", { class: "note", text: "no link" });
  return el("a", { href: parsed.href, target: "_blank", rel: "noopener noreferrer", text: parsed.href });
}

function evidenceBlocks(items) {
  const blocks = [];
  for (const [section, title] of [["company_usage", "Company Usage"], ["person_fit", "Person Fit"]]) {
    const chosen = items.filter((i) => i.section === section);
    if (!chosen.length) continue;
    blocks.push(el("b", { text: `${title} evidence` }));
    for (const i of chosen) {
      const when = i.observed_on ? `, ${i.observed_on}` : "";
      blocks.push(
        el("div", { class: "note" }, `${i.evidence_class} (${i.relationship}${when}) via ${i.source}: `, safeLink(i.url)),
        el("div", { class: "msg", text: i.quote }));
    }
  }
  return blocks;
}

function leadDetail(lead) {
  const box = el("div");
  box.append(el("div", { text: `Email: ${lead.email || "none"} · LinkedIn: ${lead.linkedin_url || "none"}` }));
  if (lead.verdict) box.append(el("div", {}, el("b", { text: "Verdict: " }), lead.verdict));
  box.append(...evidenceBlocks(lead.evidence || []));
  if (lead.invite !== null) {
    box.append(el("b", { text: "LinkedIn invite" }), el("div", { class: "msg", text: lead.invite }));
  }
  if (lead.email_body !== null) {
    box.append(el("b", { text: `Email: ${lead.email_subject}` }), el("div", { class: "msg", text: lead.email_body }));
  }
  if (lead.invite === null && lead.email_body === null) {
    box.append(el("div", { class: "note", text: "No Messages for this Lead." }));
  }
  return box;
}

async function advance() {
  if (!state.search) return;
  try {
    const days = Number($("days").value) || 0;
    const result = await post("/api/outreach/tick", { search_id: state.search, advance_days: days });
    setStatus(`Advanced ${days} day(s): ${result.fired} action(s).`);
    await loadSearches();
    await openSearch(state.search);
  } catch (error) {
    setStatus(error.message, true);
  }
}

for (const button of document.querySelectorAll("#mode button")) {
  button.addEventListener("click", () => setMode(button.dataset.mode));
}
$("preview").addEventListener("click", preview);
$("run").addEventListener("click", run);
$("vendor").addEventListener("change", fillProducts);
$("advance").addEventListener("click", advance);
$("reveal").addEventListener("change", () => state.search && openSearch(state.search));
setMode("free_text");
loadCatalog();
loadSearches();
