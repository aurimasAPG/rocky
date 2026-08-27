const $ = (id) => document.getElementById(id);

function headers() {
  const token = $("token").value.trim();
  const h = { "Content-Type": "application/json" };
  if (token) h.Authorization = `Bearer ${token}`;
  return h;
}

async function boot() {
  $("workspace").value = "";
  try {
    const res = await fetch("/api/meta", { headers: headers() });
    if (res.status === 401) {
      $("token-wrap").classList.remove("hidden");
      return;
    }
    const meta = await res.json();
    if (meta.needs_token) $("token-wrap").classList.remove("hidden");
    $("workspace").value = (meta.workspaces && meta.workspaces[0]) || meta.default_workspace || "";
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

function handle(event) {
  const p = event.payload || {};
  if (event.type === "cost" || event.type === "done") paintBill(p);
  if (event.type === "run_started") $("status-meter").textContent = "running";
  if (event.type === "cast") {
    const models = p.models || {};
    const reasons = p.reasons || {};
    ["critic", "planner", "worker", "verifier"].forEach((role) => {
      $(`model-${role}`).textContent = models[role] || "—";
      $(`why-${role}`).textContent = reasons[role] || "";
    });
    setActive("critic");
  }
  if (event.type === "critique") {
    setActive("critic");
    const ol = $("plan-log");
    const note = document.createElement("p");
    note.textContent = `critic · ${p.clarity}: ${p.quality_bar || ""}`;
    ol.appendChild(note);
  }
  if (event.type === "plan") {
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
  }
  if (event.type === "subtask_start") {
    setActive("worker");
    const li = document.createElement("li");
    li.textContent = `subagent · ${p.title}`;
    $("work-log").appendChild(li);
  }
  if (event.type === "tool_call") {
    setActive("worker");
    const li = document.createElement("li");
    li.textContent = `→ ${p.name}${p.subtask ? " · " + p.subtask : ""}`;
    $("work-log").appendChild(li);
  }
  if (event.type === "tool_result") {
    const li = document.createElement("li");
    const line = String(p.result || "").split("\n")[0];
    li.textContent = `← ${line.slice(0, 180)}`;
    $("work-log").appendChild(li);
  }
  if (event.type === "verify") {
    setActive("verifier");
    const card = document.createElement("div");
    card.className = `verify-card ${p.passed ? "pass" : "fail"}`;
    card.innerHTML = `<strong>${p.passed ? "pass" : "fail"}</strong> · score ${p.score}<p></p>`;
    card.querySelector("p").textContent = p.summary || "";
    $("verify-log").prepend(card);
  }
  if (event.type === "done") {
    $("status-meter").textContent = p.passed ? "passed" : "unmet";
    $("go").disabled = false;
    setActive(p.passed ? "verifier" : "planner");
  }
  if (event.type === "error") {
    $("form-error").hidden = false;
    $("form-error").textContent = p.message || "Run failed";
    $("status-meter").textContent = "error";
    $("go").disabled = false;
  }
}

function listen(id) {
  const token = $("token").value.trim();
  const url = `/api/runs/${id}/events${token ? `?token=${encodeURIComponent(token)}` : ""}`;
  const es = new EventSource(url);
  es.onmessage = (msg) => {
    try { handle(JSON.parse(msg.data)); } catch { /* ignore keepalives */ }
  };
  es.addEventListener("end", () => es.close());
  es.onerror = () => {
    /* browser retries; stop if the run is over */
  };
}

$("run-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("form-error").hidden = true;
  $("go").disabled = true;
  $("plan-log").innerHTML = "";
  $("work-log").innerHTML = "";
  $("verify-log").innerHTML = "";
  paintBill({ prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, usd: 0, eur: 0 });
  $("status-meter").textContent = "starting";
  try {
    const res = await fetch("/api/runs", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({
        goal: $("goal").value,
        workspace: $("workspace").value,
        budget: Number($("budget").value),
        max_loops: Number($("loops").value),
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Could not start");
    listen(data.id);
  } catch (err) {
    $("form-error").hidden = false;
    $("form-error").textContent = err.message;
    $("go").disabled = false;
    $("status-meter").textContent = "idle";
  }
});

boot();
