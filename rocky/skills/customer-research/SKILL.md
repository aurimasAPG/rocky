---
name: customer-research
description: Customer interviews and surveys. Whom to recruit (operators, maintainers, budget owners, nonconsumers, trade buyers), what to ask, how to record workarounds, use context, evidence.
---

# Rocky · Customer research: whom to interview, what to ask, how to record evidence

Rocky cannot run interviews. These rules say whom the marketer must recruit, the questions to ask them, and how to write what the web already says, so that a role with no public voice is marked missing instead of filled in. Apply them to the goal; do not quote them in the deliverable.

## Interview the people who live with the purchase

In a business purchase the operator, the person who maintains the product and the budget owner each have their own reason to refuse. Reviews online are mostly written by operators, so the other two reasons never surface unless you look for them.

- When the buyer is a business, `runs/notes/voc.md` has one section per role: `Role: operator`, `Role: maintainer`, `Role: budget owner`.
- Each section holds sourced quotes filtered by role words (chef, technician, owner, CFO), or the line `no public evidence — recruit N for interviews`.
- Each section ends with three interview questions written for that role's failure reason: daily friction, upkeep and downtime, payback. No quote appears under a role marked `no public evidence`.

Source: Jobs to Be Done, pp. 84–86.

## Inspect the workaround itself

A strap added to a mount or a cut-down profile says more about the operating condition than any satisfaction score. Ask to see what was changed, borrowed or built, and read the condition from it.

- When the goal mentions hacks, mods, DIY or workarounds, first ask with `clarify` for photos or videos of the modification, and run `vision_analyze` on anything in the folder before reading reviews.
- Write `## Workarounds` in `runs/notes/voc.md`, one entry per change: `Changed: … (source or photo) — Condition: … — observed|reported|assumed`.
- Each `Condition` feeds a `Segment:` circumstance in `runs/notes/facts.md` and one ad angle. With no photos or links, write a five-question show-me guide ("Show me what you changed and why" / „Parodykite, ką pakeitėte ir kodėl“) and mark every condition `assumed`.

Source: Democratizing Innovation, pp. 49–54.

## Observe the use context the spec leaves out

A condition everyone on site takes for granted, the cold hall or the machine running next door, never reaches the supplier's sheet. Copy and specs written from that sheet repeat the gap.

- Write `## Use context` in `runs/notes/research.md`: environment, operator, time of use, co-used tools, failure moments, each tagged `observed` (marketer photos or video via `vision_analyze`, a site-visit note), `reported` (quote with URL) or `assumed`.
- With zero `observed` items, add an `Observation plan`: where to go, five things to watch, thirty minutes.
- Copy and spec lines state only `observed` or `reported` conditions as fact; `assumed` conditions become questions, not promises.

Source: Democratizing Innovation, pp. 82–85.

## Search an analogous market for a harder version of the problem

Everyone in an industry copies each other. A field where the same physical or operational constraint is more severe has already solved problems this one still argues about.

- State the core constraint in one line. List two to four fields where it is harsher, and fetch one primary source per field with `web_search` and `web_fetch`.
- Write `## Analogous markets` in `runs/notes/research.md` as a table: field | why harder | practice or product | what transfers | source URL. A field with no source stays in the table as `no source found` and does not count.
- If the constraint is not clear from the goal, ask with `clarify`: "What is the hard physical or operational problem your product solves?" / „Kokią sunkią fizinę ar veiklos problemą sprendžia jūsų produktas?“

Source: Democratizing Innovation, pp. 149–151.

## Find rare experts through successive referrals

The person with a firsthand solution to the harder version of the problem is rarely found by a search. They are found by asking whom each knowledgeable source cites, co-authors with or speaks after, and following the trail.

- Start from the analogous-market sources. For each author, speaker or company, record whom they cite or present alongside; hop two to four times toward the more demanding version; stop at people with firsthand solutions (patents, papers, product pages).
- Write `runs/notes/experts.md` as a table: hop | person or role | organisation | why their version is harder | how found | public profile URL | source URL. Public professional information only; no private emails or phone numbers.
- A dead end is written `chain stopped at hop N: no public referral found`, with what to ask the last person. Never invent a name.

