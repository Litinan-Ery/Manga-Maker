# Manga Maker

**还不能用，以下是需求**

Manga Maker 是一个面向本机单用户的小说漫画化工具。它把 TXT 小说中的一个章节改编为结构化漫画分镜，通过 NovelAI 适配器逐格生成画面，再由本地排版引擎组合为可编辑、可回退、可导出的完整漫画页面。

> 当前状态：**v0.2 离线 Mock 闭环已完成；v0.3 已完成 Wave 3，整体尚未完成。** v0.3 的架构护栏、durable work/outbox/lineage、版式先行、PromptPlan/PromptPackage v2、NovelAI V4 多角色映射、Prompt/GenerationApproval 冻结和 Prompt Inspector 已完成 Mock 验收；候选质检/接受/PageApproval、迁移发布门禁与 Token 感知流水线仍待交付。真实文本模型、NovelAI 付费 smoke 与代表性授权章节生产仍需用户单独批准，不能由 Mock 结果替代。

完整产品需求、数据契约和验收标准见 [PRD.md](PRD.md)，系统边界、NovelAI 接口决策与实施架构见 [TECHNICAL_ARCHITECTURE.md](TECHNICAL_ARCHITECTURE.md)，优先级和实时进度见 [WORK_ITEMS.md](WORK_ITEMS.md)，v0.3 所有权与追踪基线见 [V03_IMPLEMENTATION_BASELINE.md](docs/architecture/V03_IMPLEMENTATION_BASELINE.md)，关键决策的兼容/回滚/删除条件见 [ADR-010-018.md](docs/adr/ADR-010-018.md)，v0.2 P0 的分层证据与未完成真实门禁见 [P0_ACCEPTANCE_REPORT.md](P0_ACCEPTANCE_REPORT.md)。

## 当前可用范围

