---
name: document-summarizer
description: Summarize and structure raw document text (PDF/Word/Markdown/PPT) into a format optimized for PPT generation. Use when the user uploads a document and wants to generate a presentation from it.
---

# Document Summarizer for PPT Generation

## Purpose

Transform raw extracted text from PDF, Word, Markdown, or PPT documents into a structured summary that feeds directly into the PPT generation pipeline. The output is designed so that each `section` maps naturally to one or more PPT slides, preserving document hierarchy, extracting key facts and data tables, and providing actionable hints for slide planning.

## When to Use

- User uploads a PDF, Word, Markdown, or PPT/PPTX document and wants to generate a PPT from it
- Need to understand document structure before creating a PPT outline
- This step runs **before** the PPT planning stage — its output becomes the input context for the PPT outline planner

## How It Integrates With the PPT Pipeline

```
PDF/Word/Markdown/PPT → extract text → this summarizer → structured_summary
                                                    ↓
                                          PPT outline planner
                                                    ↓
                                         逐页生成 + QA (pptagent)
```

The `sections` array maps to PPT slide groups (each section typically covers 1-3 slides).
The `ppt_generation_hints` directly seeds parameters for the downstream PPT planner:
`audience`, `style_preference`, and `suggested_total_slides` become planner inputs.

## Input

Raw text extracted from PDF or Word document, optionally with:
- Extracted tables (list of 2D arrays from `pdfplumber`)
- Document metadata (`{ title, author, date, page_count }`)
- User's stated goal, target audience, or style preference

If the document has a clear hierarchical structure (chapters → sections → subsections), preserve it.
If the document is narrative or prose-heavy, infer logical breaking points.

## Output Format

**Only output JSON** — no markdown code fences, no explanations, no preamble.

### Schema

```json
{
  "document_title": "string (required) — original title or inferred",
  "document_summary": "string (required) — 2-3 sentence overview of the document's purpose and scope",
  "sections": [
    {
      "section_title": "string (required)",
      "section_summary": "string (required) — 1-3 sentences, what this section covers and why it matters",
      "key_points": [
        "string — complete, standalone fact or idea. No vague language, no references to other sections."
      ],
      "tables": [
        {
          "description": "string (required) — what this table shows and why it is important",
          "headers": ["string"],
          "rows": [["string"]]
        }
      ],
      "important_data": [
        "string — specific number, statistic, date, or ranking. No invented data."
      ],
      "suggested_slide_count": "integer (required) — 1 for brief sections, 2-3 for substantive ones",
      "narrative_transition": "string (optional) — one sentence explaining how this section connects to the next"
    }
  ],
  "metadata": {
    "source_file": "string (optional)",
    "total_pages": "integer (optional)",
    "language": "string — zh-CN | en-US | auto-detect",
    "estimated_reading_time_minutes": "integer (optional)"
  },
  "ppt_generation_hints": {
    "suggested_total_slides": "integer — min 4, max 20, target 6-12",
    "audience": "string — general | student | technical | business | academic",
    "style_preference": "string — informative | executive | creative | academic",
    "recommended_visual_elements": ["string — diagrams | statistics_callouts | timeline | comparison | process_flow"],
    "content_focus": "string — knowledge_sharing | proposal | report | training | introduction",
    "special_requirements": "string (optional) — any explicit user request or constraint noted in the document"
  }
}
```

## Required vs Optional Fields

| Field | Required |
|-------|----------|
| `document_title` | Yes |
| `document_summary` | Yes |
| `sections` (non-empty) | Yes |
| `sections[].section_title` | Yes |
| `sections[].section_summary` | Yes |
| `sections[].key_points` (non-empty) | Yes |
| `sections[].suggested_slide_count` | Yes |
| `sections[].tables` | No (omit if none) |
| `sections[].important_data` | No (omit if none) |
| `sections[].narrative_transition` | No |
| `metadata.*` | No |
| `ppt_generation_hints.*` | No (but recommended) |

## Rules

