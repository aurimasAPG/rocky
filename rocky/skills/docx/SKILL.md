---
name: docx
description: Build or inspect Word documents for a Rocky run. Use for reports, memos, and formatted .docx deliverables.
---

# Rocky · Word

You are a Rocky **worker**. Stay in the workspace. Never `write_file` a `.docx`. Use **python-docx** via `bash`, then `office_read` to QA.

## Create

```python
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

doc = Document()
sec = doc.sections[0]
sec.page_width = Cm(21.0)
sec.page_height = Cm(29.7)
sec.left_margin = Cm(2.5)
sec.right_margin = Cm(2.5)

doc.add_heading("Title", level=1)
p = doc.add_paragraph("One idea per paragraph.")
p.runs[0].font.name = "Calibri"
p.runs[0].font.size = Pt(11)
p.runs[0].font.color.rgb = RGBColor(0x1C, 0x2B, 0x33)

table = doc.add_table(rows=2, cols=2)
table.style = "Table Grid"
table.cell(0, 0).text = "A"
table.cell(0, 1).text = "B"
doc.save("deliverables/report.docx")
```

## Rocky rules

- Headings via `add_heading`. Do not fake structure with bold body text.
- New paragraph for a new idea — no `\n` inside one `add_paragraph`.
- After save: `office_read` and confirm headings and tables survived.
- If the brief is Lithuanian, quotes are „like this“.
- Stop when this subtask's file exists. The next wave owns slides or sheets.
