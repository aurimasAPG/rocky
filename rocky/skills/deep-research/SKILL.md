---
name: deep-research
description: Rocky research wave. Cite the web before copy or slides. Use web_search and web_fetch; optional research-prompt or perplexity-deep-research if those skills are listed.
---

# Rocky · Research

This is wave 1. You only write `research.md` (and optionally `research-brief.md`). Do not start VOC or ads.

Rocky has a euro cap. Search with intent. Three to six sub-questions is enough.

## 1. Brief

If `research-prompt` is in `skills_list`, `skill_view` it and write **one paragraph** (goal, numbered sub-questions, source rules) to `research-brief.md`. Otherwise write that paragraph yourself.

## 2. Hunt

For each sub-question:

1. `web_search` with a tight query.
2. `web_fetch` two to four **primary** URLs (docs, filings, official posts, papers). Forums and social are weak signal.
3. Conflicts stay as fact vs inference. Do not flatten them.

If `perplexity-deep-research` is listed **and** the user asked for a deep cited report, follow it once with a long timeout. Do not spend the whole run budget on that call.

## 3. File

`research.md` — each finding: URL, specific claim, one line why it matters. End with unknowns.

**No invented statistics.** Then stop and leave the folder for the VOC worker.
