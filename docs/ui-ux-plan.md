# Rocky UI/UX assessment and implementation plan

Date: 2026-11-08 · Baseline: `a7d9bf2` (`playbook-ui`)

Working branch: `codex/rocky-ui-ux-plan` · Isolated worktree: `/Users/aurimas/Documents/APPS/openrouter-ui-ux`

**Recommendation:** make Rocky a workspace for completing and reviewing tasks. First repair run/session state, then reorganize the interface around the task, its progress, and its deliverables. Preserve the background-job architecture and existing visual identity. A cosmetic redesign alone would leave the most consequential problems intact.

This deliverable is an assessment and implementation plan. Product code has not been changed. The original checkout was left on its existing branch; untracked material there was not moved or included. Findings refer to the baseline above, so recheck touched interfaces against other sessions' changes before implementation.

## Audience and scope

Working assumption: the web UI serves people producing marketing and business deliverables; CLI/TUI users also need efficient technical supervision. The existing ad board, marketing recipes, playbook, and general document tools support this mixed audience. Keep general tasks first-class; do not make every run look like an ad campaign.

The primary user journey is **choose a workspace → describe the task and budget → understand progress → answer if needed → review and use the output → continue or recover**. The interface should always answer: Which task is selected? Is it still working? Does it need me? What exists? What can I do next?

In scope: web shell, run selection and state, results, history, playbook interactions, responsive/accessibility behavior, CLI/TUI parity, and the minimum backend contracts those flows require. Model selection, orchestration strategy, recipe content, pricing policy, and tool behavior remain owned by the other workstreams. No framework migration is needed for this plan.

## Assessment

The foundations are useful: jobs persist independently of viewers; CLI commands cover attach, stop, resume, open and cost; partial files survive a stop; the ad board has practical copy/evidence/fix actions; follow-up costs are disclosed; HTML previews are sandboxed. Main form fields already have labels, text rendering generally uses escaped content, and reduced-motion rules exist.

The web screen nevertheless reads like a demonstration of orchestration: a large introduction, five receipt cells, four model cards, and three empty logs compete with the user's task. History is below all of them. “Three models” is inconsistent with the four visible roles and assessment stage. Completed non-ad deliverables are paths rather than usable results. The TUI has a similar imbalance: at 80×24 its three log panes are only 26–27 columns by 6 rows including borders; at 60×20 they are 20×2.

### Evidence and limits

- Read the web/terminal implementation, lifecycle code, and associated tests at the baseline commit.
- Viewed the actual web UI in Chrome using an isolated fixture server, including desktop, a completed ad run, switching to a crashed run, and a 390×844 responsive viewport. Worker startup and API access were disabled in the harness; all task data was synthetic.
- Reproduced six JavaScript state scenarios against unchanged `app.js` with minimal DOM/network doubles. [Probe](ui-ux-evidence/state-probe.cjs) and [observations](ui-ux-evidence/state-observations.json) are retained. These are diagnostic reproductions, not browser integration tests or assertions of corrected behavior.
- Exercised Textual headlessly at 120×40, 80×24 and 60×20, plus scripted input/error/verification scenarios. [80×24 snapshot](ui-ux-evidence/tui-80x24.svg).
- Ran 101 existing focused tests: all passed, with two dependency deprecation warnings. Passing backend/source tests do not establish correct browser state transitions.
- [390px browser capture](ui-ux-evidence/web-390px.png) is a raw full-page capture, capped at 16,384 physical pixels. Its embedded preview is blank in the captured image; use it for outer-shell hierarchy/spacing only. It is not proof of an iframe rendering defect. Chrome extensions were present, so extension-generated markup is not attributed to Rocky.
- No paid model runs, real provider failures, actual network-disconnect experiment, screen-reader session, or user usability study was performed. Reconnection duplication is supported by replay behavior and the server implementation, not a live disconnect test. Proposed usability targets below are acceptance goals, not measured results.

### Prioritized findings

Priority means implementation order: **P0** can target the wrong work or prevent reliable operation; **P1** impairs trust or task completion; **P2** reduces efficiency and clarity. “Reproduced” means a runtime fixture or native browser observation; “source” means inspected behavior requiring full interaction coverage during implementation.

