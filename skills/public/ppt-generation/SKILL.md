---
name: ppt-generation
description: 当用户请求生成、创建、制作 PPT、演示文稿、课件时使用此技能。这是教育场景中最常用的技能，适合老师备课、课程汇报、学术演讲等需求。
---

# PPT Generation Skill

## 触发条件

当用户说以下类似的话时，应使用此技能：

- "帮我做一个 PPT"、"生成演示文稿"、"做个课件"
- "做一份关于 XX 的幻灯片"
- "帮我制作 PPT"、"创建演示文稿"
- "做一个 XX 主题的教学 PPT"
- 用户明确要求创建 .pptx/.ppt 文件

泛化判断原则：

- 只有当用户的目标产物仍然是一个新的 PPT，或明确要求修改/重生成现有 PPT 时，才触发本技能
- 如果用户只是把现有 PPT 当素材，要求总结、解读、翻译、问答、讲稿、speaker notes、逐页讲解词或其他文本结果，不要触发本技能

## 工作流

### Step 1: 理解用户需求

从用户的请求中提取以下信息：

- **topic（主题）**: PPT 的核心主题，这是最重要的参数
- **output_language（输出语言）**: 内容语言，默认"中文"
- **target_audience（目标受众）**: 听众群体，默认"通用受众"
  - 示例：大学生、投资人、企业老板、技术团队、中学生
- **style（风格）**: 视觉风格偏好，留空则自动决定
  - 可选：商务、学术、简约、科技感、清新
- **min_slides / max_slides（页数范围）**: 默认 6-10 页
- **model_provider（模型）**: "minmax"（默认）或 "claude"
- **image_mode（图片模式）**: "generate"（默认 AI 生图）/ "search"（仅搜图）/ "auto"（先搜后生）/ "off"（无图）
- **enable_web_search（联网搜索）**: 是否补充网络资料，默认 False
- **content（附加要求）**: 用户的特殊内容要求、章节安排等

如果用户是“根据上传文档生成 PPT”，这里有一个额外规则：

- `content` 应该放文档提炼后的主题摘要、章节总结、关键事实、表格要点、受众和约束
- **不要**在主 agent 侧提前拍板“第1页讲什么、第2页讲什么”这类最终分页方案，除非用户明确要求先给出大纲
- 具体的页数分配、逐页结构和封面/正文组织应交给 `generate_ppt` 的规划阶段完成

### Step 2: 调用 generate_ppt 工具

使用提取的参数调用 `generate_ppt` 工具：

```python
generate_ppt(
    topic="Python 基础教程",  # 必填，PPT 主题
    output_language="中文",
    target_audience="大学生",
    style="",
    min_slides=6,
    max_slides=10,
    model_provider="minmax",
    image_mode="generate",
    enable_web_search=False,
    content="需要包含 Python 基础语法、数据类型、控制流程三章"
)
```

如果输入来自上传文档，推荐这样理解 `content`：

```python
generate_ppt(
    topic="DeepSeek从入门到精通",
    min_slides=4,
    max_slides=4,
    content=\"\"\"
文档摘要：介绍 DeepSeek 的定位、主要能力、提示语设计方法和进阶使用方式。
核心章节：
1. DeepSeek 是什么
2. DeepSeek 能做什么
3. 提示语设计与提示语链
4. AI 进阶使用与人机共生
关键约束：面向入门用户，压缩为 4 页，保留核心概念和方法论。
\"\"\"
)
```

注意：
- 上面这种写法是在给 `generate_ppt` 提供“素材和约束”
- **不是**在主 agent 里提前决定最终每一页的标题和版式

### Step 3: 解释结果

工具返回后，向用户解释：

1. 告诉用户可以点击链接查看实时生成进度
2. 介绍生成流程（大纲规划 → 资料补充 → 视觉主题 → 逐页生成 → 质量评估）
3. 告知生成完成后可以在 PPT 生成页面下载文件

### Step 4: 迭代优化（如需要）

如果用户要求修改：

- **修改某一页**: 调用 `generate_ppt` 时在 content 中说明需要修改的具体页面和内容
- **增加页数**: 调整 min_slides / max_slides
- **更换风格**: 修改 style 参数
- **质量评估**: 使用 `evaluate_ppt` 工具对生成的 PPT 进行评估

## 示例对话

### 示例 1: 简单请求
```
用户: 帮我做一个关于机器学习的 PPT
助手: (调用 generate_ppt 工具，topic="机器学习")
助手: ✅ PPT 生成任务已启动！
      主题: 机器学习
      页数: 6-10 页
      📊 查看生成进度: [PPT 生成页面](/workspace/ppt?task=xxx)
```

### 示例 2: 详细请求
```
用户: 我要做一个面向大学生的线性代数教学 PPT，要求 8 页左右，使用学术风格，需要联网补充一些最新应用案例
助手: (调用 generate_ppt 工具)
助手: ✅ PPT 生成任务已启动！
      主题: 线性代数
      页数: 8 页
      风格: 学术
      已启用联网搜索补充案例
      📊 查看生成进度: [PPT 生成页面](/workspace/ppt?task=xxx)
```

## 注意事项

- **topic 是必填参数**: 如果用户没有明确主题，需要先询问
- **页数范围**: 建议 6-10 页，太少内容不够丰富，太多则重点不突出
- **规划职责边界**: 主 agent 负责理解需求和整理素材，`generate_ppt` 负责真正的 PPT 大纲规划与逐页结构设计
- **产物类型优先**: 先判断用户最终想要的产物是不是 PPT。若最终产物是文本、问答、讲稿、总结或说明，而不是新的演示文稿，就不要调用 `generate_ppt`
- **图片模式**: "generate" 适合创意演示，"off" 适合纯文字/代码讲解
- **目标受众**: 不同受众需要不同的内容深度和表达方式
- **联网搜索**: 仅当用户需要最新信息时开启，否则可能增加生成时间

## 与其他技能的配合

- **配合 lesson-plan-generation**: 用户说"帮我做一个 PPT 并配套教案"
  → 可以同时调用 `generate_ppt` 和 `generate_lesson_plan`
- **配合 evaluate_ppt**: PPT 生成完成后，可询问用户是否需要质量评估

## 工具输出格式

`generate_ppt` 工具返回 Markdown 格式的结果，包含：

- 生成任务状态
- 关键参数确认
- 进度查看链接
- 生成流程说明
