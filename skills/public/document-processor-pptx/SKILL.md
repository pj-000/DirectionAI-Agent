---
name: pptx
description: Extract text and tables from PowerPoint (.pptx) files. Use when a user uploads a PPT or PPTX document that needs to be processed.
---

# PowerPoint Processing for Text Extraction

## Purpose

Extract readable text and structured tables from PPTX files so the content can be summarized and turned into a new PPT.

## When to Use

- User uploads a `.pptx` file
- User uploads a legacy `.ppt` file that has already been converted or can be converted to `.pptx`
- User references an existing PPT/PPTX generated earlier in the same conversation
- Need to extract presentation content for summarization, analysis, regeneration, speaker notes, or scripts

## Approach

### Step 1: Try `python-pptx` First

```python
from pptx import Presentation

prs = Presentation("presentation.pptx")
text_parts = []
tables = []

for slide in prs.slides:
    for shape in slide.shapes:
        if shape.has_text_frame:
            for paragraph in shape.text_frame.paragraphs:
                line = paragraph.text.strip()
                if line:
                    text_parts.append(line)

        if shape.has_table:
            table_data = []
            for row in shape.table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                table_data.append(cells)
            if table_data:
                tables.append(table_data)
```

### Step 2: Fallback to `markitdown`

```bash
python -m markitdown presentation.pptx
```

Use this when `python-pptx` is unavailable or extraction quality is better with markdown output.

### Step 3: Handle Legacy `.ppt`

If the original file is `.ppt`, convert it first:

```bash
soffice --headless --convert-to pptx file.ppt
```

Then process the generated `.pptx`.

## Handling Common Issues

| Problem | Solution |
|---------|----------|
| Legacy `.ppt` file | Convert to `.pptx` first |
| `python-pptx` missing | Use `markitdown` fallback |
| Empty extraction | Try markdown conversion or inspect whether slides are image-heavy |
| Tables not found | Check `shape.has_table`; some "tables" are actually pasted images |

## Integration Notes

For the upload-to-PPT workflow:
1. Extract slide text and tables from the uploaded PPT/PPTX
2. Preserve slide count as a page estimate
3. Pass the extracted content to `document-summarizer`
4. Then call `generate_ppt(content=...)`

General follow-up rule for an existing deck:
- Treat the uploaded PPT as source material first.
- If the user wants any derived text output, analysis, notes, translation, or explanation, stop after extraction and answer that request directly.
- Only call `generate_ppt` when the requested final artifact is a PPT and the user explicitly wants a new deck or slide revisions/regeneration.

## Output Format

Return `(raw_text: str, tables: list[list[list[str]]], slide_count: int)`:
- `raw_text`: text extracted from all slides
- `tables`: tables extracted from slide shapes
- `slide_count`: total slide count