| ID | Priority / evidence | Current problem and consequence | Source at baseline | Required change |
| --- | --- | --- | --- | --- |
| UX-01 | P0 · reproduced | Changing the workspace refreshes the playbook/history but leaves the old selected run. Apply still posts to the previous workspace and campaign. | `static/app.js:443–470` | Treat workspace + run as one scoped selection. Detach/reset on workspace change and disable campaign actions until a valid selection exists. |
| UX-02 | P0 · reproduced + source | Opening history after an authenticated reload can delete the existing cookie because the token input is blank. Initial token entry also does not retry boot, leaving the workspace unset after a 401. | `static/app.js:17–44,242–246` | Add an explicit Connect flow; preserve the session on empty input; retry metadata/history after connection; show auth failure with a recovery action. |
| UX-03 | P1 · Chrome + probe | Switching runs leaves the previous ad preview, model names and active role visible. The displayed output can belong to another run. | `static/app.js:234–240,269` | Reset every run-owned view atomically, including iframe source/height, questions, model cast, result, receipt and status. Hydrate only the selected identity. |
| UX-04 | P1 · Chrome + probe | A crashed run can show “running” with Stop disabled. Terminal `meta` is sent by the server but ignored by the web client; TUI can replace error state with “ready.” | `static/app.js:105–192,253`; `webapp.py:373–377`; `runs.py:169–177`; `tui.py:195–214` | Reconcile terminal metadata and retain the final outcome. Separate “viewer disconnected” from “run stopped.” |
| UX-05 | P1 · probe | A sparse stop `done` event changes €0.456/1,200 tokens to €0/0 and reports no output. Later authoritative metadata is ignored. | `runs.py:155–164`; `static/app.js:57–64,108,173–190` | Merge partial updates. Missing fields mean unknown/unchanged, not zero/absent. Reconcile final cost and available files. Real stopped-run metadata completeness also needs checking. |
| UX-06 | P1 · replay probe + source | Reconnecting starts a fresh event cursor; repeated events append duplicate logs/cards. Connection loss has no visible status. | `webapp.py:356–363`; `static/app.js:67–83,250–254` | Adopt stable event positions or snapshot-and-replay semantics, deduplicate, show reconnecting and last update, then reconcile. |
| UX-07 | P1 · source | Only `deliverables/ads.html` has a web preview. Other files and partial work are plain paths; recovery sends users to CLI commands. | `static/app.js:173–197,273–275`; `webapp.py:332–347` | Show artifact inventory, preview/download actions, and contextual Resume/Follow up. Keep a copyable workspace-aware CLI command as fallback. |
| UX-08 | P1 · source | A historical run's board endpoint serves the current shared workspace file, which a later run may overwrite. | `webapp.py:332–340` | Label “Current workspace output” immediately. Only promise a historical result when immutable per-run artifacts exist. |
| UX-09 | P1 · Textual/mock | Busy TUI submission discards a draft. CLI Ctrl+C during a question can submit an empty answer; TUI interprets `q/quit/exit` before treating text as an answer. | `tui.py:117–136`; `cli.py:70–78,635–646` | Clear drafts only after acceptance; separate answer and command handling; cancellation must never write an accidental answer. |
| UX-10 | P1 · Textual + source | TUI omits verification blockers/issues and question choices. The web plan never updates step completion; workers collapse into a single role indicator. | `tui.py:139–147,191–194,309–314`; `static/app.js:128–159`; `engine.py:1523` | Show blockers, notes, options and step states using stable IDs. Preserve concurrent worker activity rather than implying one worker at a time. |
| UX-11 | P1 · source + calculation | Missing explicit labels for ask/search; no app-owned status/error announcements; chip and disclosure states are not exposed. Small apricot labels on paper calculate to about 1.75:1 contrast. | `static/index.html:20,62–69,142`; `static/app.js:391–408`; `static/styles.css:61–67,143–148` | Semantic labels/live regions, expanded/pressed state, purposeful focus, clear errors, and separate light/dark foreground tokens. |
| UX-12 | P1 · source + viewport/layout checks | Web grids remain two columns below 860px; narrow header/path handling is weak. Fixed TUI panels leave minimal usable content at normal terminal sizes. | `static/styles.css:34–49,159–188,195`; `tui.py:48–61,83–96` | Single-column mobile flow; compact wrapping context; responsive TUI tabs; long-content and zoom coverage. |
| UX-13 | P1 · source | “Already applied by Rocky” means membership in the built-in registry, not campaign-specific execution. Check toggles persist for the workspace without saying so. | `webapp.py:287–297`; `static/app.js:359–368,422` | Use an accurate capability label and explicit workspace scope. Show pending/saved/failed changes and eligibility for paid follow-ups. |
| UX-14 | P2 · source | History leads with IDs, truncates goals, shows only 12 entries, and has no durable selection URL. Loops/model internals dominate the primary flow. | `static/app.js:257–279`; `static/index.html:27–50,75–151` | Goal-first history, selected state, dates/cost, search/load more, recoverable navigation and progressive disclosure. |
| UX-15 | P2 · source + mock | TUI lacks in-app lifecycle actions, requires a key for read-only attach, scrolls Work regardless of focus, and mixes interface languages. | `tui.py:65–70,323–365`; `cli.py:185–235`; `HELP.md:109–110` | Add contextual actions and focused-pane scrolling; allow viewing without a key; standardize UI vocabulary independently of deliverable language. |
| UX-16 | P2 · source | Search/preview responses can arrive out of order; workspace change does not refresh the goal-check preview; several failures disappear silently. | `static/app.js:367–391,432–470` | Cancel/version requests, scope responses to workspace/query, refresh all dependent views, and provide retryable loading/error/empty states. |

