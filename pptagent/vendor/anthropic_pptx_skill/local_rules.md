# Local PPTX Generation Rules

这是在 Anthropic 官方 PPTX skill 基础上的本地增强规则。
生成 PptxGenJS 代码时，必须同时遵守官方 skill 与本规则。

---

## 1. Hard Constraints（硬规则，必须遵守）

### 颜色写法
- ❌ `color: "#FF0000"`
- ✅ `color: "FF0000"`
- 原因：`#` 号会导致生成的 PPTX 文件损坏或样式异常

### 透明度写法
- ❌ 在 hex 里编码透明度：`"00000020"`
- ✅ 使用独立属性：
  ```javascript
  shadow: { type: "outer", color: "000000", opacity: 0.12 }
  ```
- 原因：8 位 hex 颜色会导致文件损坏

### 项目符号写法
- ❌ `"• 第一条"`
- ✅
  ```javascript
  slide.addText([
    { text: "第一条", options: { bullet: true, breakLine: true } },
    { text: "第二条", options: { bullet: true } }
  ], {...})
  ```
- 原因：unicode bullet 会造成双项目符号，观感差且不规范

### shadow 对象复用
- ❌ 复用同一个 shadow 对象
  ```javascript
  const shadow = { type: "outer", blur: 6, offset: 2, color: "000000", opacity: 0.15 };
  slide.addShape(..., { shadow });
  slide.addShape(..., { shadow });
  ```
- ✅ 每次用工厂函数生成新对象
  ```javascript
  const makeShadow = () => ({ type: "outer", blur: 6, offset: 2, color: "000000", opacity: 0.15 });
  slide.addShape(..., { shadow: makeShadow() });
  slide.addShape(..., { shadow: makeShadow() });
  ```
- 原因：PptxGenJS 会原地修改对象，复用会导致第二次调用异常

### 形状选择
- ❌ `ROUNDED_RECTANGLE + 矩形叠加装饰边`
- ✅ 统一使用 `RECTANGLE`
- 原因：圆角矩形无法和矩形边缘装饰条严密贴合，会露出圆角，视觉不干净

### 正文对齐
- ❌ 正文居中
- ✅ 正文必须左对齐，只有封面主标题 / closing 标题 / stat-callout 数字允许居中

### 标题装饰线
- ❌ 标题下方紧跟装饰横线
- ✅ 用空白、背景分区、边框色块、侧边色带替代
- 原因：这是 AI 幻灯片最强烈的反模式之一

---

## 2. Visual Design Principles（软规则，给原则不给代码）

### 配色原则
- 根据演讲主题自主选择配色，不得每次都使用蓝色系
- 主色占视觉比重 60-70%，搭配 1-2 个辅色和 1 个高亮点缀色
- 封面页和结尾页必须使用深色背景（建议明度 < 40%）
- 内容页使用浅色背景，形成“深-浅-浅…-深”的三明治结构

### 色系方向参考（根据主题自主取色）
- 科技 / 区块链 / AI → 深海蓝 / 午夜紫
- 商业 / 金融 → 墨绿 / 炭灰
- 医疗 / 健康 → 青绿 / 白
- 文化 / 创意 → 赤陶 / 珊瑚
- 建筑 / 极简 → 炭灰 / 米白
- 教育 / 活力 → 珊瑚 / 金黄

### 每页必须包含的元素
- 背景色（必须有）
- 至少一个非文字视觉元素：addShape 色块、装饰条、图标圆形、流程卡片等
- 标题下方禁止紧跟装饰横线
- 正文文字左对齐，只有主标题允许居中

### 间距与密度原则
- 保持 0.5" 左右的外边距
- 内容块之间保持 0.3-0.5" 间距
- 不要把所有内容塞满整页，留出呼吸空间
- 不要让元素贴边或过于拥挤

### 字号规范
- 幻灯片标题：36-44pt，加粗
- 小节标题：20-24pt，加粗
- 正文文字：14-16pt
- 说明文字 / 脚注：10-12pt，可用浅色

### 视觉重心原则
- 每页都要有一个明显视觉重心：大数字、卡片组、圆形图标区、半屏色块等
- 不要每页都平均用力，要有主次关系
- 一页中最好只有一个 dominant visual focal point

---

## 3. Layout Vocabulary（布局词汇表）

生成每页时，根据内容性质自主选择以下布局之一：

