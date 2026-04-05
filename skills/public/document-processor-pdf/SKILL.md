---
name: pdf
description: Extract text and tables from PDF files. Use when a user uploads a PDF document that needs to be processed.
---

# PDF Processing for Text Extraction

## Purpose

Extract readable text and structured tables from PDF files. This skill is designed to feed extracted content into downstream processing (e.g., document summarization for PPT generation).

## When to Use

- User uploads a `.pdf` file
- Need to extract document content for summarization, analysis, or conversion to other formats
- PDF contains text that can be extracted without OCR
- PDF contains tables that need to be captured as structured data

## Approach

### Step 1: Try pdfplumber First (Best for Text + Tables)

```python
import pdfplumber

text_parts = []
tables = []

with pdfplumber.open("document.pdf") as pdf:
    for page in pdf.pages:
        # Extract text (preserves layout better than pypdf)
        t = page.extract_text()
        if t:
            text_parts.append(t.strip())

        # Extract tables as 2D arrays
        page_tables = page.extract_tables()
        for pt in page_tables:
            if pt and len(pt) >= 2:
                tables.append(pt)
```

**Why pdfplumber first:**
- Better layout preservation for complex documents
- Native table extraction with cell coordinates
- Falls back gracefully per-page

### Step 2: Fallback to pypdf (Text Only)

```python
from pypdf import PdfReader

reader = PdfReader("document.pdf")
text_parts = []
for page in reader.pages:
    t = page.extract_text()
    if t:
        text_parts.append(t.strip())
```

**Use pypdf when:**
- pdfplumber is not installed
- pdfplumber raises an exception
- Table extraction is not needed

### Step 3: OCR for Scanned PDFs (Last Resort)

```python
# Requires: pip install pytesseract pdf2image
import pytesseract
from pdf2image import convert_from_path

images = convert_from_path('scanned.pdf')
text = ""
for i, image in enumerate(images):
    text += f"Page {i+1}:\n"
    text += pytesseract.image_to_string(image)
    text += "\n\n"
```

**When to use OCR:**
- PDF is scanned (image-based, no selectable text)
- `pdftotext` or pdfplumber returns empty text
- Text quality is garbled or contains only symbols

### Step 4: Command Line Tools

```bash
# Fast plain text extraction
pdftotext input.pdf output.txt

# Preserve layout
pdftotext -layout input.pdf output.txt

# Specific page range
pdftotext -f 1 -l 5 input.pdf output.txt
```

## Handling Common Issues

| Problem | Solution |
|---------|----------|
| Empty text (scanned PDF) | Use OCR (Step 3) |
| Garbled text | Check if PDF is encrypted or uses non-standard encoding |
| Tables not extracting | Try `page.extract_tables(settings={"vertical_strategy": "lines"})` |
| Memory issues on large PDFs | Process page-by-page in a loop |
| Images instead of text | OCR or skip if images are decorative |

## Integration Notes

For pptagent pipeline:
1. Extract text → pass to document-summarizer skill
2. Extract tables → pass alongside text to summarizer
3. Combine pages with double newline: `"\n\n".join(text_parts)`
4. Attach page count: `len(pdf.pages)`

## Output Format

Return `(raw_text: str, tables: list[list[list[str]]], page_count: int)`:
- `raw_text`: all pages joined with double newlines
- `tables`: list of tables, each table is `list[list[str]]` (rows → cells)
- `page_count`: total number of pages
