# DirectionAI Agent

基于 DeerFlow v2 框架的教育 AI Agent 系统，集成了 PPT 生成、教案生成、试题生成等教育技能。

## 架构

```
DirectionAI-Agent/
├── backend/              # DeerFlow LangGraph Server (symlink)
├── frontend/            # DeerFlow Next.js 前端 (symlink)
├── pptagent/            # PPT 生成服务 (symlink to /Users/sss/directionai/pptagent)
├── skills/              # 技能定义
│   └── public/
│       ├── ppt-generation/
│       ├── document-processor-pdf/
│       ├── document-processor-docx/
│       ├── document-processor-markdown/
│       ├── document-processor-pptx/
│       ├── document-summarizer/
│       ├── lesson-plan-generation/
│       └── exam-generation/
├── docker-compose.yaml  # Docker 编排配置
├── config.yaml          # DeerFlow 主配置
└── .env                 # 环境变量
```

## 快速开始

### 1. 初始化配置

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env 填入 API Key
vim .env
```

### 2. 创建 symlinks

```bash
# 链接 DeerFlow 源码
ln -sf ../deer-flow-github/backend backend
ln -sf ../deer-flow-github/frontend frontend
ln -sf ../pptagent pptagent
```

### 3. Docker 启动

```bash
docker compose up -d
# 访问 http://localhost:2026
```

### 4. 本地开发

```bash
cd backend && uv sync
cd frontend && pnpm install
make dev
```

## 集成说明

### PPT 生成

- **Tool**: `generate_ppt` (DeerFlow Tool)
- **Skill**: `skills/public/ppt-generation/SKILL.md`
- **文档处理 Skills**:
  - `skills/public/document-processor-pdf/SKILL.md`
  - `skills/public/document-processor-docx/SKILL.md`
  - `skills/public/document-processor-markdown/SKILL.md`
  - `skills/public/document-processor-pptx/SKILL.md`
  - `skills/public/document-summarizer/SKILL.md`
- **SSE 路由**: `/pptagentapi/stream_ppt`
- **前端页面**: `/workspace/ppt`
- **流式体验**: ThinkingProcess 组件实时显示生成进度

当用户上传 PDF / Word / Markdown / PPT 文档并要求生成 PPT 时，可以组合使用上述文档处理 skill：
先提取文档文本与表格，再生成结构化摘要，最后把摘要喂给 PPT 规划与逐页生成流程。

当前在 DeerFlow 聊天链路中的适配方式是：
- 前端上传后会把原文件虚拟路径与转换后的 Markdown 虚拟路径一起写入消息元数据
- Lead Agent 会优先读取转换后的 Markdown，而不是直接读取 PDF / DOCX / PPTX 二进制文件；如果用户上传的本身就是 `.md`，则直接读取该文件
- 只有当用户明确要求“根据上传文档生成 PPT”时，Agent 才会强制参考 `document-processor-pdf` / `document-processor-docx` / `document-processor-markdown` / `document-processor-pptx` / `document-summarizer` 完成文档抽取与结构化
- 在这条文档到 PPT 的链路里，主 agent 只负责提炼文档主题、章节和关键事实，不负责提前拍板最终每一页内容；真正的分页规划交给 `generate_ppt`
- 最终通过 `generate_ppt(content=...)` 把文档摘要、章节结构、关键事实和页数约束传给 PPT 生成器；这些内容现在会真正透传到流式 `/stream_ppt` 请求

### 路由架构

```
用户 → DeerFlow Chat → Lead Agent → generate_ppt Tool
                                    ↓
                            pptagent SSE stream
                                    ↓
                      /workspace/ppt (前端 SSE 订阅)
                                    ↓
                            实时进度 + 缩略图预览
```

### Nginx 路由

- `/` → Next.js 前端
- `/api/*` → DeerFlow Gateway API
- `/api/langgraph/*` → LangGraph Server
- `/pptagentapi/*` → PPT 生成服务

## 开发说明

### 添加新的 Education Tool

1. 在 `backend/packages/harness/deerflow/directionai/tools/` 创建新文件
2. 使用 `@tool` decorator 定义 Tool
3. 在 `tools/__init__.py` 中导出
4. 在 `config.yaml` 的 `tools` 数组中注册

### 添加新的 Skill

1. 在 `skills/public/` 创建新目录
2. 编写 `SKILL.md` 定义工作流
3. Lead Agent 会自动加载并根据 description 决定何时调用

### 前端开发

PPT 生成页面的 React 组件位于:
`frontend/src/app/workspace/ppt/page.tsx`