Source: Democratizing Innovation, pp. 151–152.

## Put a draft in front of a trade buyer

A purchasing manager at a retailer sees what shoppers ask for every day. A real draft, not a question about opinions, gets that knowledge out.

- When the goal names a trade buyer, distributor or retailer, write `runs/notes/expert_review.md`: a `Who:` line with the person or role, a one-page draft to show, and five to eight questions about the expert's customers, not about the draft: "Which sizes do shoppers return most?" / „Kokius dydžius pirkėjai dažniausiai grąžina?“ None asks "do you like it".
- Add `Pending: expert reply` to `runs/notes/voc.md`. When the reply arrives through `rocky resume`, mark each changed line `[expert]`.
- With no named buyer, ask with `clarify` once; otherwise write `Who: unknown` and leave the file as an open item.

Source: My Life in Advertising, pp. 72–73.

## Ask what customers would remove

Users asked what to add will always name something. Asked to remove one feature or step, they point at where the product feels heavier than it needs to be, and the follow-up says why.

- Enumerate the product's current steps or features first, from the site or a walkthrough, and have the marketer confirm; do not assume the list.
- Write `runs/notes/removal_survey.md` with the exact question, forced single choice: "If you could remove one feature or step from X, which one?" / „Jei iš X galėtumėte pašalinti vieną žingsnį ar funkciją, ką pašalintumėte?“ and the follow-up "Why that one? What would you do instead?" / „Kodėl būtent tai? Ką darytumėte vietoj to?“
- Add a tally template (step or feature | votes | verbatim reasons) with the rows prefilled and the counts empty. A survey that asks "what would you add?" instead does not meet this rule.

Source: Getting Real, p. 50.

## Include nonconsumers in entry-price research

Reviews and forums are written by people who already buy in the category. An entry price or a starter tier is for those who do the job by hand or skip it, and their reasons differ from premium buyers' complaints.

- When the goal concerns an entry price, a cheaper tier, a starter plan or opening the category, write `## Nonconsumers` in `runs/notes/voc.md`: sourced quotes from people doing the job manually (Excel, paper, an accountant on the side) or forgoing it, what stops them, and the simplification they would need.
- Write `## Current buyers' objections` separately, and say where the two conflict.
- With no nonconsumer voices found, write `Nonconsumers: no direct evidence found; interview 5 manual-process firms before pricing`. Do not invent quotes.

Source: Jobs to Be Done, pp. 74–75.

## Measure digital influence on store sales

When most sales happen in a shop, online orders alone make the site and the ads look worse than they are. Ask buyers at the till whether something online shaped their choice, and count store revenue with it.

- When the brief says most sales are offline, draft a three-question till or QR survey: "Did a website, review or comparison shape your choice?" / „Ar svetainė, atsiliepimas ar palyginimas paveikė jūsų pasirinkimą?“; "Which one?" / „Kuris?“; "Did you visit our site?" / „Ar lankėtės mūsų svetainėje?“
- With a survey export and receipts, compute the share and the influenced revenue with `data_analyze` and write `Offline influence: X % of store buyers (n=…), €Y of €Z store revenue` in `runs/notes/facts.md`. Blended return counts influenced store revenue, not online orders alone.
- Without the survey, deliver the questionnaire and a collection plan (four weeks, n ≥ 100) and write `Offline influence: not yet measured`. Never quote a percentage.

Source: Winning the Zero Moment of Truth, pp. 37–38.

## Measure familiarity and ad memory apart

Someone may know the name and misremember where they met it. One question, "have you seen our advertising?", counts recognition as ad recall, which is the confound an awareness survey exists to avoid.

