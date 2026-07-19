/* ArchitectOS app logic: panels, streaming, graph wiring. */
"use strict";

const $ = (sel) => document.querySelector(sel);
const graph = new GraphView($("#graph"));
window.appGraph = graph;
let ENGINE = { mode: "demo" };
let LAST_IMPACT_REQUEST = "";

/* ------------------------------------------------------------------ utils */

function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.toggle("error", isError);
  el.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add("hidden"), 3600);
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return res.json();
}

/* SSE-over-fetch reader: calls handlers[eventName](data) for each event. */
async function streamSSE(path, body, handlers) {
  const res = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok || !res.body) throw new Error(`${path} failed (${res.status})`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      let event = "message", data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7).trim();
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (data && handlers[event]) {
        try { handlers[event](JSON.parse(data)); } catch (err) { console.error(err); }
      }
    }
  }
}

/* --------------------------------------------------------------- markdown */

function renderMarkdown(text) {
  const lines = text.split("\n");
  let html = "", inCode = false, codeLang = "", codeLines = [], fileLabel = "", listOpen = null, lastHeading = "";

  const flushList = () => { if (listOpen) { html += `</${listOpen}>`; listOpen = null; } };
  const inline = (s) =>
    esc(s)
      .replace(/\[\[([^\]]+)\]\]/g, (_, id) => `<span class="node-chip" data-node="${esc(id)}">${esc(id)}</span>`)
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*([^*\s][^*]*)\*/g, "<em>$1</em>");

  for (const line of lines) {
    const fence = line.match(/^```(\w*)/);
    if (fence) {
      if (!inCode) {
        inCode = true; codeLang = fence[1] || ""; codeLines = [];
        fileLabel = /[./]/.test(lastHeading) ? lastHeading : "";
      } else {
        const codeId = "cb" + Math.random().toString(36).slice(2, 8);
        html += `<div class="codeblock"><div class="cb-head"><span>${esc(fileLabel || codeLang || "code")}</span>` +
          `<button class="cb-copy" data-copy="${codeId}">copy</button></div>` +
          `<pre id="${codeId}">${esc(codeLines.join("\n"))}</pre></div>`;
        inCode = false;
      }
      continue;
    }
    if (inCode) { codeLines.push(line); continue; }

    const h = line.match(/^(#{1,4})\s+(.*)/);
    if (h) {
      flushList();
      lastHeading = h[2].replace(/[*`]/g, "").trim();
      html += `<h3>${inline(h[2])}</h3>`;
      continue;
    }
    const li = line.match(/^\s*(?:[-*]|(\d+)[.)])\s+(.*)/);
    if (li) {
      const tag = li[1] ? "ol" : "ul";
      if (listOpen !== tag) { flushList(); html += `<${tag}>`; listOpen = tag; }
      html += `<li>${inline(li[2])}</li>`;
      continue;
    }
    flushList();
    if (line.trim() === "") continue;
    html += `<p>${inline(line)}</p>`;
  }
  if (inCode) { // unterminated fence while streaming
    html += `<div class="codeblock"><div class="cb-head"><span>${esc(fileLabel || codeLang || "code")}</span></div>` +
      `<pre>${esc(codeLines.join("\n"))}</pre></div>`;
  }
  flushList();
  return html;
}

document.addEventListener("click", (ev) => {
  const chip = ev.target.closest(".node-chip");
  if (chip) {
    if (chip.classList.contains("invalid")) return;
    const id = chip.dataset.node;
    if (!graph.focusNode(id)) showNodeCard(id);
    return;
  }
  const copy = ev.target.closest(".cb-copy");
  if (copy) {
    const pre = document.getElementById(copy.dataset.copy);
    if (pre) navigator.clipboard.writeText(pre.textContent).then(() => toast("Copied"));
  }
});

/* ------------------------------------------------------------------- boot */

