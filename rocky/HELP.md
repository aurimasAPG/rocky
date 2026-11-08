# Rocky — how to use it

Rocky is an OpenRouter **agent orchestrator**. You give it a goal and a folder. It picks models from the live catalog, splits the work, uses tools, and checks the result. The default money cap is **€1 per run**.

The command is `rocky`.

---

## 1. One-time setup

One line — it installs `uv` if needed, then Rocky as an isolated tool:

```bash
curl -fsSL https://raw.githubusercontent.com/aurimasAPG/rocky/main/install.sh | sh
```

Windows (PowerShell): `irm https://raw.githubusercontent.com/aurimasAPG/rocky/main/install.ps1 | iex`

Upgrade = run it again. Remove = `uv tool uninstall rocky`. Check = `rocky --version`.

Developers, from a clone: `uv tool install --force --reinstall .` (rerun after every code change). Put `~/.local/bin` on your `PATH`.

Put your key in `~/.env` (never commit it):

```
OPENROUTER_API_KEY=sk-or-...
```

Project `.env` and the process environment also work. No key yet? Just run `rocky` — on a terminal it asks for the key (LT / EN), saves it to `~/.env` with mode 600, and runs `rocky doctor` right away.

```bash
rocky doctor
```

checks the machine: Python version, key present and accepted by OpenRouter, `~/.env` permissions, workspace writable, sandbox mode, Playwright + Chromium, skills found, and the worker model resolved against the live catalog (a `ROCKY_WORKER` that does not exist is a `!`, because runs would silently fall back to the cheapest eligible model). `✓` fine, `!` works but could be safer, `✗` must be fixed. Exit code 1 when anything is `✗`.

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
rocky -d /tmp/rocky-demo "write a 3-sentence summary to deliverables/summary.md"
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
| `rocky ps` | List runs in this folder: status, **€ spent**, when it finished, goal, and the deliverable path (or the final summary). |
| `rocky cost` | What this folder has spent: total, today, last 7 days, per usable result, money spent on runs that gave nothing, the three costliest runs. |
| `rocky attach` | Reattach to the latest run. |
| `rocky attach ID` | Reattach to that run (prefix is enough). |
| `rocky resume [ID]` | Continue an `unmet` / `error` / `crashed` / `stopped` run from its saved plan and files. No id = the latest one. |
| `rocky stop [ID]` | End a running job now. Files already written stay; the run becomes `stopped` and can be resumed. No id = the latest run. |
| `rocky resume [ID] "what to change"` | Follow up on **any** finished run, passed ones included: same cast, same file, no new assess / critic; the planner starts from your instruction. |
| `rocky open [ID]` | Open the run's deliverable with the system opener and print its path. No id = the latest run. |
| `rocky serve` | Team web UI (default http://127.0.0.1:8787). |
| `rocky route "goal"` | Only assess + pick models. No tools, no files. |
| `rocky "goal" --json` | Headless: run to the end, print **one JSON object** (status, deliverable, score, cost). Questions get a safe default answer. Exit 0 = passed. |
| `rocky config` | Show the config files Rocky reads, the effective budget / loops / worker / language, and an example file if there is none. |
| `rocky doctor` | Check key, OpenRouter access, folder, sandbox, browser, skills. Exit 1 if anything is broken. |
| `rocky help` | Print this guide. `rocky --help` lists the commands and flags in one screen. |

---

## 4. Flags

| Flag | Default | Meaning |
| --- | --- | --- |
| `-d`, `--dir` | current directory | Workspace folder. File tools may only touch files inside it. |
| `--budget` | `1.00` | Max **EUR** for this run. |
| `--max-loops` | `4` | Plan → work → verify cycles. |
| `--model` | catalog default | Worker model for this run (an OpenRouter id such as `deepseek/deepseek-v4.1-flash`). Same as `ROCKY_WORKER`. |
| `--lang` | the goal's language | Write the deliverable in this language (`lt`, `en`, …) whatever the goal is written in. Same as `ROCKY_LANGUAGE`. |
| `--no-server-tools` | off | Do not use OpenRouter server-side web tools. |
| `--host` / `--port` | `127.0.0.1` `8787` | For `serve` only. |
| `--attach` | empty | TUI: attach to an existing run id. |
| `--json` | off | No live view; print the final result as JSON (for scripts, CI, other agents). |

The **next** run still defaults to €1 unless you pass `--budget` again — or you set a default in the config file.

### Config file

`~/.rocky/config.toml` holds defaults for every run; `<workspace>/.rocky/config.toml` overrides them per project. A flag on the command line wins over both, and an environment variable wins over the file.

```toml
budget = 3.0                          # EUR cap per run
max_loops = 4
worker = "google/gemini-3.8-flash"    # OpenRouter id
language = "lt"                       # force the deliverable language; omit to follow the goal
sandbox = "sandbox-exec"              # macOS; "docker" or "none"
# token = "team-secret"               # rocky serve
# workspaces = ["/Users/me/projects"] # rocky serve
```

Other keys: `image_model`, `sandbox_image`, `skills`, `recipes`, `bash_deny`, `base_url` — each maps to the `ROCKY_*` variable of the same meaning. `rocky config` prints which files are read and what a run would use; `rocky doctor` reports a broken file.

---

## 5. What happens on a run

1. **Assess** — cheap model: complexity (`trivial` / `moderate` / `hard`), domain, deliverable, and the **language** of the goal (`lt`, `en`, …; a heuristic fills it in if the model does not). Every later role is told to write the deliverable, summaries, notes, and any question to you in that language unless the goal asks for another — a Lithuanian brief gets a Lithuanian file, and the verifier treats the wrong language as blocking. The budget question comes in Lithuanian for Lithuanian goals (`taip` / `ne` / a number).
2. **Budget check** — if the guess is above your cap, Rocky asks to raise **this run only**. Reply `yes`, `keep`, or a number (`3`, `8`).
3. **Cast** — live OpenRouter catalog. Planner (**Claude Opus 5.5** by default; the strongest current model if the catalog lacks it), critic (the strongest model no dearer than the planner), worker (**Gemini 3.8 Flash** by default — Lithuanian + tools; override with `ROCKY_WORKER`), verifier (other family; read-only tools; strongest model under a price cap of $5 / $8 / $12 per million blended tokens for trivial / moderate / hard goals, because it re-reads the deliverable over several turns and its input tokens dominate — the first invohub run paid €1.50 per verification at $18/M). Automatic picks skip models the catalog gives an end date and models released more than 90 days before the newest of their own family, so a higher price cannot favour a previous generation. A worker you pin is exempt.
4. **Critic** — attacks the brief, sets a quality bar **sized to the goal and the cap**: a one-line goal with a €1 cap gets the bar a good freelancer meets in an afternoon, written as a few checkable statements — never "zero factual errors as judged by a historian", extra methodology sections, or formats you did not ask for. Skipped for `trivial` goals.
5. **Plan** — subtasks with `depends_on`. Skipped for `trivial` goals (one worker step straight to the verifier; the planner only steps in if verification fails). Independent steps can run in parallel (up to 4).
6. **Workers** — each subtask is its own chat, shared folder.
7. **Verifier** — judges the **whole** result. Trivial goals use the deliverable excerpt with at most two model calls and no tool loop. Moderate goals get up to 4 read-only turns and hard goals up to 6, with a JSON-only fallback if the provider will not mix tools and structured output. It can open files itself (`read_file`, `list_dir`, `grep`, `office_read` for decks and sheets). It sorts what it finds into **blocking** defects (file missing, wrong language or format, a required part absent, core content wrong) and **notes** (polish, headings, citations nobody asked for). A blocking defect is a fail: the planner resplits and workers continue. Notes alone end the run right there as `passed_with_notes` — you get the file and the notes instead of paying for loops of polish. On a replan the **deliverable path is kept**: the steps may change, the file you will be handed does not.
   If two verifications in a row fail, Rocky assumes the worker model is the problem: it keeps the plan and **swaps the worker** for a stronger one — the most capable model under ~$12/M from a family not tried yet (`stall` event) — instead of replanning again.
   Every replan sees the **plans already tried and their scores** and must change the approach, not resubmit the same steps. Steps that already completed are preserved only when their declared files still exist, so a repair does not pay to redo validated research or writing; a deleted file makes that step run again. If two loops in a row fail without beating the best score so far, Rocky **stops early** (`no_progress` event) and keeps the rest of the cap for a `rocky resume` with a changed brief, instead of paying for two more identical loops.
8. Stop when it passes, loops run out, or the euro cap is hit. Three endings: **passed**; **passed_with_notes** — the loops or the cap ran out, the deliverable is on disk, and the verifier says it is **usable** (a person could use it for the goal after fixing the listed points themselves in under an hour), or it scored ≥ 0.7; you get the file plus the notes, with any remaining blocking defects first as `must fix: …` (`--json` returns them under `notes`, exit code 0); **unmet** — the verifier says the result has to be redone, or there is no file. Inside the loop "almost right" is still a fail: that is what drives the replan.
   The last lines of every run are the **handover**: `result deliverables/…` (plus `also written …` for the notes and other files), or `no result … was never written`, `partial work …` listing what did land on disk, and `continue with rocky resume <id>`. Errors, nudges, incomplete steps, compactions, and loop guards are printed in words, not as raw event dicts. `--json` carries the same under `deliverable_path`, `deliverable_exists`, and `files`; `meta.json` stores them too.

Each worker chat is capped at 20 turns and kept small: old tool results become one-line stubs, and past ~60 % of the model's window — or **100k tokens**, whichever comes first — the middle of the thread is summarised by the cheap assess model (`compact` event). The 100k ceiling matters for 1M-context workers: without it a long research step re-sends hundreds of thousands of tokens on every turn.

Anthropic models get **prompt-cache breakpoints** on the system block and the task message, so every later turn of a worker or verifier chat re-reads them at cache price (`cached` in the receipt). OpenAI, Gemini, and DeepSeek cache a stable prefix automatically; Rocky keeps the system + task head of each chat byte-identical between turns so that prefix hits. A worker that repeats the exact same tool call is refused on the third try and stopped on the fourth (`loop_guard` event).

Before every chat completion Rocky estimates prompt cost and bounds `max_tokens` to what remains of the cap. An unaffordable request is blocked before it reaches the provider (`budget`, stage `preflight`). Actual usage still comes from the provider receipt: prompt estimates can differ, and image/tool charges or price changes can exceed the estimate. The cap is therefore a guarded budget, not an absolute billing guarantee. Verification gets no extra grace above it.

Every completion prints a **receipt**: input / output / total tokens (plus reasoning and cached tokens when the provider reports them), USD, EUR (3 decimals). The final receipt is also **split by role** — `assess €0.001 · critic €0.021 · planner €0.034 · worker €0.012 · verifier €0.009` — so you can see which model is eating the budget. `--json` returns the same split under `by_role`.

---

## 6. Detach and reattach (tmux-style)

A run is a **separate process**. Closing the viewer does not kill the job.

| You are in | Detach (job keeps going) |
| --- | --- |
| CLI attach | **Enter** or **Ctrl+C** |
| TUI | **q** then Enter (or Ctrl+Q) |

Detaching never kills the job. To end it: `rocky stop` (latest run) or `rocky stop ID`. The job gets a moment to exit cleanly, the run is marked `stopped`, whatever it wrote stays on disk, and `rocky resume ID` picks it up later.

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

### Resume a run that died

```bash
rocky resume            # latest unmet / error / crashed run in this folder
rocky resume 20260828-11
rocky resume --budget 3 # new cap for the continuation
```

Everything Rocky needs is already in `events.jsonl` and the workspace: the assessment, the critic's bar, the plan, the cast, the last verdict, and which steps finished. A resumed run does **not** pay for assess / critic / plan again. Steps that finished after the last plan and whose `writes` files are still on disk are skipped (`subtask_skipped` event); the rest run as usual. If the old run had already been judged by the verifier, the resume starts with a replan from that verdict. The new run gets its own id, is linked to the old one (`rocky ps` shows `↻`), and keeps the old cap unless you pass `--budget`.

Transient OpenRouter failures — timeouts, connection drops, 408 / 429 / 5xx, empty answers — are retried up to 3 times with backoff before a run gives up, so a single hiccup no longer kills a 25-minute job.

### Follow up on a good result

```bash
rocky open                                   # open the latest deliverable
rocky resume "sutrumpink iki 300 žodžių"     # build on the latest finished run
rocky resume 20260905-11 "add a CTA per ad"  # or on that one
```

A passed run is never resumed as-is (there is nothing to redo), but with an instruction it becomes the base of a **follow-up**: the assessment, critic's bar, cast, and the deliverable path are kept, and the planner replans from your instruction as if the verifier had asked for it. The new run is linked to the old one like any resume. In the interactive CLI the same thing happens when you just type the next instruction — memory ties it to the last deliverable — but `resume` skips the assess / critic calls and pins the file.

---

## 7. Live status

On a TTY, one spinner line is rewritten in place (Claude Code / OpenCode style): `thinking · plan`, `researching · …`, `writing · article.md`. Plan, critic, verify, and the price stay as panels. Detach with Enter.

---

## 8. CLI keys

- Type a goal, Enter — start a run and attach.
- Empty line or `q` at the **goal** prompt — leave Rocky.
- **Enter** or **Ctrl+C** while a run is streaming — detach (the job keeps going; `rocky stop` ends it).
- **Ctrl+C** at the goal prompt — leave Rocky.
- If the agent asks a question — type `yes` / `keep` / a number / a short answer.

Copy-paste in the CLI is normal terminal selection: drag, then Cmd+C (macOS) or Ctrl+C.

---

## 9. TUI keys

While a run is attached, one live line (Claude Code / OpenCode style) shows the current action: `thinking · plan`, `researching · …`, `writing · article.md`. It stays on the same row. The TUI puts that line above the three panes.

Panes: **plan** | **work** | **verify**. Type in the box at the bottom.

| Key | Action |
| --- | --- |
| Enter | Submit a new goal, or answer a question |
| q then Enter | Detach (does **not** kill the run; the input stays usable while a run streams) |
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

- `ROCKY_TOKEN` — shared password for the page. **Set this before binding a non-loopback host.** The page keeps it in a cookie and the `Authorization` header, never in the URL.
- `ROCKY_WORKSPACES` — comma-separated allowed folders. Unset, only the folder `rocky serve` was started in (and its subfolders) is allowed.

A run started from the page is the **same background job** as `rocky "goal"`: it appears in `rocky ps`, and `rocky attach`, `rocky stop`, `rocky resume`, and `rocky open` work on it. Every open tab follows the run's `events.jsonl` with its own cursor, so a second viewer or a page reload sees the whole run. The page renders budget offers, `clarify` questions (an answer box appears — the job waits about 10 minutes, then continues with a safe default), stalls, nudges, incomplete steps, the blocking-vs-notes verdict, and the final handover (result path, partial work, notes). **Stop the run** ends the job like `rocky stop`. The list at the bottom shows the folder's runs; click one to follow it.

---

## 11. Workspace and safety

Rocky is **fully autonomous inside `--dir`**. There is no “are you sure?” on file writes. File tools reject paths that escape the folder.

`bash` has two guards:

1. **Deny list** — commands that are destructive whatever the folder are refused before they run: wiping `/` or `~`, `mkfs`, `dd` to a device, fork bombs, `curl … | sh`, `sudo`, shutdown, `git push --force`, `git reset --hard`, `git clean -fdx`, `chmod 777 /`, and creating or replacing a virtual environment (`uv venv`, `uv run --python`, `python -m venv` — a worker once rebuilt a project's `.venv` with an empty interpreter; Rocky's own `python3` already has the Office libraries and comes first on the worker's `PATH`). Add your own regexes with `ROCKY_BASH_DENY="npm\s+publish;terraform\s+apply"`.
2. **Sandbox** (opt-in) — `ROCKY_SANDBOX=sandbox-exec` (macOS) lets a command read anything but write only inside `--dir` and temp folders. `ROCKY_SANDBOX=docker` runs it in a container with the workspace mounted at `/work` (`ROCKY_SANDBOX_IMAGE`, default `python:3.12-slim`; install `python-pptx`, `openpyxl` etc. in your image if workers build Office files). Default is `none`: the process starts in `--dir`, but a shell command can still `cd`, `curl`, or touch files elsewhere.

Set `ROCKY_SANDBOX=sandbox-exec` on a Mac. It costs nothing and it is the difference between “the agent edited my project” and “the agent edited my home folder”.

Do not point `-d` at your home directory. Use a dedicated project folder.

Never commit `OPENROUTER_API_KEY`. Put it in `~/.env` or a project `.env` that stays out of git.

`rocky serve` defaults to `127.0.0.1` (this machine only). `rocky serve --host 0.0.0.0` **without** `ROCKY_TOKEN` is refused: the process exits and the server does not bind. Set `ROCKY_TOKEN` before exposing the UI.

---

## 12. Workspace layout

Every plan follows one convention, recipe or not:

| What | Where |
| --- | --- |
| Working files — research, notes, drafts, scripts, scratch data | `runs/notes/` |
| Final deliverables — what you asked for | `deliverables/` |
| Workspace root | nothing, unless you name a root path in the goal |

The planner and every worker get this rule in their prompt; a bare deliverable name from the assessment or the planner (`summary.md`) is placed under `deliverables/` automatically. The bundled marketing recipe writes `runs/notes/research.md`, `runs/notes/voc.md`, `runs/notes/copy.md`, then `deliverables/campaign.pptx` and `deliverables/variants.xlsx`.

---

## 13. Tools the worker can call

`bash`, `read_file`, `write_file`, `append_file`, `patch`, `list_dir`, `grep`, `todo`, `web_search`, `web_fetch`, `browser_navigate` / `browser_snapshot` / `browser_close`, `vision_analyze`, `image_generate`, `clarify`, `skills_list`, `skill_view`, `office_read`, `memory`, `runs_search`, `recipe`, plus any **MCP** tools you configure (below).

### Images

`image_generate` draws a PNG with an OpenRouter image model and saves it under `deliverables/` (`prompt`, optional `path`, `reference_path` to edit or match an existing image, `aspect_ratio` such as `1:1`, `9:16`, `16:9`). Default model `google/gemini-nano-banana-2.1`; pin another with `ROCKY_IMAGE_MODEL` (e.g. `google/gemini-3-pro-image`, `openai/gpt-5.4-image-2`). If the first model refuses, the next candidate is tried. Every image is billed into the run's receipt like any other call. `vision_analyze` lets the worker check the result against its brief.

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

## 14. Skills

Bundled: `xlsx`, `docx`, `pptx`, `pdf`, `deep-research`, `marketing-pipeline`.

Rocky also loads a **research + marketing allowlist** from `~/.claude/skills` (copywriting, voc-to-copy, meta-ads-copy, …) plus the Lithuanian language skills `lietuviu-kalbos-rasyba` and `turtinga-lietuviu-kalba` when they exist there; a worker writing Lithuanian copy is told to open the spelling skill first. Personal skills stay out. Extra folders:

```bash
export ROCKY_SKILLS=/path/to/more/skills
```

Ads / copy / campaign goals are planned as **research → VOC → copy → pptx + xlsx → visuals** — that is the bundled `marketing` recipe. The visuals step writes `deliverables/visual-brief.md` (one block per creative: format, message, composition, brand hex colours, text overlay) and then `image_generate`s each block into `deliverables/visual-1.png`, `visual-2.png`, …

### Recipes

A recipe is a plan skeleton Rocky enforces when a goal matches: ordered steps, `depends_on`, the deliverable path, and keyword hints. Files are TOML:

- bundled: `rocky/recipes/*.toml` (`marketing` today — research, VOC, copy, deck + sheet, visuals)
- shared: folders in `ROCKY_RECIPES=/path/a,/path/b`
- this workspace: `<workspace>/.rocky/recipes/*.toml` (wins on the same name)

Each step can declare `writes = ["runs/notes/research.md"]` — files it must leave on disk. When a worker stops without them, Rocky nudges it (twice at most, `nudge` event); if they still do not exist the step is reported to the verifier as **INCOMPLETE** so later steps and the verdict cannot pretend the work happened. The planner's own steps get a `writes` list too.

Match order: `recipe:<name>` anywhere in the goal → assessed domain → hint words. If the planner already covers every required stage the plan is kept; otherwise the recipe steps replace it. Workers can `recipe list / view / save` — a saved recipe is used from the **next** run, so review it. See `rocky/recipes/marketing.toml` for the format.

---

## 15. Budget hints

| Looks like | Typical guess vs €1 |
| --- | --- |
| One-liner (`trivial`) | No critic, no planner: assess → worker → verifier, usually well under €0.01 |
| Short rewrite, no web | Stays under the cap — no question |
| Hard research, ads, multi-file | Rocky asks to raise **this run only** |

`--budget 10` on the command line skips the nag if the guess is below €10.

The guess is **calibrated from this folder's history**: Rocky takes the median of *actual ÷ estimated* over finished runs (same complexity when there are at least two such runs, otherwise all runs; clamped to 0.5–4×) and scales the table value by it. The `assessed` line shows `~€9.10 (×1.6 from 4 past runs)` when a factor is applied; `--json` carries `estimate_base_eur` and `calibration` in the assessment. Each finished run stores `eur`, `estimated_eur`, and `complexity` in its `meta.json` so the next guess learns from it.

While workers run, Rocky warns once (`budget_warn`, stage `half`) when **50 % of the cap is spent and the deliverable is still not on disk** — the moment to attach and decide whether to let it continue, or `rocky stop` it.

`rocky cost` sums the folder's `meta.json` files: total and per-period spend, spend per usable result, and how much went to runs that ended `unmet` / `error` / `crashed` / `stopped`.

---

## 16. Troubleshooting

| Symptom | Fix |
| --- | --- |
| `OPENROUTER_API_KEY is missing` | Run `rocky` on a terminal and paste the key, or put it in `~/.env`; then `rocky doctor` |
| `OpenRouter rejected the API key (401)` | The key is wrong or revoked. `rocky doctor`, then a fresh key from openrouter.ai/keys into `~/.env` |
| `OpenRouter account has no credits (402)` | Top up at openrouter.ai/credits |
| `Cannot reach OpenRouter after 4 attempts` | Network, DNS, or proxy. The catalog fetch and every completion are retried with backoff first; check the connection, then `rocky doctor` |
| `ModuleNotFoundError: rocky` | Reinstall without `--editable` (Python 3.13) |
| `rocky: command not found` | `uv tool install --force --reinstall .` from the clone; ensure `~/.local/bin` is on `PATH` |
| TUI “nothing happens” | Wait for the catalog fetch; the status line shows “starting”, then “fetching models”. If it stays there, check the key with `rocky doctor`. |
| Copy does not work in TUI | Select text, then Cmd+C. Quit with **q**, not Ctrl+C. |
| Run died after closing the window | It should not. `rocky ps` — if status is `crashed`, read `.rocky/runs/<id>/worker.log`, then `rocky resume` |
| Run ended `error` / `crashed` mid-way | `rocky resume [id]` — continues from the saved plan and the files already written |
| Verifier almost-passes | Only a **blocking** defect fails a loop and triggers a replan. Notes alone end the run as `passed_with_notes` with the file. After the last loop a score ≥ 0.7 with the file on disk and nothing blocking also ends as `passed_with_notes`. |

---

## 17. Memory

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

## 18. What Rocky is not

It is not a coding harness like Claude Code or Codex. Those keep one strong model in a long loop. Rocky is a **cast + budget + verify** shop: cheap hands, independent checker, euro cap. Use it for research, copy, decks, and bounded autonomous work in a folder. For a nasty repo refactor, a dedicated coding agent is usually better.
