const POLL_STABLE_MS = 3000;
const POLL_SWITCH_MS = 800;
const SWITCH_UI_TIMEOUT_MS = 10 * 60 * 1000;

const SENSERVE_DEFAULT_KEYS = new Set(["worker_port", "worker_base_port"]);
const KNOWN_DEFAULT_FIELDS = [
  { key: "worker_port", label: "Worker base port", type: "number" },
  { key: "allowed_local_media_path", label: "Allowed local media path", type: "text" },
  { key: "gpu_memory_utilization", label: "GPU memory utilization", type: "number", step: "0.01" },
  { key: "max_model_len", label: "Max model len", type: "text" },
  { key: "tensor_parallel_size", label: "Tensor parallel size", type: "number" },
  { key: "trust_remote_code", label: "Trust remote code", type: "checkbox" },
  { key: "load_format", label: "Load format", type: "text" },
  { key: "tool_call_parser", label: "Tool call parser", type: "text" },
];

let pollTimer = null;
let switching = false;
let switchStartedAt = null;
let pendingTargetId = null;
let controlsWired = false;
let configWired = false;
let configLoadPromise = null;
let lastModels = [];
let lastAdmin = null;
let lastHealth = null;
let lastPickerSignature = "";
let userPickedModelId = null;
let configDoc = { defaults: {}, models: [] };
let vllmFlags = [];
let modelEditIndex = null;

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

function displayName(models, modelId) {
  if (!modelId) return "—";
  const m = models.find((x) => x.id === modelId);
  return m?.name || modelId;
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
  if (v === true) return "yes";
  if (v === false) return "no";
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
  pollTimer = setTimeout(() => refreshRuntime().catch(onFetchError), ms);
}

function isEngineLoading(admin) {
  const s = admin?.state;
  return s === "switching" || s === "starting" || switching || pendingTargetId;
}

function pollInterval(admin, health) {
  if (isEngineLoading(admin) || !health?.ready) return POLL_SWITCH_MS;
  return POLL_STABLE_MS;
}

function onFetchError(err) {
  console.error(err);
  el("banner-error").textContent = `Network error: ${err.message}`;
  el("banner-error").classList.remove("hidden");
  el("status-line").textContent = "Unreachable";
  schedulePoll(POLL_STABLE_MS);
}

function parseConfigValue(raw, type) {
  if (type === "checkbox") return raw === "on" || raw === true;
  if (type === "number") {
    const n = Number(raw);
    return Number.isNaN(n) ? raw : n;
  }
  return raw;
}

function defaultsVllmEntries() {
  const d = configDoc.defaults || {};
  const nested = d.vllm && typeof d.vllm === "object" ? d.vllm : {};
  const out = [];
  for (const [k, v] of Object.entries(d)) {
    if (SENSERVE_DEFAULT_KEYS.has(k) || k === "vllm") continue;
    if (KNOWN_DEFAULT_FIELDS.some((f) => f.key === k)) continue;
    out.push([k, v]);
  }
  for (const [k, v] of Object.entries(nested)) {
    out.push([k, v]);
  }
  return out;
}

function renderKvRows(containerId, entries, onChange) {
  const container = el(containerId);
  container.innerHTML = entries
    .map(
      ([key, val], i) =>
        `<div class="kv-row" data-idx="${i}">
          <input class="kv-key" list="vllm-flag-list" value="${esc(String(key))}" placeholder="option" />
          <input class="kv-val" value="${esc(String(val ?? ""))}" placeholder="value" />
          <button type="button" class="btn btn-sm kv-remove" data-idx="${i}">×</button>
        </div>`
    )
    .join("");
  container.querySelectorAll(".kv-remove").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = Number(btn.getAttribute("data-idx"));
      onChange(entries.filter((_, j) => j !== idx));
    });
  });
  container.querySelectorAll(".kv-key, .kv-val").forEach((input) => {
    input.addEventListener("change", () => onChange(readKvRows(container)));
  });
}

