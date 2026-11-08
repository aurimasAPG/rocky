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

As soon as the goal reads like an ad job, a line under the goal box says what Rocky will check. **edit** switches a check off for this folder (stored in `.rocky/playbook.json`), and its findings stop appearing. A run started from the page is the **same background job** as `rocky "goal"`: it appears in `rocky ps`, and `rocky attach`, `rocky stop`, `rocky resume`, and `rocky open` work on it. Every open tab follows the run's `events.jsonl` with its own cursor, so a second viewer or a page reload sees the whole run. The page renders budget offers, `clarify` questions (an answer box appears — the job waits about 10 minutes, then continues with a safe default), stalls, nudges, incomplete steps, the blocking-vs-notes verdict, and the final handover (result path, partial work, notes). **Stop the run** ends the job like `rocky stop`. The list at the bottom shows the folder's runs; click one to follow it.

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

`bash`, `read_file`, `write_file`, `append_file`, `patch`, `list_dir`, `grep`, `todo`, `web_search`, `web_fetch`, `browser_navigate` / `browser_snapshot` / `browser_close`, `vision_analyze`, `image_generate`, `clarify`, `skills_list`, `skill_view`, `office_read`, `memory`, `brief`, `runs_search`, `recipe`, `ad_board`, `unit_economics`, `customer_economics`, `experiment_plan`, `funnel_analyze`, `table_analyze`, `repeat_buying`, `reach_overlap`, `media_plan`, `campaign_calc`, `page_inspect`, `seo_inspect`, `claim_check`, `survey_check`, `outcome_scores`, `research_plan`, `plan_check`, plus any **MCP** tools you configure (below).

### The brief

`brief` keeps one structured brief per workspace in `.rocky/brief.json`: brand, URL, product, audience, offer, price and cost, volume, channels, KPI, budget, language, constraints, market, competitors and deadline. Every role sees it as a BRIEF block in its prompt, so a worker reads it before asking you anything and only asks for what `missing_for` lists for its kind of work (ads, pricing, test, media, research). Each field records who set it and on what evidence; a value a worker inferred without a URL or file is marked *assumed* until you confirm it or evidence is attached. `unit_economics`, `experiment_plan`, `reach_overlap`, `ad_board` and `page_inspect` fill arguments the worker left out from the brief. When a deliverable recommends spending, the worker records a decision — one internal strength or weakness, one external opportunity or threat, the investment and what would change its mind — and the brief flags any part that is missing or generic. `clear` removes fields or the whole brief.

### Numbers

`unit_economics` does the arithmetic behind pricing, discounts, promotions and acquisition decisions, so a worker never estimates it in prose: contribution per unit and margin, total contribution, break-even units when `fixed_costs` are given, and for a `new_price` or `discount_pct` the sales needed to keep today's contribution (`required_units`, `extra_units`, `required_uplift_pct`). Give it an `expected_uplift_pct` and it returns a decision plus the unit cost and the uplift at which that decision flips. When `variable_cost` is unknown the result says so under `missing` and gives a conditional table across typical margins; the worker then asks you with `clarify` or labels the assumption.

Ask Rocky "We sell 100 units at €100, variable cost €60. If we cut the price to €90, how many extra sales do we need?" and the answer is **34 extra sales, 134 in total**, to keep €4,000 of contribution.

It now covers a whole pricing brief in one call. It reports markup next to margin (and accepts `margin_pct` or `markup_pct` instead of the unit cost), shows the price actually realized after markdowns or a sales mix, and ranks several price plans by the contribution they are expected to net, naming the volume each one must win back. It also judges a bundle against the items bought separately with the attach rate that makes the discount pay, blends tiers or variants against the baseline with the share of full-price buyers who may switch before total contribution falls, builds the shelf price up through a distribution chain and back-solves the production cost that hits a target, and adds one-off, per-period and per-unit obligations to break-even and payback. A price of 0 is accepted for a free tier when obligations or paid variants carry the economics.

