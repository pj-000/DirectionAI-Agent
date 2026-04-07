"""File conversion utilities.

Converts document files to Markdown using structured extraction first and
markitdown as a fallback.
No FastAPI or HTTP dependencies — pure utility functions.
"""

import logging
from pathlib import Path

from .document_extraction import extract_document_content, format_extracted_markdown

logger = logging.getLogger(__name__)

# File extensions that should be converted to markdown
CONVERTIBLE_EXTENSIONS = {
    ".pdf",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".doc",
    ".docx",
}


async def convert_file_to_markdown(file_path: Path) -> Path | None:
    """Convert a file to markdown using shared extraction logic.

    Args:
        file_path: Path to the file to convert.

    Returns:
        Path to the markdown file if conversion was successful, None otherwise.
    """
    try:
        raw_text, tables, page_count = extract_document_content(str(file_path))
        has_table_content = any(
            any(any(str(cell or "").strip() for cell in row) for row in table)
            for table in tables
        )
        if not raw_text.strip() and not has_table_content:
            logger.warning("No extractable content found in %s", file_path.name)
            return None

        md_path = file_path.with_suffix(".md")
        markdown = format_extracted_markdown(
            raw_text,
            tables,
            source_name=file_path.name,
            page_count=page_count,
        )

        if not markdown.strip():
            logger.warning("No content extracted from %s", file_path.name)
            return None

        md_path.write_text(markdown, encoding="utf-8")

        logger.info(f"Converted {file_path.name} to markdown: {md_path.name}")
        return md_path
    except Exception as e:
        logger.error(f"Failed to convert {file_path.name} to markdown: {e}")
        return None