Source paths above are relative to `rocky/`.

## Proposed experience

Use three clear destinations: **Work**, **Runs**, and **Playbook**. Keep workspace and connection state in a compact header. Work is the task composer when no run is selected and the run detail when one is selected. Runs is a drawer/list on large screens and its own view on small screens; it should never be buried below diagnostics. Playbook remains available but campaign-specific actions require an eligible selected campaign.

```text
rocky       Workspace: Campaign A ▾       Connected       Runs | Playbook
────────────────────────────────────────────────────────────────────────
Create a campaign for small business owners                 [New task]
Working · Drafting the ads                 €0.12 / €1.00     [Stop run]
Plan ✓ ───────── Work ● ───────── Verify ○ ───────── Result ○

Task progress                             Result / available files
✓ Research the offer                      ads.html      [Preview]
● Draft the ads                           sources.md    [Download]
○ Check claims                            Files are still being updated

[Question panel appears here when input is needed]
Activity ▾                Models & receipt ▾
```

The signature is the **Plan → Work → Verify → Result path**, connected to real task states and evidence. It expresses Rocky's actual workflow. It is not a percentage completion bar; stages can repeat and simple tasks can skip planning. On mobile it becomes a compact vertical sequence, with question and result content taking precedence over logs.

### Before a run

- Replace the large pitch with “What do you want to get done?” and one short example appropriate to the workspace.
- Primary inputs: task, allowed workspace selector with visible path, and budget. Explain that Rocky can change files in that workspace and that the budget is a guard rather than an absolute billing guarantee.
- Move maximum attempts, model/recipe diagnostics, and detailed usage into Advanced. Use “Maximum attempts” with a short explanation instead of unexplained “Loops.” Preserve current numeric limits until the configuration owner reconciles UI/API differences (UI minimum €0.10, API €0.05).
- Preserve an unsent draft when navigating or when a submission fails. Local draft persistence may be added without storing team tokens in browser local storage.
- Offer an explicit connection/setup state and allowed workspace list. A missing key is detected before a user commits effort to an otherwise unusable run; setup copy points to the existing local setup flow.
- Name the CTA “Start task.” Show what will be checked, whether checks apply to this workspace, and whether changes are saved. Do not imply skipped checks apply only to this one run.

### During a run

- Persistent selected goal, workspace, stage, current activity, spend/budget and Stop action. Only claim progress supported by events; show waiting/skipped/retrying states explicitly.
- Put questions ahead of progress. Present supplied choices as selectable answers with free text available. Announce the question without repeatedly stealing focus during streaming; preserve an in-progress answer.
- Activity follows new events only while the user is already at the end. Otherwise show “New activity.” Keep raw tool output in expandable details and bound the displayed log for long runs.
- Closing the viewer leaves work running. “Stop run” ends work; show “Stopping…” until acknowledged. “Pause” is not a label for either action.
- Show connection status separately: “Reconnecting — showing the last update.” Do not tell the user to restart a paid task merely because the viewer lost its connection.

### At completion or interruption

| Backend outcome | User-facing label | Primary next step |
| --- | --- | --- |
| `passed` | Complete | Open result; download files |
| `passed_with_notes` | Complete with notes | Open result; review notes |
| `unmet` | Needs more work | Review blockers; resume if possible |
| `error` / `crashed` | Failed / Interrupted | Explain cause if known; show partial files; Resume if recoverable, otherwise start a new task with preserved context |
| `stopped` | Stopped | Show saved work; Resume if recoverable, otherwise restart/edit the task |
| Waiting for an answer | Needs your input | Answer; retain Stop and detach semantics |