| 能力 | 状态 | 当前边界 |
|---|---|---|
| 本地 FastAPI + React 应用 | 已实现 | 只监听 loopback；启动器打开一次性本地会话 |
| SQLite 与本机项目工作区 | 已实现 | 迁移、单写者、UUIDv7、安全路径和不可变来源版本 |
| 应用本地加密凭证库 | 已实现 | Argon2id + XChaCha20-Poly1305；支持界面内创建、解锁、锁定和保存凭证 |
| TXT 导入与章节修正 | 已实现 | UTF-8/BOM/GB18030/GBK 候选；支持改名、拆分、合并 |
| SourceAnchor 与 StoryBeat | 已实现 | 本地确定性提取，不调用模型；初始状态为 `unresolved` |
| 文本模型与结构化改编 | 已实现（Mock 验收） | 界面只需 API 链接、模型名称、密钥三项；密钥本地加密保存，同一配置生成分镜、角色/风格设定、固定 Tags 和逐格 Prompt；未做真实调用 |
| CharacterBible、StyleBible 与参考图 | 已实现（Mock 验收） | 从已审批分镜调用当前文本模型草拟；支持编辑、独立审批、影响面板记录，以及经授权确认和安全解码的 PNG/JPEG/WebP 参考图 |
| CharacterTagSet、PromptPlan 与 ProviderExecutionSpec | v0.3 多角色链路已实现（Mock 验收） | 固定角色 Tags 独立版本化审批；PromptPlan v2 保留每角色正负区块、动作、顺序、版式坐标与关系动作；版本化 mapper 生成并冻结 NovelAI V4 base/正负角色 captions、坐标、payload hash；旧 flat prompt 只读且不能创建新 Job |
| Prompt Inspector 与生成审批 | v0.3 已实现（Mock 验收） | 逐格显示结构化字段、固定 Tags、映射对照、脱敏 payload、哈希、影响与调用/成本边界；编辑后必须保存并重新预览，审批与 Job 创建均幂等，尚不包含候选审片 |
| NovelAI 契约、配置与连接测试 | 已实现（Mock 验收） | 固定官方 Swagger 哈希与模型能力；Token 在应用本地加密保存；连接测试须点击触发且只查标签、不出图；未做真实调用 |
| 有界串行生成队列 | v0.3 冻结已实现（Mock 验收） | GenerationApproval 原子冻结 PromptPlan、ProviderExecutionSpec/payload、Layout、CharacterTagSet、模型/mapping/rule、seed、参考图来源和每格候选数；全局单在途、暂停/取消和重启转人工审阅 |
| NovelAI 逐格执行与素材版本 | 已实现（离线 Mock 验收） | 二次明确确认后才执行；执行前先复验全部冻结哈希、再读取凭证，只发送审批时冻结的 payload；固定 host、最多一张 Precise Reference、严格 201 JSON/PNG 校验、有界重试、不可变 `original.png`/规格/provenance；未做真实付费 smoke |
| 本地页面排版与不可变 PageVersion | 已实现 | 16 种分页/条漫模板、黑白或彩色、LTR/RTL/竖向阅读、裁切焦点/缩放、气泡/旁白/音效/页码；后端按页面尺寸规范输出 PNG；修改不访问图像 API |
| 项目可复用素材库 | 已实现 | 收藏已有不可变面板素材，维护角色/道具/场景/面板标签，跨页引用；归档可恢复，不复制图片、不调用图像 API |
| reroll、inpaint 与历史恢复 | 已实现（离线 Mock 验收） | 单格/整页冻结父版本与成本，PNG 蒙版局部重绘，结果创建 AssetVersion + PageVersion；两层人工确认后才可执行；恢复不调用外部服务 |
| 工程包、PNG、PDF、CBZ 导出 | 已实现 | v1.5 工程包包含 v0.3 版式、审批、lineage、ProviderExecutionSpec 与 GenerationApproval；导出在 staging 完整校验后登记，失败不改旧导出 |
| 工程包 dry-run 与恢复 | 已实现 | v1.5 完整 round-trip；保留 v1.4 compatibility reader。恢复会重建 ID 绑定的哈希与快照，并把历史生成审批标为 stale，重新配置凭据、估算和审批后才可再次付费执行。schema 16 迁移前备份与旧主版本回滚演练仍由 MM-044/MM-059 收口 |
| 崩溃恢复与本地完整性检查 | 已实现 | 启动时只做本地 reconciliation；未知计费转人工审阅，半成品保留在恢复边界，不会自动重放付费任务 |
| 导出凭证零泄露扫描 | 已实现 | 解锁后以内存中的真实凭证字节扫描普通文件与 ZIP 条目；命中即失败关闭，旧成功导出不受影响 |
| 跨章节连续性账本 | 已实现 | 按章汇总角色、服装、道具、场景和剧情状态；手工修改追加新版本，并定位未来已审批章节中受影响的分格 |
| 整本预算与逐章生产计划 | 已实现 | 冻结全书页数、分格、调用/成本硬上限并逐章审批；每次人工推进最多创建一个本地章节任务，执行仍需独立二次确认；重启不自动续跑 |

## 本地启动

需要 Python 3.12、uv、Node.js 与 pnpm。首次准备和启动：

```bash
uv sync --python 3.12
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build
uv run python -m backend.app.launcher
```

启动器会选择本机端口并打开浏览器。运行数据默认写入 `~/Library/Application Support/Manga Maker/`，不会写入仓库。当前界面可以管理本地加密凭证、导入与改编章节、审批角色/风格与跨章连续性、配置 NovelAI，冻结整本预算并逐章推进，预检、启动、二次确认执行初次生成或 revision 有界队列，编辑、重绘和恢复漫画页，并创建或恢复四类本地导出。连接测试只查标签；只有点击执行按钮并完成第二次明确确认才会进入图像请求路径。整本“推进”只创建一个本地任务，不替代这次确认。页面文字、格框、裁切、历史恢复和导出均为本机操作，不产生真实费用。

开发验收命令：

```bash
uv run ruff check backend tests
uv run mypy backend
uv run pytest
pnpm --dir frontend test
pnpm --dir frontend build
```

