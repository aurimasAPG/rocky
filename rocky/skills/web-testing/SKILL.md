---
name: web-testing
description: How a worker tests a site, form, flow or app with the browser tools: a walkthrough row per step, what to measure, what counts as a fail, what cannot be tested headless.
---

# Rocky · Testing a site, form, flow or app

`browser_navigate` opens a URL in headless Chromium and returns the title, URL and visible text; `browser_snapshot` re-reads the same page. Neither clicks, types, scrolls, switches to a phone viewport, takes a screenshot or reports timing. A step that needs a click or a typed value is done through an `mcp__playwright__*` tool when one is listed; otherwise ask the marketer with `clarify` for a screenshot or screen recording of that step, read it with `vision_analyze`, and record the step as `not reached headless`. Never describe a screen you did not see.

Every test writes `runs/notes/walkthrough.md`, one table row per step: `url | action | what appeared | seconds | money mentions`. `action` is what was done (`navigate`, `marketer: tap Apmokėti`, `type jonas@`); `what appeared` quotes the visible text that matters (heading, error, button label); `seconds` is `curl -s -o /dev/null -w '%{time_total}' <url>` from `bash` for a page Rocky opened, the recording's clock for a marketer step, `—` otherwise; `money mentions` lists every € figure and every „nemokamai“ / "free" on the screen verbatim. Each rule below adds its own fail condition. Apply them; do not quote them in the deliverable.

## Write the demo as claims before walking the real flow

"Upload → approve → paid in 3 clicks" is a claim, and the real signup has screens the demo skips. The ad may promise only what the real flow does.

- Before opening anything, write `## Demo claims` in `runs/notes/walkthrough.md`: one numbered line per promised step; the count („3 paspaudimai“) is itself a claim.
- `browser_navigate` the start URL and add a row per screen; a required field, a confirmation email, a login wall or a payment screen is its own row. Steps that need a click or typed data follow the headless routing above.
- Write `## Demo vs reality`: demo step | real steps | divergence, and the line `Realiai: N žingsnių, iš jų M demo nerodo`. Fail: the real count exceeds the claimed count, or the flow stopped; then no ad carries the "N clicks" claim and the note says `flow could not be completed past step N`, never the remaining steps.

Source: Getting Real, p. 61.

## Inspect the empty account screen by screen

A dashboard with no data and no first action strands the new user on day one. Every screen must be opened empty, before anyone has typed a record into it.

- A fresh account is needed: ask with `clarify` for a throwaway login or sandbox URL. Without one, test only public screens and write `Empty account: not inspectable`.
- Enumerate screens from the navigation text of the first snapshot; every menu item is a row. `browser_navigate` each and add the columns `has data: no` and `first action offered: yes/no + exact CTA text`.
- Fail `stranded`: a screen with no example data and no CTA. For each, propose one first-action sentence („Pridėkite pirmą klientą — užtruks 30 s“). A menu item without a row means the audit is incomplete; say so instead of claiming coverage.

Source: Getting Real, pp. 81–83.

## Attempt the export before promising portability

"You can leave any time" is believed when a trial user has watched the export happen. A help-centre page that says CSV is not proof; a file that opens is.

- Find the export in the snapshot text (Nustatymai, Eksportuoti, Export, CSV). The download itself needs a click: `mcp__playwright__*` when listed; otherwise ask the marketer to run the export and drop the file in the workspace.
- `office_read` the file and record in the row: file name, format, the tables and columns found, and which data (invoices, clients, attachments) is absent.
- Copy may claim only what the file shows: „Visas sąskaitas ir klientus eksportuojate CSV formatu (Nustatymai → Eksportuoti)“ with the walkthrough row as its source. No file → the copy says „Eksporto nepavyko patikrinti“ and names no format. Fail: a format claim without a file.

Source: Getting Real, pp. 93–94.

## Walk open → upgrade → cancel before launch

Cancellation is where a paid plan loses trust: a surprise charge, or a confirmation that does not say what happens to the data or the paid month. Walk it without help before the launch date.

- Needed: trial URL, a card the marketer confirms is a test card on a test-mode account, permission for a throwaway account. Ask with `clarify`; without a test card stop at the upgrade screen and write `cancel_not_reached`, never cancellation from help pages.
- Every screen is a row; `money mentions` holds every € figure on it; quote the cancel confirmation verbatim.
- Report total steps and total seconds. Fail `unexpected charge`: a money mention on a cancel step absent from the upgrade step. Fail `unclear outcome`: the confirmation lacks any of end date, what happens to the data, refund or proration. Each fail quotes the text.

Source: Getting Real, pp. 103–104.

## Test error messages with wrong values

