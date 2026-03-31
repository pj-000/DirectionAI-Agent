# PPT Agent — 项目规范

## 项目概述

一个基于多 Agent 架构的 PPT 自动生成系统，分 4 个阶段迭代开发。
当前处于：Phase 1（MVP 骨架）

LLM 使用 GLM（通过 OpenAI 兼容接口调用），方便未来切换模型供应商。

## 核心数据模型（不得随意修改）

所有模块之间传递的数据必须使用 `models/schemas.py` 中定义的 Pydantic 模型：
- `PresentationPlan`：完整 PPT 规划，Planner 输出，Renderer 接收
- `SlideSpec`：单页规格，包含布局类型和所有文字元素
- `TextElement`：单个文本框，包含内容 + 坐标 + 样式

## 坐标单位

所有位置和尺寸使用**英寸（inch）**，不使用像素或 EMU。
python-pptx 接收时用 `Inches(value)` 转换。
幻灯片尺寸：宽 13.333 inch，高 7.5 inch（16:9）。

## 模块职责（不得交叉）

- `agents/planner.py`：只负责调用 GLM API 和解析 JSON，不涉及任何 PPTX 操作
- `tools/pptx_renderer.py`：只负责渲染，不调用任何 LLM
- `tools/templates.py`：只负责坐标模板，不含业务逻辑
- `agents/orchestrator.py`：只负责调度，不直接操作 PPTX

## 错误处理规范

- LLM 返回非法 JSON → 抛出 `ValueError`，说明原始内容
- Pydantic 校验失败 → 让错误向上传播，不吞掉
- API 调用失败 → 不重试（Phase 1），直接抛出

## 禁止事项

- 不引入 LangChain、LangGraph 或任何 agent 框架
- 不在 `tools/` 目录里调用 LLM API
- 不在 `agents/` 目录里操作文件系统（除 orchestrator 记录日志）
- Pydantic 模型字段不得使用 `Any` 类型

## 测试要求

- 每个新功能必须有对应测试
- 涉及 LLM 调用的测试默认使用 mock，单独标记真实 API 测试
- 运行测试：`pytest tests/ -v`
- 运行单个文件：`pytest tests/test_renderer.py -v`

## 阶段边界

Phase 1（当前）：固定模板坐标，只生成文字
Phase 2：Planner 输出动态坐标，替换 templates.py
Phase 3：加入 Research Agent + Asset Agent（并行执行）
Phase 4：加入 Evaluator Agent + Revise 循环