`customer_economics` does the arithmetic of acquiring and keeping a customer, so a worker never writes "CLV is roughly 3× CAC" from memory: contribution per period from `price` + `variable_cost` (or `arpu` + `gross_margin_pct`), expected lifetime from churn, CLV over a horizon (36 months or 3 years by default, optionally discounted), payback period, and an `acquire` / `marginal` / `do not acquire at this CAC` verdict against `target_clv_cac_ratio` together with the maximum CAC and the maximum churn that would still pay back. Optional blocks switch on only when their inputs are present: `returns`, `category_ceiling`, `funding` (a price cut as acquisition spend), `follow_on` (a free or subsidised entry offer against paid follow-ons, with the break-even attach rate), `subscription_rollout`, `channels` (CPA, AOV, net after spend, rank and a "profitable but secondary" flag) and `research_value`. Whatever is missing is named under `missing`, and when the cost or the retention is unknown the result gives a conditional table instead of a guess.

`experiment_plan` sizes an A/B test instead of letting a worker guess "run it for two weeks": from a `baseline_rate` (percent), the `minimum_effect` worth detecting (relative by default, or absolute percentage points) and `weekly_traffic` it returns the sample per variant, the total, the number of whole weeks, and a decision rule (fixed sample, no early stopping, the lift and p-value that count as a win). Several challengers split alpha across comparisons. Give it `max_weeks` and it says what lift is detectable in the time you have. Without traffic the duration is reported as unknown and the worker asks for it.

Rocky can also read out a finished test, not only plan one. Give `experiment_plan` the `mode: evaluate` and the arms with units exposed and conversions (or mean and sd for a measured value) and it returns, for every challenger, the difference, its 95 % interval, the p-value and a verdict: winner, loser, no difference detected, or underpowered: keep running when the planned sample was not reached. Pass the plan written before the test (sample per variant and minimum effect) and the verdict follows that rule, so a lift that is significant but smaller than planned is called out rather than shipped. Guardrails such as refund rate or order value are checked from the same counts and a breach is named against the winner. For measured quantities `mode: plan_means` sizes the test from the standard deviation (or a pilot of 20 units when it is unknown), and a paired design where every participant tries every variant needs fewer people.

`reach_overlap` stops a worker adding channel reaches up as if nobody saw two channels: from the `audience`, each channel's `reach` (people, or `reach_unit: pct`) and whatever pairwise `overlaps` are known, it returns the combined reach as a lower and an upper bound, a point estimate only under a stated model (`assumption: independent` for random duplication, or the overlaps you supplied), and the incremental reach of each channel in a greedy order by reach per cost. Unknown pairs are listed under `missing`, `bounds_width_pct` says how wide the gap is, and `narrows_most` names the single overlap worth asking for, so the worker asks you with `clarify` or labels the assumption instead of inventing a figure. Give `cost` per channel and it adds cost per thousand reached and a `budget` check; give `frequency` and it adds impressions, GRPs and the average frequency range.

`table_analyze` reads a CSV or XLSX file in the workspace as data and answers one question about it with real numbers, so no worker summarises a spreadsheet by eye: `describe` lists the columns, their types, blanks and ranges; `group` counts, sums or averages a metric by one or more columns (or by `week:` / `month:` of a date column) and names the gap between the best and worst group; `crosstab` gives cell counts, row shares and a chi-square test when every expected cell is at least 5; `before_after` compares the means either side of `split_date` with a Welch t-test and an optional `normalize_by`; `period_compare` shows the latest period against the previous one and the same period a year ago; `proportion_diff` compares two groups' shares with a Wald 95 % interval and a z-test (or gives one share with a Wilson interval); `intervals` turns two date columns into p25/p50/p75/p90/max days; `coverage` sets list shares against `population_shares`; `text_themes` counts regex themes in a free-text column with sample rows; `search_terms_split` splits a Google Ads search-terms export into branded and non-branded spend, CPA and ROAS. Groups or cells below `min_rows` (default 30) are marked thin and never ranked; the result reports the dates actually covered, the rows used, and under `missing` the blank cells, bad numbers, unreadable dates and skipped total rows. Where a statistic is involved, `method` says how it was computed.

