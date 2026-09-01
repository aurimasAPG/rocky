---
name: marketing-pipeline
description: Rocky marketing sequence. Research → VOC → copy → pptx + xlsx. Use for ads, campaigns, landing copy, and SEO/CRO briefs.
---

# Rocky · Marketing pipeline

Rocky **forces** this order. Later workers only read files the earlier waves left on disk. Skip a wave and the verifier should fail.

| Wave | `skill_view` | Writes |
| --- | --- | --- |
| 1 Research | `deep-research` | `runs/notes/research.md` |
| 2 VOC | `voc-to-copy` (and `product-marketing-context` if listed) | `runs/notes/voc.md` |
| 3 Copy | `copywriting` plus `meta-ads-copy` or `google-ads-copy` if the goal names a channel | `runs/notes/copy.md` |
| 4 Package | `pptx` then `xlsx` | `deliverables/campaign.pptx`, `deliverables/variants.xlsx` |

Independent research queries may run in parallel. VOC waits on research. Copy waits on VOC. The deck waits on copy.

## Rocky rules

- Copy is built from VOC phrases. Do not invent reviews, quotes, or proof numbers.
- No customer data? Mine public reviews / Reddit / G2 with `web_search` and label weak sources.
- Never `write_file` `.pptx` / `.xlsx`. `bash` + python-pptx / openpyxl, then `office_read`.
- Stay in the workspace. Working files under `runs/notes/`, finals under `deliverables/`, nothing in the root — unless the user named files.
- One subtask, one wave. When your file is on disk, stop calling tools.
