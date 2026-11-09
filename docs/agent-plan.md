# Rocky as one agent: phases 1 to 3

Today a goal passes through an assessor, a critic, a planner, one fresh worker context per step and a separate verifier. This plan makes one model hold the whole job from goal to handover. Phases 4 to 6 (one model, surfaces, removing the old path) come later.

## Assumptions

- **Model.** The agent runs on the model the cast already calls `worker` (`ROCKY_WORKER`, `--model`). Choosing Opus or Sonnet as the default is phase 4.
- **Strict verifier.** It stays available behind `--verify strict`. Dropping it is a separate decision.
- **Old path.** `--mode orchestrate` runs the previous loop unchanged until phase 6.
- **Paid runs.** Verification uses scripted clients and a local stand-in for OpenRouter driven through the real CLI. No paid model run is part of these phases.

## Phase 1: one context

| Work | Where |
|---|---|
| Agent loop: one message thread, tool dispatch, spend, compaction, repeat guard, write-only pass at the turn cap | `rocky/agent.py` |
| Tool rules and tool-call execution shared with workers | `rocky/engine.py` |
| `done` tool: the agent names the deliverable and ends the work | `rocky/agent.py` |
| `--mode agent` by default, `--mode orchestrate`, `mode` in config | `rocky/cli.py`, `rocky/config.py`, `rocky/job.py` |

Done when: the three marketing briefs (pricing, ads, data file) end in a deliverable on the agent path with scripted clients.

## Phase 2: no assessor, no critic, no planner

| Work | Where |
|---|---|
| Complexity, domain, deliverable and web need from the words of the goal | `rocky/assess.py` |
| A recipe becomes a checklist seeded into `todo`; items can be skipped with a reason | `rocky/agent.py` |
| `todo` updates drive the existing step events | `rocky/agent.py` |
| `rocky route` answers without a model call | `rocky/cli.py` |

Done when: no assess, critic or planner call appears in the events of any goal, and the budget question still fires.

## Phase 3: code first, then one self-review

| Work | Where |
|---|---|
| Final gate in code: deliverable on disk, recipe files, ad board clean, every figure through `claim_check`, `plan_check` where a plan was written | `rocky/gate.py` |
| Every fetched page and calculation kept as evidence for the figure check | `rocky/gate.py` |
| One self-review turn in the same context with the verifier's rubric | `rocky/agent.py` |
| Independent frontier verifier behind `--verify strict` | `rocky/agent.py`, `rocky/cli.py` |

Done when: verifier spend is under €0.20 on a moderate goal, and `passed_with_notes` means what it meant before.

## Verification, 2026-11-09

| Check | Result |
|---|---|
| Full test suite on Python 3.13 and 3.11 | Pass |
| Real CLI and background job against a local stand-in for OpenRouter: pricing, ads and data briefs | Each ended `passed` with its deliverable on disk |
| Same harness: a figure with no source | Sent back once by the check, removed, then `passed` |
| Same harness: `rocky resume "..."` on a finished agent run | `passed`, file updated |
| Same harness: `--verify strict` and `--mode orchestrate` | `passed` |
| Web progress reducer fed the recorded runs | Stages and steps correct, a skipped step shown as skipped |
| Assess, critic or planner call on an agent run | None in any scenario |
| Review turn on a full run of 104,622 tokens, list price, no cache | €0.08 on Gemini 3.8 Flash, the default |

No paid model run was made, so answer quality on real goals is still unmeasured.

## Before phase 4

The prompt cache breakpoints cover only the system and task messages (`CACHE_HEAD` in `rocky/llm.py`). That was enough for short worker chats. In one long agent thread on an Anthropic model the middle of the conversation is billed at full price every turn. The same review turn costs €0.20 on Sonnet 5.5 and €0.39 on Opus 5.5 without a cache hit, so a rolling breakpoint has to land, and be tried on a real call, before either becomes the default.