`repeat_buying` turns the marketer's own transaction file (CSV or XLSX, one row per order with customer id, date and optionally amount, order id, sku, brand and units) into loyalty and brand-growth numbers, so claims about repeat buyers, penetration or light buyers come from counts rather than impressions. Pick an `analysis`: `summary`, `frequency` (how many buyers bought once and what share of sales they brought), `repeat` (two definitions of repeat side by side, days between purchases and the repeat rate at 30, 90 and a recommended number of days), `skip_return`, `cohorts` (with an optional baseline-versus-test window that splits buyers into new and returning), `entrants_leavers` (new, lapsed and returning buyers per period, cumulative penetration when a `universe` is given, and how much summing periods double-counts), `duplication` (which brands share buyers and the duplication ratio, repertoire size, share of requirements for `focus_brand`) and `spikes` (periods above a rolling-median baseline, baseline growth, before and after a `change_date`). Two analyses need no file: `double_jeopardy` fits loyalty on the log of penetration across brands and says whether the focus brand is above, below or in line with its size, and `share_vs_growth` reads a brand's sales growth alongside the category to say whether share was gained, lost or held. Every answer carries the window, rows used, what was blank or unreadable, warnings such as `small_base` (fewer than `min_buyers`, default 30), the method and a one-sentence summary to quote.

### Ad against its landing page

`page_inspect` checks that the page an ad or email links to keeps what the ad says, by code rather than by a worker's impression. Give it the ad (`headline`, `primary_text`, `cta`, `price`, `audience`, `offer`, `language`) and the page as a `url`, a workspace `path` or raw `html`; it pulls out the title, H1s, first-screen text, buttons, prices, forms and language. It then reports findings with the exact ad copy and the exact page element behind each: a price in the ad that the page does not show is a **block**, a page with no button or form is a block, and a promise the H1 does not restate, a CTA no button repeats, audience or offer words missing from the page, a language switch or a thin page are warnings. Prices are normalised, so `1 200,50 €` and `€1,200.50` match, and Lithuanian endings are tolerated when words are compared. The ads recipe runs it for every ad before handing over; a page Rocky could not fetch goes under `missing`, never into a guess.

