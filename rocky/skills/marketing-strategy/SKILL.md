---
name: marketing-strategy
description: Market, segment and portfolio rules. Use before ads, campaigns or positioning, and when choosing which product lines get investment.
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
