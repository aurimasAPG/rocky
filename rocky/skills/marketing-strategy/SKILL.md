---
name: marketing-strategy
description: Market, segment and portfolio rules. Use before ads, campaigns or positioning, when mapping competitors, and when choosing which product lines get investment.
---

# Rocky · Market strategy and positioning

Apply these to the product in the goal. Each rule changes what you write. Do not quote them in the deliverable.

## Define the market around a durable job

Before any ad, write one line saying what the customer is trying to get done, in words that would still be true if today's product, platform or technology disappeared. "Get a steady flow of paying customers", not "run Meta ads". A listener wants to organize music, and records and streaming are two ways of doing that.

- The line names the customer's goal. It does not name the brand, the tool or the channel.
- Test it: swap the technology for an older or a newer one. If the line breaks, rewrite it.
- Put it first in `runs/notes/facts.md` as `Job: …`, and build every ad's angle on it.

Source: Jobs to Be Done, pp. 54–56.

## Segment by patterns of unmet needs

Age, job title and company size describe people. They do not say why anyone would buy. Group buyers by the outcome they struggle to reach, then name the circumstance that explains it and that someone could spot from outside: "runs a shop whose ad costs rose this quarter", not "managers aged 30–50".

- Write two or three `Segment: …` lines in `runs/notes/facts.md`, each with the struggle and the circumstance.
- Base them on what the site and its sources show. Mark a segment as a guess when nothing confirms it.
- Give each ad one segment. Its `audience` field states the struggle and the circumstance, so the marketer can turn it into targeting.

Source: Jobs to Be Done, pp. 97–98.

## Test repositioning before changing the product

A new message costs far less than a new product, so try it first. Find an outcome the product already delivers that a segment struggles to reach, and write that as the challenger. Run it against the pitch the site uses today, which is the control.

- The challenger promises only what the product does now. If keeping the promise would need a product change, it is not a repositioning.
- Quote today's pitch in `runs/notes/facts.md` as `Pitch: …`, so the control is the real message and not a weaker rewrite.

Source: Jobs to Be Done, pp. 99–101.

## Protect category table stakes

Some outcomes matter to customers and every serious competitor already delivers them: delivery on time, a working checkout, a reply within a day. Doing them earns no preference, and missing one loses the sale, whatever the ad says.

- Write two or three `Table stake: …` lines in `runs/notes/facts.md`, each with the outcome, the minimum acceptable level, and whether this brand meets it. Write `unknown` when the sources do not say.
- Pass them to `ad_board` as `table_stakes`. A missed minimum is flagged at the top of the page, because it should be fixed before money goes on ads.
- No ad leads on a table stake. Differentiate on something competitors do not already deliver.

Source: Jobs to Be Done, pp. 105–106.

## Match an existing portfolio to segments

A company with several products should not push the same one at everybody. For each product already on sale, find the segment whose struggle it solves best, so whoever answers a lead knows what to recommend.

- Write one `Match: <product> → <segment>` line per product in `runs/notes/facts.md`.
- Each ad's `mix.product` is the product matched to that ad's segment. The page turns these into a guide for the people handling the leads.
- A product that matches no segment gets no ad. Say so in the facts instead of forcing one.

Source: Jobs to Be Done, pp. 113–115.

## Question investment choices with the growth-share matrix

When a goal compares product lines or asks where money should go, place each line on two axes: how fast its market grows, and its share relative to its largest competitor.

- High share in a growing market needs cash to hold its place. High share in a slow market pays for the others. Low share in a growing market needs a decision to back it or leave. Low share in a slow market needs a reason to stay.
- Then test the proposal against the position: does this line's cash need support the investment or withdrawal being proposed? Say so plainly when it does not.
- Share and growth are rarely public for a small company. Use sourced figures where they exist. Otherwise label each position as an estimate, give its range, and name the number that would change the answer.

Source: Principles of Marketing, pp. 71–73.

## Separate market development from market penetration

Selling more to the market a brand already serves is penetration. Taking the same product to new buyers or a new country is development, and it carries a risk penetration does not: those buyers may not be reachable at all.

