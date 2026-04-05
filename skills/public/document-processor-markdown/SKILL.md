---
name: markdown
description: Extract text and tables from Markdown (.md) files. Use when a user uploads a Markdown document that needs to be processed.
---

# Markdown Processing for Text Extraction

## Purpose

Extract readable text and structured tables from Markdown files. This skill feeds extracted content into downstream processing such as document summarization for PPT generation.

## When to Use

- User uploads a `.md` file
- Need to extract document content for summarization, analysis, or conversion to other formats
- Markdown contains code blocks, tables, or structured content that should be preserved

## Approach

### Step 1: Read the File Directly

Markdown is plain text, so read it directly.

```python
with open("document.md", "r", encoding="utf-8") as f:
    raw_text = f.read().strip()
```

If UTF-8 fails, try `utf-8-sig` or `gbk`.

### Step 2: Normalize and Strip Frontmatter

```python
def strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :].lstrip()
    return text

text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
text = strip_frontmatter(text)
```

### Step 3: Extract Tables When Present

```python
import re

def extract_markdown_tables(text: str) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    current_table: list[list[str]] = []

    for line in text.split("\n"):
        line = line.strip()
        if not line.startswith("|"):
            if current_table:
                tables.append(current_table)
                current_table = []
            continue

        if re.match(r"^\|[\s\-:|]+\|$", line):
            continue

        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells:
            current_table.append(cells)

    if current_table:
        tables.append(current_table)
    return tables
```

## Handling Common Issues

| Problem | Solution |
|---------|----------|
| UTF-8 decode fails | Retry with `utf-8-sig` or `gbk` |
| YAML frontmatter pollutes content | Strip the opening `--- ... ---` block |
| Large file | Truncate or summarize in chunks before downstream use |
| No tables present | Return an empty list |

## Integration Notes

For the upload-to-PPT workflow:
1. Read raw Markdown text
2. Extract tables if present
3. Pass the result to `document-summarizer`
4. Then call `generate_ppt(content=...)`

## Output Format

Return `(raw_text: str, tables: list[list[list[str]]], estimated_pages: int)`:
- `raw_text`: normalized markdown text
- `tables`: extracted markdown tables as `list[list[str]]`
- `estimated_pages`: rough estimate, e.g. `max(1, len(text) // 3000)`
