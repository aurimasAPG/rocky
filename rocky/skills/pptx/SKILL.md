---
name: pptx
description: Build or inspect PowerPoint decks for a Rocky run. Use for campaign decks and 16:9 presentations as .pptx.
---

# Rocky · PowerPoint

You are a Rocky **worker**. Stay in the workspace. Never `write_file` a `.pptx`. Use **python-pptx** via `bash`. QA with `office_read`.

Rocky decks use the product palette, not a leftover Office theme:

| Token | Hex |
| --- | --- |
| Slate ground | `#1C2B33` |
| Paper | `#E8F0EA` |
| Coral | `#F07167` |
| Sea-glass | `#3D9B8F` |
| Apricot | `#E8A87C` |

## Create (16:9, blank layout)

```python
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
blank = prs.slide_layouts[6]
slide = prs.slides.add_slide(blank)

fill = slide.shapes.add_shape(
    MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), Inches(13.333), Inches(7.5)
)
fill.fill.solid()
fill.fill.fore_color.rgb = RGBColor(0x1C, 0x2B, 0x33)
fill.line.fill.background()

title = slide.shapes.add_textbox(Inches(0.6), Inches(0.45), Inches(12), Inches(1))
p = title.text_frame.paragraphs[0]
p.text = "Slide title"
p.font.size = Pt(32)
p.font.bold = True
p.font.color.rgb = RGBColor(0xF0, 0x71, 0x67)

body = slide.shapes.add_textbox(Inches(0.6), Inches(1.7), Inches(12), Inches(5))
bt = body.text_frame
bt.word_wrap = True
bt.paragraphs[0].text = "First point"
bt.add_paragraph().text = "Second point"
for para in bt.paragraphs:
    para.font.size = Pt(18)
    para.font.color.rgb = RGBColor(0xE8, 0xF0, 0xEA)

prs.save("deliverables/campaign.pptx")
```

## Rocky rules

- Blank layout + explicit boxes. Do not fight template placeholders.
- Margins ≥ 0.5". One idea per slide. Body ≤ ~6 lines.
- Copy on the slides comes from `copy.md` / VOC — do not invent proof.
- After save, `office_read`. If text is missing, fix the python and save again once.
- This wave only builds the deck. Spreadsheet variants are a different subtask.
