"use strict";
// The dashboard's Search tab. It registers itself with the dashboard (window.leadforgeTabs)
// before the dashboard script runs. All text from the API is set with textContent, never
// as HTML. The store (Demo dataset | My store) is the dashboard's header toggle.

(() => {
  const ui = {
    mode: "users", plan: null, store: null, reveal: null, search: null,
    catalog: { vendors: [] }, catalogLoaded: false, ctx: null, busy: false, live: [],
  };

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
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
    if (!response.ok) {
      const detail = body && body.detail;
      throw new Error((typeof detail === "string" ? detail : detail && JSON.stringify(detail)) || text || response.statusText);
    }
    return body;
  }

  function post(path, body) {
    return api(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-LeadForge": "1" },
      body: JSON.stringify(body),
    });
  }

  const storeQuery = () => `store=${ui.store}`;
  const storeName = () => (ui.store === "demo" ? "Demo dataset" : "My store");

  // ------------------------------------------------------------------- static layout

  const scope = el("div", { class: "scope" });
  const modeButtons = [["users", "Company users"], ["workers", "Company workers"], ["free_text", "Free text"]]
    .map(([mode, label]) => el("button", { "data-mode": mode, onclick: () => setMode(mode) , text: label }));
  const vendor = el("select", { "aria-label": "Vendor", onchange: () => fillProducts() });
  const products = el("select", { multiple: "", "aria-label": "Products", onchange: () => resetPlan() });
  const query = el("input", { type: "text", "aria-label": "Query", oninput: () => resetPlan() });
  const domains = el("input", { type: "text", "aria-label": "Domains", placeholder: "company domains, comma separated" });
  const previewButton = el("button", { class: "btn", onclick: () => preview(), text: "Review plan" });
  const runButton = el("button", { class: "btn primary", disabled: "", onclick: () => run(), text: "Run search" });
  const browseButton = el("button", { class: "btn", onclick: () => ui.ctx.setTab("leads"), text: "Browse all leads" });
  const review = el("div", { class: "review hidden", "aria-live": "polite" });
  const status = el("div", { class: "status", "aria-live": "polite" });
  const searchesBox = el("div");
  const detailTitle = el("h3", { text: "Leads" });
  const days = el("input", { type: "number", min: "0", step: "1", value: "3", "aria-label": "Advance days" });
  const download = el("a", { download: "outreach-report.md", text: "Download report (Markdown)" });
  const funnel = el("div", { class: "funnel" });
  const notes = el("div", { class: "notes" });
  const leadsBox = el("div");
  const detail = el("section", { class: "panel hidden", "aria-label": "Search detail" },
    el("div", { class: "row first" }, detailTitle, el("div", { style: "flex:1" }),
      el("label", {}, "Advance days ", days),
      el("button", { class: "btn", onclick: () => advance(), text: "Advance" }), download),
    funnel, notes, leadsBox);

  const root = el("div", { class: "search-tab" },
    el("section", { class: "panel", "aria-label": "Search" },
      el("div", { class: "row first" }, el("h3", { text: "Lead search" }), el("div", { style: "flex:1" }), browseButton),
      el("div", { class: "row" }, scope),
      el("div", { class: "row" }, el("div", { class: "seg", role: "tablist", "aria-label": "Mode" }, ...modeButtons)),
      el("div", { class: "row" }, vendor, products, query, domains, previewButton, runButton),
      review, status),
    el("section", { class: "panel", "aria-label": "Searches" }, el("h3", { text: "Searches" }), searchesBox),
    detail);

  // ------------------------------------------------------------------------- state

  function setStatus(text, isError) {
    status.textContent = text;
    status.className = isError ? "status error" : "status";
  }

  function resetPlan() {
    ui.plan = null;
    runButton.disabled = true;
    review.classList.add("hidden");
  }

  function setMode(mode) {
    ui.mode = mode;
    resetPlan();
    domains.classList.toggle("hidden", mode !== "workers");
    vendor.classList.toggle("hidden", mode !== "users");
    products.classList.toggle("hidden", mode !== "users");
    query.placeholder = {
      free_text: "Describe who you want to reach",
      workers: "Company name",
      users: "Optional note (leave empty to use the products)",
    }[mode];
    for (const button of modeButtons) button.classList.toggle("on", button.dataset.mode === mode);
  }

  function renderScope() {
    const demo = ui.store === "demo";
    scope.className = demo ? "scope" : "scope live";
    scope.textContent = demo
      ? "Searching the Demo dataset: 40 companies, 250 people, all synthetic. Nothing is spent, nothing is sent (dry run). No demo run is needed first."
      : "Searching My store: sources with their keys set run LIVE and spend provider credits. You will see which before you run. Sends are always dry runs.";
    runButton.textContent = demo ? "Run on demo dataset" : "Run on my store";
  }

  // Vendors and products come from GET /api/catalog; nothing about them is in this file.
  function fillProducts() {
    const chosen = ui.catalog.vendors.find((v) => v.key === vendor.value);
    const own = new Set(chosen ? chosen.products.map((p) => p.key) : []);
    const options = chosen ? [...chosen.products, ...chosen.ecosystem] : [];
    products.replaceChildren(...options.map((p) => {
      const option = el("option", { value: p.key, text: p.name });
      option.selected = own.has(p.key);
      return option;
    }));
    resetPlan();
  }

  async function loadCatalog() {
    try {
      ui.catalog = await api("/api/catalog");
    } catch (error) {
      setStatus(`Catalog unavailable: ${error.message}`, true);
      return;
    }
    ui.catalogLoaded = true;
    vendor.replaceChildren(...ui.catalog.vendors.map((v) => el("option", { value: v.key, text: v.name })));
    fillProducts();
  }

  function request() {
    const body = { mode: ui.mode, query: query.value, domains: [], store: ui.store };
    if (ui.mode === "workers") body.domains = domains.value.split(",").map((d) => d.trim()).filter(Boolean);
    if (ui.mode === "users") {
      const picked = [...products.selectedOptions].map((o) => o.value);
      body.vendor = vendor.value || null;
      body.products = picked;
      if (!body.query.trim()) body.query = picked.join(" ");
    }
    return body;
  }

  // ----------------------------------------------------------------- plan, then run

  function showReview(answer) {
    const live = answer.live_sources || [];
    const lines = answer.notes.map((note) => {
      const isLive = live.includes(note);
      return el("li", {}, el("span", { class: isLive ? "badge b-warn" : "badge b-muted", text: isLive ? "LIVE" : "ok" }), ` ${note}`);
    });
    review.replaceChildren(
      el("b", { text: `Review before running on ${storeName()}` }),
      el("ul", {}, ...lines),
      live.length
        ? el("div", { class: "error", text: `${live.length} source(s) are LIVE: running will spend provider credits.` })
        : el("div", { class: "note", text: "No source is live: this run spends no credits." }),
      el("details", {}, el("summary", { text: "Compiled plan" }), el("pre", { class: "mono", text: JSON.stringify(answer.plan, null, 2) })));
    review.classList.remove("hidden");
  }

  async function preview() {
    setStatus("Compiling plan…");
    runButton.disabled = true;
    try {
      const answer = await post("/api/outreach/plan", request());
      ui.plan = answer.plan;
      showReview(answer);
      ui.live = answer.live_sources || [];
      runButton.disabled = false;
      setStatus(`Plan compiled (${answer.plan.compiler}). Nothing has been spent yet. Press "${runButton.textContent}" to run it.`);
    } catch (error) {
      setStatus(error.message, true);
    }
  }

  async function run() {
    if (!ui.plan || ui.busy) return;
    if (ui.store === "main" && ui.live.length &&
        !confirm(`Run on your store?\n\n${ui.live.join("\n")}\n\nThese sources are LIVE and will spend provider credits.`)) return;
    ui.busy = true;
    runButton.disabled = true;
    previewButton.disabled = true;
    const store = ui.store;
    setStatus(store === "demo" ? "Running the search on the demo dataset…" : "Running the search…");
    try {
      const summary = await post("/api/outreach/searches", { plan: ui.plan, store });
      const counts = Object.entries(summary.counts).map(([k, v]) => `${v} ${k}`).join(", ");
      setStatus(`Done: ${summary.gathered} gathered (${counts}), ${summary.invited} invited. ${summary.notes.join("; ")}`);
      await loadSearches();
      await openSearch(summary.search_id);
      ui.ctx.reload();
    } catch (error) {
      setStatus(error.message, true);
    } finally {
      ui.busy = false;
      previewButton.disabled = false;
    }
  }

  // ------------------------------------------------------------- searches and leads

  async function loadSearches() {
    searchesBox.replaceChildren();
    let searches = [];
    try {
      ({ searches } = await api(`/api/outreach/searches?${storeQuery()}`));
    } catch (error) {
      searchesBox.append(el("div", { class: "note", text: ui.store === "demo"
        ? "No searches on the demo dataset yet: pick a vendor and run one."
        : `No searches yet on My store (${error.message}).` }));
      return;
    }
    if (!searches.length) {
      searchesBox.append(el("div", { class: "note", text: "No searches yet." }));
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
    searchesBox.append(table);
  }

  async function openSearch(id) {
    ui.search = id;
    const reveal = Boolean(ui.ctx.state.reveal);
    const { report } = await api(`/api/outreach/searches/${id}?${storeQuery()}&reveal=${reveal}`);
    detail.classList.remove("hidden");
    detailTitle.textContent = `Leads: ${report.mode} / ${report.query}`;
    download.href = `/api/outreach/searches/${id}/report?${storeQuery()}&format=md&reveal=${reveal}`;
    funnel.replaceChildren(...Object.entries(report.funnel).map(([name, count]) =>
      el("div", {}, el("b", { text: String(count) }), name)));
    notes.textContent = report.notes.join(" · ");
    const table = el("table", {},
      el("thead", {}, el("tr", {}, ...["Lead", "Status", "Score", "Sequence", "Reasons"].map((h) => el("th", { text: h })))));
    const body = el("tbody");
    // Filter by status and by text (name, email, company, reasons); selected first.
    const statuses = [...new Set(report.leads.map((l) => l.status))].sort();
    const statusPick = el("select", {},
      el("option", { value: "", text: `All statuses (${report.leads.length})` }),
      ...statuses.map((s) => el("option", {
        value: s, text: `${s} (${report.leads.filter((l) => l.status === s).length})`,
      })));
    const textPick = el("input", { type: "search", placeholder: "Filter by name, email domain, verdict or reason" });
    const shown = el("span", { class: "note" });
    const rows = [];
    const order = (l) => (l.status === "selected" ? 0 : l.status === "manual_review" ? 1 : 2);
    for (const lead of [...report.leads].sort((a, b) => order(a) - order(b))) {
      const row = el("tr", { class: "lead" },
        el("td", { text: lead.name || lead.email || lead.lead_id.slice(0, 8) }),
        el("td", {}, el("span", { class: `badge ${lead.status}`, text: lead.status })),
        // Users mode: the score only ranks selected people; for the rest it means nothing.
        el("td", { text: lead.verdict && lead.status !== "selected" ? "—" : String(lead.score) }),
        el("td", { text: lead.sequence }),
        el("td", { text: lead.reasons.join(", ") }));
      const more = el("tr", { class: "hidden" }, el("td", { colspan: "5" }, leadDetail(lead)));
      row.addEventListener("click", () => more.classList.toggle("hidden"));
      const haystack = [lead.name, lead.email, lead.verdict, lead.company_usage, lead.person_fit, ...lead.reasons]
        .filter(Boolean).join(" ").toLowerCase();
      rows.push({ lead, row, more, haystack });
      body.append(row, more);
    }
    function applyFilter() {
      const status = statusPick.value;
      const text = textPick.value.trim().toLowerCase();
      let count = 0;
      for (const r of rows) {
        const keep = (!status || r.lead.status === status) && (!text || r.haystack.includes(text));
        r.row.classList.toggle("hidden", !keep);
        if (!keep) r.more.classList.add("hidden");
        if (keep) count += 1;
      }
      shown.textContent = `${count} shown`;
    }
    statusPick.addEventListener("change", applyFilter);
    textPick.addEventListener("input", applyFilter);
    table.append(body);
    leadsBox.replaceChildren(el("div", { class: "row" }, statusPick, textPick, shown), table);
    applyFilter();
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
    if (lead.company_usage) {
      const why = lead.company_usage_reason ? ` (${lead.company_usage_reason})` : "";
      box.append(el("div", {}, el("b", { text: "Company Usage: " }), `${lead.company_usage}${why}`));
    }
    if (lead.person_fit) box.append(el("div", {}, el("b", { text: "Person Fit: " }), lead.person_fit));
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
    if (!ui.search) return;
    try {
      const n = Number(days.value) || 0;
      const result = await post("/api/outreach/tick", { search_id: ui.search, advance_days: n, store: ui.store });
      setStatus(`Advanced ${n} day(s): ${result.fired} action(s).`);
      await loadSearches();
      await openSearch(ui.search);
    } catch (error) {
      setStatus(error.message, true);
    }
  }

  // ------------------------------------------------------------ dashboard integration

  // Called on every dashboard render: act only when the store or reveal toggle changed.
  function view(ctx) {
    ui.ctx = ctx;
    if (ui.store !== ctx.state.store) {
      ui.store = ctx.state.store;
      ui.search = null;
      detail.classList.add("hidden");
      resetPlan();
      renderScope();
      setStatus("");
      loadSearches();
    } else if (ui.reveal !== ctx.state.reveal && ui.search) {
      openSearch(ui.search).catch((error) => setStatus(error.message, true));
    }
    ui.reveal = ctx.state.reveal;
    return root;
  }

  setMode("users");
  loadCatalog();
  window.leadforgeTabs = [{ key: "search", label: "Search", view }, ...(window.leadforgeTabs || [])];
})();