## P0 一页结论

| 项目 | P0 决策 |
|---|---|
| 产品形态 | 仅绑定本机的本地 Web 应用 |
| 用户范围 | 本机单用户，不提供公网访问或多人账户 |
| 输入 | TXT 小说；P0 每次选择一个章节进入改编闭环 |
| 文本改编 | 可配置、支持结构化输出的 LLM |
| 图像生成 | NovelAI Image Generation API |
| 漫画形态 | 黑白分页漫画，左到右、从上到下阅读，简体中文横排 |
| 生产方式 | NovelAI 逐格出图，本地确定格框、对白、旁白、音效和页码 |
| 修改能力 | 修改任意页的脚本、布局、对白与提示词；整页、单格 reroll；蒙版局部重绘 |
| 版本原则 | 新生成不覆盖旧版本，历史版本可比较、恢复和重新设为当前版本 |
| 导出 | 可编辑工程包、逐页 PNG、PDF、CBZ |
| P0 验收单位 | 一个完整章节，而不是整本长篇小说 |

## P0 完整目标流程

```text
导入 TXT
  → 检测编码、章节与正文范围
  → 选择一个章节并确认改编页数预算
  → LLM 生成剧情节拍、场景和分页分格脚本
  → 用户审阅分镜与来源覆盖
  → 文本模型生成角色设定表和风格板
  → 用户编辑、上传参考图并确认设定
  → 文本模型生成角色固定 Tags 与逐格 PromptPackage
  → 本地编译器注入固定 Tags，用户预览并审批最终 Prompt
  → 展示页数、调用数和预计成本
  → 用户明确启动有界生成队列
  → NovelAI 逐格生成画面
  → 本地排版格框、对白、旁白与音效
  → 逐页审阅、修改、reroll 或局部重绘
  → 导出工程包、PNG、PDF、CBZ
```

未经用户确认分镜、角色/画风、页数和成本上限，系统不得发出 NovelAI 图像生成请求。P0 不提供定时任务、导入即生成或无限后台重试。

## P0 目标能力

### TXT 导入与章节选择

- 识别 UTF-8、UTF-8 BOM 和常见中文编码；无法可靠判断时要求用户确认。
- 自动识别常见中文章节标题，并允许手工拆分、合并或调整章节边界。
- 保存源文件哈希、字符偏移和章节版本，使每个剧情节拍都能回到原文核验。
- P0 只把用户明确选择的一个章节发送给文本模型，不上传整本小说。

### 小说改编与分镜

- 文本模型输出场景、剧情节拍、页、格、角色、对白、旁白、音效和视觉提示词。
- 输出必须通过 JSON Schema 校验；格式错误先修复，无法修复时停止并交给用户处理。
- 每个分镜保留原文来源锚点，并标记为直接呈现、合并改编或明确省略。
- 用户可以在出图前修改页数、分格、对白、镜头、节奏和提示词。

### 角色与画风一致性

- 从章节自动草拟角色外观、服装、道具、关系和情绪范围。
- 生成黑白漫画风格板，固定线条、网点、光影、背景密度和禁用元素。
- 支持上传用户拥有使用权的角色图和风格参考图。
- 角色设定表与风格板必须经用户确认后才能进入批量出图。
- 多角色画面不直接叠加多个 Precise Reference 造成特征融合；优先使用多角色提示、分步生成或局部重绘。

### 逐格生成与本地排版

- NovelAI 只负责无对白文字的面板画面；提示词默认要求 `no text`。
- 本地排版器负责格框、留白、对白气泡、旁白框、音效、页码和阅读顺序。
- 默认页面为 2:3 竖版，基准导出尺寸为 2048 × 3072 px。
- 已实现的规范渲染使用固定坐标、固定压缩参数和字体文件哈希；同一 PageVersion 与素材得到相同 PNG 哈希。

### 修改、reroll 与恢复

