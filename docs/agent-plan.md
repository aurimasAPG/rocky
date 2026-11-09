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
| Every tool result and fetched page kept as evidence for the figure check | `rocky/agent.py` |
| One self-review turn in the same context with the verifier's rubric | `rocky/agent.py` |
| Independent frontier verifier behind `--verify strict` | `rocky/agent.py`, `rocky/cli.py` |

Done when: verifier spend is under €0.20 on a moderate goal, and `passed_with_notes` means what it meant before.