A checkout that loses a third of shoppers on the address step usually tells them something is wrong without saying what or how to fix it. Type wrong values and read the message, do not infer it.

- Default probes: email without `@` (`jonas@`), a four-digit phone (`8612`), an empty required field. Typing needs `mcp__playwright__*` or the marketer's screenshot of each error state, read with `vision_analyze`. Stop before any submit that would place an order or a payment; write `not tested: would place a real order`.
- Write `## Form errors`: Laukas | Įvesta | Įvardija lauką | Paaiškina, kaip taisyti | Išlieka iki pataisymo, each Taip/Ne with the exact error text quoted.
- Fail: any `Ne`, or a message with no fix instruction („Neteisingas formatas“ alone). An untested field is `netestuota`, not a guess; `web_fetch` of the HTML (`required`, `pattern`) tells what is validated, not what the shopper sees.

Source: eMarketing, pp. 163–164.

## Run the searches that find nothing

Shoppers misspell, skip diacritics and ask for brands the shop does not stock. The empty result page is where they leave or are rescued.

- Probe set, at least one each: a misspelling (`grazstas`), a spelling without diacritics (`dazai`), an unstocked brand term, a synonym. Derive terms from the site's category names and the marketer's known gaps.
- If the search is a GET URL (`/search?q=…`, `/paieska?q=…`; find the pattern with one term), `browser_navigate` each term's URL and read the snapshot. Otherwise it needs typing (`mcp__playwright__*` or a marketer screenshot) and the row says `search not reachable headless`.
- Write the table Užklausa | Rezultatų | Pasako, kad tikslaus atitikmens nėra | Siūlo alternatyvą | Aklavietė. Fail `dead end`: zero results with no link other than header and footer, or no sentence saying nothing matched. Each fail gets a one-line fix (rodyti kategoriją Grąžtai ir pasiūlymą „ar turėjote omenyje: grąžtas“).

Source: eMarketing, pp. 165–166.

## Test the checkout on the devices customers use

A new checkout gets paid traffic only after a test order has reached the thank-you page on each device customers actually use. Headless Chromium is a desktop; the phones are the marketer's.

- Devices come from the brief or the Analytics device split. Rocky walks the desktop path with `browser_navigate` up to the payment button; for each phone the marketer places the same test order and sends a screenshot per screen (cart, address, payment, thank-you), asked with `clarify`, read with `vision_analyze`.
- One `## Device: …` block per device; its last row holds the order number or thank-you text, or the error. Never submit a payment without a test card, sandbox or €0 code; without one write `payment and confirmation not tested: no safe payment path`.
- Fail: a device whose block ends without a confirmation or a recorded error. The summary names each device: „iPhone: užsakymas #10452 patvirtintas. Pixel 7: mygtukas „Apmokėti“ nepasiekiamas — jį dengia slapukų juosta. Nesiųsti mokamo srauto, kol nepataisyta.“

Source: eMarketing, pp. 207–208.

## Estimate the peak and ask the host, never load-test

An ad budget on a shop that falls over at the peak hour is wasted twice. Rocky estimates the spike and hands the host the questions; it never fires concurrent requests at a live shop.

- Show the arithmetic in the deliverable: clicks = budget / CPC; daily = clicks / campaign days; peak hour = 25 % of daily (an assumption, say so); requests per second = peak visitors × pages per visit (default 4) / 3600. No CPC → `expected traffic unknown — give CPC or expected visits`.
- Measure each key page with `bash`: `curl -s -o /dev/null -w '%{time_starttransfer} %{time_total} %{http_code}' <url>`, three runs, median in the walkthrough row.
- Write `## Hosting questions` for the marketer to send: peak requests per second the plan serves; CDN and page cache; autoscaling or a fixed plan and what happens above it; checkout and payment-provider limits; who is on call on the campaign dates. Fail: no host answer before day one; say so.

Source: eMarketing, p. 236.

## Measure both pages before planning a speed test

A speed test is only a speed test when the fast version is measurably faster and sells the same thing at the same price. Measure first, size second.

- The browser tools report no timing. Measure each URL with `bash`: `curl -s -o /dev/null -A '<mobile UA>' -w '%{time_starttransfer} %{time_total} %{size_download}'`, five runs each; record medians and spread in the walkthrough. Label it server and transfer time, not render time. Spread above 50 % of the median → `timing_reliable: no`.
- Parity: `web_fetch` both URLs, diff the body copy and the price blocks, list differing blocks. A different offer or price → the test is confounded; say so and stop.
- Only then `experiment_plan` with the mobile baseline, weekly mobile sessions and the smallest lift that matters. The plan states: unit mobile visitor, 50/50 split, primary metric completed purchase, guard metric bounce, weeks from the tool. Variant not measurably faster → refuse to quote a conversion effect.