- Say which one the campaign is, on a `Growth: …` line in `runs/notes/facts.md`.
- For development, add a `Reach: …` line with evidence: a channel they use, the language, delivery that covers them. A Lithuanian site and Lithuanian ads do not reach Latvian buyers.
- If you cannot find that evidence, leave `reach` empty. The page warns the marketer, which is better than an invented answer.

Source: Principles of Marketing, pp. 75–76.

## Check whether a segment is commercially usable

A segment can be real and still not be worth an ad. Lithuania is a small market, so a niche that works elsewhere may be a few hundred buyers here.

- Estimate each segment's size, its buying power and what it costs to reach. Say where the number came from, or label it an estimate.
- Drop a segment that is too small or too poor to pay back, and say so in the facts.
- A separate offer is justified only if the segment would respond differently. If two segments want the same thing for the same reason, merge them.

Source: Principles of Marketing, pp. 188–189.

## Build a perceptual map from buyers' judgments

A positioning map is only worth having if the axes and the placements come from buyers. A map drawn from the marketer's own opinion just restates it.

- Take the axes from what buyers say decides their choice: reviews, forums, comparison sites. Quote where each came from.
- Place each competing brand from those same sources, in a table a reader can check.
- Rocky cannot survey buyers. Label the map a hypothesis until real buyers have confirmed it, and name who to ask.
- An empty corner is a question, not a position. Before recommending it, look for signs of demand: search interest, buyers asking for it, a competitor that tried. No sign of demand means the corner may be empty for a reason, so say that.

Source: Principles of Marketing, pp. 197–199.

## Fund the introduction, not only the conversion

An online-only seller can put all its money into search, shopping and retargeting and see a fine return, because those channels catch people who already know the brand or are already looking. The harder job, making new people aware, goes unfunded, and the new-customer share falls while the ROAS looks healthy.

- When the business sells only online and every channel listed is search, shopping or retargeting, write `Spend split: X % in-market, Y % new audiences` in `runs/notes/facts.md`, from the account export or labelled an estimate.
- Propose one broad-reach test with a control, a regional split or a holdout, and size it with `experiment_plan` on the new-customer rate, not on ROAS.
- If the new-customer rate is missing, write that the test cannot be sized and ask for it with `clarify`. Do not invent a duration.

Source: Effectiveness in Context, pp. 30–31.

## Look inside the company before funding a feature

A team somewhere in the company may already reach the outcome customers ask for, with a spreadsheet, a Zapier flow or a vendor tool. Making that customer-ready is usually cheaper than building, and the question costs nothing to ask.

- Before any build-or-not verdict, search the workspace and the marketer's files for an existing tool or process. If nothing turns up, ask with `clarify`: "Does any team already achieve this outcome with a tool or a manual process?" / „Ar kuri nors komanda šį rezultatą jau pasiekia kokiu nors įrankiu ar rankiniu procesu?“
- Write one line in `runs/notes/facts.md`: `Internal solution: <tool or process> — to make customer-ready: <list>`, or `Internal solution: none found (asked)`.
- Headless, with no answer, write `Internal solution: not checked — ask ops/support`. Never write `none` without having asked.

Source: Jobs to Be Done, p. 118.

## Test affect and capability separately

A warm, likeable campaign can make buyers rate the supplier's product as more capable, but a single "which do you prefer" question cannot show that. Measure the feeling and the judgment of capability apart, or you will never know which one moved.

- Design the test as two cells, warm creative against a neutral control, with two scored outcomes on the same scale: warmth or liking, and perceived capability.
- Write the design in `runs/notes/research.md` under `## Mechanism test`: the two cells, the two outcomes, and a decision rule for three cases: both up, liking up only, neither.
- Size it with `experiment_plan` using the share who rate capability 4 or 5 out of 5 as the rate. Report both deltas; a liking gain without a capability gain is reported as exactly that.

Source: The 5 Principles of Growth in B2B Marketing, pp. 25–27.

## Show verified proof where the buyer has no experience

Ratings do most of their work when the buyer cannot judge from experience: a new brand, or a comparison against names they know. Beside a familiar brand they add little; beside an unknown one they can decide the choice.

