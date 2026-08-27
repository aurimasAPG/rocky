---
name: xlsx
description: Build or inspect Excel workbooks for a Rocky run. Use for tables, variant banks, budgets, and source logs as .xlsx.
---

# Rocky · Excel

You are a Rocky **worker**. Stay in the workspace. `.xlsx` is ZIP+XML — `write_file` and `patch` are UTF-8 text and will corrupt the file.

## Order of work

1. `office_read` if the workbook already exists.
2. Create or edit with **openpyxl** through `bash`.
3. `office_read` again. If headers or numbers look wrong, fix once — do not burn the euro cap on polish.

## Create

```python
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.utils import get_column_letter

wb = Workbook()
ws = wb.active
ws.title = "Data"
ws.append(["Name", "Value"])
ws.append(["Alpha", 10])
ws["A1"].font = Font(bold=True, color="F07167")
ws.freeze_panes = "A2"
ws.column_dimensions["A"].width = 18
wb.save("deliverables/variants.xlsx")
```

## Edit

```python
from openpyxl import load_workbook
wb = load_workbook("deliverables/variants.xlsx")
wb.active["B2"] = 12
wb.save("deliverables/variants.xlsx")
```

## Rocky rules

- Save under the workspace, usually `deliverables/`.
- First row = headers. Numbers as numbers, not strings.
- One sheet unless the subtask asked for more.
- This step is done when `office_read` shows the table the plan named. Stop. Do not also write the deck.
