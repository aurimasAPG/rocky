# Rocky — how to use it

Rocky is an OpenRouter **agent orchestrator**. You give it a goal and a folder. It picks models from the live catalog, splits the work, uses tools, and checks the result. The default money cap is **€1 per run**.

The command is `rocky`. `orche` is a silent alias for the same binary.

---

## 1. One-time setup

From a clone of this repository:

```bash
uv tool install --force --reinstall .
```

Put `~/.local/bin` on your `PATH`. After you change the code, run the same command again from the clone.

Put your key in `~/.env` (never commit it):

```
OPENROUTER_API_KEY=sk-or-...
```

Project `.env` and the process environment also work.

Python 3.13: do **not** use `pip install -e .`. Hidden `.pth` files are skipped and `rocky` will fail to import. Use a normal install or `uv tool install` as above.

Optional browser tools (Playwright + Chromium):

```bash
uv tool install --force --reinstall ".[browser]"
playwright install chromium
```

---

## 2. First run

```bash
cd /path/to/the/project/you/want/edited
rocky
```

You should see a golden retriever banner. Type a task, press Enter.

One shot:

```bash
rocky -d /tmp/rocky-demo "write a 3-sentence summary to summary.md"
```

Interactive three-pane viewer:

```bash
rocky tui -d /tmp/rocky-demo
```

Read this file anytime:

```bash
rocky help
```

---

## 3. Commands