### 1. Map Document Structure to Sections

- Each major chapter/heading in the source becomes a `section`
- Sub-sections under the same heading can be merged if they share a similar theme
- **Never skip a major section** — every significant heading must appear
- If the document has no clear headings (prose-heavy), infer logical breaks: introduction → core argument → evidence/examples → conclusion
- For long documents (> 30k characters): mark core sections distinctly, group minor supplementary sections together, and note this in `special_requirements`

### 2. Write Key Points That Stand Alone

Each entry in `key_points` must be:
- **Self-contained**: understandable without reading surrounding text or other sections
- **Specific**: include concrete details (names, numbers, dates, comparisons), not generic descriptions
- **Concise**: 1-2 sentences maximum
- **Distinct**: never repeat the same fact with different wording

Never include:
- Vague phrases ("various factors", "etc.", "many aspects")
- Introductory/conclusion fillers ("In summary...", "To conclude...")
- References forward or backward ("As discussed earlier...", "This will be covered later...")
- Multi-part ideas split across bullets

### 3. Extract Tables Selectively

Include a table only if it contains data that benefits from visual presentation:
- Comparative data (features, specs, prices, metrics across items)
- Process/role/architecture information (components, steps, responsibilities)
- Timeline or ranking data

Skip tables with < 2 columns or < 2 rows. For tables > 6 rows, keep first 5 and append `rows: [["..."]]`.

Every table **must** have a `description` explaining what it shows and why it matters for the PPT.

### 4. Extract Important Data Precisely

- Only numbers, statistics, dates, percentages, rankings explicitly stated in the document
- Do not calculate or infer — if the document says "increased by 20%", include exactly that
- If no significant data exists in a section, omit `important_data` entirely
- Useful for stat callout slides: one strong number can anchor an entire page

### 5. Estimate Slide Count Honestly

`suggested_slide_count` per section:
- **1**: pure overview, introduction, or very brief (under 200 words of content)
- **2**: standard section with 3-5 key points and moderate detail
- **3+**: complex section with multiple sub-topics, heavy data, or a diagram worth its own slide

`suggested_total_slides` (top-level):
- Range: 4 to 20, target 6-12 for most documents
- Formula: sum of section slide counts + 2 (cover + closing)
- If the sum exceeds 20, prioritize the most substantive sections and merge or drop minor ones

### 6. Maintain Content Integrity

- **Never fabricate** — every key_point and summary must be derivable from the source text
- If a section is too thin to extract meaningful points, write a brief descriptive note instead of inventing content
- Preserve technical terminology accurately — do not simplify technical terms for general audiences unless instructed
- Keep language consistent — all text in the same language as the source document

### 7. Guide PPT Visual Strategy

`ppt_generation_hints.recommended_visual_elements` should match what the document actually contains:
- `statistics_callouts` — if `important_data` has strong numbers
- `diagrams` — if the document describes processes, structures, or relationships
- `timeline` — if chronological data or milestones are present
- `comparison` — if tables contain comparative data
- `process_flow` — if steps or workflows are described
- `map` — if geographic data is present

`content_focus` should reflect the document's primary purpose:
- `knowledge_sharing` — training materials, educational content
- `proposal` — project or business proposals
- `report` — status reports, research summaries
- `training` — onboarding, procedure manuals
- `introduction` — topic overviews, company/product introductions

## Complete Example

### Input (abbreviated source text)

> **Chapter 1: 大模型技术概述**
> 大语言模型（Large Language Model, LLM）是基于深度学习的自然语言处理技术...
> 2020年GPT-3发布后，LLM进入爆发期，参数量从亿级增长到万亿级...
>
> **Chapter 2: 核心技术架构**
> Transformer架构是LLM的基础，由Vaswani等人于2017年提出...
> 核心组件包括：自注意力机制（Self-Attention）、前馈神经网络（FFN）、位置编码...
>
> **Chapter 3: 训练与优化**
> 预训练阶段使用海量无标注文本进行自监督学习...