function readKvRows(container) {
  return [...container.querySelectorAll(".kv-row")]
    .map((row) => {
      const key = row.querySelector(".kv-key")?.value.trim();
      const val = row.querySelector(".kv-val")?.value.trim();
      return [key, val];
    })
    .filter(([k]) => k);
}

function renderDefaultsVllm() {
  renderKvRows("defaults-vllm-rows", defaultsVllmEntries(), (entries) => {
    applyDefaultsVllmEntries(entries);
    renderDefaultsVllm();
  });
}

function applyDefaultsVllmEntries(entries) {
  const d = { ...(configDoc.defaults || {}) };
  const known = new Set(KNOWN_DEFAULT_FIELDS.map((f) => f.key));
  for (const k of Object.keys(d)) {
    if (!SENSERVE_DEFAULT_KEYS.has(k) && !known.has(k) && k !== "vllm") delete d[k];
  }
  delete d.vllm;
  for (const [k, v] of entries) {
    d[k] = coerceYamlScalar(v);
  }
  configDoc.defaults = d;
}

function coerceYamlScalar(s) {
  if (s === "true") return true;
  if (s === "false") return false;
  if (s === "auto") return "auto";
  const n = Number(s);
  if (s !== "" && !Number.isNaN(n) && String(n) === s) return n;
  return s;
}

function renderDefaultsForm() {
  const d = configDoc.defaults || {};
  el("defaults-form").innerHTML = KNOWN_DEFAULT_FIELDS.map((f) => {
    const val = d[f.key];
    if (f.type === "checkbox") {
      const checked = val ? "checked" : "";
      return `<label class="default-field">${esc(f.label)}
        <input type="checkbox" data-key="${esc(f.key)}" ${checked} /></label>`;
    }
    const v = val != null ? esc(String(val)) : "";
    const step = f.step ? ` step="${f.step}"` : "";
    const inputType = f.type === "number" ? "number" : "text";
    return `<label class="default-field">${esc(f.label)}
      <input type="${inputType}" data-key="${esc(f.key)}" value="${v}"${step} /></label>`;
  }).join("");
  el("defaults-form").querySelectorAll("input").forEach((input) => {
    input.addEventListener("change", () => {
      const key = input.getAttribute("data-key");
      const field = KNOWN_DEFAULT_FIELDS.find((f) => f.key === key);
      configDoc.defaults = configDoc.defaults || {};
      configDoc.defaults[key] = parseConfigValue(
        field?.type === "checkbox" ? input.checked : input.value,
        field?.type
      );
    });
  });
  renderDefaultsVllm();
}

function renderConfigModelsTable() {
  const tbody = el("config-models-body");
  tbody.innerHTML = configDoc.models
    .map((m, i) => {
      const caps = (m.capabilities || [])
        .map((c) => `<span class="badge badge-cap">${esc(c)}</span>`)
        .join("");
      return `<tr>
        <td>${esc(m.display_name || m.id)}</td>
        <td>${caps || "—"}</td>
        <td class="source-cell" title="${esc(m.source)}">${esc(m.source)}</td>
        <td class="actions-cell">
          <button type="button" class="btn btn-sm" data-edit="${i}">Edit</button>
          <button type="button" class="btn btn-sm btn-danger" data-del="${i}">Delete</button>
        </td>
      </tr>`;
    })
    .join("");
  tbody.querySelectorAll("[data-edit]").forEach((btn) => {
    btn.addEventListener("click", () => openModelDialog(Number(btn.getAttribute("data-edit"))));
  });
  tbody.querySelectorAll("[data-del]").forEach((btn) => {
    btn.addEventListener("click", () => {
      configDoc.models.splice(Number(btn.getAttribute("data-del")), 1);
      renderConfigModelsTable();
    });
  });
}

function readModelVllmRows() {
  return readKvRows(el("model-vllm-rows")).map(([k, v]) => [k, coerceYamlScalar(v)]);
}

function renderModelVllmRows(vllm) {
  renderKvRows("model-vllm-rows", Object.entries(vllm || {}), () => {});
}