The same call can run named `checks` on that page: who runs it and whether absolute claims have a source (`credibility`), whether its internal links work (`links`, through the engine's link checker), which pressure cues it uses such as countdowns, stock counts and pre-ticked add-ons (`trust`), whether each pricing step buys something new (`price_ladder`), which facts a crawler sees in the raw HTML and which appear only after rendering (`required_facts` + `rendered_text`), which trackers load before any consent banner (`data_use`), schema against the visible page, navigation, promises, touchpoints and UTM-tagged links. Add `query` and `intent` and it checks that the page answers the search word by word. With `kind: receipt|shipping|welcome` it checks a transactional email for items, amount, order reference, delivery, support contact and unsubscribe link. Anything that needs a browser (viewport, clicks, scripts) is outside its scope.

### Search

`seo_inspect` runs the search-channel checks a marketer wants before paying for traffic, by code instead of by eye. Give it a `mode`: `snippet` reads titles and descriptions across pages (length, duplicates, brand, whether the snippet's promise and price appear in the H1 and first screen, competing titles for a query); `urls` judges whether addresses are readable and whether canonical tags, noindex and redirects point one way; `crawl_rules` parses robots.txt per crawler, tells which important pages are blocked and which are missing from the sitemaps (indexes are followed three levels deep); `structured_data` compares JSON-LD price, availability and rating with what the page shows; `crawler_content` lists facts that exist only after JavaScript renders; `templates` measures how much of a templated page is unique text and which buying fields it lacks; `serp` ranks the owned domain and competitors for a query ladder and flags queries with mixed intent; `keywords` clusters a search-terms export, finds ad groups that mix needs and lists negative-keyword candidates with the money they would save. Pages come as html, a workspace path or a url the engine fetches; whatever could not be fetched lands under `missing`, never guessed. Fix the blocks before you buy traffic to the page.

### Customer research designs

`survey_check`, `outcome_scores` and `research_plan` check customer-research designs by code before anyone is asked a question. `survey_check` audits a questionnaire written as JSON: it flags double-barrelled and leading wording, vague quantifiers without a number, closed lists with no "other / don't know", mismatched scales, jargon, "why" questions with no behaviour question to back them, past events with no time frame, single choices where several answers are plausible, routing (`ask_if`) that points to an id or option that does not exist, awareness surveys that do not measure unaided recall, aided familiarity, ad recall and source of first contact separately, and blind-comparison protocols with a fixed order or no blinded arm; it returns findings by level, the respondent paths with their question counts, an estimated duration and a five-person pilot plan. `outcome_scores` validates outcome statements (direction + metric + object + situation, no "better/fast/reliable", no feature in place of a result), computes opportunity = importance + max(importance − satisfaction, 0), classes each outcome as underserved, appropriately served or overserved, gives the gap to the best competitor with a `comparable` flag only when both brands have n ≥ 30, and in `job_map` mode checks a job map against the eight phases. `research_plan` turns concepts or focus-group criteria into a fieldable design: quota per concept, sample sufficiency through `experiment_plan`, an instrument that passes `survey_check`, a decision rule with its defaults labelled, and a `single_voice_criterion` warning for anything one person said. All three say what is missing instead of inventing a number or a question.

### Plans checked before they are handed over

`plan_check` checks a structured marketing plan by code before it reaches the marketer; `kind` picks the checker. `content_calendar` finds weeks without items, items with no owner, date or goal, two posts on one channel the same day, a plan stuck in one buying stage, formats the audience evidence does not support, timely pieces with no expiry, a crowded launch window, overlap with stale existing content and weeks over the stated capacity. `pr_board` checks a press release, announcement, sponsorship pack or message house: news hook, attributed quote, boilerplate of 100 words or fewer, contact and date, byline with its disclosure, a fact-desk source per figure (`claim_check` when sources are given), partners with sourced goals, affected groups in their own words, a reader reason per publication, and one core sentence with at most three evidenced pillars that every channel variant keeps. `demo_board`, `response_board`, `partner_board`, `qualification` and `community_rules` do the same for a demonstration, an escalation matrix, a partner programme, a sales sheet or recruit screener and a rules document. `evidence_log` appends sourced observations to `runs/notes/evidence.jsonl` and audits a secondary source's measure, population and period; `retell` and `ad_pretest` send a statement or ad to a cold reader model that has never seen the brief and score which `must_survive` terms survived, and without a reader model they return the prompts instead of judging themselves. Every kind says what is missing rather than filling a slot the worker left empty.

### Claims against sources

`claim_check` matches every measurable claim in a draft to the passage that supports it, so neither the worker nor the verifier has to take a figure on trust. It pulls out each sentence that states a number, a percentage, a price, a date, a ranking ("first", "largest", "pirmas", "didžiausias") or a credential ("certified", "partner", "award"), then looks for it in `runs/notes/facts.md` and any other workspace files or URLs given under `sources`. Numbers are normalised first, so `12 400`, `12,400` and `12400` are the same figure, `2,5 %` equals `2.5%`, and `200+` matches `200`. Each claim comes back as `supported` with the source, its line or character range and the passage, as `number_differs` when the source says 180 where the draft says 200 (both figures reported for a human to settle), or as `unsupported`. Nothing is marked supported without a passage to show for it. The marketing recipe runs it on the copy against the research, and the verifier has it as a read-only tool, so an unsupported claim becomes an issue rather than a published sentence.

It now does more than match a figure to a passage. With `check_scope: true` it reads the qualifiers in the supporting passage ("tik" / "only", a year or period, a place, a surveyed population, a lab or test condition) and when the draft drops them the claim comes back as `supported_scope_narrower` with the dropped conditions listed, while a draft that generalises ("visi", "every", "always") beyond its source gets `misleading_inference`. Give `quote_sources` (interview notes) and every sentence in quotation marks of six words or more is checked verbatim, returned as supported, altered or unsupported, and a name beside the quote must appear near that passage. A credential such as "Google Premier Partner" or "ISO 9001" needs a source that names the issuer, and each supported one says who stands behind it; `own_domains` labels every source as own or third party and flags a sentence that does not say whose figure it uses. `mode: reply` checks a thread answer point by point against the facts it cites, the spokesperson and the thread URL; `mode: price_anchor` checks that a struck-through reference price appears in the evidence, matches the words the draft uses for it and was actually charged. Every result also carries a ready-to-paste `fact_sheet` of supported claims with their sources and a `corrections` list with one line per differing or unsupported claim; HTML sources are indexed by table row and image alt text.

`reach_overlap` also reads a few optional per-channel fields and leaves the plain call untouched. Tag each channel's `source` (paid, owned, earned) and give `required_reach`, and it returns `by_source` bounds and cost per group, the earned share of impressions and `unpaid_sustains_required` (yes / no / unknown) with the sentence that says how much paid must still deliver; give a `role` (brand, activation, both) and it compares the cost split with the 60/40 comparison point. `addressable_pct` per channel adds addressable reach, cost per thousand people who can actually buy and a re-ranking of the channels, `category_buyers` restates the combined reach against the category rather than your followers, and `existing_customer_share` separates reach among current customers from reach among future buyers. A `frequency_distribution` with a `cap` yields `cap_scenario`: released impressions and budget, the unexposed pool and the extra reach as bounds; an average frequency alone is refused. A `measure` block keeps views, impressions and people on different rulers: a channel measured in views is excluded from the combined figure and named under `comparability`.

`media_plan` is one tool with a `mode` for the media-budget arithmetic a worker would otherwise do in prose: `ledger` totals budget lines by channel, cost type and role and shows every channel's share on a media-only and an all-in basis; `sov` turns spend and market share into share of voice, share of market and excess share of voice in points, with the spend a target ESOV needs; `budget` costs activities against an objective (coverage, cost per result, headroom, cut candidates) and compares the brand/activation split with a sourced B2B or B2C benchmark, applying each adjustment once; `offers` puts media proposals on one denominator (viewable CPM, cost per click, cost per the event you actually want through the stated conversion path) and refuses to rank a CPA offer whose payment event is unnamed; `volume` multiplies population, penetration, purchases per buyer and units per purchase and reconciles with the reported volume; `calendar` lays a year out as 52 weeks of always-on plus flights around known Lithuanian occasions and flags overlaps, short leads and the budget cap; `channel_fit` checks a new platform for sourced evidence and team hours; `classify` sorts spend rows into brand, activation or mixed from the creative's own words, never by channel.

`campaign_calc` is one tool with thirteen modes for the campaign arithmetic a worker would otherwise do in prose, each returning the figures, the `method` behind them, what is `missing`, `warnings` and a one-sentence `summary`. `lift_compare` compares sampling, coupon or geo arms by cost per buyer with a Wilson interval and only calls a buyer incremental when `baseline_buyers` is given; `referral_loop` turns invites × acceptance × activation into K, the amplification 1/(1−K), users after n cycles and the break-even incentive; `seeding_compare` ranks seeded against organic arms; `bass` forecasts adoption from a market size and either six or more periods of history or given p and q, flagging the defaults; `buyer_overlap` reads buyer × brand rows into penetration, duplication of purchase and candidate partitions; `network_value` gives reachable counterparties after an integration under a linear and a Metcalfe model, both labelled as models; `commission_compare` shows what each plan pays per sale and per rep and names the payout-neutral gross-profit rate; `catchment_compare` scores sites with the weights stated and the winner's sensitivity to them; `account_penetration` splits growth left in existing accounts from new logos; `mental_availability` shows in which buying situations the brand is missing from unaided recall; `benchmark_fit` checks an external average against the brand's industry, market, budget, objective and period and returns a range, never a bare point; `line_metrics` and `audience_panel` measure copy rhythm and respondent coverage. Impossible inputs are refused with a plain sentence, and no missing number is ever estimated.

### Your own data

`funnel_analyze` reads a CSV or XLSX file in the workspace and turns it into a funnel with real numbers: the count at every stage, the share kept from the previous stage and from the first, the step that loses the most, and the overall conversion. Give it the stage columns in order (`stages`) for a wide file, or `stage_column` with `count_column` or `id_column` for one-row-per-event data; add `date_column` with `date_from` / `date_to` to limit the window, and `group_by` for a funnel per channel, campaign or cohort with the worst group named. The result reports the dates actually covered and how many rows were used, not the dates you asked for. Anything the data cannot answer — blank cells, text where a number should be, unreadable dates, an empty window, a column that does not exist — is listed under `missing` instead of being guessed, and a later stage larger than an earlier one triggers a warning. The one-sentence `summary` is written to be quoted in the report as is.

It now also compares groups: with `group_by` (or `cohort_by` / `periods`, which derive the group from a date) it runs two-proportion z-tests at `compare_stage` against the first group and returns the difference in percentage points, a 95 % interval, the p-value and a verdict of better, worse, no_difference or insufficient_data (fewer than `min_n`, default 100), warning when the groups' date windows are not concurrent; `cost_per_group` or `cost_column` adds net cost, cost per converted and the pooled campaign figure, while `metrics` and `share_of` add per-group means and a selection-bias check. `lag_from` / `lag_to` bucket the days between two dates with the median and the share after day 0, and `maturity_days` keeps groups of different ages comparable; `bucket_by` turns a timestamp into hour or weekday slots and names the fewest slots covering `cover_share` of the final stage. `derived_stages` build a funnel from a per-user table with rules such as not_blank, gt:0 or after:signup+14d, a two-column `group_by` gives a teller × listener matrix, and `paid_at_stage` counts units paid for that never reached the last stage. Five modes read the same files for other questions: `buyer_classes` (promotion units by prior buying frequency), `offers` (discount share and discount-only customers per period), `trend` (cost of a new buyer per launch period), `recruits` (buyers of a new product who are new to the brand) and `categories` (counts, shares and the Pareto set of a category column).

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

Bundled: `xlsx`, `docx`, `pptx`, `pdf`, `deep-research`, `marketing-pipeline`, `marketing-strategy`, `marketing-numbers`, `customer-research`, `pr-response`, `web-testing`.

`web-testing` tells a worker how to test a site, form, flow or app with the browser tools: a row per step in `runs/notes/walkthrough.md` (url, action, what appeared, seconds, money mentions), what counts as a fail for a signup, empty account, export, cancellation, form errors, zero-result search, checkout, peak load, page speed, mobile task, self-check tool, video retention, cluttered feed and visual recall, and what the headless browser cannot do, so a click or a typed value goes through a Playwright MCP tool or the marketer's screenshot instead of a guess. `marketing-strategy` holds the judgment rules from the playbook that code cannot settle (positioning, portfolio, pricing transitions, service behaviour, channel conflict, production feasibility, and more), each with its source pages; the recipes load it when a goal matches the situation. `marketing-numbers` says which calculator produces each figure. `customer-research` tells a worker whom to interview for a purchase (operator, maintainer, budget owner, nonconsumers, trade buyers), the exact questions to ask in Lithuanian and English, and how to record workarounds, use context and survey blocks so a role with no evidence is marked missing rather than invented. `pr-response` covers public replies to criticism in the original thread with a named spokesperson and sourced facts, press visits with independent Q&A and disclosed costs, staff social-media guidelines, and contributor or community programmes designed around why people take part.

Rocky also loads a **research + marketing allowlist** from `~/.claude/skills` (copywriting, voc-to-copy, meta-ads-copy, …) plus the Lithuanian language skills `lietuviu-kalbos-rasyba` and `turtinga-lietuviu-kalba` when they exist there; a worker writing Lithuanian copy is told to open the spelling skill first. Personal skills stay out. Extra folders:

```bash
export ROCKY_SKILLS=/path/to/more/skills
```

Facebook, Instagram and Meta ad goals follow the bundled `ads` recipe: **facts → ads → visuals**, no deck and no spreadsheet. The steps are fixed, so the planner is only called on a replan. The result is `deliverables/ads.html`, one self-contained page (`rocky open` opens it): each ad as it looks in the feed, cut where the feed cuts it, with copy buttons, a download for the visual, and a stamp saying whether it is ready. Beside each ad the page lists what the buyer gets, what it costs and where they get it, so you can hold the promise against the offer; a gap is a warning. It also shows each segment's estimated size, buying power and cost to reach, and warns about whatever is missing, because a niche can be too small to pay back. Ad 1 is the control, the site's current pitch, and the others are challengers about outcomes the product already delivers, so the set is a ready test. A section below the ads lists what buyers expect from anyone in the category, the minimum, and whether the brand meets it; a missed minimum is flagged at the top, to fix before paying for ads. The page says whether the campaign sells more in an existing market or opens a new one, and warns when a new market has no evidence that its buyers can be reached. When the ads offer more than one product, a guide says which product to offer someone who arrives from each ad. Every finding on the page has **Why?** with the reasoning and source, an action, and a way to dismiss it. **Fix it** starts one follow-up run capped at €0.50 that changes only that point. In `rocky serve` the page opens under **Result** and the button runs the fix there; opened as a file, it copies the `rocky resume` command. A finding Rocky cannot fix, such as a missed minimum, offers **Mark as handled** instead. Workers publish through the `ad_board` tool and code checks every call: a number in the copy needs a `claims` entry whose source URL is in `runs/notes/facts.md`, otherwise the ad is stamped for fixing and the worker is told exactly what to change. Copy longer than the feed shows and absolute promises are warnings for you to decide on. The page is written as soon as the copy exists and rebuilt when the visuals arrive, so a run that dies later still hands over the ads; if the verifier never ran and the automatic checks are clean, the run ends as `passed_with_notes`.

Other marketing goals, and any ad goal that names a deck or a sheet (`pptx`, `xlsx`, `deck`), are planned as **research → VOC → copy → pptx + xlsx → visuals** — that is the bundled `marketing` recipe. The visuals step writes `deliverables/visual-brief.md` (one block per creative: format, message, composition, brand hex colours, text overlay) and then `image_generate`s each block into `deliverables/visual-1.png`, `visual-2.png`, …

### Recipes

A recipe is a plan skeleton Rocky enforces when a goal matches: ordered steps, `depends_on`, the deliverable path, and keyword hints. Files are TOML:

- bundled: `rocky/recipes/*.toml` (`ads` — facts, ads, visuals on one page; `marketing` — research, VOC, copy, deck + sheet, visuals)
- shared: folders in `ROCKY_RECIPES=/path/a,/path/b`
- this workspace: `<workspace>/.rocky/recipes/*.toml` (wins on the same name)

Each step can declare `writes = ["runs/notes/research.md"]` — files it must leave on disk. When a worker stops without them, Rocky nudges it (twice at most, `nudge` event); if they still do not exist the step is reported to the verifier as **INCOMPLETE** so later steps and the verdict cannot pretend the work happened. The planner's own steps get a `writes` list too.

Match order: `recipe:<name>` anywhere in the goal → a recipe matched by words alone (`ads`) → assessed domain → hint words. If the planner already covers every required stage the plan is kept; otherwise the recipe steps replace it. Workers can `recipe list / view / save` — a saved recipe is used from the **next** run, so review it. See `rocky/recipes/marketing.toml` for the format.

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
