---
name: pdf
description: Read or create PDF files for a Rocky run using pypdf, markitdown, and reportlab.
---

# Rocky · PDF

You are a Rocky **worker**. Stay in the workspace. Do not `write_file` raw PDF bytes.

## Read

Prefer `office_read path.pdf`. For page counts:

```python
from pypdf import PdfReader
r = PdfReader("report.pdf")
print("pages", len(r.pages))
print(r.pages[0].extract_text())
```

## Create

```python
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

styles = getSampleStyleSheet()
story = [
    Paragraph("Title", styles["Title"]),
    Spacer(1, 12),
    Paragraph("Body wraps on its own.", styles["Normal"]),
]
SimpleDocTemplate("deliverables/note.pdf", pagesize=A4).build(story)
```

## Rocky rules

- Reportlab table cells must be `Paragraph`, not bare strings.
- After create, `office_read` and check the brief is actually on the page.
- Scanned PDFs need OCR (not in Rocky by default). Say so. Do not invent text.
- One file, then stop. Do not also emit pptx unless this subtask named both.