- **整页 reroll**：基于当前分镜重新生成该页所有面板，形成新的 `PageVersion`。
- **单格 reroll**：只为目标面板创建新的 `AssetVersion`，其他格保持不变。
- **局部重绘**：用户绘制蒙版后调用 inpaint，原图和重绘结果同时保留。
- **脚本修改**：可以修改对白、旁白、镜头、角色状态、提示词、负面提示词和布局。
- **版本恢复**：恢复只切换当前版本指针，不删除后续版本，也不再次调用 API。
- inpaint 蒙版在本机规范化为与父素材同尺寸的 8-bit 黑白 PNG：白色区域允许替换，黑色区域保留；空蒙版和全图蒙版会被拒绝。

## 数据、密钥与版权边界

- 小说、分镜、图片、版本和导出文件默认只保存在本机。
- LLM 与 NovelAI 凭证存入 Manga Maker 自己管理的本地加密凭证库。凭证库位于应用数据目录、独立于项目工作区，使用用户主密码解锁，不写入工程包、SQLite、日志或版本库。
- 日志只记录请求 ID、模型、参数摘要、状态、耗时和成本，不记录密钥或完整小说正文。
- 用户必须拥有输入小说和参考图，或已经获得改编、处理和生成所需授权。
- 用户必须满足所选模型供应商的年龄、账户、订阅和使用条款。
- 产品不抓取公共小说，不绕过登录或访问控制，不自动发布生成漫画，也不代替版权审核。
- NovelAI 接口、模型、费用和条款可能变化；实现与上线前必须重新核对官方文档。

## 目标导出成果

| 成果 | 内容 | 是否包含源小说 |
|---|---|---|
| 可编辑工程包 | 清单、源章节、分镜、设定、提示词、素材、版本与审计记录 | 默认包含，用于本机备份与迁移 |
| PNG | 按阅读顺序编号；默认 2048 × 3072，彩色分页与条漫保留 PageVersion 定义的尺寸 | 不包含 |
| PDF | 页面、文字、页码及基本元数据 | 不包含 |
| CBZ | 与 PNG 一致的页面顺序及漫画元数据 | 不包含 |

面向分享和发布的导出不得包含 API Token、模型服务配置、原文全文、调试日志或废弃版本。

## P0 非目标

- 整本长篇小说的一键无人值守生成。
- 彩色漫画、竖向条漫或从右到左阅读。
- 多人协作、云同步、公开 SaaS、远程账户或团队权限。
- 自动发布到漫画平台、版权授权或商业发行管理。
- 依赖 NovelAI 直接生成包含可读中文对白的完整漫画页。
- EPUB、PDF、DOCX、扫描件或网页小说导入。
- 无限并发、无限自动重试或绕过 NovelAI 服务限制。
- 手机端原生应用。

## 推荐实现基线

以下是 PRD 确定的实现基线；具体完成范围以“当前可用范围”和工单为准：

- **后端**：Python 3.12、FastAPI、SQLite、单写者任务编排器。
- **前端**：React、TypeScript、Vite、支持图层与蒙版的 Canvas 组件。
- **文本模型**：OpenAI-compatible 适配层，模型和端点由用户配置。
- **图像模型**：NovelAI Image Generation API，默认顺序调用。
- **本地合成**：服务端生成稳定导出，前端提供所见即所得编辑。
- **密钥**：应用本地加密凭证库；主密码不落盘，测试环境只允许显式环境变量注入。

## 当前项目结构

