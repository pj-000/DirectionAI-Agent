---
name: lesson-plan-generation
description: 当用户请求生成教案、教学设计、备课材料时使用此技能。适合老师备课、课程设计、培训资料准备等需求。
---

# Lesson Plan Generation Skill

## 触发条件

当用户说以下类似的话时，应使用此技能：

- "帮我生成教案"、"做个教案"
- "设计一份 XX 课程的教学方案"
- "如何备课 XX 主题"
- "给我一个 XX 的教学设计"
- 用户明确要求创建 lesson plan / teaching plan / 教案

## 工作流

### Step 1: 理解用户需求

从用户的请求中提取以下信息：

- **topic（主题）**: 课程或知识点的核心主题（必填）
- **output_language（输出语言）**: 教案语言，默认"中文"
- **target_audience（目标受众）**: 学生群体
  - 示例：小学生、初中生、高中生、大学生、职场人士
- **course（课程名称）**: 所属课程（可选）
  - 示例：高中数学、大学物理、职业培训
- **units（单元）**: 需要覆盖的单元名称
- **lessons（课时）**: 具体课时安排
- **knowledge_points（知识点）**: 需要覆盖的具体知识点
- **enable_web_search（联网搜索）**: 是否搜索参考资料，默认 False
- **content（附加要求）**: 教学时长、特殊要求等

### Step 2: 调用 generate_lesson_plan 工具

使用提取的参数调用 `generate_lesson_plan` 工具：

```python
generate_lesson_plan(
    topic="三角函数",  # 必填
    output_language="中文",
    target_audience="高中生",
    course="高中数学",
    units="第四章 三角函数",
    lessons="4.1 任意角与弧度制, 4.2 三角函数的概念",
    knowledge_points="弧度制转换, sin/cos/tan定义, 诱导公式",
    enable_web_search=False,
    content="需要包含例题和课堂练习"
)
```

### Step 3: 解释结果

工具返回后，向用户解释教案包含的内容：

- 教学目标（知识与技能、过程与方法、情感态度与价值观）
- 教学内容大纲
- 教学活动设计（导入、新授、练习、总结）
- 课堂练习与作业

### Step 4: 迭代优化（如需要）

- **调整深度**: 修改 target_audience 或 content 说明调整方向
- **增加课时**: 在 lessons 中指定更多课时
- **配合 PPT**: 询问是否需要配套生成 PPT

## 示例对话

```
用户: 帮我做一个关于"光的折射"的物理教案，受众是初中二年级学生
助手: (调用 generate_lesson_plan 工具)
助手: ✅ 教案生成任务已启动！
      主题: 光的折射
      课程: 初中物理
      受众: 初中二年级学生
      📋 教案将包含：教学目标、内容大纲、教学活动设计、课堂练习与评估
```

## 注意事项

- **topic 是必填参数**: 如果用户没有明确主题，先询问
- **受众决定深度**: 初中生和大学生对同一主题的理解深度差异很大
- **课时数量**: 指定具体课时数量可以更精确地规划内容
- **知识连贯性**: 如果是课程的一部分，提供 course 和 units 信息有助于保持知识连贯性
