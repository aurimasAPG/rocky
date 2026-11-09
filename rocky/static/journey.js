/* A task's journey is reconstructed from recorded events, never a guessed percent. */
const JOURNEY_STAGES = [
  ["assessing", "Understand", "Understanding your brief"],
  ["planning", "Plan", "Making a plan"],
  ["working", "Create", "Creating your result"],
  ["verifying", "Review", "Checking the result"],
];
const JOURNEY_END = new Set(["passed", "passed_with_notes", "unmet", "error", "crashed", "stopped"]);
const STEP_LABELS = { pending: "Up next", active: "In progress", finished: "Step finished", incomplete: "Needs attention", skipped: "Saved work reused" };
const TOOL_ACTIVITY = {
  write_file: "Writing a file", append_file: "Adding to a file", read_file: "Reading saved work",
  list_dir: "Checking workspace files", bash: "Running a workspace command", todo: "Updating the work plan",
  web_search: "Searching for sources", web_fetch: "Reading a source", ask_user: "Preparing a question for you",
  image_generate: "Creating an image", browser: "Checking a page",
};

function initialJourney() {
  return { stage: "assessing", status: "running", steps: [], loop: 0, max: 0, waiting: false,
    notice: "", activity: "Getting ready to read your brief.", startedAt: null, finishedAt: null,
    terminalAuthority: false, skippedPlan: false };
}

function reduceJourney(previous, event) {
  const state = { ...previous, steps: previous.steps.map(step => ({ ...step })) };
  const p = event.payload || {};
  const t = event.type;
  const terminal = JOURNEY_END.has(state.status);
  const stage = (name, activity) => {
    state.stage = name;
    if (!terminal) state.activity = activity;
  };
  const step = () => state.steps.find(item => String(item.id) === String(p.id));
  if (t === "run_started") {
    state.startedAt = state.startedAt || event.ts;
    state.max = p.max_loops || state.max;
  }
  if (t === "phase") {
    const label = JOURNEY_STAGES.find(item => item[0] === p.stage);
    if (label) stage(p.stage, label[2]);
    state.loop = Math.max(state.loop, p.loop || 0);
    state.max = p.max || state.max;
  }
  if (t === "catalog" && p.status === "fetching") stage("assessing", "Choosing models for your task.");
  if (t === "assessed" || t === "critique") stage("planning", "Turning your brief into a workable plan.");
  if (t === "plan") {
    state.steps = (p.steps || []).map(item => ({ id: item.id, title: item.title || "Untitled step", detail: item.detail || "", status: "pending" }));
    state.skippedPlan = p.skipped === "trivial";
    stage("working", "The plan is ready. Starting the work.");
  }
  if (t === "loop") {
    if (p.n > state.loop) state.steps.forEach(item => { item.status = "pending"; });
    state.loop = Math.max(state.loop, p.n || 0);
    state.max = p.max || state.max;
    stage("working", p.n > 1 ? "Revising the work after review." : "Starting the planned steps.");
  }
  if (t === "subtask_start") {
    let item = step();
    if (!item) { item = { id: p.id, title: p.title || "Working on the task", detail: p.detail || "" }; state.steps.push(item); }
    if (!["finished", "incomplete", "skipped"].includes(item.status)) item.status = "active";
    stage("working", p.title || "Working on the task.");
  }
  if (t === "subtask_incomplete" && step()) step().status = "incomplete";
  if (t === "subtask_done" && step()) {
    const item = step();
    item.status = item.status === "incomplete" || p.aborted ? "incomplete" : "finished";
    if (!terminal) state.activity = state.steps.some(item => item.status === "active")
      ? "Other steps are still in progress." : "The work steps have ended. Waiting for review.";
  }
  if (t === "subtask_skipped" && step()) step().status = "skipped";
  if (t === "tool_call") {
    if (p.subtask === "verifier") stage("verifying", "Checking the saved work against your brief.");
    else stage("working", TOOL_ACTIVITY[p.name] || "Working on the task.");
  }
  if (t === "tool_result" && ["ask_user", "clarify"].includes(p.name)) state.waiting = false;
  if (t === "ask" && !terminal) state.waiting = true;
  if (t === "answer_sent") state.waiting = false;
  if (t === "verify") {
    stage("verifying", p.passed ? "Review finished. Preparing your files." : "Review found issues. Rocky may revise the work.");
  }
  if (t === "error" && !terminal) state.notice = p.message || "A step reported a problem. Check the activity details.";
  if (t === "budget_warn" && !terminal) state.notice = p.note || "The task is approaching its spending limit.";
  if (t === "budget" && !terminal) state.notice = "The spending limit has been reached. Rocky is saving its progress.";
  if (t === "stall" && !terminal) state.notice = "Rocky changed the worker to try to make progress.";
  if (t === "no_progress" && !terminal) state.notice = p.reason || "Rocky could not make further progress.";
  if (t === "meta") {
    state.startedAt = state.startedAt || p.created;
    state.finishedAt = p.finished || state.finishedAt;
    if (!terminal && Object.prototype.hasOwnProperty.call(p, "ask")) state.waiting = Boolean(p.ask);
    if (JOURNEY_END.has(p.status)) { state.status = p.status; state.terminalAuthority = true; }
  }
  if (t === "done" && !state.terminalAuthority) {
    state.status = p.status || (p.passed ? "passed" : "unmet");
    state.finishedAt = event.ts || state.finishedAt;
  }
  if (JOURNEY_END.has(state.status)) {
    state.waiting = false;
    if (state.status === "passed" || state.status === "passed_with_notes") {
      state.stage = "verifying";
      state.activity = "Review complete. Your saved files are ready below.";
      state.notice = "";
    } else {
      state.activity = "The task has ended. Review any saved files and Rocky’s notes below.";
    }
  }
  return state;
}