async function loadConfig() {
  const cfg = await api("/api/config");
  ENGINE = cfg.engine;
  const badge = $("#engine-badge");
  if (ENGINE.live) {
    const via = ENGINE.provider === "openrouter" ? "OpenRouter" : "OpenAI";
    badge.className = "engine-badge live";
    badge.textContent = `${via} · ${ENGINE.reasoning_model}`;
    badge.title = `Live models via ${via}: ${ENGINE.reasoning_model} + ${ENGINE.codex_model}`;
  } else {
    badge.className = "engine-badge demo";
    badge.textContent = "Offline demo mode";
    badge.title = "No API key — graph & impact are live; prose answers come from the cached demo set.";
  }
  if (cfg.stats) renderStats(cfg.stats);
}

function renderStats(stats) {
  $("#stats-chips").innerHTML =
    `<span class="stat-chip"><b>${esc(stats.repo || "?")}</b></span>` +
    `<span class="stat-chip"><b>${stats.nodes}</b> nodes</span>` +
    `<span class="stat-chip"><b>${stats.edges}</b> edges</span>` +
    (stats.redactions ? `<span class="stat-chip" title="Credential-looking strings redacted before storage or any model call"><b>🔒 ${stats.redactions}</b> redacted</span>` : "");
}

async function loadGraph() {
  const data = await api("/api/graph");
  graph.setData(data.nodes, data.edges);
  renderStats(data.stats);
  buildFilters(data.nodes);
  buildLegend();
}

function buildFilters(nodes) {
  const counts = {};
  for (const n of nodes) counts[n.type] = (counts[n.type] || 0) + 1;
  const isLarge = nodes.length > LARGE_GRAPH_NODE_THRESHOLD;
  const wrap = $("#type-filters");
  wrap.innerHTML = "";
  for (const [type, color] of Object.entries(NODE_COLORS)) {
    if (!counts[type]) continue;
    const startOff = isLarge && AUTO_HIDE_TYPES_ON_LARGE_GRAPH.includes(type);
    const chip = document.createElement("div");
    chip.className = startOff ? "tf-chip off" : "tf-chip";
    chip.innerHTML = `<span class="dot" style="background:${color}"></span>${type} <b>${counts[type]}</b>`;
    graph.setTypeVisible(type, !startOff);
    chip.onclick = () => {
      chip.classList.toggle("off");
      graph.setTypeVisible(type, !chip.classList.contains("off"));
    };
    wrap.appendChild(chip);
  }
  if (isLarge) {
    const hidden = AUTO_HIDE_TYPES_ON_LARGE_GRAPH.filter((t) => counts[t]);
    if (hidden.length) {
      toast(`Large repo (${nodes.length} nodes) — ${hidden.join(" & ")} hidden by default for readability. Click the chip to show them.`);
    }
  }
}

function buildLegend() {
  $("#legend").innerHTML =
    `<div class="row" style="margin-bottom:2px;color:#cfd9ea">Impact risk</div>` +
    Object.entries(RISK_COLORS)
      .map(([k, c]) => `<div class="row"><span class="dot" style="background:${c}"></span>${k}</div>`)
      .join("");
}

/* -------------------------------------------------------------- node card */

async function showNodeCard(id) {
  try {
    const data = await api(`/api/node?id=${encodeURIComponent(id)}`);
    const n = data.node;
    const card = $("#node-card");
    card.innerHTML =
      `<button class="close">✕</button>` +
      `<h3>${esc(n.label)}</h3>` +
      `<div class="meta">${esc(n.type)} · ${esc(n.cluster || "")}${n.path ? " · " + esc(n.path) : ""}` +
      `${n.lineno ? ":" + n.lineno : ""}</div>` +
      (n.doc ? `<div style="font-size:12px;color:#aeb9cf;margin-bottom:4px">${esc(n.doc)}</div>` : "") +
      (n.snippet ? `<pre>${esc(n.snippet)}</pre>` : "") +
      `<div class="neigh">` +
      data.neighbors.slice(0, 14).map((x) =>
        `<span class="node-chip" data-node="${esc(x.id)}" title="${esc(x.rel)}">${esc(x.label)}</span>`
      ).join("") +
      `</div>`;
    card.classList.remove("hidden");
    card.querySelector(".close").onclick = () => {
      card.classList.add("hidden");
      graph.selected = null;
    };
  } catch (err) {
    toast(String(err.message || err), true);
  }
}

graph.onSelect = (id) => { if (id) showNodeCard(id); };

