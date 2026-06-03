const POLL_STABLE_MS = 5000;
const POLL_SWITCH_MS = 2000;
const SWITCH_UI_TIMEOUT_MS = 10 * 60 * 1000;

let pollTimer = null;
let switching = false;
let switchStartedAt = null;

function el(id) {
  return document.getElementById(id);
}

function fmtTime(d) {
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s ?? "";
  return d.innerHTML;
}

function stateClass(engine) {
  return `state-${engine || "idle"}`;
}

function badgeStatus(status) {
  const s = status || "cold";
  if (s === "ready") return `<span class="badge badge-ok">${esc(s)}</span>`;
  if (s === "starting" || s === "switching") return `<span class="badge badge-warn">${esc(s)}</span>`;
  if (s === "error") return `<span class="badge badge-err">${esc(s)}</span>`;
  return `<span class="badge">${esc(s)}</span>`;
}

function sleepLabel(v) {
  if (v === true) return "Sì";
  if (v === false) return "No";
  return "—";
}

function workerFor(workers, modelId) {
  return (workers || []).find((w) => w.model_id === modelId);
}

async function fetchJson(path, options) {
  const r = await fetch(path, options);
  let body = null;
  const ct = r.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    try {
      body = await r.json();
    } catch {
      body = null;
    }
  }
  return { ok: r.ok, status: r.status, body };
}

function schedulePoll(ms) {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = setTimeout(() => refresh().catch(onFetchError), ms);
}

function onFetchError(err) {
  console.error(err);
  el("banner-error").textContent = `Errore di rete: ${err.message}`;
  el("banner-error").classList.remove("hidden");
  schedulePoll(POLL_STABLE_MS);
}

function renderCards(health, admin) {
  const engine = admin.state || health.engine;
  const cards = [
    { label: "Engine", value: engine, cls: stateClass(engine) },
    { label: "Pronto", value: health.ready ? "Sì" : "No" },
    { label: "Modello attivo", value: health.active_model || admin.active_model_id || "—" },
    { label: "Gateway", value: health.gateway || "ok" },
  ];
  el("status-cards").innerHTML = cards
    .map(
      (c) =>
        `<div class="card"><div class="card-label">${esc(c.label)}</div>` +
        `<div class="card-value ${c.cls || ""}">${esc(c.value)}</div></div>`
    )
    .join("");
}

function renderSummary(models, workers, admin) {
  const byWorkerState = {};
  for (const w of workers || []) {
    byWorkerState[w.state] = (byWorkerState[w.state] || 0) + 1;
  }
  const warm = models.filter((m) => m.status !== "cold").length;
  const parts = [
    `Catalogo: <strong>${models.length}</strong>`,
    `Worker in pool: <strong>${(workers || []).length}</strong>`,
    `Non cold (catalogo): <strong>${warm}</strong>`,
  ];
  for (const [st, n] of Object.entries(byWorkerState).sort()) {
    parts.push(`${esc(st)}: <strong>${n}</strong>`);
  }
  if (admin.message) {
    parts.push(`Messaggio: <strong>${esc(admin.message)}</strong>`);
  }
  el("summary").innerHTML = parts.join(" · ");
}

function renderSwitchBanner(admin) {
  const banner = el("banner-switch");
  if (admin.state === "switching") {
    banner.classList.remove("hidden");
    banner.textContent = `Switch in corso → ${admin.target_model_id || "?"}${
      admin.message ? ` — ${admin.message}` : ""
    }`;
    switching = true;
    if (!switchStartedAt) switchStartedAt = Date.now();
  } else {
    banner.classList.add("hidden");
    switching = false;
    switchStartedAt = null;
  }
}

function renderErrorBanner(admin) {
  const banner = el("banner-error");
  if (admin.error) {
    banner.classList.remove("hidden");
    banner.textContent = admin.error;
  } else if (!banner.textContent.startsWith("Errore di rete")) {
    banner.classList.add("hidden");
    banner.textContent = "";
  }
}

function renderTable(models, workers, admin, health) {
  const tbody = el("models-body");
  const globalSwitch = admin.state === "switching";
  tbody.innerHTML = models
    .map((m) => {
      const w = workerFor(workers, m.id);
      const caps = (m.capabilities || [])
        .map((c) => `<span class="badge badge-cap">${esc(c)}</span>`)
        .join("");
      const rowStarting = m.status === "starting";
      const canLoad = !m.loaded && !globalSwitch && !rowStarting;
      const btn = m.loaded
        ? '<button class="load-btn" disabled>Attivo</button>'
        : `<button class="load-btn" data-model-id="${esc(m.id)}" ${canLoad ? "" : "disabled"}>Carica</button>`;
      return `<tr>
        <td>${esc(m.name || m.id)}</td>
        <td><code>${esc(m.id)}</code></td>
        <td>${badgeStatus(m.status)}</td>
        <td>${m.loaded ? '<span class="badge badge-ok">sì</span>' : '<span class="badge">no</span>'}</td>
        <td>${caps || "—"}</td>
        <td>${w ? esc(String(w.port)) : "—"}</td>
        <td>${w && w.pid != null ? esc(String(w.pid)) : "—"}</td>
        <td>${sleepLabel(w ? w.is_sleeping : null)}</td>
        <td class="source-cell" title="${esc(m.source)}">${esc(m.source)}</td>
        <td>${btn}</td>
      </tr>`;
    })
    .join("");

  tbody.querySelectorAll(".load-btn[data-model-id]").forEach((btn) => {
    btn.addEventListener("click", () => loadModel(btn.getAttribute("data-model-id")));
  });

  if (switchStartedAt && Date.now() - switchStartedAt > SWITCH_UI_TIMEOUT_MS) {
    el("banner-error").classList.remove("hidden");
    el("banner-error").textContent =
      "Timeout switch (10 min). Controlla i log del container; il primo avvio può richiedere molto tempo.";
  }

  const interval = switching || globalSwitch || !health.ready ? POLL_SWITCH_MS : POLL_STABLE_MS;
  schedulePoll(interval);
}

async function loadModel(modelId) {
  el("banner-error").classList.add("hidden");
  el("banner-error").textContent = "";
  const { status, body } = await fetchJson("/v1/admin/models/load", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model_id: modelId }),
  });
  if (status === 202) {
    switching = true;
    switchStartedAt = Date.now();
    await refresh();
    return;
  }
  const msg =
    body?.error?.message || body?.detail || (status === 503 ? "Switch già in corso" : `HTTP ${status}`);
  el("banner-error").textContent = msg;
  el("banner-error").classList.remove("hidden");
  await refresh();
}

async function refresh() {
  const [healthRes, modelsRes, adminRes] = await Promise.all([
    fetchJson("/health"),
    fetchJson("/v1/models"),
    fetchJson("/v1/admin/models/status"),
  ]);

  if (!healthRes.ok || !modelsRes.ok || !adminRes.ok) {
    throw new Error(
      `API error (health=${healthRes.status}, models=${modelsRes.status}, admin=${adminRes.status})`
    );
  }

  const health = healthRes.body;
  const models = modelsRes.body.data || [];
  const admin = adminRes.body;
  const workers = admin.workers || [];

  el("last-refresh").textContent = `Aggiornato ${fmtTime(new Date())}`;

  renderCards(health, admin);
  renderSwitchBanner(admin);
  renderErrorBanner(admin);
  renderSummary(models, workers, admin);
  renderTable(models, workers, admin, health);
}

refresh().catch(onFetchError);
