---
name: pr-response
description: Public replies to criticism, press visits, staff social-media guidelines, contributor and community programmes. Who speaks, where, with which facts, and what to disclose.
---

# Rocky · PR, public replies and communities

A reply the public can check, a journalist who can see for themselves, a staff member who says who they are: each of these is a rule about disclosure. Apply them to the goal; do not quote them in the deliverable.

## Give staff three things to say and one way to say who they are

Staff answer customers online whether or not there is a guide. A list of prohibitions leaves them guessing; showing what they may say, how to disclose their job and what stays confidential lets them answer well.

- The guide names at least three concrete topics staff may discuss publicly (how the order process works, how a product is used, public prices and delivery terms), with an example sentence for each.
- It contains the exact affiliation sentence: „Dirbu X, rašau savo vardu.“ / "I work at X, I am writing in my own name."
- It lists confidential categories by name: customer data, unreleased prices, supplier terms, and whatever the marketer adds. Without an answer, use those three and say they were assumed.
- It has a `## Repeticija` section: a realistic customer post, the model reply that names the employee's role and moves the case to a ticket, and one line on what made it right.

Source: World Wide Rave, pp. 113–115.

## Answer criticism in the thread where it appears, with a name

A substantive criticism in a public thread is answered there, point by point, with facts the reader can check and a person who signs it. "Sorry, DM us" moves the discussion out of sight before anything has been said.

- Fetch the thread with `web_fetch`; if it fails, say so and ask for the pasted text. Quote each criticism point verbatim.
- Answer every point with a fact and its source from `runs/notes/facts.md` or a public page. The reply ends with the spokesperson's name and role; „Komanda“, "team" or "support" is not a spokesperson. Without one, ask with `clarify`; headless, write `[Vardas, Pareigos]` and report it as open.
- Write the reply JSON (`thread_url`, `criticism_points`, `facts`, `spokesperson`, `reply_text`) and run `claim_check` in reply mode before delivering. The reply continues in the original thread and points nowhere else first.

Source: World Wide Rave, pp. 138–141.

## Let reporters see for themselves and say who pays

A journalist who watches the line run and questions the shift supervisor without a PR person present writes something readers trust. Editorial policies on hosted trips mean the costs must be stated, or the outlet will state them for you.

- A press-visit deliverable (`deliverables/press-visit.md`) has three headings: `What you will see` (firsthand, named places and people), `Independent Q&A` (a time slot with named operational staff, no communications staff present) and `Costs covered` (transport, accommodation, meals, each listed as covered or not provided, or `none`).
- Add a line granting permission to publish anything observed, so the outlet can decide on disclosure.
- If the marketer has not said what is covered, ask with `clarify`; the headless default is "nothing beyond on-site refreshments". Never leave the section out.

Source: How We Advertised America, pp. 269–270.

## Design participation around why people make things

Contributors send drawings, templates or test reports because solving their own problem, learning a skill or enjoying the work rewards them. A programme whose only prize is "being featured" misses the motives that actually drive participation.

- When the brief involves contributors, UGC, beta testers or a community, the deliverable has a motives table: motive (solve own problem / learn a skill / enjoy the work / recognition) | evidence (a quoted contributor or forum statement with URL in `runs/notes/facts.md`, or `assumption — verify with the survey`) | design element that serves it.
- At least one row each for learning and for solving a problem; recognition alone fails the rule.
- Add two or three questions to ask contributors why they took part: "What made you want to send this?" / „Kas paskatino jus tai atsiųsti?“ With no evidence, those questions are the first step.

Source: Democratizing Innovation, pp. 76–77.

## Give contributors a reason to share what they built

A user who built a modification for private use loses something by publishing it: time to maintain it, sometimes an edge over competitors. They share when credit, improvements from others or the supplier's support outweigh that cost.