| Command | What it does |
| --- | --- |
| `rocky` | Interactive CLI. Type a goal, watch the loop, type another. |
| `rocky "goal"` | Start that goal as a **background run** and attach. |
| `rocky tui` | Same engine, three panes (plan / work / verify). |
| `rocky tui --attach ID` | Open the TUI on an existing run. |
| `rocky ps` | List runs in this folder. |
| `rocky attach` | Reattach to the latest run. |
| `rocky attach ID` | Reattach to that run (prefix is enough). |
| `rocky serve` | Team web UI (default http://127.0.0.1:8787). |
| `rocky route "goal"` | Only assess + pick models. No tools, no files. |
| `rocky "goal" --json` | Headless: run to the end, print **one JSON object** (status, deliverable, score, cost). Questions get a safe default answer. Exit 0 = passed. |
| `rocky help` | Print this guide. |

---

## 4. Flags

| Flag | Default | Meaning |
| --- | --- | --- |
| `-d`, `--dir` | current directory | Workspace folder. File tools may only touch files inside it. |
| `--budget` | `1.00` | Max **EUR** for this run. |
| `--max-loops` | `4` | Plan → work → verify cycles. |
| `--no-server-tools` | off | Do not use OpenRouter server-side web tools. |
| `--host` / `--port` | `127.0.0.1` `8787` | For `serve` only. |
| `--attach` | empty | TUI: attach to an existing run id. |
| `--json` | off | No live view; print the final result as JSON (for scripts, CI, other agents). |

The **next** run still defaults to €1 unless you pass `--budget` again.

---

## 5. What happens on a run

1. **Assess** — cheap model: complexity (`trivial` / `moderate` / `hard`), domain, deliverable.
2. **Budget check** — if the guess is above your cap, Rocky asks to raise **this run only**. Reply `yes`, `keep`, or a number (`3`, `8`).
3. **Cast** — live OpenRouter catalog. Planner (strong), worker (**Gemini 3.7 Flash** by default — Lithuanian + tools; override with `ROCKY_WORKER`), verifier (other family; read-only tools).
4. **Critic** — attacks the brief, sets a quality bar. Skipped for `trivial` goals.
5. **Plan** — subtasks with `depends_on`. Skipped for `trivial` goals (one worker step straight to the verifier; the planner only steps in if verification fails). Independent steps can run in parallel (up to 4).
6. **Workers** — each subtask is its own chat, shared folder.
7. **Verifier** — judges the **whole** result. It can open files itself (`read_file`, `list_dir`, `grep`, `office_read` for decks and sheets; up to 6 read-only calls). Almost-right is a fail; the planner resplits and workers continue.
   If two verifications in a row fail, Rocky assumes the worker model is the problem: it keeps the plan and **swaps the worker** for a stronger one — the most capable model under ~$12/M from a family not tried yet (`stall` event) — instead of replanning again.
8. Stop when it passes, loops run out, or the euro cap is hit.

Each worker chat is capped at 20 turns and kept small: old tool results become one-line stubs, and past ~60 % of the model's window the middle of the thread is summarised by the cheap assess model (`compact` event). A worker that repeats the exact same tool call is refused on the third try and stopped on the fourth (`loop_guard` event).

Every completion prints a **receipt**: input / output / total tokens (plus reasoning and cached tokens when the provider reports them), USD, EUR (3 decimals). The final receipt is also **split by role** — `assess €0.001 · critic €0.021 · planner €0.034 · worker €0.012 · verifier €0.009` — so you can see which model is eating the budget. `--json` returns the same split under `by_role`.

---

## 6. Detach and reattach (tmux-style)

A run is a **separate process**. Closing the viewer does not kill the job.

| You are in | Detach (job keeps going) |
| --- | --- |
| CLI attach | **Enter** |
| TUI | **q** then Enter (or Ctrl+Q) |

Come back:

```bash
rocky ps
rocky attach
rocky attach 20260827-14
rocky tui --attach 20260827-14
```

Logs live under the workspace:

```
.rocky/runs/<run-id>/
  meta.json
  events.jsonl
  worker.log
```

If Rocky asks a budget or clarify question while you are detached, attach again and answer. The worker waits (about 10 minutes), then continues with a safe default.

---

## 7. Live status

On a TTY, one spinner line is rewritten in place (Claude Code / OpenCode style): `thinking · plan`, `researching · …`, `writing · article.md`. Plan, critic, verify, and the price stay as panels. Detach with Enter.

---

## 8. CLI keys

- Type a goal, Enter — start a run and attach.
- Empty line or `q` at the **goal** prompt — leave Rocky.
- **Enter** while a run is streaming — detach.
- If the agent asks a question — type `yes` / `keep` / a number / a short answer.

Copy-paste in the CLI is normal terminal selection: drag, then Cmd+C (macOS) or Ctrl+C.

---

## 9. TUI keys

While a run is attached, one live line (Claude Code / OpenCode style) shows the current action: `thinking · plan`, `researching · …`, `writing · article.md`. It stays on the same row. The TUI puts that line above the three panes.

Panes: **plan** | **work** | **verify**. Type in the box at the bottom.

| Key | Action |
| --- | --- |
| Enter | Submit a new goal, or answer a question |
| q then Enter | Detach (does **not** kill the run) |
| Ctrl+Q | Quit the TUI |
| Page Up / Page Down | Scroll the work log |
| Drag | Select text |
| Cmd+C or Ctrl+C | Copy selection (macOS uses the real clipboard) |
| Cmd+V or Ctrl+V | Paste into the input |

Ctrl+C copies; it is not “kill the app”. Shift+drag uses the terminal’s own selection if you ever need it.

---

## 10. Team web UI

```bash
rocky serve
rocky serve --host 0.0.0.0 --port 8787
```

Env:

- `ROCKY_TOKEN` — shared password for the page. **Set this before binding a non-loopback host.**
- `ROCKY_WORKSPACES` — comma-separated allowed folders

The web UI has no human-in-the-loop for budget raises. If a task looks expensive, it keeps the cap and logs a warning.

---

## 11. Workspace and safety

Rocky is **fully autonomous inside `--dir`**. There is no “are you sure?” on file writes. File tools reject paths that escape the folder.

`bash` has two guards:

1. **Deny list** — commands that are destructive whatever the folder are refused before they run: wiping `/` or `~`, `mkfs`, `dd` to a device, fork bombs, `curl … | sh`, `sudo`, shutdown, `git push --force`, `git reset --hard`, `git clean -fdx`, `chmod 777 /`. Add your own regexes with `ROCKY_BASH_DENY="npm\s+publish;terraform\s+apply"`.
2. **Sandbox** (opt-in) — `ROCKY_SANDBOX=sandbox-exec` (macOS) lets a command read anything but write only inside `--dir` and temp folders. `ROCKY_SANDBOX=docker` runs it in a container with the workspace mounted at `/work` (`ROCKY_SANDBOX_IMAGE`, default `python:3.12-slim`; install `python-pptx`, `openpyxl` etc. in your image if workers build Office files). Default is `none`: the process starts in `--dir`, but a shell command can still `cd`, `curl`, or touch files elsewhere.

Set `ROCKY_SANDBOX=sandbox-exec` on a Mac. It costs nothing and it is the difference between “the agent edited my project” and “the agent edited my home folder”.

Do not point `-d` at your home directory. Use a dedicated project folder.

Never commit `OPENROUTER_API_KEY`. Put it in `~/.env` or a project `.env` that stays out of git.

`rocky serve` defaults to `127.0.0.1` (this machine only). `rocky serve --host 0.0.0.0` **without** `ROCKY_TOKEN` is refused: the process exits and the server does not bind. Set `ROCKY_TOKEN` before exposing the UI.

---

## 12. Tools the worker can call

`bash`, `read_file`, `write_file`, `patch`, `list_dir`, `grep`, `todo`, `web_search`, `web_fetch`, `browser_navigate` / `browser_snapshot` / `browser_close`, `vision_analyze`, `clarify`, `skills_list`, `skill_view`, `office_read`, `memory`, `runs_search`, plus any **MCP** tools you configure (below).

### MCP servers

Rocky speaks MCP over stdio. Put servers in `<workspace>/.rocky/mcp.json` (project) or `~/.rocky/mcp.json` (everywhere); the project file wins per server name. `mcpServers` (Claude Code / Cursor style) is accepted too.

```json
{
  "servers": {
    "playwright": {"command": "npx", "args": ["@playwright/mcp@latest"]},
    "notion": {"command": "notion-mcp", "env": {"NOTION_TOKEN": "${NOTION_TOKEN}"}},
    "old": {"command": "x", "disabled": true}
  }
}
```

Each server's tools appear to workers as `mcp__<server>__<tool>` with the server's own schema. Servers start on first use and stop when the run ends; a server that fails to start is skipped and listed in the `mcp` event.

### Project instructions

If the workspace has `ROCKY.md`, `AGENTS.md`, or `CLAUDE.md` (first found, up to 8,000 chars), it is injected into every role's prompt for the run as **PROJECT INSTRUCTIONS** — conventions, tone, paths, what not to touch.

Office/PDF (`.xlsx` `.docx` `.pptx` `.pdf`): the worker must `skill_view` the matching skill, then create the file with **openpyxl / python-docx / python-pptx / reportlab** via `bash`. Never `write_file` those binaries.

---

## 13. Skills

Bundled: `xlsx`, `docx`, `pptx`, `pdf`, `deep-research`, `marketing-pipeline`.

Rocky also loads a **research + marketing allowlist** from `~/.claude/skills` (copywriting, voc-to-copy, meta-ads-copy, …). Personal skills stay out. Extra folders:

```bash
export ROCKY_SKILLS=/path/to/more/skills
```

Ads / copy / campaign goals are planned as **research → VOC → copy → pptx + xlsx** — that is the bundled `marketing` recipe.

### Recipes

A recipe is a plan skeleton Rocky enforces when a goal matches: ordered steps, `depends_on`, the deliverable path, and keyword hints. Files are TOML:

- bundled: `rocky/recipes/*.toml` (`marketing` today)
- shared: folders in `ROCKY_RECIPES=/path/a,/path/b`
- this workspace: `<workspace>/.rocky/recipes/*.toml` (wins on the same name)

Each step can declare `writes = ["research.md"]` — files it must leave on disk. When a worker stops without them, Rocky nudges it (twice at most, `nudge` event); if they still do not exist the step is reported to the verifier as **INCOMPLETE** so later steps and the verdict cannot pretend the work happened. The planner's own steps get a `writes` list too.

Match order: `recipe:<name>` anywhere in the goal → assessed domain → hint words. If the planner already covers every required stage the plan is kept; otherwise the recipe steps replace it. Workers can `recipe list / view / save` — a saved recipe is used from the **next** run, so review it. See `rocky/recipes/marketing.toml` for the format.

---

## 14. Budget hints

| Looks like | Typical guess vs €1 |
| --- | --- |
| One-liner (`trivial`) | No critic, no planner: assess → worker → verifier, usually well under €0.01 |
| Short rewrite, no web | Stays under the cap — no question |
| Hard research, ads, multi-file | Rocky asks to raise **this run only** |

`--budget 10` on the command line skips the nag if the guess is below €10.

---

## 15. Troubleshooting

| Symptom | Fix |
| --- | --- |
| `OPENROUTER_API_KEY is missing` | Put the key in `~/.env` |
| `ModuleNotFoundError: rocky` | Reinstall without `--editable` (Python 3.13) |
| `rocky: command not found` | `uv tool install --force --reinstall .` from the clone; ensure `~/.local/bin` is on `PATH` |
| TUI “nothing happens” | Wait for catalog fetch; you should see “working…” immediately. Check the key. |
| Copy does not work in TUI | Select text, then Cmd+C. Quit with **q**, not Ctrl+C. |
| Run died after closing the window | It should not. `rocky ps` — if status is `crashed`, read `.rocky/runs/<id>/worker.log` |
| Verifier almost-passes | That is a fail. Rocky replans until loops or budget run out. |

---

## 16. Memory

Each new goal used to start blank. Rocky now keeps Hermes-style memory in the same workspace.

| Store | Where | Cap | What belongs there |
| --- | --- | --- | --- |
| **USER.md** | `~/.rocky/memories/USER.md` | 1,375 chars | You: language, tone, habits |
| **MEMORY.md** | `<workspace>/.rocky/MEMORY.md` | 2,200 chars | This folder: paths, conventions, learned facts |
| **Recent runs** | `.rocky/runs/*/meta.json` | last 5 finished | Previous goals and verifier summaries |

USER + MEMORY are **frozen at run start** and injected into critic, planner, workers, and verifier. Writes via the `memory` tool hit disk immediately but show up in the **next** run’s prompt (same freeze as Hermes, so this run does not rewrite its own system prompt).

`runs_search` looks through older runs when the user says “that article” / “the last deck”. Follow-up goals in the same folder (“now make a pptx from that”) should continue the last deliverable unless you clearly replace it.

Do not dump transcripts into memory. Compact facts only. If a file is full, replace or remove an entry first.

Override the home folder in tests or odd setups: `ROCKY_HOME=/path`.

---

## 17. What Rocky is not

It is not a coding harness like Claude Code or Codex. Those keep one strong model in a long loop. Rocky is a **cast + budget + verify** shop: cheap hands, independent checker, euro cap. Use it for research, copy, decks, and bounded autonomous work in a folder. For a nasty repo refactor, a dedicated coding agent is usually better.