Source: Decoding Decisions, p. 92.

## Run a phone task to its answer

"Find the nearest store that has the drill in stock" either ends with an answer in a few taps or it does not. Zoom, a tiny target or a sideways scroll on the way is the finding.

- Write the task in one sentence first („rasti artimiausią parduotuvę, kurioje yra Bosch GSR 12V“). Rocky maps the route on desktop with `browser_navigate`; the phone pass is the marketer's screen recording (`clarify`), read screen by screen with `vision_analyze`.
- Rows: tap number | screen | what was tapped | what appeared | seconds. In `what appeared`, flag `zoom needed`, `horizontal scroll`, `target too small to hit` with the element quoted.
- Final line: `Atsakymas pasiektas: taip/ne, N paspaudimų, S s` or `sustojo žingsnyje K, nes …`. Fail: not reached, or a flagged tap on the path to the answer. Without a recording write `mobile pass: not done`; never rate the phone view from desktop text.

Source: Winning the Zero Moment of Truth, p. 49.

## Prove a self-check tool with worked cases

A diagnostic page earns leads when the buyer gets a usable result from their own numbers before any form. A wrong divisor in the result costs the trust the tool was built for.

- The page under `deliverables/` takes 3–6 inputs the buyer knows, shows the result and the next step before any form, and asks for an email only to send the full report. Each formula and its source goes in `runs/notes/facts.md`, marked `prielaida` when unsourced.
- Before handing over, take two or three worked cases from the marketer (inputs → expected result), run the page's calculation function with `node -e` (a Python re-implementation when node is absent, said so) on each case; write `## Self-test`: case | inputs | expected | computed | pass.
- Fail: a case off by more than the stated tolerance, a form before the result, or a result with no next-step sentence. No cases given → `no cases supplied — formula unverified`; never invent them.

Source: World Wide Rave, pp. 149–152.

## Read the retention curve before saying where to cut

The place viewers leave a video is a timestamp, and the passage spoken there is what to cut. Watching once and guessing is not a diagnosis.

- Ask for the YouTube Studio export (Advanced mode → Audience retention → Export CSV: position % and absolute retention %), the video length in seconds and the transcript (SRT or VTT). Without the export write `no retention data: cannot locate the drop`; only the generic 30-second hook check may be suggested.
- `table_analyze describe` the file, then compute in `bash` (Python): retention per 10-second window, the three largest drops (start, end, from %, to %, points lost), hook loss in the first 30 s. Fewer than 10 rows is unusable.
- Quote the sentence spoken at each drop; the recommendation names range and sentence: „2:42–2:58, 58 % → 31 %, per sakinį „Dabar šiek tiek apie mūsų klinikos istoriją…“ — iškirpti arba perkelti po kainos.“ No window above 5 points → `no concentrated exit point; decline is gradual`.

Source: eMarketing, p. 355.

## Judge the ad in a cluttered feed, blind

An ad read alone is read; the same ad between three competitors and four ordinary posts may not be noticed, or be remembered for the wrong offer. Only a blind viewer can tell.

- Build `deliverables/ads_clutter.html`: each test ad between two or three competitor ads (Meta Ad Library via `web_fetch` or `browser_navigate`, text and image when available, stored in `runs/notes/facts.md`) and three or four neutral posts, in random order. No real competitor ads → placeholders and the result is `UNCALIBRATED`.
- The viewers are the marketer and two or three colleagues who have not seen the ads: open the page, scroll for 30 seconds, then answer „Kuriuos įrašus pastebėjote ir ką kiekvienas siūlė?“ A screenshot of the scrolled feed gets `vision_analyze` with the same question as one more viewer.
- Write `## Clutter test`: viewer | noticed | recalled offer | matches pitch. Fail `NOT_NOTICED`: half the viewers missed the ad; fail `MISREAD`: the recalled offer differs from the pitch. Fix before spending.

Source: The Psychology of Advertising, pp. 181–183.

## Ask what the offer was after a glance at the card

A beautiful picture can carry the viewer's memory and leave the headline, which holds the reason to buy, unread. Show the card, then ask what was being sold.

- For every ad with a visual, call `vision_analyze` on the image with the headline in the prompt and nothing else: „Vienu sakiniu: kas siūloma ir kodėl pirkti?“ The prompt must not contain the angle or the primary text.
- Write `## Visual recall`: ad | recall sentence | content words shared with headline and angle. Fail `visual obscures`: fewer than two shared content words, or a recall that names the picture's subject (a woman on a beach) and not the offer (an implant at a fixed price). Regenerate with the offer's object as the subject and run the question once more.
- A vision call that failed or was blocked by budget is `recall unchecked`, never a pass.

Source: Scientific Advertising, pp. 46–47.