let journey = initialJourney();
let journeyReconnecting = false;
let journeyRows = "";
const journeyExpandedSteps = new Set();

function resetJourney() {
  journey = initialJourney();
  journeyReconnecting = false;
  journeyRows = "";
  journeyExpandedSteps.clear();
  $("task-journey").open = true;
  $("run-details").open = false;
  renderJourney();
}

function updateJourney(event) {
  if (["cost", "memory", "cast", "worker", "compact", "budget_set", "resumed"].includes(event.type)) return;
  const wasTerminal = JOURNEY_END.has(journey.status);
  journey = reduceJourney(journey, event);
  if (!wasTerminal && JOURNEY_END.has(journey.status)) $("task-journey").open = false;
  renderJourney();
}

function setJourneyConnection(reconnecting) {
  journeyReconnecting = reconnecting;
  renderJourney();
}

function renderJourney() {
  const terminal = JOURNEY_END.has(journey.status);
  const passed = journey.status === "passed" || journey.status === "passed_with_notes";
  const index = JOURNEY_STAGES.findIndex(item => item[0] === journey.stage);
  const title = journey.waiting ? "Your answer is needed" : terminal ? "Task progress" : JOURNEY_STAGES[index][2];
  $("journey-title").textContent = title;
  $("journey-detail").textContent = journey.waiting ? "Answer the question below so Rocky can continue." : journey.activity;
  if (!terminal && current.id) $("handover-title").textContent = title;
  if (journey.waiting && !terminal) $("delivery-status").textContent = "Needs your input";
  $("journey-attempt").textContent = journey.loop ? `Attempt ${journey.loop}${journey.max ? ` of ${journey.max}` : ""}` : "";
  const finished = journey.steps.filter(item => ["finished", "skipped"].includes(item.status)).length;
  $("journey-count").textContent = journey.steps.length ? `${finished} of ${journey.steps.length} work steps finished` : "Preparing the work plan";
  $("journey-stages").replaceChildren();
  JOURNEY_STAGES.forEach(([key, label], position) => {
    const li = document.createElement("li");
    const simple = key === "planning" && journey.skippedPlan;
    const state = simple ? "skipped" : passed || position < index ? "finished" : position === index ? (terminal ? "ended" : "active") : "pending";
    li.dataset.state = state;
    if (state === "active") li.setAttribute("aria-current", "step");
    const name = document.createElement("strong"); name.textContent = label;
    const status = document.createElement("span");
    status.textContent = simple ? "Simple task" : state === "finished" ? "Done" : state === "active" ? (journey.waiting ? "Waiting for you" : "Current stage") : state === "ended" ? "Ended here" : terminal ? "Not reached" : "Up next";
    li.append(name, status);
    $("journey-stages").appendChild(li);
  });
  const rows = JSON.stringify([terminal, journey.steps]);
  if (rows !== journeyRows) {
    journeyRows = rows;
    $("journey-steps").replaceChildren();
    journey.steps.forEach(item => {
    const li = document.createElement("li"); li.dataset.state = item.status;
    const label = document.createElement("span"); label.className = "step-state";
    label.textContent = terminal && ["active", "pending"].includes(item.status) ? "Not completed" : STEP_LABELS[item.status];
    const name = document.createElement("strong"); name.textContent = item.title;
    li.append(label, name);
    if (item.detail) {
      const detail = document.createElement("details");
      const key = JSON.stringify([item.id, item.title, item.detail]);
      detail.open = journeyExpandedSteps.has(key);
      detail.addEventListener("toggle", () => {
        if (detail.open) journeyExpandedSteps.add(key); else journeyExpandedSteps.delete(key);
      });
      const summary = document.createElement("summary"); summary.textContent = "Step details";
      const text = document.createElement("p"); text.textContent = item.detail;
      detail.append(summary, text); li.appendChild(detail);
    }
    $("journey-steps").appendChild(li);
    });
  }
  $("journey-notice").textContent = journey.notice;
  $("journey-notice").classList.toggle("hidden", !journey.notice);
  $("journey-connection").textContent = journeyReconnecting && !terminal ? "Reconnecting to live progress… Your task can continue while this page reconnects." : "";
  $("journey-connection").classList.toggle("hidden", !journeyReconnecting || terminal);
}