function openModelDialog(index) {
  modelEditIndex = index;
  const form = el("model-form");
  const dlg = el("model-dialog");
  if (index == null) {
    el("model-dialog-title").textContent = "Add model";
    form.id.value = "";
    form.display_name.value = "";
    form.source.value = "";
    form.enabled.checked = true;
    form.default.checked = false;
    form.querySelectorAll('input[name="cap"]').forEach((c) => {
      c.checked = c.value === "text";
    });
    renderModelVllmRows({});
  } else {
    const m = configDoc.models[index];
    el("model-dialog-title").textContent = "Edit model";
    form.id.value = m.id;
    form.id.readOnly = true;
    form.display_name.value = m.display_name || "";
    form.source.value = m.source || "";
    form.enabled.checked = m.enabled !== false;
    form.default.checked = !!m.default;
    const caps = new Set(m.capabilities || ["text"]);
    form.querySelectorAll('input[name="cap"]').forEach((c) => {
      c.checked = caps.has(c.value);
    });
    renderModelVllmRows(m.vllm || {});
  }
  if (index == null) form.id.readOnly = false;
  dlg.showModal();
}

function wireConfigEditor() {
  if (configWired) return;
  configWired = true;

  el("config-panel").addEventListener("toggle", () => {
    if (el("config-panel").open) ensureConfigLoaded().catch(onFetchError);
  });

  el("defaults-vllm-add").addEventListener("click", () => {
    const entries = defaultsVllmEntries();
    entries.push(["", ""]);
    applyDefaultsVllmEntries(entries);
    renderDefaultsVllm();
  });

  el("config-add-model-btn").addEventListener("click", () => openModelDialog(null));

  el("model-vllm-add").addEventListener("click", () => {
    const entries = readKvRows(el("model-vllm-rows"));
    entries.push(["", ""]);
    renderKvRows("model-vllm-rows", entries, () => {});
  });

  el("model-dialog-cancel").addEventListener("click", () => el("model-dialog").close());

  el("model-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const form = el("model-form");
    const caps = [...form.querySelectorAll('input[name="cap"]:checked')].map((c) => c.value);
    if (!caps.length) {
      alert("Select at least one capability");
      return;
    }
    const vllm = Object.fromEntries(readModelVllmRows());
    const entry = {
      id: form.id.value.trim(),
      display_name: form.display_name.value.trim() || form.id.value.trim(),
      source: form.source.value.trim(),
      capabilities: caps,
      enabled: form.enabled.checked,
      default: form.default.checked,
      vllm,
    };
    if (entry.default) {
      configDoc.models.forEach((m, i) => {
        if (modelEditIndex !== i) m.default = false;
      });
    }
    if (modelEditIndex == null) {
      if (configDoc.models.some((m) => m.id === entry.id)) {
        alert("Model id already exists");
        return;
      }
      configDoc.models.push(entry);
    } else {
      configDoc.models[modelEditIndex] = entry;
    }
    el("model-dialog").close();
    renderConfigModelsTable();
  });

  el("config-save-btn").addEventListener("click", () => saveConfig());
}

async function ensureConfigLoaded() {
  if (configLoadPromise) return configLoadPromise;
  el("config-load-hint").textContent = "Loading configuration…";
  configLoadPromise = loadConfigEditor()
    .then(() => {
      el("config-load-hint").classList.add("hidden");
      el("config-editor-root").classList.remove("hidden");
      renderConfigModelsTable();
    })
    .finally(() => {
      configLoadPromise = null;
    });
  return configLoadPromise;
}

