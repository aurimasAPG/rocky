---
name: marketing-numbers
description: Pricing, discounts, promotions, margin, CAC and A/B-test sizing with Rocky's calculators (unit economics, break-even, experiment plan), and what to write when an input is missing.
---

# Rocky · Numbers a marketer will act on

A recommendation with a figure in it is only worth what the figure is worth. These rules say which tool produces each figure, how to read it, and what to write when the marketer did not give you enough.

## Never do the arithmetic yourself

- Margin, contribution, break-even, "how many more do we need to sell", bundles, tiers, channel price build-up: call `unit_economics`. Quote its numbers; do not round them differently or re-derive them in prose.
- Anything about one customer over time or the money spent to win them — CLV against CAC, payback, churn, subscription rollout, returns, a free or subsidised entry offer, channel ROI, whether research is worth buying: call `customer_economics`. It computes only the blocks whose inputs you gave and lists the rest under `missing`.
- Sample size, how long a test runs, what counts as a win: call `experiment_plan`.
- If a figure in your draft did not come from a tool result or a cited source, remove it or label it an estimate with the assumption that produced it.

## Contribution is not margin is not markup

- *Contribution per unit* = price − variable cost. It is the money one more sale adds before fixed costs. `unit_economics` returns it as `contribution_per_unit`.
- *Margin* is contribution as a share of price (`margin_pct`). *Markup* is contribution as a share of cost. A 40 % margin is a 67 % markup. When the marketer says "margin", ask which one if the answer changes the decision.
- A price cut lowers contribution per unit, so volume must rise by more than the cut: a 10 % cut at a 40 % margin needs 33 % more sales (`required_uplift_pct`). Write that sentence, not "sales need to grow somewhat".

Source: Principles of Marketing on markup and margin; My Life in Advertising on break-even counts.

## What to write when an input is missing

- `unit_economics` lists what it lacked under `missing` and gives a `conditional` table. Either ask with `clarify` ("What does one unit cost you to make or buy?") or write the answer as a labelled condition: "If your unit cost is €60 (40 % margin), the cut needs 34 extra sales; at €80 it needs 100." Never pick one row silently.
- `experiment_plan` without `weekly_traffic` gives the sample but not the duration. Ask for the traffic or write "duration depends on eligible traffic: at 10 000 per week, 11 weeks".
- When a tool says a change cannot be recovered (contribution at or below zero) or a test is not feasible in the time available, that is the recommendation. Say it plainly and give the nearest thing that works (`detectable_in_max_weeks`, a smaller cut).

## Make the decision visible

- When the marketer states what they expect (a 20 % lift, a 15 % uplift from the promotion), pass it as `expected_uplift_pct` and report the `decision` and the two flip points: the unit cost and the uplift at which the answer changes. That is the sentence the marketer argues about in the meeting.
- A promotion that pays only above the flip point should be written as a test with a stop rule, sized by `experiment_plan`, not as a plan.

## A/B tests

- The baseline is the current rate as a percentage, the effect is the smallest lift worth acting on, not the lift you hope for. A 10 % relative lift on 3 % is 3.3 %, which is 0.3 percentage points.
- Several challengers against one control split the error budget; the tool raises the sample. Prefer one strong challenger over three weak ones.
- Copy the `decision_rule` into the plan word for word: fixed sample, whole weeks, no early stopping, the lift and p-value that count.

Source: the measurement and experiment-design entries of the playbook (#455–#500), especially #488 minimum detectable improvement and #464 break-even unit count.