/* ----------------------------------------------------------------- search */

let searchTimer = null;
$("#search-input").addEventListener("input", (ev) => {
  clearTimeout(searchTimer);
  const q = ev.target.value.trim();
  if (!q) {
    $("#search-results").classList.add("hidden");
    graph.setSearch(null);
    return;
  }
  searchTimer = setTimeout(async () => {
    const data = await api(`/api/search?q=${encodeURIComponent(q)}`);
    graph.setSearch(new Set(data.results.map((r) => r.id)));
    const box = $("#search-results");
    box.innerHTML = data.results.map((r) =>
      `<div data-id="${esc(r.id)}"><span class="t">${esc(r.type)}</span>${esc(r.label)}</div>`
    ).join("") || `<div>no matches</div>`;
    box.classList.remove("hidden");
    box.querySelectorAll("div[data-id]").forEach((el) => {
      el.onclick = () => {
        graph.focusNode(el.dataset.id);
        box.classList.add("hidden");
      };
    });
  }, 180);
});
$("#search-input").addEventListener("blur", () => setTimeout(() => $("#search-results").classList.add("hidden"), 250));

/* ------------------------------------------------------------------- tabs */

document.querySelectorAll(".tab").forEach((tab) => {
  tab.onclick = () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".tab-page").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    $(`#page-${tab.dataset.tab}`).classList.add("active");
  };
});

document.querySelectorAll(".suggestions span").forEach((s) => {
  s.onclick = () => {
    const target = s.parentElement.dataset.for;
    $(`#${target}-input`).value = s.textContent;
    $(`#${target}-form`).requestSubmit();
  };
});

/* -------------------------------------------------------------- messages */

function addMsg(log, who, html) {
  const div = document.createElement("div");
  div.className = `msg ${who}`;
  div.innerHTML = `<div class="who">${who === "user" ? "You" : "ArchitectOS"}</div><div class="body">${html}</div>`;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div.querySelector(".body");
}

function engineNote(body, info, audit) {
  const note = document.createElement("div");
  note.className = "engine-note";
  let text = info.cached
    ? "⚡ offline demo response (cached GPT-5.6 output)"
    : `● ${info.engine}`;
  if (audit && audit.total) {
    const bad = (audit.invalid || []).length;
    text += bad
      ? ` · ⚠ ${audit.total - bad}/${audit.total} citations verified against the graph`
      : ` · ✓ ${audit.total}/${audit.total} citations verified against the graph`;
  }
  note.textContent = text;
  body.appendChild(note);
}

/* Server-side citation audit: strike chips whose [[id]] isn't a real graph node. */
function markInvalidCitations(container, ids) {
  for (const id of ids || []) {
    container.querySelectorAll(`.node-chip[data-node="${CSS.escape(id)}"]`).forEach((el) => {
      el.classList.add("invalid");
      el.title = "Citation not found in the knowledge graph";
    });
  }
}

