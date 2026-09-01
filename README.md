# rocky

OpenRouter agent orchestrator. One command reads a goal, picks three models from the live catalog, then loops **plan → work → verify** until the goal is met. Default cap: **€1 per run**.

```bash
rocky "write a 3-sentence summary to summary.md"
rocky tui
rocky serve
rocky help
```

`orche` is a silent alias for `rocky`.

## Install

One line. It installs [uv](https://docs.astral.sh/uv/) if you do not have it, then Rocky as an isolated tool.

**macOS / Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/aurimasAPG/rocky/main/install.sh | sh
```

**Windows (PowerShell)**

```powershell
irm https://raw.githubusercontent.com/aurimasAPG/rocky/main/install.ps1 | iex
```

**uv / pipx**

```bash
uv tool install git+https://github.com/aurimasAPG/rocky
pipx install git+https://github.com/aurimasAPG/rocky
```

**From a clone** (developers — rerun after every code change)

```bash
git clone https://github.com/aurimasAPG/rocky && cd rocky
uv tool install --force --reinstall .
```

Then `rocky --version`. Browser tools: `ROCKY_EXTRAS=browser` before the one-liner, or `uv tool install "rocky[browser] @ git+…"` plus `playwright install chromium`. Pin a branch or tag with `ROCKY_REF=v0.1.0`. Python 3.11+; the installer picks 3.12.

**Python 3.13:** do not use `pip install -e .`. Hidden `.pth` files are skipped and `rocky` will fail to import.

Upgrade: run the same one-liner again. Remove: `uv tool uninstall rocky`.

Key (never commit it) in `~/.env`, project `.env`, or the environment:

```
OPENROUTER_API_KEY=sk-or-...
```

No key yet? Run `rocky` — it asks for the key, saves it to `~/.env`, and runs `rocky doctor` (key, OpenRouter access, folder, sandbox, browser, skills).

Optional browser tools:

```bash
uv tool install --force --reinstall ".[browser]"
playwright install chromium
```

Full English guide: `rocky help` (same text as [`rocky/HELP.md`](rocky/HELP.md)). Lietuviškai: [`docs/lt.md`](docs/lt.md).

## Quick start

```bash
mkdir -p /tmp/rocky-demo
rocky -d /tmp/rocky-demo
```

Type a task, press Enter. One shot:

```bash
rocky -d /tmp/rocky-demo "write a 3-sentence summary to summary.md"
```

Three-pane viewer: `rocky tui -d /tmp/rocky-demo`.

On a TTY the attach view keeps **one live status line** (`thinking · plan`, `researching · …`, `writing · article.md`) like Claude Code / OpenCode. Plan, critic, verify, and the price stay as panels.

A run keeps going if you detach. Enter (CLI) or `q` (TUI) unhooks the viewer; the worker continues. Reattach later:

```bash
rocky ps
rocky attach
rocky attach 20260827-12
rocky tui --attach 20260827-12
```

Flags: `--dir`, `--budget` (EUR, default **1.00**), `--max-loops` (default 4).

Every run prints a **receipt**: input / output / total / cached tokens, USD (OpenRouter), EUR (ECB via Frankfurter), and a per-role split (assess · critic · planner · worker · verifier).

## Commands

| Command | What it does |
| --- | --- |
| `rocky "goal"` | Start a background run and attach |
| `rocky tui "goal"` | Same, three-pane viewer (`q` detaches) |
| `rocky ps` | List runs in this folder |
| `rocky attach [id]` | Reattach (Enter detaches) |
| `rocky serve` | Team web UI at http://127.0.0.1:8787 |
| `rocky resume [id]` | Continue an unmet / error / crashed run from its saved plan |
| `rocky doctor` | Check key, OpenRouter access, sandbox, browser, skills |
| `rocky help` | Full English user guide |
| `rocky "goal" --json` | Headless: print one JSON result, exit 0 on pass |
| `rocky route "goal"` | Only assess + pick models (no tools) |

## Safety

Rocky is **fully autonomous** in `--dir`. File tools reject paths outside that folder. There is no confirmation on writes.

`bash` refuses a deny list of destructive commands (`rm -rf /`, `sudo`, `mkfs`, `curl | sh`, `git push --force`, …; extend with `ROCKY_BASH_DENY`). It is **not sandboxed by default**: set `ROCKY_SANDBOX=sandbox-exec` (macOS, writes only inside the workspace) or `ROCKY_SANDBOX=docker` (workspace mounted at `/work`). Do not point `-d` at your home directory.

`rocky serve` binds `127.0.0.1` by default. `rocky serve --host 0.0.0.0` without `ROCKY_TOKEN` is **refused** (the server does not bind). Set `ROCKY_TOKEN` and optionally `ROCKY_WORKSPACES` before exposing the UI.

## Memory

The next goal in the same folder continues the last one. Frozen at each run start (Hermes-style):

- `~/.rocky/memories/USER.md` — who you are (1,375 chars)
- `<workspace>/.rocky/MEMORY.md` — facts about this project (2,200 chars)
- last 5 finished runs (goal + summary)

Workers can `memory` add/replace/remove and `runs_search` older runs. Disk updates immediately; the prompt snapshot refreshes on the **next** run. A `ROCKY.md` / `AGENTS.md` / `CLAUDE.md` in the workspace is injected as project instructions. Details: `rocky help`.

## MCP

Add stdio MCP servers in `<workspace>/.rocky/mcp.json` or `~/.rocky/mcp.json`; their tools show up to workers as `mcp__<server>__<tool>`. Format and examples: `rocky help`.

## How models are chosen

On every run Rocky fetches OpenRouter’s `/models` catalog (tool-capable models + pricing):

1. A cheap model **assesses** complexity (`trivial` / `moderate` / `hard`) and domain.
2. A **critic** challenges the brief and sets a quality bar (skipped, together with the planner, when the task is `trivial`).
3. **Planner** splits work into subtasks with `depends_on` (independent steps can run in parallel).
4. **Workers** — one subagent per subtask. Default model: **Google Gemini 3.7 Flash** (`google/gemini-3.7-flash`). Override with `ROCKY_WORKER`. Isolated conversation, shared folder.
5. **Verifier** judges the **whole** result against the original goal and the critic’s quality bar.

Failures go back to the planner. Marketing / ads / copy goals follow the bundled `marketing` **recipe** (research → VOC → copy → pptx + xlsx → visuals via `image_generate`). Add your own recipes as TOML in `<workspace>/.rocky/recipes/` or `ROCKY_RECIPES`; workers can propose one with the `recipe` tool.

## Dev setup

```bash
uv venv
uv pip install ".[dev]"
pytest
```

On Python 3.13, install the package (not editable).

## License

MIT. Author: Aurimas Gircys.
