"""Middleware to inject uploaded files information into agent context."""

import logging
import re
from pathlib import Path
from typing import NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from deerflow.config.paths import Paths, get_paths
from deerflow.utils.file_conversion import CONVERTIBLE_EXTENSIONS

logger = logging.getLogger(__name__)

PDF_SKILL_PATH = "/mnt/skills/public/document-processor-pdf/SKILL.md"
DOCX_SKILL_PATH = "/mnt/skills/public/document-processor-docx/SKILL.md"
MARKDOWN_SKILL_PATH = "/mnt/skills/public/document-processor-markdown/SKILL.md"
PPTX_SKILL_PATH = "/mnt/skills/public/document-processor-pptx/SKILL.md"
SUMMARIZER_SKILL_PATH = "/mnt/skills/public/document-summarizer/SKILL.md"
PPT_SKILL_PATH = "/mnt/skills/public/ppt-generation/SKILL.md"


class UploadsMiddlewareState(AgentState):
    """State schema for uploads middleware."""

    uploaded_files: NotRequired[list[dict] | None]
    referenced_artifacts: NotRequired[list[dict] | None]
    conversation_artifacts: NotRequired[list[dict] | None]


class UploadsMiddleware(AgentMiddleware[UploadsMiddlewareState]):
    """Middleware to inject uploaded files information into the agent context.

    Reads file metadata from the current message's additional_kwargs.files
    (set by the frontend after upload) and prepends an <uploaded_files> block
    to the last human message so the model knows which files are available.
    """

    state_schema = UploadsMiddlewareState

    def __init__(self, base_dir: str | None = None):
        """Initialize the middleware.

        Args:
            base_dir: Base directory for thread data. Defaults to Paths resolution.
        """
        super().__init__()
        self._paths = Paths(base_dir) if base_dir else get_paths()

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        size_kb = size_bytes / 1024
        return f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"

    @staticmethod
    def _is_converted_markdown_sidecar(file_path: Path, available_names: set[str]) -> bool:
        if file_path.suffix.lower() != ".md":
            return False
        for extension in CONVERTIBLE_EXTENSIONS:
            if f"{file_path.stem}{extension}" in available_names:
                return True
        return False

    @staticmethod
    def _find_markdown_virtual_path(file_path: Path, uploads_dir: Path) -> str | None:
        markdown_path = file_path.with_suffix(".md")
        if markdown_path == file_path or not markdown_path.is_file():
            return None
        try:
            markdown_name = markdown_path.relative_to(uploads_dir).name
        except ValueError:
            markdown_name = markdown_path.name
        return f"/mnt/user-data/uploads/{markdown_name}"

    def _create_files_message(self, new_files: list[dict], historical_files: list[dict]) -> str:
        """Create a formatted message listing uploaded files.

        Args:
            new_files: Files uploaded in the current message.
            historical_files: Files uploaded in previous messages.

        Returns:
            Formatted string inside <uploaded_files> tags.
        """
        lines = ["<uploaded_files>"]

        lines.append("The following files were uploaded in this message:")
        lines.append("")
        if new_files:
            for file in new_files:
                size_str = self._format_size(file["size"])
                lines.append(f"- {file['filename']} ({size_str})")
                lines.append(f"  Path: {file['path']}")
                if file.get("markdown_path"):
                    lines.append(f"  Markdown Path: {file['markdown_path']}")
                    lines.append("  Preferred for reading and summarizing document content before PPT generation.")
                lines.append("")
        else:
            lines.append("(empty)")

        if historical_files:
            lines.append("The following files were uploaded in previous messages and are still available:")
            lines.append("")
            for file in historical_files:
                size_str = self._format_size(file["size"])
                lines.append(f"- {file['filename']} ({size_str})")
                lines.append(f"  Path: {file['path']}")
                if file.get("markdown_path"):
                    lines.append(f"  Markdown Path: {file['markdown_path']}")
                    lines.append("  Preferred for reading and summarizing document content before PPT generation.")
                lines.append("")
            lines.append("Historical uploads are background context only.")
            lines.append("Do not assume the current request is about regenerating or transforming these files unless the user explicitly refers to them.")
            lines.append("")

        lines.append("Use the `read_file` tool with the paths shown above.")
        lines.append("If a Markdown Path is available, read that file first because it is the converted text version of the uploaded document.")
        lines.append("Treat uploaded files as source material by default.")
        lines.append("Do not regenerate, rewrite, or convert an uploaded file into a new artifact unless the user explicitly asks for a new output file or an update to that artifact.")
        lines.append("If the user wants a summary, translation, explanation, notes, outline, Q&A, or other text derived from an uploaded file, read the file content and answer directly instead of calling a file-generation tool.")
        lines.append("When the user wants a PPT from uploaded documents, extract or summarize the document first, then pass the distilled structure and facts into `generate_ppt(content=...)`.")
        lines.append("</uploaded_files>")

        return "\n".join(lines)

    def _create_referenced_artifacts_message(self, references: list[dict]) -> str:
        lines = ["<referenced_artifacts>"]
        lines.append("The following existing artifacts from this conversation were explicitly referenced in this message:")
        lines.append("")
        for reference in references:
            lines.append(f"- {reference['filename']}")
            lines.append(f"  Path: {reference['path']}")
            lines.append("")
        lines.append("Treat these referenced artifacts as existing conversation context / source material, not as newly uploaded files.")
        lines.append("If the user asks for a summary, notes, translation, explanation, script, Q&A, or other derived text, work from these artifacts directly.")
        lines.append("If a referenced artifact is a binary office document such as PPTX, DOCX, or PDF, extract its content first using the relevant processor skill or tool before answering.")
        lines.append("Only create or regenerate a new artifact when the user explicitly asks for a new output file or a revision to an existing artifact.")
        lines.append("</referenced_artifacts>")
        return "\n".join(lines)

    def _create_conversation_artifacts_message(self, artifacts: list[dict]) -> str:
        lines = ["<conversation_artifacts>"]
        lines.append("The following artifacts were generated earlier in this conversation and are available as background context:")
        lines.append("")
        for artifact in artifacts:
            lines.append(f"- {artifact['filename']}")
            lines.append(f"  Path: {artifact['path']}")
        lines.append("")
        lines.append("Treat these prior conversation artifacts as contextual knowledge by default.")
        lines.append("If the user asks a follow-up question that is based on prior generated outputs, read the relevant artifact and use it as source material.")
        lines.append("Do not treat the mere mention of an existing PPT, markdown file, or exported artifact as an instruction to regenerate it.")
        lines.append("Do NOT regenerate these artifacts unless the user explicitly asks to create a new file, export to another format, or revise the existing artifact.")
        lines.append("</conversation_artifacts>")
        return "\n".join(lines)

    @staticmethod
    def _is_explicit_ppt_request(content: str) -> bool:
        lowered = content.lower()

        # If the user explicitly asks for some other output form, prefer that
        # target over the mere presence of "ppt" as source material.
        non_ppt_output_patterns = (
            r"\bmd\b",
            r"markdown",
            r"\bpdf\b",
            r"\bdocx?\b",
            r"\btxt\b",
            r"\bhtml?\b",
            r"\bjson\b",
            r"\bxlsx?\b",
            r"\bcsv\b",
            r"演讲稿",
            r"讲稿",
            r"speaker\s*notes?",
            r"notes?",
            r"总结",
            r"摘要",
            r"翻译",
            r"讲解",
            r"问答",
            r"script",
        )
        if any(re.search(pattern, lowered, re.IGNORECASE) for pattern in non_ppt_output_patterns):
            return False

        # Strong signals that the desired output artifact is itself a PPT.
        strong_patterns = (
            r"(生成|制作|创建|做(?:一[个份])?|做成|转成)\s*(?:一[个份]\s*)?(ppt|演示文稿|幻灯片|课件)\b",
            r"\b(ppt|演示文稿|幻灯片|课件)\s*(生成|制作|创建)\b",
            r"(修改|更新|重做|重生成|优化|调整|补充|完善|改写)\s*[^。；，,\n]{0,20}(ppt|演示文稿|幻灯片|课件)\b",
            r"\b(ppt|演示文稿|幻灯片|课件)\b[^。；，,\n]{0,20}(修改|更新|重做|重生成|优化|调整|补充|完善|改写)\b",
            r"(导出|输出|保存)\s*(?:为|成)?\s*(ppt|演示文稿|幻灯片|课件)\b",
            r"\b(create|generate|make|build)\s+(?:a\s+new\s+)?(ppt|slides|presentation)\b",
            r"\b(revise|update|modify|regenerate)\b.{0,20}\b(ppt|slides|presentation)\b",
            r"\b(ppt|slides|presentation)\b.{0,20}\b(revise|update|modify|regenerate)\b",
            r"\b(ppt|slides|presentation)\s+(?:creation|generation|update|revision)\b",
        )
        return any(re.search(pattern, lowered, re.IGNORECASE) for pattern in strong_patterns)

    def _append_skill_workflow(
        self,
        files_message: str,
        all_files: list[dict],
        *,
        explicit_ppt_request: bool,
    ) -> str:
        lowered_extensions = {str(file.get("extension") or "").lower() for file in all_files}
        needs_ppt_workflow = bool({".ppt", ".pptx"} & lowered_extensions)

        if needs_ppt_workflow and not explicit_ppt_request:
            followup_lines = [
                "",
                "Important intent rule for existing PowerPoint files:",
                "- The presence of an uploaded `.ppt`/`.pptx` file does NOT by itself mean you should call `generate_ppt`.",
                "- If the user asks to summarize, explain, translate, extract, compare, write speaker notes, or write a speech/script based on the existing deck, treat the PowerPoint as source material to read and analyze.",
                "- Only call `generate_ppt` when the user explicitly asks to create a new deck, regenerate slides, revise slide content/layout, or otherwise produce/update PPT output.",
            ]
            files_message = files_message.replace("</uploaded_files>", "\n".join(followup_lines) + "\n</uploaded_files>")

        if not explicit_ppt_request:
            return files_message

        needs_pdf_workflow = ".pdf" in lowered_extensions
        needs_docx_workflow = bool({".doc", ".docx"} & lowered_extensions)
        needs_markdown_workflow = ".md" in lowered_extensions
        needs_document_workflow = (
            needs_pdf_workflow
            or needs_docx_workflow
            or needs_markdown_workflow
            or needs_ppt_workflow
        )
        if not needs_document_workflow:
            return files_message

        workflow_lines = [
            "",
            "Because the user explicitly asked to generate a PPT from uploaded documents, you must follow the document-to-PPT skill workflow before calling `generate_ppt`:",
            f"- First load the PPT workflow skill: {PPT_SKILL_PATH}",
        ]
        if needs_pdf_workflow:
            workflow_lines.append(f"- For PDF files, load: {PDF_SKILL_PATH}")
        if needs_docx_workflow:
            workflow_lines.append(f"- For Word files, load: {DOCX_SKILL_PATH}")
        if needs_markdown_workflow:
            workflow_lines.append(f"- For Markdown files, load: {MARKDOWN_SKILL_PATH}")
        if needs_ppt_workflow:
            workflow_lines.append(f"- For PowerPoint files, load: {PPTX_SKILL_PATH}")
        workflow_lines.extend(
            [
                f"- Then load the summarizer skill: {SUMMARIZER_SKILL_PATH}",
                "- Use the relevant document processor skill to extract text and tables from the uploaded file if the Markdown sidecar is empty or incomplete.",
                "- Use the summarizer skill to turn extracted raw text into a structured summary of themes, sections, key facts, tables, and constraints.",
                "- Do not pre-decide a final page-by-page slide outline yourself unless the user explicitly asks for an outline first.",
                "- Leave the actual slide planning, page allocation, and per-page structure to `generate_ppt`.",
                "- Only then call `generate_ppt(content=...)` with that structured summary.",
                "- If extraction still fails, do not invent content from general knowledge without asking the user.",
            ]
        )

        return files_message.replace("</uploaded_files>", "\n".join(workflow_lines) + "\n</uploaded_files>")

    def _files_from_kwargs(self, message: HumanMessage, uploads_dir: Path | None = None) -> list[dict] | None:
        """Extract file info from message additional_kwargs.files.

        The frontend sends uploaded file metadata in additional_kwargs.files
        after a successful upload. Each entry has: filename, size (bytes),
        path (virtual path), status.

        Args:
            message: The human message to inspect.
            uploads_dir: Physical uploads directory used to verify file existence.
                         When provided, entries whose files no longer exist are skipped.

        Returns:
            List of file dicts with virtual paths, or None if the field is absent or empty.
        """
        kwargs_files = (message.additional_kwargs or {}).get("files")
        if not isinstance(kwargs_files, list) or not kwargs_files:
            return None

        files = []
        for f in kwargs_files:
            if not isinstance(f, dict):
                continue
            filename = f.get("filename") or ""
            if not filename or Path(filename).name != filename:
                continue
            if uploads_dir is not None and not (uploads_dir / filename).is_file():
                continue
            markdown_file = f.get("markdown_file")
            markdown_path = f.get("markdown_virtual_path") or f.get("markdown_path")
            if markdown_file and Path(markdown_file).name != markdown_file:
                markdown_file = None
            if markdown_file and uploads_dir is not None and not (uploads_dir / markdown_file).is_file():
                markdown_file = None
                markdown_path = None
            files.append(
                {
                    "filename": filename,
                    "size": int(f.get("size") or 0),
                    "path": f.get("path") or f"/mnt/user-data/uploads/{filename}",
                    "extension": Path(filename).suffix,
                    "markdown_file": markdown_file,
                    "markdown_path": markdown_path or (f"/mnt/user-data/uploads/{markdown_file}" if markdown_file else None),
                }
            )
        return files if files else None

    def _artifact_references_from_kwargs(
        self,
        message: HumanMessage,
        outputs_dir: Path | None = None,
    ) -> list[dict] | None:
        kwargs_refs = (message.additional_kwargs or {}).get("artifact_references")
        if not isinstance(kwargs_refs, list) or not kwargs_refs:
            return None

        references = []
        for ref in kwargs_refs:
            if isinstance(ref, str):
                virtual_path = ref
            elif isinstance(ref, dict):
                virtual_path = ref.get("path")
            else:
                continue

            if not isinstance(virtual_path, str) or not virtual_path.startswith("/mnt/user-data/outputs/"):
                continue

            filename = Path(virtual_path).name
            if not filename:
                continue

            if outputs_dir is not None and not (outputs_dir / filename).is_file():
                continue

            references.append(
                {
                    "filename": filename,
                    "path": virtual_path,
                    "extension": Path(filename).suffix,
                }
            )

        return references if references else None

    def _conversation_artifacts_from_dir(
        self,
        outputs_dir: Path | None = None,
    ) -> list[dict]:
        if outputs_dir is None or not outputs_dir.exists():
            return []

        artifacts: list[dict] = []
        for file_path in sorted(outputs_dir.iterdir()):
            if not file_path.is_file() or file_path.name.startswith("."):
                continue
            artifacts.append(
                {
                    "filename": file_path.name,
                    "path": f"/mnt/user-data/outputs/{file_path.name}",
                    "extension": file_path.suffix,
                    "size": file_path.stat().st_size,
                }
            )
        return artifacts

    @override
    def before_agent(self, state: UploadsMiddlewareState, runtime: Runtime) -> dict | None:
        """Inject uploaded files information before agent execution.

        New files come from the current message's additional_kwargs.files.
        Historical files are scanned from the thread's uploads directory,
        excluding the new ones.

        Prepends <uploaded_files> context to the last human message content.
        The original additional_kwargs (including files metadata) is preserved
        on the updated message so the frontend can read it from the stream.

        Args:
            state: Current agent state.
            runtime: Runtime context containing thread_id.

        Returns:
            State updates including uploaded files list.
        """
        messages = list(state.get("messages", []))
        if not messages:
            return None

        last_message_index = len(messages) - 1
        last_message = messages[last_message_index]

        if not isinstance(last_message, HumanMessage):
            return None

        # Resolve uploads directory for existence checks
        thread_id = (runtime.context or {}).get("thread_id")
        uploads_dir = self._paths.sandbox_uploads_dir(thread_id) if thread_id else None
        outputs_dir = self._paths.sandbox_outputs_dir(thread_id) if thread_id else None

        # Get newly uploaded files from the current message's additional_kwargs.files
        new_files = self._files_from_kwargs(last_message, uploads_dir) or []
        referenced_artifacts = self._artifact_references_from_kwargs(last_message, outputs_dir) or []
        conversation_artifacts = self._conversation_artifacts_from_dir(outputs_dir)

        # Collect historical files from the uploads directory (all except the new ones)
        new_filenames = {f["filename"] for f in new_files}
        historical_files: list[dict] = []
        if uploads_dir and uploads_dir.exists():
            available_names = {path.name for path in uploads_dir.iterdir() if path.is_file()}
            for file_path in sorted(uploads_dir.iterdir()):
                if not file_path.is_file() or file_path.name in new_filenames:
                    continue
                if self._is_converted_markdown_sidecar(file_path, available_names):
                    continue

                stat = file_path.stat()
                historical_files.append(
                    {
                        "filename": file_path.name,
                        "size": stat.st_size,
                        "path": f"/mnt/user-data/uploads/{file_path.name}",
                        "extension": file_path.suffix,
                        "markdown_file": file_path.with_suffix(".md").name if file_path.suffix.lower() in CONVERTIBLE_EXTENSIONS else None,
                        "markdown_path": self._find_markdown_virtual_path(file_path, uploads_dir)
                        if file_path.suffix.lower() in CONVERTIBLE_EXTENSIONS
                        else None,
                    }
                )

        if not new_files and not historical_files and not referenced_artifacts and not conversation_artifacts:
            return None

        logger.debug(f"New files: {[f['filename'] for f in new_files]}, historical: {[f['filename'] for f in historical_files]}")

        # Extract original content - handle both string and list formats
        original_content = ""
        if isinstance(last_message.content, str):
            original_content = last_message.content
        elif isinstance(last_message.content, list):
            text_parts = []
            for block in last_message.content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            original_content = "\n".join(text_parts)

        explicit_ppt_request = self._is_explicit_ppt_request(original_content)
        context_blocks: list[str] = []

        if referenced_artifacts:
            context_blocks.append(self._create_referenced_artifacts_message(referenced_artifacts))

        if conversation_artifacts:
            non_referenced_conversation_artifacts = [
                artifact
                for artifact in conversation_artifacts
                if artifact["path"] not in {ref["path"] for ref in referenced_artifacts}
            ]
            if non_referenced_conversation_artifacts:
                context_blocks.append(
                    self._create_conversation_artifacts_message(
                        non_referenced_conversation_artifacts
                    )
                )

        if new_files or historical_files:
            files_message = self._create_files_message(new_files, historical_files)
            files_message = self._append_skill_workflow(
                files_message,
                new_files,
                explicit_ppt_request=explicit_ppt_request,
            )
            context_blocks.append(files_message)

        # Create new message with combined content.
        # Preserve additional_kwargs (including files metadata) so the frontend
        # can read structured file info from the streamed message.
        updated_message = HumanMessage(
            content=f"{'\n\n'.join(context_blocks)}\n\n{original_content}",
            id=last_message.id,
            additional_kwargs=last_message.additional_kwargs,
        )

        messages[last_message_index] = updated_message

        return {
            "uploaded_files": new_files,
            "referenced_artifacts": referenced_artifacts,
            "conversation_artifacts": conversation_artifacts,
            "messages": messages,
        }
