---
name: docx
description: Extract text and tables from Word (.docx) files. Use when a user uploads a Word document that needs to be processed.
---

# Word Document Processing for Text Extraction

## Purpose

Extract readable text and structured tables from DOCX files. This skill feeds extracted content into downstream processing (e.g., document summarization for PPT generation).

## When to Use

- User uploads a `.docx` or `.doc` file
- Need to extract document content for summarization, analysis, or conversion
- Document contains structured tables that need to be captured
- Legacy `.doc` files must be converted to `.docx` first

## Approach

### Step 1: Try python-docx First

```python
from docx import Document

doc = Document("document.docx")

# Extract paragraphs (skip empty)
paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
raw_text = "\n\n".join(paragraphs)

# Extract tables
tables = []
for table in doc.tables:
    rows_data = []
    for row in table.rows:
        cells = [cell.text.strip() for cell in row.cells]
        rows_data.append(cells)
    if rows_data:
        tables.append(rows_data)
```

**Why python-docx:**
- Native DOCX support, no external dependencies
- Preserves paragraph boundaries and table structure
- Handles both text and tables in one pass

### Step 2: Fallback to pandoc (Command Line)

```bash
# Convert to plain text
pandoc document.docx -o output.txt --to plain

# Preserve more formatting
pandoc document.docx -o output.md --to markdown

# Track changes (if present)
pandoc document.docx -o output.txt --track-changes=all
```

```python
import subprocess

result = subprocess.run(
    ["pandoc", "document.docx", "-o", "-", "--to", "plain"],
    capture_output=True, text=True, timeout=60
)
if result.returncode == 0:
    raw_text = result.stdout
```

**Use pandoc when:**
- python-docx is not installed
- Need markdown output for richer formatting info
- Document has complex formatting that python-docx misses

### Step 3: Legacy .doc Files

Convert to `.docx` first using LibreOffice:

```bash
soffice --headless --convert-to docx document.doc
```

Then process as `.docx` (Step 1 or 2).

## Handling Common Issues

| Problem | Solution |
|---------|----------|
| `.doc` not `.docx` | Convert with LibreOffice first |
| Empty paragraphs | Filter `if p.text.strip()` |
| Table cells with nested content | Use `.text` on each cell (simplest) |
| Track changes in document | Use pandoc with `--track-changes=all` |
| python-docx not installed | Use pandoc fallback |
| pandoc not installed | Use LibreOffice to convert to text |

## Document Structure Preservation

- **Headings**: Check `paragraph.style.name` for "Heading 1", "Heading 2", etc.
- **Lists**: Detect bullet/number patterns in text
- **Bold/Italic**: Inspect `run.bold` and `run.italic` for emphasis
- **Tables**: Preserve as 2D arrays, include header row separately

```python
# Preserve heading structure
for para in doc.paragraphs:
    style = para.style.name
    if "Heading" in style:
        level = style  # "Heading 1", "Heading 2", etc.
        text = para.text.strip()
        # ... heading with level
```

## Integration Notes

For pptagent pipeline:
1. Extract text paragraphs → pass to document-summarizer skill
2. Extract tables → pass alongside text
3. For DOCX with clear headings: preserve heading hierarchy, summarizer will map to sections
4. Estimate page count: `max(1, len(paragraphs) // 30)` (rough estimate)

## Output Format

Return `(raw_text: str, tables: list[list[list[str]]], estimated_pages: int)`:
- `raw_text`: paragraphs joined with double newlines
- `tables`: list of tables, each table is `list[list[str]]` (rows → cells)
- `estimated_pages`: rough page estimate based on paragraph count