- Write `runs/notes/survey.md` with four blocks as headings, in this order: `Spontaniškas prisiminimas` (unaided recall), `Žinomumas su užuomina` (aided familiarity), `Reklamos prisiminimas` (advertising recall), `Kur pirmą kartą matėte` (claimed first contact). No single question combines the brand and the advertising.
- Report the four as separate figures and include the sentence „Prekės ženklo atpažinimas neįrodo, kad reklama buvo matyta; šie du skaičiai pateikiami atskirai.“
- Without a baseline wave, say the first wave is the baseline and no lift can be claimed yet. Rocky cannot field the survey: name who runs it and label results pending.

Source: The Psychology of Advertising, pp. 160–161.

## Ask buyers for the map's axes before its placements

The perceptual-map rule in `marketing-strategy` builds the map from public buyer sources and labels it a hypothesis. When the marketer can reach buyers, this is how to confirm it.

- Ask first, open: "When you choose between these brands, what decides it?" / „Kai renkatės iš šių prekių ženklų, kas nulemia pasirinkimą?“ Take the two most-named attributes as the axes; do not offer your own.
- Then have each buyer place the competing brands on those two axes, and record the placements in `runs/notes/research.md` under `## Perceptual map (buyers)` next to the hypothesis table, with the number of respondents.
- An empty corner is a position only if buyers also say they want it; add that question before recommending it.

Source: Principles of Marketing, pp. 197–199.

## Ask whom buyers trusted for this decision

A café owner follows one blogger for coffee and asks someone else entirely about the internet contract. Seeding by follower count reaches the first and misses the second.

- An opinion-leader or seeding recommendation cites decision-specific trust: an interview answer, a forum thread or a review where buyers name whom they asked about this decision. Follower counts are not evidence.
- The interview guide carries the question „Kieno nuomonės klausėte, kai paskutinį kartą rinkotės interneto tiekėją?“ / "Whose opinion did you ask the last time you chose an internet provider?" Record each answer in `runs/notes/voc.md` under `## Authority`: person | decision | evidence (quote or URL) | trusted for other decisions: yes/no.
- A candidate with only popularity is labelled „bendras populiarumas — netinka“ and left out of the seed list. No interviews or threads yet → deliver the guide and mark candidates `hypotheses until interviews run`; do not rank them.

Source: Propaganda, pp. 40–41.

## Reconstruct the choice from the shelf backwards

The eight seconds at the shelf decide between the brands that survived; the others were dropped weeks earlier by a video, a bad batch or a price memory. Where a rival was discarded is where to fight it.

- A shopper-path note has `## Shelf-back` in `runs/notes/research.md` with three lists: the final set at the point of purchase; alternatives discarded before arriving, each with the stage (search, social, prior use, price memory) and a quoted source (interview line, review or forum via `web_search` and `web_fetch`); alternatives never considered.
- Each discard has a source or the tag `assumption — verify with 5 shopper intercepts`; interview counts are written as fractions (`2/6 quotes`).
- The recommendation says where to fight each rival: on the shelf (price, visibility) or upstream, at the stage where it was discarded. No shopper evidence → the section says so and holds a five-question intercept script instead of invented discards.

Source: Winning the Zero Moment of Truth, p. 20.

## Audit a platform's ecosystem before recommending it

A marketplace is chosen for who already sells and buys there, what it costs to get in, and how often it changes the rules on its sellers. A fee comparison alone picks the wrong platform.

- When the goal names two or more platforms, marketplaces or distributors, write `runs/notes/channels.md`, one block per platform with three sourced lines: `Participants:` (active sellers and buyers in the category), `Entry:` (fees in € and %, approval, days to first listing, API openness), `Adaptation:` (fee, ranking and policy changes in 24 months, dated).
- Run `claim_check` on channels.md with the fetched pages as sources; every fee and count must be `supported`. An unsourced line reads `Participants: unknown — no public seller count; ask the platform`, never an estimate.
- End with one recommendation and the condition that flips it: „Pradėti „Etsy“; pereiti į „Amazon.de“, kai mėnesio apimtis viršys N vnt., nes tada 39 €/mėn. mokestis bus mažesnis už „Etsy“ mokesčius už pardavimą“, with N from the fees.

Source: New Rules for the New Economy, pp. 75–77.