- Before proposing incentives, write `## Contributor incentives` in `runs/notes/research.md`, one row per known modification: what they built | who else benefits if it spreads | what disclosing costs them | offset (credit / reciprocal improvements / supplier support such as free seats, priority support, co-maintenance).
- Every incentive in the plan names the contributor cost it offsets; an incentive without a matching row is dropped.
- With no real modifications known, say so and propose a discovery step, a survey of power users. Do not invent contributor types.

Source: Democratizing Innovation, pp. 102–104.

## Give fans the first look and watch what they repeat

A community that hears first, from a page it can check, spreads the launch for you; a community that hears with everyone else spreads nothing. What fans repeat is the test of the announcement.

- When the brief names an existing community (group, newsletter, club), `deliverables/launch-fan-first.md` has: the first-look asset with at least one fact the public will not get yet; one canonical reference URL (or `deliverables/reference-page.md` to publish first); the gap in hours before public release; the three to five facts to watch for in shares.
- Run `claim_check` with the fan announcement as the draft and the reference page as the only source; every claim is `supported` by that page before it goes out, and a `number_differs` is fixed in the draft, not explained.
- After launch, collect shared posts (`web_search`, pasted links) and tally which watched facts appear under `Repeated:` in `runs/notes/facts.md`. No community → write that fan-first does not apply.

Source: World Wide Rave, pp. 23–25.

## Prove a bridge person is in both communities

The people who carry news between two groups are the few who genuinely take part in both. A plausible name is the easiest thing to invent, so each one is proved on two pages.

- Each candidate is a row in `runs/notes/bridges.md`: name or handle | community A URL | community B URL | evidence A | evidence B | how information travels between them.
- Evidence is the handle found on the fetched page: `web_fetch` or `browser_navigate` each URL and quote the snippet; run `claim_check` with the row as the draft and the two URLs as sources so the handle is matched, not remembered. A closed group is `not accessible`.
- Only rows with both snippets are `patvirtinta`; the rest are `nepatvirtinta — tikrinti rankiniu būdu`, listed separately and never as confirmed. For each community pair name one observed channel (a cross-post, a shared event, a named reposter) with its URL; none found → say so and ask the admin.

Source: Propaganda, pp. 21–22.

## Choose review outlets by whom their readers advise

A review in the accountants' association newsletter reaches the people who tell the buyer what to use; a review in a national portal reaches everyone else. Rank outlets by who reads them, not by how many.

- When the goal mentions review, press, PR, apžvalga or žiniasklaida, write `runs/notes/outlets.md`: outlet | URL | readership (who, with a URL proving it: media kit, about page, member list) | size figure + source URL | readers advise the target market? yes/no + evidence | rank.
- A specialist outlet whose readers are or advise the buyer ranks above a larger general outlet; a row with `readership unknown` cannot rank above an evidenced row.
- Run `claim_check` on outlets.md with the table's URLs as sources; every size figure must come back `supported`. A circulation remembered from general knowledge is deleted, not kept with a caveat.

Source: The Macintosh Way, pp. 140–141.

## Give channel staff the product, then watch a demo

A reseller's rep who pitches from a PDF cannot answer the buyer's second question. Enablement means a seat of their own, a real task and someone watching them do it.

- An enablement deliverable for resellers, partners, dealers or distributors has four sections: `Prieiga` (own seat or sandbox per rep, for how long); `Demonstracinė užduotis` (one realistic task from the rep's own work with an observable pass: done without the trainer in N minutes); `Pirkėjo klausimai` (at least five follow-up questions with answers); `Stebėjimas` (date and owner).
- Every figure in the answers passes `claim_check` against `runs/notes/facts.md`; a question with no sourced answer stays with `atsakymas: nežinomas — paklausti produkto savininko`.
- If the rep's daily work is unknown, ask with `clarify` or label the demo task an assumption; never invent reseller names. Slides and a sales sheet alone do not meet the rule.

Source: The Macintosh Way, pp. 89–90.