Separate output existence from quality. A file may exist even when verification fails; a verification pass does not make a missing file downloadable. Show actual availability, concise result summary, blockers before optional notes, and the final receipt or clearly marked last-known cost.

Artifacts should expose filename/type, location, availability and Preview/Download actions. Render text safely; download office files; preserve the existing sandbox for HTML. Do not attempt full in-browser Office editing in this increment. If added, “Open folder” belongs in the local CLI/TUI; the existing CLI opens a deliverable. A remote browser should get a download or copyable path, not a promise that it can open the server's Finder.

Resume must state its budget and relationship to the original run before starting. Follow-up/fix remains a separate run with its disclosed cap and parent link. Until history is immutable, explicitly mark previews as current workspace output and avoid implying past runs retain past file versions.

### Playbook

Keep search useful independently of a campaign. Label registry membership as “Built-in check” where accurate; only show “Applied to this run” when recorded evidence supports it. Show the selected target campaign next to Apply, disable it for active/ineligible runs, preserve the disclosed €0.50 cap, and prevent duplicate submissions. Workspace changes must invalidate selection and pending requests. Empty results should suggest clearing a filter; failed searches should offer Retry. Fix/Ignore/Handled remain distinct actions with visible consequences.

### Visual direction

Refine the existing identity rather than introducing an unrelated theme. Retain Fraunces for the wordmark and restrained page titles, Atkinson Hyperlegible for controls/body text, and JetBrains Mono for paths, IDs and receipts. Remove italic styling from routine action labels. Use body text around 16px/1.5, supporting text 14px, and compact metadata no smaller than 12–13px.

| Token | Proposed value | Purpose |
| --- | --- | --- |
| Canvas | `#1c2b33` | Existing dark blue-gray workspace |
| Surface | `#243540` | Grouped controls/details |
| Paper | `#e8f0ea` | Main text on dark; occasional light result surface |
| Ink | `#152228` | Text on light surfaces |
| Action | `#ff938a` | Primary action and focus accent; verify each pairing |
| Muted text | `#aabbb8` | Supporting text on dark surfaces |

Use ink for labels on paper; do not reuse the pale apricot foreground there. Status also needs a word/icon, not color alone. Keep spacing on a small 4/8/12/16/24/32px scale, minimize texture and offset shadows, and use borders to group related information. Target at least 44px controls for the main touch actions as a project design choice. Keep reduced-motion support. Use native focus behavior plus a consistent visible focus treatment.

The deliberate design choice is to make the workflow path and its artifacts the distinctive element. Large decorative model cards and a marketing hero would undermine that choice; keep model details available in a disclosure instead.

## Implementation sequence

Effort bands are rough engineering estimates, not commitments: S = up to about one developer day; M = about 1–3; L = about 3–5. Backend coordination, review and newly discovered issues can change them. Ship independently reviewable slices; do not bundle the whole redesign into one change.