- For an unfamiliar brand or a comparison block, write the rating with its count and platform, "4,6 iš 5 (312 atsiliepimų, Trustpilot)", never a bare "4,6". Fetch the rating page and put its URL in `runs/notes/facts.md`; an unfetched rating is written `unconfirmed`.
- Propose the display as a test only where buyers lack first-hand experience, and size it with `experiment_plan` on the step's baseline rate and weekly traffic.
- Do not invent ratings, counts or review text.

Source: Decoding Decisions, p. 50.

## Judge a preview on resolved uncertainty, not clicks

A try-on, a configurator or "see it on me" earns its place if it helps the buyer choose between alternatives and come back less often with a return. Conversion alone can rise while returns rise with it.

- When the brief involves preview, try-on or visualising the product on the buyer's own image, make one mock preview with `image_generate` using the marketer's product image as `reference_path`, so the deliverable shows the idea.
- Size the test with `experiment_plan` on the stated baseline. The plan names two outcomes: conversion and a resolution metric (return rate, or share of sessions comparing two or more alternatives before buying), with the `decision_rule` copied word for word.
- A missing return rate is written "to be measured from the first week", not estimated.

Source: World Wide Rave, pp. 153–154.

## Invite a fair comparison instead of claiming superiority

When buyers keep switching between the brand and a named competitor, a side-by-side trial on the attributes they say decide the choice is a stronger argument than any adjective. The ad invites; the trial argues.

- Write a `Compare:` line in `runs/notes/facts.md`: the two or three attributes buyers say decide (with URLs), the fair conditions (blind, same preparation, same price), and the metric, share of participants who choose ours at their next purchase, with its current baseline.
- The ad's `angle` is the invitation. No copy says "the best" or names the competitor as worse.
- Size the trial with `experiment_plan`: baseline is the current share among switchers, effect is the lift worth the trial's cost. Without evidence of substitution, say the comparison is not warranted and why.

Source: Scientific Advertising, p. 34.

## Map what the buyer feels at each touchpoint

A first-time buyer tracking a late parcel needs different words from someone choosing between products. Copy written for one stage and shown at another reads as indifference.

- When the goal mentions a journey, touchpoints, an email flow or after-sales, write `runs/notes/journey.md` as a table: touchpoint | stage (discover / choose / buy / wait / use / return) | emotion | information needed | what the brand shows now | gap.
- The emotion cell holds a quoted VOC source with URL, or `not evidenced`. The "shows now" cell holds a fetched URL or snapshot, or `not observed — paste the email`.
- In `runs/notes/voc.md`, tag each quote with its stage (`[wait]`, `[choose]`), so the table can be filled from it. Never assert an emotion without a quote or an observed page.

Source: eMarketing, pp. 46–47.

## Compare the settings where the product is accepted and rejected

The same product can be rejected at home and bought daily in a café. The difference is rarely the product; it is the preparation, the form or the company it keeps, and that difference is the message.

- When the brief or the research shows acceptance in one setting and rejection in another, write `## Setting contrast` in `runs/notes/research.md`: setting | form | preparation | presentation | accompaniment | source URL, one row per setting.
- Below it, one line `Difference: …` naming what to carry into copy („šildyk kaip kavinėje“).
- If only one setting has evidence, write `no second setting observed` and say where to look: menus, social photos, store visits. Do not invent the contrast.

Source: My Life in Advertising, p. 109.

## List the problems a new behaviour creates

When a behaviour takes off, it brings problems for the people doing it, for partners and for bystanders, and each problem is a service someone will pay for. The opportunity is in the second order, not in copying the first.

- When the goal mentions a trend, a boom, adoption or „antrinės problemos“, write `## Second-order opportunities` in `runs/notes/research.md` as a table: stakeholder (users, operators, partners, regulators, bystanders) | problem created | evidence URL | who pays today | candidate offer.
- Every problem row needs a source URL in `research.md` or `runs/notes/facts.md`; otherwise mark it `hypothesis`.
- Cover at least three stakeholders. End with a ranked shortlist and a line saying how many rows are sourced and how many are hypotheses.