| 布局名称 | 适用场景 | 关键特征 |
|----------|----------|----------|
| hero-cover | 封面、章节过渡页 | 全深色背景，超大标题，副标题，底部装饰色带 |
| stat-callout | 有核心数据 / 指标的页面 | 60-72pt 大数字 + 小说明标签，搭配辅助色块 |
| two-column | 对比、优劣势、左文右图 | 左右各占约 50%，右侧用色块矩形代替图片 |
| icon-row | 3-4 条并列要点 | 每条：圆形色块图标 + 加粗标题 + 描述文字 |
| card-grid | 4-6 个同级内容块 | 2×2 或 2×3 等间距卡片，矩形背景，可带 shadow |
| timeline | 流程、步骤、历史 | 横向或纵向色块 + 连接线 + 编号 |
| closing | 结尾页 | 深色背景，居中大字，联系方式或 Q&A 提示 |

### 每种布局的视觉倾向
- **hero-cover**：强视觉冲击，少文字，大留白，深底浅字
- **stat-callout**：数字是主角，说明是配角
- **two-column**：适合“左说明，右图示”或“左概念，右实例”
- **icon-row**：适合三到四个平级卖点，不适合长段解释
- **card-grid**：适合分类信息、应用场景、能力模块
- **timeline**：适合历史、发展、流程、步骤
- **closing**：简洁、强收束，不能像普通内容页

---

## 4. Selection Rules（布局选择规则）

- 禁止连续两页使用相同布局
- 同一份 PPT 中 `icon-row` 不超过 2 次，避免视觉疲劳
- 每份 PPT 必须覆盖至少 4 种不同布局
- 封面必须使用 `hero-cover`
- 结尾必须使用 `closing`
- 若页面包含关键数据，优先考虑 `stat-callout`
- 若页面是对比类内容，优先考虑 `two-column`
- 若页面是多个平级模块，优先考虑 `card-grid` 或 `icon-row`
- 若页面是发展、步骤、阶段演进，优先考虑 `timeline`

---

## 5. Style Mapping Rules（风格映射规则）

当用户显式指定 `style` 时，必须优先遵守下面的映射，不要自行切换到其他风格。

### style = auto
- 根据主题自行选择最匹配的色系与视觉母题

### style = executive
- 优先色系：Midnight Executive / Charcoal Minimal
- 倾向布局：hero-cover, stat-callout, two-column, closing
- 视觉感觉：商务、稳重、深色、高对比、少量强装饰

### style = ocean
- 优先色系：Ocean Gradient / Teal Trust
- 倾向布局：hero-cover, two-column, timeline, closing
- 视觉感觉：科技感、流动感、冷色调、清爽

### style = minimal
- 优先色系：Charcoal Minimal
- 倾向布局：hero-cover, stat-callout, card-grid, closing
- 视觉感觉：极简、留白更多、文字更克制、装饰更少但更精准

### style = coral
- 优先色系：Coral Energy
- 倾向布局：hero-cover, icon-row, card-grid, closing
- 视觉感觉：活力、教育、创意、年轻化

### style = terracotta
- 优先色系：Warm Terracotta
- 倾向布局：hero-cover, card-grid, timeline, closing
- 视觉感觉：温暖、人文、叙事感

### style = teal
- 优先色系：Teal Trust
- 倾向布局：hero-cover, icon-row, two-column, closing
- 视觉感觉：清洁、现代、健康、可信赖

### style = forest
- 优先色系：Forest & Moss
- 倾向布局：hero-cover, card-grid, timeline, closing
- 视觉感觉：自然、环保、可持续

### style = berry
- 优先色系：Berry & Cream
- 倾向布局：hero-cover, card-grid, icon-row, closing
- 视觉感觉：柔和、时尚、生活方式

### style = cherry
- 优先色系：Cherry Bold
- 倾向布局：hero-cover, stat-callout, two-column, closing
- 视觉感觉：强对比、警示感、强调性强

### 额外强约束
- 当 style 不是 auto 时，至少 80% 的页面视觉风格必须与该 style 的色系和布局倾向一致
- 不允许出现“用户指定 minimal，但整体仍是 ocean/executive 风格”的偏移
- 当 style 明确指定时，封面页和结尾页必须明显体现该风格的主色系

---

## 6. Preferred Generation Heuristics（生成启发式）

- 先决定整份 PPT 的视觉母题，再决定单页布局
- 视觉母题例子：
  - 左侧色带 + 圆形图标
  - 顶部深色色块 + 卡片内容区
  - 大号数字 + 细说明文字
  - 卡片背景 + 轻阴影
- 同一份 PPT 中要保持母题一致，而不是每页完全换风格

---

## 6. Quality Bar（质量门槛）

生成的 PPT 应满足：
- 打开后看起来像设计过，而不是默认模板改字
- 至少有明显的层次关系：背景 / 主区 / 次区 / 装饰
- 不出现“标题 + 横线 + 项目符号列表”这种典型 AI 模式
- 每页的布局都能一眼看出用途不同
- 配色和主题之间要有明显关联，而不是通用蓝色套壳
