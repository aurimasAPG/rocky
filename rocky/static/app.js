const $ = (id) => document.getElementById(id);
const BOARD_PATH = "deliverables/ads.html";

let current = { id: null, workspace: "", source: null, running: false };

function token() {
  return $("token").value.trim();
}

function headers() {
  const h = { "Content-Type": "application/json" };
  const t = token();
  if (t) h.Authorization = `Bearer ${t}`;
  return h;
}

function rememberToken() {
  // EventSource cannot send headers; a cookie keeps the token out of the URL and out of access logs.
  const t = token();
  document.cookie = t
    ? `rocky_token=${encodeURIComponent(t)}; path=/; SameSite=Strict`
    : "rocky_token=; path=/; Max-Age=0; SameSite=Strict";
}

async function api(path, options = {}) {
  const res = await fetch(path, { headers: headers(), ...options });
  let data = {};
  try { data = await res.json(); } catch { /* no body */ }
  if (res.status === 401) $("token-wrap").classList.remove("hidden");
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

async function boot() {
  $("workspace").value = "";
  try {
    const meta = await api("/api/meta");
    if (meta.needs_token) $("token-wrap").classList.remove("hidden");
    $("workspace").value = (meta.workspaces && meta.workspaces[0]) || meta.default_workspace || "";
    await refreshRuns();
  } catch {
    $("workspace").placeholder = "/absolute/path/to/project";
  }
}

function setActive(role) {
  document.querySelectorAll(".strip").forEach((el) => {
    el.classList.toggle("active", el.dataset.role === role);
  });
}

function commas(n) {
  return Number(n || 0).toLocaleString("en-US");
}

function paintBill(p) {
  $("tok-in").textContent = commas(p.prompt_tokens);
  $("tok-out").textContent = commas(p.completion_tokens);
  $("tok-total").textContent = commas(p.total_tokens);
  $("usd").textContent = `$${Number(p.usd || 0).toFixed(3)}`;
  $("eur").textContent = `€${Number(p.eur || 0).toFixed(3)}`;
  $("cost-meter").textContent =
    `$${Number(p.usd || 0).toFixed(3)} · €${Number(p.eur || 0).toFixed(3)} · ${commas(p.total_tokens)} tok`;
}

function workLine(text, cls) {
  const li = document.createElement("li");
  li.textContent = text;
  if (cls) li.className = cls;
  $("work-log").appendChild(li);
  $("work-log").scrollTop = $("work-log").scrollHeight;
}

function card(kind, title, body) {
  const el = document.createElement("div");
  el.className = `verify-card ${kind}`;
  const strong = document.createElement("strong");
  strong.textContent = title;
  const p = document.createElement("p");
  p.textContent = body || "";
  el.append(strong, p);
  $("verify-log").prepend(el);
}

function setRunning(on) {
  current.running = on;
  $("go").disabled = on;
  $("stop").disabled = !on;
}

function showAsk(ask) {
  $("ask-form").classList.remove("hidden");
  $("ask-question").textContent = ask.question || "";
  const opts = ask.options || [];
  $("ask-options").textContent = opts.length ? opts.join("  ·  ") : "";
  $("ask-input").value = "";
  $("ask-input").focus();
}

function hideAsk() {
  $("ask-form").classList.add("hidden");
}

function handle(event) {
  const p = event.payload || {};
  const t = event.type;
  if (t === "cost" || t === "done") paintBill(p);
  if (t === "run_started") $("status-meter").textContent = "running";
  if (t === "assessed") {
    workLine(`assess · ${p.complexity} · ${p.domain}${p.language ? " · " + p.language : ""}${p.estimated_eur != null ? " · ~€" + p.estimated_eur : ""}`);
  }
  if (t === "cast") {
    const models = p.models || {};
    const reasons = p.reasons || {};
    ["critic", "planner", "worker", "verifier"].forEach((role) => {
      $(`model-${role}`).textContent = models[role] || "—";
      $(`why-${role}`).textContent = reasons[role] || "";
    });
    setActive("critic");
  }
  if (t === "critique") {
    setActive("critic");
    const note = document.createElement("p");
    note.textContent = `critic · ${p.clarity}: ${p.quality_bar || ""}`;
    $("plan-log").appendChild(note);
  }
  if (t === "plan") {
    setActive("planner");
    const ol = $("plan-log");
    ol.innerHTML = "";
    const summary = document.createElement("p");
    summary.textContent = p.summary || "";
    ol.appendChild(summary);
    (p.steps || []).forEach((step) => {
      const li = document.createElement("li");
      li.textContent = `${step.title}: ${step.detail}`;
      ol.appendChild(li);
    });
    const d = document.createElement("p");
    d.className = "dim";
    d.textContent = `deliverable: ${p.deliverable_path || "—"}`;
    ol.appendChild(d);
  }
  if (t === "loop") workLine(`loop ${p.n}/${p.max}`, "dim");
  if (t === "subtask_start") { setActive("worker"); workLine(`subagent · ${p.title}`); }
  if (t === "subtask_skipped") workLine(`skipped · ${p.title} (done in a previous run)`, "dim");
  if (t === "tool_call") { setActive("worker"); workLine(`→ ${p.name}${p.subtask ? " · " + p.subtask : ""}`); }
  if (t === "tool_result") workLine(`← ${String(p.result || "").split("\n")[0].slice(0, 180)}`, "dim");
  if (t === "nudge") workLine(`nudge · ${p.subtask} stopped without writing ${(p.missing || []).join(", ")}`, "warn");
  if (t === "subtask_incomplete") workLine(`incomplete · ${p.title} never wrote ${(p.missing || []).join(", ")}`, "warn");
  if (t === "compact") workLine(`compact · ${p.subtask} · ${p.before_tokens} → ${p.after_tokens} tokens`, "dim");
  if (t === "loop_guard") workLine(`loop guard · ${p.action} ${p.name} in ${p.subtask}`, "warn");
  if (t === "stall") workLine(`stall · worker ${p.previous_worker} → ${p.worker}, plan kept`, "warn");
  if (t === "no_progress") workLine(`stopping · ${p.reason}`, "warn");
  if (t === "budget_offer") workLine(`budget · looks ${p.complexity} · ~€${p.estimated_eur} vs cap €${p.current_eur} · suggest €${p.suggested_eur}`, "warn");
  if (t === "budget_set") workLine(`budget · €${p.budget_eur} this run${p.lifted ? " (raised)" : ""}`);
  if (t === "budget_warn") workLine(`budget · ${p.note || "warning"}`, "warn");
  if (t === "budget") workLine(`budget · cap reached${p.stage ? " (" + p.stage + ")" : ""}`, "warn");
  if (t === "resumed") workLine(`resume · from ${p.from_run}${p.follow_up ? " · follow-up: " + p.follow_up : ""}`);
  if (t === "ask") showAsk(p);
  if (t === "verify") {
    setActive("verifier");
    const lines = [p.summary || ""];
    (p.blocking || []).forEach((b) => lines.push(`blocking: ${b}`));
    (p.issues || []).forEach((i) => lines.push(`${"blocking" in p ? "note" : "issue"}: ${i}`));
    card(p.passed ? "pass" : "fail", `${p.passed ? (p.notes_only ? "pass with notes" : "pass") : "fail"} · score ${p.score}`, lines.join("\n"));
  }
  if (t === "error") {
    card("fail", "error", p.message || "Run failed");
    $("status-meter").textContent = "error";
  }
  if (t === "done") {
    hideAsk();
    const status = (p.status || (p.passed ? "passed" : "unmet")).replace("_", " ");
    if (p.deliverable_exists && p.deliverable_path) {
      $("status-meter").textContent = `${status} · ${p.deliverable_path}`;
      card("pass", "result", p.deliverable_path + ((p.files || []).length > 1 ? `\nalso written: ${p.files.filter((f) => f !== p.deliverable_path).join(", ")}` : ""));
    } else {
      $("status-meter").textContent = `${status} · no deliverable`;
      card("fail", "no result", (p.deliverable_path ? `${p.deliverable_path} was never written` : "nothing was written")
        + ((p.files || []).length ? `\npartial work: ${p.files.join(", ")}` : "")
        + (current.id ? `\ncontinue with: rocky resume ${current.id}` : ""));
    }
    if (p.notes && p.notes.length) card("fail", "notes", p.notes.join("\n"));
    if (p.summary) card(p.passed ? "pass" : "fail", "summary", p.summary);
    if (p.deliverable_exists && p.deliverable_path === BOARD_PATH) showResult();
    setActive(p.passed ? "verifier" : "planner");
    setRunning(false);
    refreshRuns();
  }
}

function showResult() {
  $("result").classList.remove("hidden");
  $("result-frame").src = `/api/runs/${encodeURIComponent(current.id)}/board?workspace=${encodeURIComponent(current.workspace)}&v=${Date.now()}`;
}

function dismissedKey() {
  return `rocky-dismissed:${current.workspace}`;
}

function readDismissed() {
  try { return JSON.parse(localStorage.getItem(dismissedKey()) || "[]"); } catch { return []; }
}

async function fixFinding(finding) {
  try {
    const data = await api(`/api/runs/${encodeURIComponent(current.id)}/fix`, {
      method: "POST",
      body: JSON.stringify({ workspace: current.workspace, ad: finding.ad, code: finding.code, field: finding.field, value: finding.value }),
    });
    clearBoard();
    listen(data.id, data.workspace);
    refreshRuns();
  } catch (err) {
    workLine(`fix failed: ${err.message}`, "warn");
    showResult();
  }
}

window.addEventListener("message", (ev) => {
  const frame = $("result-frame");
  if (ev.source !== frame.contentWindow || !ev.data) return;
  const d = ev.data;
  if (d.type === "rocky-height") frame.style.height = `${Math.min(Number(d.h) || 0, 30000)}px`;
  if (d.type === "rocky-ready") frame.contentWindow.postMessage({ type: "rocky-restore", list: readDismissed() }, "*");
  if (d.type === "rocky-dismissed") {
    try { localStorage.setItem(dismissedKey(), JSON.stringify(d.list || [])); } catch { /* private window */ }
  }
  if (d.type === "rocky-fix") fixFinding(d);
});

function clearBoard() {
  $("plan-log").innerHTML = "";
  $("work-log").innerHTML = "";
  $("verify-log").innerHTML = "";
  hideAsk();
  paintBill({ prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, usd: 0, eur: 0 });
}

function listen(id, workspace) {
  if (current.source) current.source.close();
  current = { id, workspace, source: null, running: true };
  rememberToken();
  const es = new EventSource(`/api/runs/${encodeURIComponent(id)}/events?workspace=${encodeURIComponent(workspace)}`);
  current.source = es;
  setRunning(true);
  $("run-id").textContent = id;
  es.onmessage = (msg) => {
    try { handle(JSON.parse(msg.data)); } catch { /* ignore keepalives */ }
  };
  es.addEventListener("end", () => { es.close(); setRunning(false); });
  es.onerror = () => { /* browser retries; the stream ends itself when the run is over */ };
}

async function refreshRuns() {
  const workspace = $("workspace").value.trim();
  if (!workspace) return;
  try {
    const rows = await api(`/api/runs?workspace=${encodeURIComponent(workspace)}`);
    const ul = $("runs");
    ul.innerHTML = "";
    rows.slice(0, 12).forEach((r) => {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = "#";
      a.textContent = r.id;
      a.addEventListener("click", (ev) => { ev.preventDefault(); clearBoard(); listen(r.id, workspace); });
      const status = document.createElement("span");
      status.className = `status ${r.status}`;
      status.textContent = r.status;
      const rest = document.createElement("span");
      rest.textContent = ` ${r.eur != null ? "€" + Number(r.eur).toFixed(2) : ""} · ${(r.goal || "").slice(0, 60)}`
        + (r.deliverable_exists ? ` → ${r.deliverable_path}` : "");
      li.append(a, " ", status, rest);
      ul.appendChild(li);
    });
  } catch { /* token or workspace not ready yet */ }
}

$("run-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("form-error").hidden = true;
  $("go").disabled = true;
  clearBoard();
  $("status-meter").textContent = "starting";
  rememberToken();
  try {
    const data = await api("/api/runs", {
      method: "POST",
      body: JSON.stringify({
        goal: $("goal").value,
        workspace: $("workspace").value,
        budget: Number($("budget").value),
        max_loops: Number($("loops").value),
      }),
    });
    listen(data.id, data.workspace);
    refreshRuns();
  } catch (err) {
    $("form-error").hidden = false;
    $("form-error").textContent = err.message;
    $("go").disabled = false;
    $("status-meter").textContent = "idle";
  }
});