Source: New Rules for the New Economy, pp. 153–155.

## Walk the configurator as a first-time user

A design-your-own tool is a promise that the factory will make what the buyer submits. If the tool lets a first-time user build something the factory sends back, the ads sell rework.

- Get the production constraints from the marketer (`clarify`) or a file. Without them, write "feasibility cannot be judged without the factory rules" and list the rules inferred from the UI as `untested`.
- Walk three scripted paths in the browser: all defaults; maximum options; one deliberately infeasible choice. Note whether each reaches the cart.
- Write `runs/notes/feasibility.md` as a table: rule | enforced in tool (yes / no / unknown) | evidence (snapshot). Name the one rule most likely to cause rework and recommend blocking it in the tool before the launch.

Source: Democratizing Innovation, pp. 170–174.

## Prove a physical item can be made before promising it

A cork-textured leaflet or a wooden display is easy to write into a deck and hard to cut, print or ship by the date. Hopkins sold a creative on a wood his own factory could barely work.

- When any deliverable is physical (print, packaging, display, merch, event build), write `runs/notes/production.md` as a table: item | material and finish | quantity | supplier (URL) | lead time in working days | MOQ | unit cost | prototype or proof date.
- Copy does not promise a finish the proof has not cleared; the deck says "subject to proof".
- Without a supplier or lead time, write `lead time unknown — get a quote and a physical proof before approving the design`. Do not invent a date.

Source: My Life in Advertising, pp. 52–54.

## Service behaviour starts with managers

Staff repeat with customers the courtesy they receive from their managers. A standard that trains baristas and says nothing about area managers coaches the wrong people first.

- Any service-standard or coaching deliverable is a table: customer-facing behaviour | the manager-to-staff behaviour that models it | observable cue for each | who observes and how often.
- Add a `## Managers first` section: managers are observed in week one, before any front-line coaching.
- Use only the scores the marketer gave. Without observation data, label the coaching priorities hypotheses to confirm in the first observation round.

Source: Publicity, p. 47.

## Route public complaints to someone who can resolve them

"Write to us privately" closes a thread and leaves the problem open. A public complaint should end with a named person who can refund, re-deliver or fix, and a public line saying it was done.

- Write `deliverables/handoff.md` as a table with one row per public channel: channel | acknowledge within | owner (a named person or exact role) | resolves via (private channel) | public close-out line.
- Add reply templates in the brand's language for acknowledge, move private and resolved, per issue type.
- If the marketer cannot name a resolver, ask once with `clarify`; otherwise write `OWNER NOT ASSIGNED` and flag it as the top open issue. Never write "team" or „komanda“ as the owner.

Source: eMarketing, pp. 429–431.

## Test a name bare, without the pack or the slogan

On the shelf, a name has to say what the product is before the tagline helps. A name that only works with the slogan has failed the test the shelf will give it.

- State in one line the use the name must convey. Then write a comprehension script: show each name alone and ask "What does this product do?" / „Ką šis produktas daro?“ and "Would you use it for X?" / „Ar naudotumėte jį X?“, with a pass target such as "at least 60 % name the category unaided".
- Any ranking by Rocky is a hypothesis; label it „hipotezė, ne pirkėjų atsakymai“. As a cheap proxy, ask five independent model calls, each shown one bare name, "What is this product?", and report the hit rate as `proxy, not buyers`.
- If the marketer gives survey answers, code them against the category and report comprehension per name.

Source: Scientific Advertising, pp. 91–93.

## Test the flow on paper before code

A booking or checkout flow is a hypothesis about which step the buyer takes next. Five people with paper screens find the wrong turn before a developer builds it.

- Write `deliverables/prototype.html`: one section per screen, each with at least two next-step buttons that move between screens. No images needed.
- Write `deliverables/walkthrough.md`: one task sentence per screen, and a five-row table with columns `Tikėtas kitas žingsnis` / `Stebėtas kitas žingsnis` / hesitation / quote.
- Name the step where you expect buyers to leave. If the marketer gives no flow, derive it from the live site and label it `current flow, not proposal`; do not invent a new one.

Source: eMarketing, pp. 160–161.