async function loadConfigEditor() {
  const [cfgRes, flagsRes] = await Promise.all([
    fetchJson("/v1/admin/config"),
    fetchJson("/v1/admin/vllm/flags"),
  ]);
  if (cfgRes.ok && cfgRes.body) {
    configDoc = {
      defaults: cfgRes.body.defaults || {},
      models: (cfgRes.body.models || []).map((m) => ({
        ...m,
        vllm: m.vllm || {},
      })),
    };
    let pathNote = cfgRes.body.path || "";
    if (cfgRes.body.local_overlay) {
      pathNote += " · local overlay is read-only";
    }
    el("config-path").textContent = pathNote;
  }
  if (flagsRes.ok && flagsRes.body?.flags) {
    vllmFlags = flagsRes.body.flags;
    el("vllm-flag-list").innerHTML = vllmFlags
      .map((f) => `<option value="${esc(f.yaml_name)}">${esc(f.cli_name)}</option>`)
      .join("");
  }
  renderDefaultsForm();
}

async function saveConfig() {
  el("banner-config-ok").classList.add("hidden");
  readDefaultsFromForm();
  applyDefaultsVllmEntries(readKvRows(el("defaults-vllm-rows")));

  const payload = {
    defaults: configDoc.defaults,
    models: configDoc.models.map((m) => ({
      id: m.id,
      display_name: m.display_name || m.id,
      source: m.source,
      capabilities: m.capabilities,
      enabled: m.enabled !== false,
      default: !!m.default,
      vllm: m.vllm || {},
    })),
  };

  const { status, body } = await fetchJson("/v1/admin/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (status === 200) {
    el("banner-config-ok").textContent = "Configuration saved";
    el("banner-config-ok").classList.remove("hidden");
    await refreshRuntime();
    return;
  }

  let msg = body?.error?.message || body?.detail;
  if (Array.isArray(body?.detail)) {
    msg = body.detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
  }
  el("banner-error").textContent = msg || `Save failed (HTTP ${status})`;
  el("banner-error").classList.remove("hidden");
}

function readDefaultsFromForm() {
  configDoc.defaults = configDoc.defaults || {};
  el("defaults-form").querySelectorAll("input").forEach((input) => {
    const key = input.getAttribute("data-key");
    const field = KNOWN_DEFAULT_FIELDS.find((f) => f.key === key);
    configDoc.defaults[key] = parseConfigValue(
      field?.type === "checkbox" ? input.checked : input.value,
      field?.type
    );
  });
}

function wireControls() {
  if (controlsWired) return;
  controlsWired = true;

  el("model-picker").addEventListener("change", () => {
    userPickedModelId = el("model-picker").value;
    updateLoadButtonState();
    updateSelectionStatus();
  });

  el("model-load-btn").addEventListener("click", () => {
    const id = el("model-picker").value;
    if (id) loadModel(id);
  });
  el("model-warmup-btn").addEventListener("click", () => runWarmup());
  el("model-cancel-btn").addEventListener("click", () => cancelSwitch());
}

function renderStatusLine(health, admin, models) {
  const engine = admin.state || health.engine;
  const activeId = health.active_model || admin.active_model_id;
  const activeName = displayName(models, activeId);
  const ready = health.ready ? "ready" : "not ready";
  let line = `<span class="${stateClass(engine)}">${esc(engine)}</span>`;
  line += ` · ${esc(ready)}`;
  if (activeId && engine === "ready") {
    line += ` · active: <strong>${esc(activeName)}</strong>`;
  } else if (admin.state === "starting" || admin.state === "switching") {
    const target = displayName(models, admin.target_model_id || pendingTargetId);
    const verb = admin.state === "starting" ? "starting" : "switching to";
    line += ` · ${verb} <strong>${esc(target)}</strong>`;
  }
  el("status-line").innerHTML = line;
}

function renderSwitchBanner(admin) {
  const banner = el("banner-switch");
  const textEl = el("banner-switch-text");
  const cancelBtn = el("model-cancel-btn");
  const loading = isEngineLoading(admin);

  if (loading) {
    banner.classList.remove("hidden");
    cancelBtn.classList.remove("hidden");
    const targetId = admin.target_model_id || pendingTargetId;
    const target = displayName(lastModels, targetId);
    textEl.textContent =
      admin.message ||
      (admin.state === "starting"
        ? `Starting vLLM: ${target || targetId || "model"}…`
        : `Switching to ${target || targetId || "model"}…`);
    switching = admin.state === "switching" || admin.state === "starting";
    if (!switchStartedAt) switchStartedAt = Date.now();
  } else {
    banner.classList.add("hidden");
    cancelBtn.classList.add("hidden");
    textEl.textContent = "";
    switching = false;
    switchStartedAt = null;
    pendingTargetId = null;
  }
}