### Output JSON

```json
{
  "document_title": "大模型技术概述",
  "document_summary": "本文件系统性介绍大语言模型（LLM）的发展历程、核心技术架构与训练方法。内容涵盖从GPT-3到当前主流模型的演进、Transformer架构的关键组件，以及预训练与微调的技术细节。",
  "sections": [
    {
      "section_title": "大模型技术概述",
      "section_summary": "本节介绍LLM的定义及其发展脉络。GPT-3于2020年发布标志着LLM进入爆发期，参数量从亿级跃升至万亿级，引发学术界与产业界的广泛关注。",
      "key_points": [
        "大语言模型（LLM）是基于深度学习的自然语言处理技术，通过海量文本数据学习语言规律与知识表示",
        "2020年GPT-3发布后LLM进入爆发期，参数量从亿级增长到万亿级，模型能力出现质的飞跃"
      ],
      "tables": [],
      "important_data": ["参数量：亿级 → 万亿级", "标志性节点：2020年 GPT-3发布"],
      "suggested_slide_count": 1,
      "narrative_transition": "理解了LLM的基本概念后，接下来深入其底层技术基础——Transformer架构"
    },
    {
      "section_title": "核心技术架构",
      "section_summary": "Transformer架构由Vaswani等人于2017年提出，是所有主流LLM的共同基础。其核心在于自注意力机制，使模型能够并行处理序列中的任意位置信息，大幅提升训练效率。",
      "key_points": [
        "Transformer架构由Vaswani等人于2017年提出，是GPT及后续所有LLM的共同基础",
        "自注意力机制（Self-Attention）允许模型并行处理任意距离的token，突破RNN的序列限制",
        "前馈神经网络（FFN）与残差连接构成Transformer的基本计算单元",
        "位置编码（Positional Encoding）为序列中的词序信息提供可学习的表示"
      ],
      "tables": [
        {
          "description": "Transformer的三大核心组件及其作用",
          "headers": ["组件", "英文名", "核心作用"],
          "rows": [
            ["自注意力", "Self-Attention", "建模token间的依赖关系"],
            ["前馈网络", "FFN", "逐位非线性变换"],
            ["位置编码", "Positional Encoding", "注入序列位置信息"]
          ]
        }
      ],
      "important_data": ["架构提出年份：2017年"],
      "suggested_slide_count": 2,
      "narrative_transition": "掌握架构原理后，下面来看模型是如何通过大规模训练获得能力的"
    },
    {
      "section_title": "训练与优化",
      "section_summary": "LLM的训练分为预训练和微调两个阶段。预训练使用海量无标注文本进行自监督学习，微调则通过少量标注数据使模型适应特定任务。",
      "key_points": [
        "预训练阶段：使用海量无标注文本进行自监督学习，目标是最小化语言建模损失",
        "微调阶段：通过少量标注数据调整模型参数，使模型适应特定下游任务",
        "主流微调方法包括：Prompt Tuning、LoRA等参数高效微调技术，大幅降低微调成本"
      ],
      "tables": [],
      "important_data": [],
      "suggested_slide_count": 1
    }
  ],
  "metadata": {
    "language": "zh-CN",
    "estimated_reading_time_minutes": 15
  },
  "ppt_generation_hints": {
    "suggested_total_slides": 7,
    "audience": "technical",
    "style_preference": "informative",
    "recommended_visual_elements": ["diagrams", "statistics_callouts"],
    "content_focus": "knowledge_sharing",
    "special_requirements": "第二章技术架构内容较深，建议为自注意力机制单独配一张示意图"
  }
}
```

## Constraints

1. **Output only JSON** — no fences, no preamble, no commentary
2. **JSON must be valid** — proper escaping, no trailing commas, arrays/objects closed correctly
3. **No truncation** — every section gets its full key_points, no "..."
4. **No fabrication** — all content traceable to source text
5. **Fields must be meaningful** — null, empty strings, and placeholder text are prohibited in required fields