function exportMarkdown(filename, text) {
  const blob = new Blob([text], { type: "text/markdown" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

/* PDF export: zero-dependency — open a print-styled window and hand off to
 * the browser's native print-to-PDF, rather than bundling a PDF library. */
function exportPdf(title, bodyEl) {
  const clone = bodyEl.cloneNode(true);
  clone.querySelectorAll(".btn-row, .engine-note").forEach((el) => el.remove());
  const win = window.open("", "_blank");
  if (!win) { toast("Allow pop-ups to export a PDF", true); return; }
  win.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>${esc(title)}</title><style>
    body{font:15px/1.55 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#1a2230;max-width:760px;margin:40px auto;padding:0 24px}
    h1{font-size:22px;margin:0 0 4px}
    .meta{color:#5b6478;font-size:12px;margin-bottom:24px}
    h2,h3{color:#1a2230;margin-top:28px}
    p{margin:10px 0}
    code{background:#f1f3f6;padding:1px 5px;border-radius:4px;font-size:13px}
    pre{background:#f1f3f6;padding:12px 14px;border-radius:8px;overflow:auto;font-size:12.5px}
    .node-chip{display:inline-block;background:#eef2f9;border:1px solid #d7deea;border-radius:5px;padding:1px 6px;font-size:12px;margin:0 2px}
    .cb-head{display:none}
    ul,ol{margin:8px 0;padding-left:22px}
    @media print{body{margin:0;padding:24px}}
  </style></head><body>
    <h1>${esc(title)}</h1>
    <div class="meta">Generated by ArchitectOS · ${new Date().toISOString().slice(0, 16).replace("T", " ")} UTC</div>
    ${clone.innerHTML}
  </body></html>`);
  win.document.close();
  win.onload = () => { win.focus(); win.print(); };
}

function streamingMd(body) {
  let text = "";
  const target = document.createElement("div");
  target.className = "md";
  // Sources/blast-radius resolve fast, but the model's first token can lag
  // well behind that — without this, the UI goes silent for that whole gap
  // and looks stuck. Cleared automatically the moment the first delta lands,
  // since push() below overwrites target.innerHTML with the real content.
  target.innerHTML = `<span class="spinner"></span><span class="waiting-note">waiting for the model…</span>`;
  body.appendChild(target);
  let raf = null;
  return {
    push(delta) {
      text += delta;
      if (!raf) raf = requestAnimationFrame(() => {
        target.innerHTML = renderMarkdown(text);
        raf = null;
        const log = body.closest(".scroll-log");
        if (log) log.scrollTop = log.scrollHeight;
      });
    },
    finish() { target.innerHTML = renderMarkdown(text); },
    get text() { return text; },
  };
}

/* --------------------------------------------------------------------- ask */

$("#ask-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const q = $("#ask-input").value.trim();
  if (!q) return;
  $("#ask-input").value = "";
  const log = $("#ask-log");
  addMsg(log, "user", esc(q));
  const body = addMsg(log, "assistant", `<span class="spinner"></span>thinking over the graph…`);
  try {
    let md = null, audit = null;
    await streamSSE("/api/ask", { question: q }, {
      sources(data) {
        body.innerHTML = `<div class="src-chips">` +
          data.nodes.map((n) => `<span class="node-chip" data-node="${esc(n.id)}">${esc(n.label)}</span>`).join("") +
          `</div>`;
        md = streamingMd(body);
        graph.setSearch(new Set(data.nodes.map((n) => n.id)));
      },
      delta(d) { if (md) md.push(d.text); },
      citations(d) { audit = d; },
      error(d) {
        toast(d.message, true);
        if (md) md.push(`\n\n⚠ ${d.message}\n`);
        else body.innerHTML = `<span style="color:var(--bad)">⚠ ${esc(d.message)}</span>`;
      },
      done(d) {
        if (md) md.finish();
        if (audit) markInvalidCitations(body, audit.invalid);
        engineNote(body, d, audit);
      },
    });
  } catch (err) {
    body.innerHTML = `<span style="color:var(--bad)">${esc(err.message)}</span>`;
  }
});

/* ------------------------------------------------------------------ impact */

$("#impact-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const q = $("#impact-input").value.trim();
  if (!q) return;
  LAST_IMPACT_REQUEST = q;
  const log = $("#impact-log");
  addMsg(log, "user", esc(q));
  const body = addMsg(log, "assistant", `<span class="spinner"></span>computing blast radius…`);
  try {
    let md = null, audit = null, blastData = null;
    await streamSSE("/api/impact", { request: q }, {
      blast(result) {
        blastData = result;
        graph.setImpact(result);
        $("#impact-banner").classList.remove("hidden");
        $("#impact-banner-text").textContent =
          `Blast radius: ${result.summary.total} nodes · ${result.summary.clusters} services`;
        const s = result.summary;
        body.innerHTML =
          `<div class="impact-summary">` +
          `<div class="imp-card hot"><b>${s.total}</b><span>affected</span></div>` +
          `<div class="imp-card warm"><b>${s.high}</b><span>high risk</span></div>` +
          `<div class="imp-card"><b>${s.endpoints_touched}</b><span>endpoints</span></div>` +
          `<div class="imp-card"><b>${s.docs_to_update}</b><span>docs</span></div>` +
          `</div>` +
          `<div class="svc-list">` +
          Object.entries(result.services).slice(0, 8).map(([name, count]) => {
            const max = Math.max(...Object.values(result.services));
            return `<div class="svc-row"><span class="name">${esc(name)}</span>` +
              `<span class="bar"><i style="width:${(count / max) * 100}%"></i></span>` +
              `<span class="n">${count}</span></div>`;
          }).join("") + `</div>` +
          `<details class="affected-list"><summary>Riskiest nodes</summary><div class="items">` +
          result.affected.filter((a) => a.risk === "seed" || a.risk === "high").slice(0, 16).map((a) =>
            `<span class="node-chip risk-${a.risk}" data-node="${esc(a.id)}">${esc(a.label)}</span>`
          ).join("") + `</div></details>`;
        md = streamingMd(body);
      },
      delta(d) { if (md) md.push(d.text); },
      citations(d) { audit = d; },
      error(d) {
        toast(d.message, true);
        if (md) md.push(`\n\n⚠ ${d.message}\n`);
        else body.innerHTML = `<span style="color:var(--bad)">⚠ ${esc(d.message)}</span>`;
      },
      done(d) {
        if (md) md.finish();
        if (audit) markInvalidCitations(body, audit.invalid);
        engineNote(body, d, audit);
        const row = document.createElement("div");
        row.className = "btn-row";
        const approve = document.createElement("button");
        approve.className = "btn primary";
        approve.textContent = "Approve plan → Codegen preview";
        approve.title = "Preview only — nothing is ever applied to your repository";
        approve.onclick = () => {
          document.querySelector('[data-tab="codegen"]').click();
          $("#codegen-input").value = LAST_IMPACT_REQUEST;
          $("#codegen-form").requestSubmit();
        };
        const exp = document.createElement("button");
        exp.className = "btn";
        exp.textContent = "Export plan (.md)";
        exp.onclick = () => {
          const s = blastData ? blastData.summary : null;
          const head = `# Impact plan: ${q}\n\n` +
            `_Generated by ArchitectOS on ${new Date().toISOString().slice(0, 16).replace("T", " ")} UTC_\n\n` +
            (s ? `**Blast radius:** ${s.total} nodes · ${s.high} high risk · ` +
                 `${s.endpoints_touched} endpoints · ${s.docs_to_update} docs — services: ` +
                 `${Object.keys(blastData.services).join(", ")}\n\n---\n\n` : "");
          exportMarkdown("architectos-impact-plan.md", head + md.text);
        };
        const pdf = document.createElement("button");
        pdf.className = "btn";
        pdf.textContent = "Export PDF";
        pdf.onclick = () => exportPdf(`Impact plan: ${q}`, body);
        row.append(approve, exp, pdf);
        body.appendChild(row);
      },
    });
  } catch (err) {
    body.innerHTML = `<span style="color:var(--bad)">${esc(err.message)}</span>`;
  }
});

$("#impact-exit").onclick = () => {
  graph.clearImpact();
  $("#impact-banner").classList.add("hidden");
};

/* ----------------------------------------------------------------- codegen */

$("#codegen-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const q = $("#codegen-input").value.trim();
  if (!q) return;
  const log = $("#codegen-log");
  addMsg(log, "user", esc(q));
  const body = addMsg(log, "assistant", `<span class="spinner"></span>agent is writing the implementation…`);
  try {
    let md = null, audit = null;
    await streamSSE("/api/generate", { request: q }, {
      blast(d) {
        body.innerHTML = `<div class="engine-note" style="margin:0 0 6px">patch preview · grounded in ${d.summary.total} graph nodes across ${d.summary.clusters} services · nothing is applied to your repo</div>`;
        md = streamingMd(body);
      },
      delta(d) { if (md) md.push(d.text); },
      citations(d) { audit = d; },
      error(d) {
        toast(d.message, true);
        if (md) md.push(`\n\n⚠ ${d.message}\n`);
        else body.innerHTML = `<span style="color:var(--bad)">⚠ ${esc(d.message)}</span>`;
      },
      done(d) {
        if (md) md.finish();
        if (audit) markInvalidCitations(body, audit.invalid);
        engineNote(body, d, audit);
      },
    });
  } catch (err) {
    body.innerHTML = `<span style="color:var(--bad)">${esc(err.message)}</span>`;
  }
});

/* --------------------------------------------------------------- architect */

$("#architect-run").onclick = async () => {
  const log = $("#architect-log");
  const body = addMsg(log, "assistant", `<span class="spinner"></span>mapping the architecture…`);
  $("#architect-run").disabled = true;
  try {
    let md = null, audit = null;
    await streamSSE("/api/architecture", null, {
      overview(d) {
        const mods = d.module_view.clusters
          .filter((c) => !["deps", "api", "root"].includes(c.id))
          .sort((a, b) => (b.files + b.symbols) - (a.files + a.symbols));
        body.innerHTML =
          `<div class="mod-grid">` +
          mods.slice(0, 10).map((c) =>
            `<div class="mod-card"><div class="name">${esc(c.id)}</div>` +
            `<div class="counts">${c.files} files · ${c.symbols} symbols · ${c.endpoints} endpoints</div></div>`
          ).join("") + `</div>` +
          `<details class="mermaid-box"><summary>Mermaid diagram source (paste into mermaid.live)</summary>` +
          `<pre>${esc(d.mermaid)}</pre></details>`;
        md = streamingMd(body);
      },
      delta(d) { if (md) md.push(d.text); },
      citations(d) { audit = d; },
      error(d) {
        toast(d.message, true);
        if (md) md.push(`\n\n⚠ ${d.message}\n`);
        else body.innerHTML = `<span style="color:var(--bad)">⚠ ${esc(d.message)}</span>`;
      },
      done(d) {
        if (md) md.finish();
        if (audit) markInvalidCitations(body, audit.invalid);
        engineNote(body, d, audit);
        const row = document.createElement("div");
        row.className = "btn-row";
        row.style.marginTop = "10px";
        const exp = document.createElement("button");
        exp.className = "btn";
        exp.textContent = "Export brief (.md)";
        exp.onclick = () => exportMarkdown("architectos-architecture-brief.md",
          `# Architecture brief\n\n_Generated by ArchitectOS_\n\n` + md.text);
        const pdf = document.createElement("button");
        pdf.className = "btn";
        pdf.textContent = "Export PDF";
        pdf.onclick = () => exportPdf("Architecture brief", body);
        row.append(exp, pdf);
        body.appendChild(row);
      },
    });
  } catch (err) {
    body.innerHTML = `<span style="color:var(--bad)">${esc(err.message)}</span>`;
  } finally {
    $("#architect-run").disabled = false;
  }
};

/* ------------------------------------------------------------------ ingest */

$("#ingest-btn").onclick = async () => {
  const value = $("#repo-input").value.trim();
  if (!value) { toast("Enter a local path or a GitHub URL"); return; }
  const isGit = /^(https?:\/\/|git@)/.test(value);
  $("#ingest-btn").disabled = true;
  $("#ingest-btn").textContent = "Ingesting…";
  try {
    if (isGit) {
      const { job } = await api("/api/ingest", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ git_url: value }),
      });
      for (;;) {
        await new Promise((r) => setTimeout(r, 1200));
        let st;
        try {
          st = await api(`/api/ingest/status?job=${job}`);
        } catch (err) {
          // The server process restarted mid-clone (e.g. --reload picked up a
          // file save) — the in-memory job is gone, not the repo. Say so plainly
          // instead of surfacing the raw "Unknown job" 404 detail.
          throw new Error("Server restarted while cloning — click Ingest again to retry.");
        }
        $("#ingest-btn").textContent = st.state === "cloning" ? "Cloning…" : "Building…";
        if (st.state === "done") break;
        if (st.state === "error") throw new Error(st.message);
      }
    } else {
      await api("/api/ingest", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: value }),
      });
    }
    await loadGraph();
    await loadConfig();
    toast("Repository ingested — graph rebuilt");
  } catch (err) {
    toast(String(err.message || err), true);
  } finally {
    $("#ingest-btn").disabled = false;
    $("#ingest-btn").textContent = "Ingest";
  }
};

/* ------------------------------------------------------------------- init */

(async function init() {
  try {
    await loadConfig();
    await loadGraph();
  } catch (err) {
    toast("Backend not reachable: " + err.message, true);
  }
})();