## Design the transition to a higher price

"We will communicate transparently" is not a plan. Current customers need to know whether they are affected, by how much and on which bill, and the company needs to know what each path costs.

- Compare at least three paths side by side: immediate with 30 days' notice; a transition period at an intermediate price; existing customers keep their terms and only new ones pay more. One `unit_economics` call per path, with expected churn as a negative uplift.
- Write an affected-customers table: segment | count | old price | new price | date of first changed bill.
- The announcement names both prices and the exact bill date for the reader's group. Missing counts make the table conditional; ask with `clarify` rather than invent them.

Source: Getting Real, p. 105.

## Price a custom offer from two numbers

Whether a cut-to-size or bespoke version can pay for itself follows from what users already spend modifying the standard product and what they say they would pay for an exact fit. Both are evidence; opinion is not.

- When the brief mentions custom, bespoke, cut-to-size or configurable, record two lines in `runs/notes/voc.md`: `modification_spend: …` (euros or hours per unit, with quotes and sources) and `fit_wtp: …` (the stated premium, with quotes or a competitor's custom price URL).
- Call `unit_economics` with new price = standard price + fit_wtp and new variable cost = standard cost + custom cost. The premium must not exceed fit_wtp and contribution must not fall; otherwise write `custom offer unsupported: premium €X < extra cost €Y`.
- No evidence of modification means "no evidence users modify this product; test interest before building".

Source: Democratizing Innovation, pp. 54–58.

## Film the task people cannot follow in writing

A how-to video pays when it covers one task that support keeps explaining and carries the title people type. "Video published" is not a result; the viewer finishing the task is.

- Choose the task from evidence: support tickets, FAQ, forum questions. Write a `Search phrase:` line in `runs/notes/copy.md` with the phrase people type and its source URL (autocomplete or a forum thread).
- The title carries that phrase and the product name. The shot list shows one action per step.
- Write a `Completion check:` line: fewer support contacts on that task against a stated baseline, or five viewers each completing the task after watching. Without a baseline, say so and use the five-viewer trial.

Source: Winning the Zero Moment of Truth, pp. 50–51.

## Treat an objection as a pointer, not a question

"Too expensive" can mean cash timing, a cheaper quote from someone else, or doubt that the work is worth it. A reply that answers the word answers the wrong concern two times out of three.

- Write `runs/notes/objection.md` with the objection verbatim, the candidate concerns with the evidence for each and a likelihood, `resolved: yes/no`, the clarifying question and the reply.
- If more than one concern remains plausible, the reply's first move is the clarifying question, "Does the other offer cover the same scope?" / „Ar kitas pasiūlymas apima tą pačią apimtį?“ No discount, instalment or concession while `resolved: no`.
- When the thread gives no signal, say so and lead with the question rather than pick a concern.

Source: Principles of Marketing, pp. 528–529.

## Locate the source of a channel conflict

A dispute between two dealers at the same level and a dispute between the supplier and its resellers call for different fixes. Before recommending, say which it is and what nobody has defined.

- When the goal names partners, dealers, distributors, pharmacies or resellers, write `## Channel conflict` in `runs/notes/research.md`, one row per dispute: parties | level (horizontal / vertical) | dimension (pricing / account ownership / service responsibility / territory) | who decides | fix.
- Rank by what is at risk: points of distribution first.
- If the brief only says "partners are unhappy", ask with `clarify` which channels and what each party believes it owns. Do not guess the dimension.

Source: Principles of Marketing, pp. 607–608.

## Name the obstacle each piece of partner support removes

A partner programme is a list of things the company gives: notes, working examples, a sandbox. Each one is only worth what it unblocks, and the proof is a partner shipping.

- For every support item, write a row in the partner deliverable: obstacle removed | support item (technical notes / working example / equipment or sandbox access) | ship signal (what the partner has shipped by when).
- No row may have an empty obstacle or ship signal. If obstacles are unknown, ask with `clarify` offering three candidates; write the ship signal as `to be agreed` rather than invent a date.
- The programme states a target: how many of the partners have shipped by which day.

Source: The Macintosh Way, pp. 117–118.
