---
name: marketing-strategy
description: Market definition and positioning rules. Use before writing ads, campaigns or a positioning statement: the job the customer wants done and segments by unmet need.
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