```text
Manga Maker/
├── README.md
├── PRD.md
├── TECHNICAL_ARCHITECTURE.md
├── WORK_ITEMS.md            # 优先级、依赖、状态和验收证据
├── contracts/novelai/       # 经审计的官方契约元数据、哈希和更新边界
├── pyproject.toml / uv.lock # Python 依赖与可复现锁文件
├── backend/app/
│   ├── bootstrap/           # typed AppContainer、composition root、installer/lifespan
│   ├── shared_kernel/       # UUIDv7、ArtifactRef、clock、SHA-256 与错误原语
│   ├── platform/            # persistence/durable work/file/security/observability 机制边界
│   ├── modules/             # v0.3 纵向业务模块及版本化公开契约
│   ├── workflows/           # 章节与整本 process manager 边界
│   ├── api/                 # 健康、凭证库、项目和来源 API
│   ├── adaptation/          # Storyboard 契约与文本模型适配器
│   ├── bibles/              # 角色/风格版本、参考图与审批门禁
│   ├── continuity/          # 跨章节状态账本、版本审批与影响分析
│   ├── book/                # 整本预算、逐章审批、串行推进与恢复
│   ├── generation/          # 固定计划、串行执行、参考图预处理与不可变素材
│   ├── ingestion/           # TXT、章节、锚点和剧情节拍
│   ├── pages/               # 16 种分页/条漫模板、PageVersion 与确定性 PNG 渲染
│   ├── library/             # 项目可复用素材引用、元数据与归档恢复
│   ├── exports/             # ExportRevision、四格式输出、工程包校验与恢复
│   └── novelai/             # 能力 profile、错误归一化、Mock 和安全连接测试
├── frontend/                # React/TypeScript 本地操作界面
│   └── src/
│       ├── app/             # shell 与跨 feature workflow
│       ├── features/        # v0.3 feature public entry 与 feature-local client
│       ├── shared/ui/       # 无业务语义的 UI 原语
│       └── generated/api/   # Canonical Schema DTO、hash 与错误边界
└── tests/                   # 后端单元/接口测试
```

现有 `api/`、`adaptation/`、`generation/`、`pages/` 等 v0.2 路径仍由 `LegacyCompatibilityBindings` 显式接入 typed AppContainer，以保持 URL 和恢复语义；它们是迁移缝，不是 v0.3 新代码的 service locator。新模块的真实 adapter 只允许在 `bootstrap/` composition root 组装。

运行时项目结构位于应用数据目录而非仓库，包括 `source/`、`storyboard/`、`bibles/`、`assets/`、`pages/`、`exports/` 和 `audit/`；本地凭证库与项目目录分离。

## 路线图

### P0：单章闭环

- TXT 导入与章节校正。
- 可配置 LLM 的结构化改编与来源覆盖审阅。
- 角色设定、风格板和用户确认门禁。
- NovelAI 逐格生成、有界队列和成本确认。
- 本地分页排版、版本管理、整页/单格 reroll、蒙版重绘。
- 工程包、PNG、PDF、CBZ 导出和完整恢复验收。

### P1：整本小说

- 跨章节剧情与角色状态账本（已实现）。
- 全书页数和成本预算、按章排队与断点续跑（已实现）。
- 跨章节角色/场景一致性检查（已实现基础账本与影响定位）。
- 只重跑失败章节（已实现人工复核后重置）；受影响页面批量重跑仍待高级创作阶段。

### P2：高级创作

- 更多漫画版式、彩色分页、右到左与竖向条漫（已实现）。
- 可复用角色/道具/场景/面板素材库与扩展模板（已实现）；独立镜头库和风格预设仍可继续扩展。
- 人工审稿协作、批注与可选发布辅助。
- 在重新核对供应商条款后评估受控并发。

## 官方参考

- [NovelAI Scripting Introduction](https://docs.novelai.net/en/scripting/introduction/)
- [NovelAI Scripting Generation API](https://docs.novelai.net/en/scripting/generation-api/)
- [NovelAI Primary API](https://api.novelai.net/docs)
- [NovelAI Image Generation API](https://image.novelai.net/docs/index.html)
- [NovelAI Text Generation API](https://text.novelai.net/docs/index.html)
- [NovelAI Image Generation](https://docs.novelai.net/en/image/)
- [NovelAI Multi-Character Prompting](https://docs.novelai.net/en/image/multiplecharacters/)
- [NovelAI Precise Reference](https://docs.novelai.net/en/image/precisereference/)
- [NovelAI Inpaint](https://docs.novelai.net/en/image/inpaint/)
- [NovelAI Terms of Service](https://novelai.net/terms)
