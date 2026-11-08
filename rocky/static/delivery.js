/* A run's handover is scoped to its identity, including async previews. */
const TERMINAL_STATES = new Set(["passed", "passed_with_notes", "unmet", "error", "crashed", "stopped"]);
const OUTCOME_LABELS = {
  starting: "Starting", running: "Working", waiting: "Needs your input",
  passed: "Complete", passed_with_notes: "Complete with notes", unmet: "Needs more work",
  error: "Failed", crashed: "Interrupted", stopped: "Stopped",
};
let filesRequest = 0;
let previewRequest = 0;
let previewContent = "";
let boardIdentity = null;

function runIdentity() { return `${current.workspace}\n${current.id}`; }

function resetDelivery() {
  filesRequest++;
  previewRequest++;
  previewContent = "";
  boardIdentity = null;
  $("handover").classList.add("hidden");
  $("delivery-files").classList.add("hidden");
  $("file-preview").classList.add("hidden");
  $("result").classList.add("hidden");
  $("result-frame").removeAttribute("src");
  $("result-frame").style.height = "";
  $("file-list").replaceChildren();
  $("preview-text").textContent = "";
  $("preview-message").textContent = "";
  $("download-result").removeAttribute("href");
  $("delivery-notes").replaceChildren();
  $("delivery-notes").classList.add("hidden");
  $("files-message").textContent = "";
  $("retry-files").classList.add("hidden");
}

function renderDelivery() {
  const meta = current.meta || {};
  const status = meta.status || "starting";
  const finished = TERMINAL_STATES.has(status);
  $("handover").classList.remove("hidden");
  $("handover").dataset.status = status;
  $("handover-title").textContent = finished
    ? (meta.passed ? "Your work, ready to review" : "Your work so far")
    : "Working on your result";
  $("selected-goal").textContent = meta.goal || "Loading task…";
  $("delivery-status").textContent = OUTCOME_LABELS[status] || status;
  $("delivery-summary").textContent = finished ? (meta.summary || "") : "Rocky is working in your workspace. Saved files will appear here when the run ends.";
  $("delivery-review").open = !finished || !meta.passed;
  $("delivery-workspace").textContent = current.workspace;
  $("new-task").textContent = finished ? "New task" : "View task";
  const notes = meta.notes || [];
  $("delivery-notes").replaceChildren();
  notes.forEach((note) => {
    const li = document.createElement("li");
    li.textContent = note;
    $("delivery-notes").appendChild(li);
  });
  $("delivery-notes").classList.toggle("hidden", !notes.length);
}

function artifactUrl(path, download = false) {
  const params = new URLSearchParams({ workspace: current.workspace, path });
  if (download) params.set("download", "true");
  return `/api/runs/${encodeURIComponent(current.id)}/artifact?${params}`;
}

function readableSize(bytes) {
  if (bytes == null) return "";
  return bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

async function previewArtifact(file) {
  const request = ++previewRequest;
  const identity = runIdentity();
  previewContent = "";
  $("preview-text").textContent = "";
  $("preview-name").textContent = file.path;
  $("file-preview").classList.remove("hidden");
  $("copy-result").disabled = true;
  $("copy-result").textContent = "Copy text";
  $("copy-result").classList.toggle("hidden", !file.previewable || !file.available);
  $("download-result").classList.toggle("hidden", !file.available);
  $("download-result").removeAttribute("href");
  if (file.available) {
    rememberToken();
    $("download-result").href = artifactUrl(file.path, true);
  }
  document.querySelectorAll("#file-list button").forEach((button) => {
    button.setAttribute("aria-current", String(button.dataset.path === file.path));
  });
  if (!file.available) {
    $("preview-message").textContent = "This file is no longer in the workspace. Its path is retained here so you can identify what is missing.";
    return;
  }
  if (!file.previewable) {
    $("preview-message").textContent = "Download this file to open it in your preferred application.";
    return;
  }
  $("preview-message").textContent = "Loading preview…";
  try {
    const data = await api(artifactUrl(file.path));
    if (request !== previewRequest || identity !== runIdentity()) return;
    previewContent = data.text;
    $("preview-text").textContent = data.text;
    $("preview-message").textContent = data.truncated ? "Showing the beginning of this file. Download it for the full contents." : "";
    $("copy-result").textContent = data.truncated ? "Copy preview" : "Copy text";
    $("copy-result").disabled = false;
  } catch (err) {
    if (request !== previewRequest || identity !== runIdentity()) return;
    $("preview-message").textContent = `Preview unavailable: ${err.message}. Select the file again to retry.`;
  }
}

async function loadDeliveryFiles() {
  if (!current.id) return;
  const request = ++filesRequest;
  const identity = runIdentity();
  $("delivery-files").classList.remove("hidden");
  $("files-message").textContent = "Checking saved files…";
  $("retry-files").classList.add("hidden");
  try {
    const data = await api(`/api/runs/${encodeURIComponent(current.id)}/artifacts?workspace=${encodeURIComponent(current.workspace)}`);
    if (request !== filesRequest || identity !== runIdentity()) return;
    $("file-list").replaceChildren();
    const files = data.files || [];
    $("files-message").textContent = files.length ? "" : "No saved files were reported for this task. The activity below may explain what happened.";
    $("delivery-layout").classList.toggle("hidden", !files.length);
    files.forEach((file) => {
      const li = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.path = file.path;
      const name = document.createElement("strong");
      name.textContent = file.name;
      const detail = document.createElement("small");
      detail.textContent = file.available ? `${file.primary ? "Main result · " : ""}${readableSize(file.size)}` : "File missing";
      button.append(name, detail);
      button.addEventListener("click", () => previewArtifact(file));
      li.appendChild(button);
      $("file-list").appendChild(li);
    });
    const primary = files.find((file) => file.primary && file.available) || files.find((file) => file.available) || files[0];
    if (primary) previewArtifact(primary);
    else {
      previewRequest++;
      $("file-preview").classList.add("hidden");
    }
    if (files.some((file) => file.path === BOARD_PATH && file.primary && file.available)) showResult();
    else {
      boardIdentity = null;
      $("result").classList.add("hidden");
      $("result-frame").removeAttribute("src");
    }
  } catch (err) {
    if (request !== filesRequest || identity !== runIdentity()) return;
    $("files-message").textContent = `Could not load saved files: ${err.message}`;
    $("retry-files").classList.remove("hidden");
  }
}

function initDelivery() {
  $("retry-files").addEventListener("click", loadDeliveryFiles);
  $("new-task").addEventListener("click", () => {
    $("task-composer").open = true;
    if (!current.running) {
      $("goal").value = "";
      loadPreview();
    } else {
      $("goal").value = current.meta.goal || "";
    }
    $("goal").focus();
    $("task-composer").scrollIntoView({ block: "start" });
  });
  $("copy-result").addEventListener("click", async () => {
    const request = previewRequest;
    try {
      await navigator.clipboard.writeText(previewContent);
      if (request === previewRequest) $("preview-message").textContent = "Copied to clipboard.";
    } catch {
      if (request !== previewRequest) return;
      $("preview-message").textContent = "Clipboard unavailable. Select the text below and copy it with your keyboard.";
      $("preview-text").focus();
    }
  });
}