$("stop").addEventListener("click", async () => {
  if (!current.id) return;
  $("stop").disabled = true;
  try {
    await api(`/api/runs/${encodeURIComponent(current.id)}/stop`, { method: "POST", body: JSON.stringify({ workspace: current.workspace }) });
    workLine("stop requested — files already written stay", "warn");
  } catch (err) {
    workLine(`stop failed: ${err.message}`, "warn");
    $("stop").disabled = false;
  }
});

$("ask-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (!current.id) return;
  const text = $("ask-input").value.trim();
  try {
    await api(`/api/runs/${encodeURIComponent(current.id)}/answer`, { method: "POST", body: JSON.stringify({ workspace: current.workspace, text }) });
    workLine(`answer · ${text}`);
    hideAsk();
  } catch (err) {
    $("ask-options").textContent = err.message;
  }
});

let previewTimer = null;
let preview = { items: [] };

function paintPreview() {
  const on = preview.items.filter((item) => !item.off);
  $("will-check").classList.toggle("hidden", !preview.items.length);
  $("will-title").textContent = preview.title || "";
  $("will-edit").textContent = preview.edit || "";
  const shown = on.slice(0, 2).map((item) => item.question).join(" · ");
  $("will-list").textContent = on.length > 2 ? `${shown} · +${on.length - 2}` : shown;
  const list = $("will-edit-list");
  list.innerHTML = "";
  preview.items.forEach((item) => {
    const row = document.createElement("li");
    const label = document.createElement("label");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = !item.off;
    box.addEventListener("change", () => toggleCheck(item, box));
    label.append(box, ` ${item.question}`);
    row.append(label);
    list.append(row);
  });
}

async function toggleCheck(item, box) {
  try {
    await api("/api/playbook/toggle", {
      method: "POST",
      body: JSON.stringify({ workspace: $("workspace").value.trim(), number: item.number, off: !box.checked }),
    });
    item.off = !box.checked;
    paintPreview();
  } catch {
    box.checked = !item.off;
  }
}

async function loadPreview() {
  const goal = $("goal").value.trim();
  const workspace = $("workspace").value.trim();
  if (!goal || !workspace) {
    preview = { items: [] };
    paintPreview();
    return;
  }
  try {
    preview = await api(`/api/playbook/preview?goal=${encodeURIComponent(goal)}&workspace=${encodeURIComponent(workspace)}`);
    paintPreview();
  } catch { /* token or workspace not ready yet */ }
}

$("goal").addEventListener("input", () => {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(loadPreview, 350);
});

$("will-edit").addEventListener("click", () => $("will-edit-list").classList.toggle("hidden"));

$("workspace").addEventListener("change", refreshRuns);

boot();