| Slice | Priority / effort | Work and likely files | Dependencies and acceptance gate |
| --- | --- | --- | --- |
| A. Reliable selection and connection | P0 / M | Explicit auth boot/retry; preserve cookie; scoped `{workspace, runId}` state; atomic reset; request cancellation and stale-response rejection. `static/app.js`, small `index.html` changes; focused browser tests. | Can start first. Authenticated reload opens history; switching workspaces cannot apply to the old run; A→B clears A's preview/questions/models. |
| B. Truthful lifecycle and receipts | P1 / M–L | Normalize metadata/events; merge sparse cost updates; handle all terminal states; separate connection status; reconnect/replay reconciliation. `static/app.js`; additive `webapp.py`/event contract work with backend owner. | A; agree event cursor/snapshot semantics first. Crash without `done`, stop after spend and reconnect preserve correct state without duplicate cards. |
| C. Task-centered web shell | P1 / M | Compact workspace header, Work/Runs/Playbook navigation, task composer, advanced controls, task progress and question panel; extract small state/view modules only as needed. `static/index.html`, `styles.css`, `app.js`. | A+B data semantics. Complete the entire start→answer→review journey at desktop and 390px; no framework/build migration. |
| D. Results and recovery | P1 / L | Artifact inventory and safe download/preview; current-output label; Resume/Follow up actions with explicit budgets; final handover. Static views + agreed `webapp.py`/`runs.py` adapters; reuse CLI behavior. | Backend artifact/resume contracts; do not duplicate engine logic. All supported file types reachable; missing/partial files truthful; no path escape; parent runs linked. |
| E. History and playbook clarity | P1/P2 / M | Goal-first rows, selected state, timestamps/cost, stable URLs, search/load more; truthful applied/check labels; workspace-scoped settings; eligibility, pending/error feedback. `static/*`, minimal agreed history/capability fields. | A; D for full result actions. Back/refresh restores selection; older runs reachable; Apply identifies the correct target and cost before submission. |
| F. Terminal input and handover correctness | P1 / M | Keep busy drafts; separate answer cancellation from detach; render question options, blockers/notes and terminal metadata; reset receipt on a new run. `tui.py`, `cli.py`, reuse/extract presentation logic from `view.py`. | Can run alongside C–E with dedicated file ownership. Pilot/mock tests cover busy input, cancellation, crash without `done`, and two consecutive runs. |
| G. Responsive TUI and lifecycle actions | P2 / M–L | Compact header; tabs when width/height cannot support columns; focused-pane scrolling; Runs, Stop, Open result, Resume; key-free viewing. `tui.py`, narrow `cli.py` routing changes, `HELP.md`. | F and existing lifecycle services. Fully keyboard-operable at 80×24 and 60×20; wide view remains useful at 120×40; no paid calls to inspect a run. |
| H. Integrated usability pass | P1 / M | Browser regression scenarios, keyboard/zoom/screen-reader checks, terminal pilots, copy consistency and guide updates. Screenshots/fixtures and relevant tests. | Accessibility is part of A–G, not deferred here. This slice verifies the composed experience and fixes integration failures. |

Suggested first implementation milestone: **A + B + F**, because it removes wrong-context actions, misleading run outcomes and lost input before changing presentation. Then deliver **C + D**, followed by **E + G** and the integrated H check. Prepare frontend fixtures while backend contracts are being agreed so UI work can proceed independently.

## Contracts and parallel-work boundaries

The isolated worktree prevents branch switching and untracked-file collisions; it does not prevent later merge conflicts. Before implementing a slice, identify current owners and compare against the latest accepted backend changes. Assign one owner per shared file and keep UI and contract patches separate.

| Owner / surface | Contract needed | Coordination rule |
| --- | --- | --- |
| UI owner: `rocky/static/*` | One selected identity, draft state, connection state, normalized run state and derived action availability | Keep framework-free; separate data reduction from DOM updates sufficiently to test transitions. Do not change orchestration rules from the client. |
| Web/lifecycle owner: `webapp.py`, `runs.py`, `events.py`, `job.py` | Snapshot with status, goal, budget, receipt completeness, ask, artifact availability, parent run, and resumability; stable stream cursor or documented full replay | Existing `_row` exposes some fields but omits budget and detailed receipt. Add only needed fields; older saved runs must remain readable. Treat capabilities as server facts. |
| Setup/history owner: `webapp.py`, `runs.py` | Non-secret setup capability such as `can_start` plus actionable reason; cursor/limit or pagination contract for history | `/api/meta` currently has token/workspace metadata only. Viewing existing runs must remain separate from readiness to start paid work. Backend history defaults to 40 entries (`runs.py:180–192`) and the UI shows 12; removing only the UI limit cannot make all older runs reachable. |
| Artifact owner | Enumerate allowed files; safe authenticated retrieval; current vs immutable version semantics | Preserve workspace containment and HTML sandboxing. No arbitrary filesystem download endpoint. Snapshot storage/retention is a separate backend decision, not a hidden UI rewrite. |
| Resume owner | Reuse existing resume validation and budget behavior via a web adapter | There is no general web resume endpoint today; `fix`/`apply` are narrower follow-ups. Agree the route/payload before wiring buttons. Handle conflicts from active workspace jobs. |
| Engine/event owner | Stable step IDs, per-step status, concurrent workers, typed ask kind and options | Reuse existing events first; do not infer budget questions from English wording or alter worker behavior to suit a view. |
| Terminal owner: `tui.py`, `cli.py`, `view.py`, `status.py` | Consistent semantic statuses/actions and handover; presentation appropriate to terminal size | Keep command compatibility. English UI is the proposed first consistent default; deliverable `--lang` remains independent. A full locale system is later work. |
| Playbook owner: `playbook.py`, `adboard.py` | Registry membership vs actual application, scope of disabled checks, action eligibility, fix cap | UI copy can improve immediately; campaign evidence must come from recorded execution. Do not change principle content or generated ad logic in the shell work. |