function renderErrorBanner(admin) {
  const banner = el("banner-error");
  if (admin.error) {
    banner.classList.remove("hidden");
    banner.textContent = admin.error;
  } else if (!banner.textContent.startsWith("Network error")) {
    banner.classList.add("hidden");
    banner.textContent = "";
  }
}

function pickerSignature(models, admin) {
  return `${admin.state}|${admin.active_model_id}|${admin.target_model_id}|${models
    .map((m) => `${m.id}:${m.status}:${m.loaded}`)
    .join(",")}`;
}

function renderModelPicker(models, admin, health) {
  const sel = el("model-picker");
  const activeId = health.active_model || admin.active_model_id;
  const sig = pickerSignature(models, admin);

  if (sig !== lastPickerSignature) {
    lastPickerSignature = sig;
    const prev = userPickedModelId || sel.value;
    sel.innerHTML = models
      .map((m) => {
        const label = m.name || m.id;
        return `<option value="${esc(m.id)}">${esc(label)}</option>`;
      })
      .join("");

    if (prev && models.some((m) => m.id === prev)) {
      sel.value = prev;
    } else if (activeId && models.some((m) => m.id === activeId)) {
      sel.value = activeId;
      userPickedModelId = activeId;
    }
  }

  updateLoadButtonState();
  updateSelectionStatus();
}

function updateSelectionStatus() {
  const statusEl = el("model-selection-status");
  const id = el("model-picker").value;
  const m = lastModels.find((x) => x.id === id);
  if (!m) {
    statusEl.textContent = "";
    return;
  }
  if (m.loaded && lastHealth?.ready) {
    statusEl.textContent = "active";
    return;
  }
  if (pendingTargetId === id || m.status === "starting") {
    statusEl.textContent = "starting…";
    return;
  }
  statusEl.textContent = m.status || "cold";
}

function updateLoadButtonState() {
  const sel = el("model-picker");
  const btn = el("model-load-btn");
  const selected = lastModels.find((m) => m.id === sel.value);
  const globalSwitch = isEngineLoading(lastAdmin);
  const selectedLoaded = selected?.loaded && lastHealth?.ready;
  const isTarget =
    pendingTargetId === sel.value ||
    ((lastAdmin?.state === "switching" || lastAdmin?.state === "starting") &&
      lastAdmin?.target_model_id === sel.value);

  btn.disabled =
    !selected ||
    globalSwitch ||
    selectedLoaded ||
    (selected.status === "starting" && isTarget);

  if (selectedLoaded) {
    btn.textContent = "Active";
  } else if (globalSwitch && isTarget) {
    btn.textContent = "Switching…";
  } else {
    btn.textContent = "Switch";
  }

  el("model-warmup-btn").disabled =
    !lastHealth?.ready || !lastHealth?.active_model || globalSwitch;
}

function renderRuntimeTable(models, workers, admin, health) {
  const tbody = el("models-body");
  const activeId = health.active_model || admin.active_model_id;
  const targetId = admin.target_model_id || pendingTargetId;

  tbody.innerHTML = models
    .map((m) => {
      const w = workerFor(workers, m.id);
      let state = m.status || "cold";
      if (m.id === activeId && m.loaded && health.ready) state = "active";
      else if (m.id === targetId && (admin.state === "switching" || admin.state === "starting"))
        state = "starting";
      const rowClass = m.id === activeId ? ' class="row-active"' : "";
      return `<tr${rowClass}>
        <td>${esc(m.name || m.id)}</td>
        <td>${badgeStatus(state)}</td>
        <td>${w ? esc(String(w.port)) : "—"}</td>
        <td>${w && w.pid != null ? esc(String(w.pid)) : "—"}</td>
        <td>${sleepLabel(w ? w.is_sleeping : null)}</td>
      </tr>`;
    })
    .join("");

  if (switchStartedAt && Date.now() - switchStartedAt > SWITCH_UI_TIMEOUT_MS) {
    el("banner-error").classList.remove("hidden");
    el("banner-error").textContent =
      "Switch timeout (10 min). Check container logs.";
  }
}

