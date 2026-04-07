# DirectionAI Agent

基于 DeerFlow v2 框架的教育 AI Agent 系统，当前聚焦 PPT 生成与文档到 PPT 的工作流。

当前核心能力：
- `generate_ppt` 负责异步流式生成 PPT，并在聊天区展示实时进度
- 上传 PDF / Word / Markdown / PPT 文档后，可先抽取与总结内容，再进入 PPT 生成链路

## 架构

```text
DirectionAI-Agent/
├── backend/             # DeerFlow LangGraph / Gateway 后端
├── frontend/            # DeerFlow Next.js 前端
├── skills/              # 技能定义
│   └── public/
│       ├── ppt-generation/
│       ├── document-processor-pdf/
│       ├── document-processor-docx/
│       ├── document-processor-markdown/
│       ├── document-processor-pptx/
│       ├── document-summarizer/
│       └── ppt-generation/
├── docker-compose.yaml  # Docker 编排配置
├── config.yaml          # DeerFlow 主配置
└── .env                 # 环境变量
```

## 快速开始

### 1. 准备环境

```bash
git clone git@github.com:pj-000/DirectionAI-Agent.git
cd DirectionAI-Agent
```

需要先安装：

- Docker Desktop 或 Docker Engine + Docker Compose
- 可访问外部模型服务的网络环境

### 2. 初始化配置

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env 填入密钥
vim .env
```

至少需要补这些变量：

- `MINIMAX_API_KEY`
- `ANTHROPIC_API_KEY`
- `BETTER_AUTH_SECRET`

可选但推荐：

- `BETTER_AUTH_URL`
- `DOUBAO_API_KEY`
- `LANGGRAPH_API_KEY`

### 3. Docker 启动

```bash
docker compose up -d --build
# 访问 http://localhost:2026
```

首次启动会比较慢，因为容器会重新创建 Python 虚拟环境并安装前后端依赖。
看到前端首页能打开，不代表后端已经完全热好；`gateway` / `langgraph` 首次 warmup 可能还需要几十秒。

### 4. 常见启动检查

```bash
docker compose ps
docker compose logs -f gateway
docker compose logs -f langgraph
```

如果 `gateway` 仍在下载或安装 Python 依赖，稍等即可。

### 5. 本地开发

```bash
cd backend && uv sync
cd frontend && pnpm install
make dev
```

## 部署说明

这个仓库现在已经是自包含结构，不再依赖额外的 `pptagent` 仓库，也不需要手动创建 `backend` / `frontend` / `pptagent` symlink。
PPT 生成链路已经内置在当前仓库的 `gateway` 服务中。

## 集成说明

### PPT 生成

- **Tool**: `generate_ppt` (DeerFlow Tool)
- **唯一实现入口**: `backend/packages/harness/deerflow/directionai/tools/generate_ppt.py`
- **配置引用**: `config.yaml` 中保持 `deerflow.directionai.tools:generate_ppt_tool`
- **Skill**: `skills/public/ppt-generation/SKILL.md`
- **文档处理 Skills**:
  - `skills/public/document-processor-pdf/SKILL.md`
  - `skills/public/document-processor-docx/SKILL.md`
  - `skills/public/document-processor-markdown/SKILL.md`
  - `skills/public/document-processor-pptx/SKILL.md`
  - `skills/public/document-summarizer/SKILL.md`
- **SSE 路由**: `/pptagentapi/stream_ppt`
- **任务化流式路由**: `/pptagentapi/stream_ppt/{task_id}`
- **前端页面**: `/workspace/ppt`
- **流式体验**: ThinkingProcess 组件实时显示生成进度
- **文件命名**: 同主题重复生成也会使用唯一文件名，避免覆盖历史产物

历史遗留的 `backend/packages/harness/deerflow/tools/generate_ppt.py` 已移除，避免出现双入口实现和文档漂移。

当用户上传 PDF / Word / Markdown / PPT 文档并要求生成 PPT 时，可以组合使用上述文档处理 skill：
先提取文档文本与表格，再生成结构化摘要，最后把摘要喂给 PPT 规划与逐页生成流程。

当前在 DeerFlow 聊天链路中的适配方式是：
- 前端上传后会把原文件虚拟路径与转换后的 Markdown 虚拟路径一起写入消息元数据
- Lead Agent 会优先读取转换后的 Markdown，而不是直接读取 PDF / DOCX / PPTX 二进制文件；如果用户上传的本身就是 `.md`，则直接读取该文件
- 只有当用户明确要求“根据上传文档生成 PPT”时，Agent 才会强制参考 `document-processor-pdf` / `document-processor-docx` / `document-processor-markdown` / `document-processor-pptx` / `document-summarizer` 完成文档抽取与结构化
- 在这条文档到 PPT 的链路里，主 agent 只负责提炼文档主题、章节和关键事实，不负责提前拍板最终每一页内容；真正的分页规划交给 `generate_ppt`
- 最终通过 `generate_ppt(content=...)` 把文档摘要、章节结构、关键事实和页数约束写入任务载荷，再由流式 PPT 路由读取并执行，避免长文本因 URL 长度受限而被截断
- 如果用户后续基于已生成的 PPT、Markdown 或其他 artifact 继续提问，系统默认把这些产物当作会话上下文来读取，而不是自动再次生成文件
- 只有当用户明确要求“重新生成 PPT”“修改 PPT”“导出成新文件”等操作时，才会把已有 artifact 当作新的生成目标

### 路由架构

```text
用户 → DeerFlow Chat → Lead Agent → generate_ppt Tool
                                    ↓
                            gateway 内部 PPT 流式生成
                                    ↓
                      /workspace/ppt (前端 SSE 订阅)
                                    ↓
                            实时进度 + 缩略图预览
```

### Nginx 路由

- `/` → Next.js 前端
- `/api/*` → DeerFlow Gateway API
- `/api/langgraph/*` → LangGraph Server
- `/pptagentapi/*` → Gateway 暴露的 PPT 流式接口

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