For terminal results, distinguish “can submit a new task” from “the previous task completed successfully.” For browser events, ignore late responses/messages from a deselected run and avoid rendering events from an obsolete connection. Do not reset the selected run merely because a history refresh finishes.

## Verification and definition of done

Preserve the existing scripted backend suite. Add browser integration tests for behavior that cannot be proven by HTTP response assertions; use fixture jobs and stub provider calls. Use Textual's pilot for terminal state/input/layout. Do not add tests that only restate CSS selectors or snapshot every pixel.

| Scenario | Required outcome |
| --- | --- |
| First visit / wrong token / correct token / reload | Actionable connection state; successful connection hydrates workspaces/history; opening a run retains authentication. |
| Missing API key / denied workspace / server unavailable | Explain the failure beside the action, preserve the draft, provide the correct setup/retry step, and do not pretend work started. |
| Switch workspace; then Apply/Fix | Old run selection is detached/reset; no request targets an unseen workspace/run. |
| Select completed ad A, then text result B or crashed C | Old iframe, questions, cast and status disappear; selected title/files/status all match B or C. |
| Crash with only terminal metadata; stop with a sparse `done` | Outcome is authoritative; known receipt does not become zero; available/unknown files remain truthful. |
| Disconnect/reconnect; replay; duplicate/late event | Visible reconnecting state; no duplicate activity/cards or stale-question focus; final snapshot reconciles. |
| Active work and clarification | Clear activity, actual step states, answer choices and preserved answer draft; repeated clicks do not submit twice. |
| Review artifacts and recover | Safe open/download, explicit missing files, partial work visible, historical/current semantics clear, Resume budget and parent link visible. |
| Keyboard and assistive technology | Every control has a name; focus is visible and follows deliberate actions; status/questions/errors are announced without narrating every token; toggles expose their state. |
| Long task/path/model ID; many steps; 320/390/768/1280/1440px | No lost controls or page-level horizontal scroll for the ordinary UI; readable one-column mobile flow; bounded logs, reachable history and result actions. |
| Terminal 120×40 / 80×24 / 60×20 | Useful content area, reachable panes/actions, focused-pane scrolling, no lost draft, readable final outcome. |
| Cancel clarification or detach | No accidental answer write; viewer exits while the job remains running/waiting. Stop is a separate deliberate action. |

Use normal-text contrast of at least 4.5:1 and large-text contrast of at least 3:1, assessed against the actual adjacent surfaces. [W3C contrast guidance](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html). Verify reflow at 320 CSS pixels and 400% zoom from a 1280px viewport, allowing genuinely two-dimensional content its own region. [W3C reflow guidance](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html). Expose meaningful status changes programmatically without requiring focus changes. [W3C status-message guidance](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html). These checks are a targeted acceptance baseline, not a claim of complete WCAG conformance.

For a short usability check, ask representative users to start a task, identify whether it needs input, find/download its result, and recover an interrupted run without coaching. Record completion, wrong-context actions, lost drafts, and confusion between Stop and leaving the viewer. Target zero wrong-context actions and zero lost drafts; establish timing baselines before assigning speed-improvement targets. No telemetry service is needed for this exercise.

The first release is ready when all P0 findings are resolved, selected identity/outcome/receipt agree across views, the start-to-result and interruption-to-resume journeys work with fixtures, ordinary mobile/terminal sizes are usable, and every remaining limitation is explicitly represented in the interface. Full immutable artifact history, broad localization, model-picker redesign and advanced workspace administration can follow separately.

## Checks performed for this assessment

```sh
node docs/ui-ux-evidence/state-probe.cjs
python -m pytest tests/test_web.py tests/test_board_actions.py \
  tests/test_goal_strip.py tests/test_playbook_search.py tests/test_cli.py \
  tests/test_view.py tests/test_status.py tests/test_runs.py tests/test_resume.py
```

The pytest command used the existing environment's Python 3.13 interpreter while importing this worktree. Result: **101 passed**, two Starlette/httpx deprecation warnings. No dependency files or environment packages were changed. The state probe records baseline defects; it is deliberately not a green regression suite. Convert its scenarios into assertions of corrected behavior during implementation.
