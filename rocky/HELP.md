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

The **next** run still defaults to €1 unless you pass `--budget` again.

---

## 5. What happens on a run

1. **Assess** — cheap model: complexity (`trivial` / `moderate` / `hard`), domain, deliverable.
2. **Budget check** — if the guess is above your cap, Rocky asks to raise **this run only**. Reply `yes`, `keep`, or a number (`3`, `8`).
3. **Cast** — live OpenRouter catalog. Planner (strong), worker (cheap, tools), verifier (other family, no tools).
4. **Critic** — attacks the brief, sets a quality bar.
5. **Plan** — subtasks with `depends_on`. Independent steps can run in parallel (up to 4).
6. **Workers** — each subtask is its own chat, shared folder.
7. **Verifier** — judges the **whole** result. Almost-right is a fail; the planner resplits and workers continue.
8. Stop when it passes, loops run out, or the euro cap is hit.

Every completion prints a **receipt**: input / output / total tokens, USD, EUR (3 decimals).

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

## 7. CLI keys

- Type a goal, Enter — start a run and attach.
- Empty line or `q` at the **goal** prompt — leave Rocky.
- **Enter** while a run is streaming — detach.
- If the agent asks a question — type `yes` / `keep` / a number / a short answer.

Copy-paste in the CLI is normal terminal selection: drag, then Cmd+C (macOS) or Ctrl+C.

---

## 8. TUI keys

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

## 9. Team web UI

```bash
rocky serve
rocky serve --host 0.0.0.0 --port 8787
```

Env:

- `ROCKY_TOKEN` — shared password for the page. **Set this before binding a non-loopback host.**
- `ROCKY_WORKSPACES` — comma-separated allowed folders

The web UI has no human-in-the-loop for budget raises. If a task looks expensive, it keeps the cap and logs a warning.

---

## 10. Workspace and safety

Rocky is **fully autonomous inside `--dir`**. There is no “are you sure?” on file writes. File tools reject paths that escape the folder.

`bash` is **not a sandbox**. The process starts in `--dir`, but a shell command can still `cd`, `curl`, or touch files elsewhere. Treat the workspace as a machine the agent may operate.

Do not point `-d` at your home directory. Use a dedicated project folder.

Never commit `OPENROUTER_API_KEY`. Put it in `~/.env` or a project `.env` that stays out of git.

`rocky serve` defaults to `127.0.0.1` (this machine only). `rocky serve --host 0.0.0.0` **without** `ROCKY_TOKEN` is refused: the process exits and the server does not bind. Set `ROCKY_TOKEN` before exposing the UI.

---

## 11. Tools the worker can call

`bash`, `read_file`, `write_file`, `patch`, `list_dir`, `grep`, `todo`, `web_search`, `web_fetch`, `browser_navigate` / `browser_snapshot` / `browser_close`, `vision_analyze`, `clarify`, `skills_list`, `skill_view`, `office_read`.

Office/PDF (`.xlsx` `.docx` `.pptx` `.pdf`): the worker must `skill_view` the matching skill, then create the file with **openpyxl / python-docx / python-pptx / reportlab** via `bash`. Never `write_file` those binaries.

---

## 12. Skills

Bundled: `xlsx`, `docx`, `pptx`, `pdf`, `deep-research`, `marketing-pipeline`.

Rocky also loads a **research + marketing allowlist** from `~/.claude/skills` (copywriting, voc-to-copy, meta-ads-copy, …). Personal skills stay out. Extra folders:

```bash
export ROCKY_SKILLS=/path/to/more/skills
```

Ads / copy / campaign goals are planned as **research → VOC → copy → pptx + xlsx**.

---

## 13. Budget hints

| Looks like | Typical guess vs €1 |
| --- | --- |
| Short rewrite, no web | Stays under the cap — no question |
| Hard research, ads, multi-file | Rocky asks to raise **this run only** |

`--budget 10` on the command line skips the nag if the guess is below €10.

---

## 14. Troubleshooting

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

## 15. What Rocky is not

It is not a coding harness like Claude Code or Codex. Those keep one strong model in a long loop. Rocky is a **cast + budget + verify** shop: cheap hands, independent checker, euro cap. Use it for research, copy, decks, and bounded autonomous work in a folder. For a nasty repo refactor, a dedicated coding agent is usually better.