function applyRuntimeData(health, models, admin) {
  const workers = admin.workers || [];
  lastModels = models;
  lastAdmin = admin;
  lastHealth = health;

  el("last-refresh").textContent = fmtTime(new Date());

  wireControls();
  wireConfigEditor();
  renderStatusLine(health, admin, models);
  renderSwitchBanner(admin);
  renderErrorBanner(admin);
  renderModelPicker(models, admin, health);
  renderRuntimeTable(models, workers, admin, health);
  schedulePoll(pollInterval(admin, health));
}

async function refreshRuntime() {
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

  applyRuntimeData(healthRes.body, modelsRes.body.data || [], adminRes.body);
}

async function cancelSwitch() {
  el("banner-error").classList.add("hidden");
  el("banner-error").textContent = "";
  pendingTargetId = null;
  const { status, body } = await fetchJson("/v1/admin/models/cancel", { method: "POST" });
  if (status !== 200) {
    const msg = body?.error?.message || body?.detail || `HTTP ${status}`;
    el("banner-error").textContent = msg;
    el("banner-error").classList.remove("hidden");
  }
  switching = false;
  switchStartedAt = null;
  refreshRuntime().catch(onFetchError);
}

async function loadModel(modelId) {
  const selected = lastModels.find((m) => m.id === modelId);
  if (selected?.loaded && lastHealth?.ready) return;

  el("banner-error").classList.add("hidden");
  el("banner-error").textContent = "";
  pendingTargetId = modelId;
  userPickedModelId = modelId;
  switching = true;
  switchStartedAt = Date.now();
  updateLoadButtonState();
  updateSelectionStatus();
  const priorActive = lastHealth?.active_model || lastAdmin?.active_model_id;
  const loadState = priorActive && priorActive !== modelId ? "switching" : "starting";
  const loadMsg =
    loadState === "starting" ? `Starting vLLM: ${modelId}…` : `Switching to ${modelId}…`;
  renderSwitchBanner({ state: loadState, target_model_id: modelId, message: loadMsg });

  const { status, body } = await fetchJson("/v1/admin/models/load", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model_id: modelId }),
  });

  if (status === 202) {
    refreshRuntime().catch(onFetchError);
    return;
  }

  pendingTargetId = null;
  switching = false;
  const msg =
    body?.error?.message || body?.detail || (status === 503 ? "Switch already in progress" : `HTTP ${status}`);
  el("banner-error").textContent = msg;
  el("banner-error").classList.remove("hidden");
  refreshRuntime().catch(onFetchError);
}

async function runWarmup() {
  const latEl = el("warmup-latency");
  const activeId = lastHealth?.active_model || lastModels.find((m) => m.loaded)?.id;
  if (!activeId) {
    latEl.textContent = "";
    return;
  }

  const runId = (runWarmup._seq = (runWarmup._seq || 0) + 1);
  latEl.textContent = "…";
  const t0 = performance.now();

  const { ok, status, body } = await fetchJson("/v1/chat/completions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: activeId,
      messages: [{ role: "user", content: "Reply with one word: ok" }],
      max_tokens: 8,
      temperature: 0,
    }),
  });

  const ms = Math.round(performance.now() - t0);
  if (runId !== runWarmup._seq) return;

  if (ok) {
    latEl.textContent = `${ms} ms`;
    return;
  }
  const errMsg = body?.error?.message || body?.detail || `HTTP ${status}`;
  latEl.textContent = `${ms} ms · ${errMsg}`;
}

refreshRuntime().catch(onFetchError);
