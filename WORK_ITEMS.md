# Manga Maker 开发工单

| 项目 | 内容 |
|---|---|
| 版本 | v0.3 |
| 日期 | 2026-08-13 |
| 状态 | v0.2 工单已完成；Storyboard 1.1 逐页分镜政策 MM-068→MM-071 已完成；恢复 MM-038 候选闭环开发 |
| 拆票代码基线 | `main@40f2cb9`；当前 v0.3 `PRD.md` 与 `TECHNICAL_ARCHITECTURE.md` 为需求来源，不代表代码已交付 |
| 产品范围 | 以 README、PRD、TECHNICAL_ARCHITECTURE 为准 |

## 优先级定义

| 优先级 | 含义 | 调度规则 |
|---|---|---|
| P0 / Blocker | v0.3 P0 主链、架构护栏或安全发布的必要能力 | 先基础、再主链、后发版门禁；同一依赖层可并行 |
| P0 / Release Gate | 不一定新增产品能力，但缺少证据时不得宣称 v0.3 P0 完成 | 实现工单完成后执行；真实付费调用仍需用户单独授权 |
| P1 / High | V03-P1-01 分层、Token 感知文本流水线 | v0.3 P0 的架构、Mock 产品与恢复门禁通过后启动；真实 P1 验收仍单独等待用户授权，不反向阻塞 P0 |
| P2 / Medium | 后续整本出版与协作增强 | P1 验收后重新排期 |
| P3 / Low | 受控并发、更多供应商或高级创作实验 | 只有证据和条款边界明确后评估 |

状态统一为 `Todo / In Progress / Blocked / Done`。`Done` 必须同时满足代码、迁移、测试、文档和验收证据，只有目录骨架、方向性实现或 Mock 单点成功不得标记完成。

“产品 P0/P1”表示产品范围；“工单 P0/P1”表示开发调度优先级。本文优先级默认指开发调度。

## v0.3 Epic：版式先行、结构化多角色与候选审片

### 已核对的当前状态

| 代码面 | v0.2 当前状态 | v0.3 差距 |
|---|---|---|
| 应用组装 | typed `AppContainer`、module installer 与 compatibility seam 已建立 | 旧 v0.2 service 仍需按后续工单收口 |
| 数据库 | schema 32；模块 migration/table ownership、durable work/outbox、lineage、layout、Prompt/GenerationApproval、生成核验调用审计与文本模型备注迁移已落地；Prompt 审批幂等键按审批对象隔离 | review/composition/exporting 新表族与 v0.2→v0.3 迁移仍待 Wave 4/5 |
| 后端能力 | 公开模块契约、版式门禁、结构化多角色 mapper、审批冻结和发送前复验已实现 | 缺少候选/质检/接受、PageApproval 与正式导出门禁 |
| 前端 | feature boundary、Layout Workbench 与 Prompt Inspector 已实现 | 缺少 Candidate Review、页面批准和真实导出预检状态 |
| 验收 | AC-09、AC-10 已完成离线 Mock 工单验收；《沙王》真实 V5 Full 零 Anlas 双角色页、12 页授权章节与 reroll 证据已完成；真实文本模型和 NovelAI 付费调用为 0 | AC-11/12、v0.3 迁移/恢复、多候选接受/PageApproval、外部文本与付费路径仍未完成 |

以上测试数字来自 v0.2 已归档完成证据，本次拆票不把它们当作重新执行后的结果。开发开始时先由 MM-024 复跑并固定基线。

### 目标代码落点与统一验证

| 工单组 | 主要目标路径 | 需要保护的现有兼容边界 |
|---|---|---|
| MM-023～MM-027、MM-052～MM-053 | `backend/app/bootstrap/`、`backend/app/shared_kernel/`、`backend/app/modules/*/public.py`、`frontend/src/app/`、`frontend/src/features/`、`tests/architecture/`、`tests/contracts/` | `backend/app/main.py`、`backend/app/api/`、`frontend/src/api.ts` |
| MM-028～MM-030、MM-054～MM-055 | `backend/app/platform/durable_work/`、`backend/app/modules/lineage/`、所属模块 `migrations/`、`tests/recovery/` | `backend/app/database.py`、`backend/app/recovery.py`、`backend/app/generation/queue.py` |
| MM-031～MM-034、MM-056 | `backend/app/modules/layout/`、`frontend/src/features/layout/`、`tests/modules/layout/` | `backend/app/pages/templates.py`、`backend/app/pages/models.py`、`frontend/src/PageComposer.tsx` |
| MM-035～MM-037、MM-057 | `backend/app/modules/prompting/`、`backend/app/modules/production/adapters/novelai/`、`frontend/src/features/prompting/`、`tests/modules/prompting/`、`tests/contracts/novelai/` | `backend/app/prompting/`、`backend/app/novelai/`、`backend/app/generation/executor.py`、`frontend/src/PromptWorkbench.tsx` |
| MM-038～MM-043、MM-058、MM-062、MM-064～MM-065 | `backend/app/modules/review/`、`backend/app/modules/composition/`、`backend/app/modules/exporting/`、`frontend/src/features/review/`、`frontend/src/features/exporting/` | `backend/app/pages/`、`backend/app/exports/`、`frontend/src/PageComposer.tsx`、`frontend/src/ExportCenter.tsx` |
| MM-044～MM-046、MM-059～MM-060、MM-063 | 各模块 `migrations/`、`tests/e2e/`、`tests/recovery/`、验收报告 | schema 16、工程包 v1.4、`P0_ACCEPTANCE_REPORT.md` |
| MM-047～MM-051、MM-061 | `backend/app/modules/text_execution/`、`backend/app/workflows/chapter_production/`、`frontend/src/features/adaptation/`、`tests/modules/text_execution/` | `backend/app/adaptation/text_model.py`、`backend/app/adaptation/service.py`、`frontend/src/StoryboardWorkbench.tsx` |
| MM-068～MM-071 | `backend/app/adaptation/`、`backend/app/modules/adaptation/`、`backend/app/pages/`、`frontend/src/StoryboardWorkbench.tsx`、`frontend/src/features/layout/`、`tests/` | Storyboard 1.0 历史只读、现有 NovelAI V5 fixture/验收改动、已批准 PageLayoutDraft 与 Panel ID 一一对应关系 |

每张后端工单至少运行其模块/契约定向测试，并在合并前运行：

```bash
uv run ruff check backend tests
uv run mypy backend
uv run pytest -q
```

每张前端工单至少运行对应 `*.test.tsx`，并在合并前运行：

```bash
pnpm --dir frontend test -- --run
pnpm --dir frontend build
```

MM-027 建成新测试目录后，P0 Release Gate 还必须运行：

```bash
uv run pytest -q tests/architecture tests/contracts tests/modules tests/workflows tests/recovery tests/e2e
```

如果定向测试路径尚未由上游工单创建，当前工单必须创建它；不得以“全量测试里间接覆盖”为理由省略模块级验收。

### PRD 追踪矩阵

| PRD 项目 | 对应需求与验收 | 主工单 | 完成判定 |
|---|---|---|---|
| 共享架构底座 | PRD §12、NFR-01～06、AC-01、架构 DoD 1～10/16 | MM-023～MM-030、MM-052～MM-055 | 新模块有公开契约、表所有者、架构门禁、durable work/outbox、SSE replay 和最小失效图；v0.2 行为仍通过 |
| V03-P0-01 版式先行 | FR-06/10/11/13、FR-20、AC-09 | MM-031～MM-034、MM-056 | 每个 PromptPackage/GenerationSpec 之前存在有效 LayoutApproval；尺寸选择确定且修改只失效必要下游 |
| V03-P0-02 多角色契约 | FR-07/10/11/12、FR-21、AC-10 | MM-035～MM-037、MM-057 | 单/双/三角色正负区块、顺序、坐标、动作/关系和固定 Tags 可验证；禁止扁平回退 |
| V03-P0-03 候选闭环 | FR-11/14/17/18、FR-22、AC-11 | MM-038～MM-043、MM-058、MM-062、MM-064～MM-065 | 供应商成功只创建候选；完整质量清单不自动接受；只有有效 accepted 候选和 PageApproval 可进入正式导出 |
| v0.3 P0 迁移与证明 | AC-01～11、PRD DoD 1～9/11～14、架构 DoD 1～14/16～19 | MM-044～MM-046、MM-059～MM-060、MM-063 | v0.2 工程安全迁移、回滚演练、Mock 全链和崩溃恢复通过；真实调用与授权章节另经用户批准完成 |
| V03-P1-01 Token 感知 | FR-05、FR-23、AC-12 | MM-047～MM-051、MM-061 | 长章节按 stage/shard 可恢复运行；硬约束不被静默裁剪，失败只重跑最小范围 |
| 逐页分镜与普通页 3–6 格 | FR-04/06、AC-04；Storyboard 1.1、`page_type`、逐页非空与 Layout 兼容政策 | MM-068～MM-071 | 新产物逐页有合法页型和分镜；普通页 3–6 格、特殊页 1–6 格；旧 1.0 只读，违规阻止审批/Layout，Panel 与叶子 Frame 一一对应 |

### 范围锁定与不可破坏项

- 保持本地模块化单体、一个 SQLite、一个发布物；不拆微服务，不引入 Redis、Kafka 或外部消息代理。
- v0.2 不可变素材、页面和导出继续可读；迁移只追加，不重写历史 migration 或覆盖旧文件。
- 新路径禁止继续扩张 `request.app.state.*`、全局 `services/models/repositories` 和 `frontend/src/api.ts`；旧路径只能作为有删除条件的 compatibility seam。
- 文本模型与 NovelAI 密钥继续只存在于应用本地加密凭证库及解锁后的短期内存，不进入 SQLite、日志、工程包或前端持久存储。
- 不改变“用户明确触发、有界预算、默认串行、重启不自动续跑付费调用”的边界。
- 自动质量规则只产生证据，不能代替人工接受或 PageApproval；生成成功、渲染成功和 Job completed 都不等于发布批准。
- 真实文本模型、NovelAI 付费 smoke、授权章节生产、永久删除、发布和外部部署不因工单存在而自动获得授权。

### v0.3 执行总览

| 波次 | 工单 | 优先级 | 状态 | 预计 | 直接依赖 |
|---:|---|---|---|---:|---|
| 0 | MM-023 v0.3 契约、ADR 与文档基线 | P0 | Done | 1–2d | 无 |
| 0 | MM-052 v0.3 Canonical Schema 与契约 fixture | P0 | Done | 1–2d | MM-023 |
| 0 | MM-024 v0.2 行为刻画与迁移 fixture | P0 | Done | 1–2d | MM-023、MM-052 |
| 0 | MM-025 模块骨架、shared kernel 与 typed AppContainer | P0 | Done | 2–3d | MM-024 |
| 0 | MM-026 后端公开 facade 与路由注入 | P0 | Done | 1–2d | MM-025、MM-052 |
| 0 | MM-053 前端 feature 边界与 feature-local client | P0 | Done | 1–2d | MM-025、MM-052 |
| 0 | MM-027 架构适应性函数与表/迁移所有权 | P0 | Done | 2–3d | MM-025、MM-026、MM-053 |
| 1 | MM-028 Durable Work 存储与幂等事务 | P0 | Done | 1–2d | MM-027 |
| 1 | MM-054 Durable Worker、租约与重试策略 | P0 | Done | 1–2d | MM-028 |
| 1 | MM-029 Outbox 与 SSE replay | P0 | Done | 1–2d | MM-028 |
| 1 | MM-055 Durable Work/Outbox 重启恢复 | P0 | Done | 1–2d | MM-029、MM-054 |
| 1 | MM-030 Artifact Dependency Graph 与最小失效 | P0 | Done | 2–3d | MM-027、MM-029 |
| 2 | MM-031 PageLayoutDraft 领域、Schema 与持久化 | P0 | Done | 2–3d | MM-027、MM-030 |
| 2 | MM-032 LayoutValidator 与 DimensionSelector | P0 | Done | 1–2d | MM-031 |
| 2 | MM-033 Layout API 与审批命令 | P0 | Done | 1–2d | MM-026、MM-031、MM-032 |
| 2 | MM-056 Layout Workbench 与影响预览 | P0 | Done | 1–2d | MM-053、MM-031、MM-032 |
| 2 | MM-034 版式生成门禁、冻结与旧入口收口 | P0 | Done | 2–3d | MM-030、MM-033 |
| 3 | MM-035 PromptPlan v2 与固定 Tags 结构化编译 | P0 | Done | 2–3d | MM-034 |
| 3 | MM-036 ProviderExecutionSpec 与 NovelAI 多角色映射 | P0 | Done | 2–3d | MM-035 |
| 3 | MM-037 Prompt 审批与 Job 冻结 | P0 | Done | 1–2d | MM-033、MM-036 |
| 3 | MM-057 Prompt Inspector 与脱敏载荷预览 | P0 | Done | 1–2d | MM-053、MM-035、MM-036 |
| 3 | MM-066 Opus 零 Anlas 有界生成与 10 页回归 | P0 | Done | 1–2d | MM-037、MM-057 |
| 6 | MM-067 整本重试历史调用与成本累计 | P1 | Done | 1–2d | MM-066、MM-102 |
| 2A | MM-068 Storyboard 1.1 契约、页面政策与模型修复 | P0 | Done | 1–2d | MM-009、MM-031 |
| 2A | MM-069 分镜校验/审批、1.0 升级与 Layout 门禁 | P0 | Done | 1–2d | MM-068、MM-033、MM-034 |
| 2A | MM-070 改编工作台页型状态与模板兼容体验 | P0 | Done | 1–2d | MM-053、MM-069 |
| 2A | MM-071 Storyboard 1.1 回归、迁移与 E2E 门禁 | P0 / Release Gate | Done | 1–2d | MM-068～MM-070 |
| 4 | MM-038 PanelCandidateSet 与生成结果接入 | P0 | Todo | 2–3d | MM-030、MM-037、MM-055 |
| 4 | MM-039 QualityRun/Finding 框架与确定性规则 | P0 | Todo | 1–2d | MM-032、MM-038 |
| 4 | MM-062 视觉质量检查清单与金标 fixture | P0 | Todo | 1–2d | MM-039 |
| 4 | MM-040 ReviewDecision 状态机与 API | P0 | Todo | 1–2d | MM-039 |
| 4 | MM-064 候选审片台与质量证据 UI | P0 | Todo | 1–2d | MM-040、MM-053、MM-062 |
| 4 | MM-041 reroll/inpaint 候选回环 | P0 | Todo | 1–2d | MM-040 |
| 4 | MM-042 PageApproval 与页面状态机 | P0 | Todo | 1–2d | MM-040、MM-062 |
| 4 | MM-065 页面批准与状态导航 UI | P0 | Todo | 1–2d | MM-042、MM-053、MM-064 |
| 4 | MM-043 ExportPreflight 引擎与 TOCTOU 门禁 | P0 | Todo | 1–2d | MM-042 |
| 4 | MM-058 Export Center 预检交互与问题定位 | P0 | Todo | 1–2d | MM-043、MM-053、MM-065 |
| 5 | MM-044 v0.2→v0.3 数据迁移与只读恢复 | P0 | Todo | 1–2d | MM-034、MM-037、MM-043 |
| 5 | MM-059 工程包升级、恢复与 v0.2 回滚演练 | P0 | In Progress | 1–2d | MM-044 |
| 5 | MM-045 P0 架构与契约测试门禁 | P0 / Release Gate | Todo | 1–2d | MM-027、MM-036、MM-039、MM-043、MM-044 |
| 5 | MM-060 P0 Mock 产品 E2E 与 v0.2 回归 | P0 / Release Gate | Todo | 1–2d | MM-034、MM-037、MM-041、MM-056、MM-057、MM-058、MM-059、MM-062、MM-064、MM-065 |
| 5 | MM-063 崩溃注入、恢复与未知结果矩阵 | P0 / Release Gate | Todo | 1–2d | MM-030、MM-043、MM-055、MM-059 |
| 5 | MM-046 真实 smoke、授权章节与 v0.3 P0 报告 | P0 / Release Gate | Blocked | 1–2d | MM-045、MM-060、MM-063、用户对服务/预算/素材的单独授权 |
| 6 | MM-047 ModelCapabilitySnapshot 与 TokenBudget | P1 | Todo | 2–3d | MM-060、MM-063 |
| 6 | MM-048 TextStageRun、checkpoint 与 durable 执行 | P1 | Todo | 2–3d | MM-029、MM-047、MM-054 |
| 6 | MM-049 Stage DAG、shard、缓存与最小重跑 | P1 | Todo | 2–3d | MM-030、MM-048 |
| 6 | MM-050 分层改编/设定/Prompt 后端接线 | P1 | Todo | 1–2d | MM-049 |
| 6 | MM-061 TextStage 阶段 UI 与 Token 报告 | P1 | Todo | 1–2d | MM-049、MM-053 |
| 6 | MM-051 长章节与真实文本流水线验收 | P1 | Blocked | 1–2d | MM-050、MM-061、用户对真实文本模型与授权章节的单独授权 |

工期是单个熟悉仓库的工程师对一张可独立评审工单的粗估，不包含等待付费服务授权、供应商响应或人工审片的时间。超过 3 个开发日仍未达到验收时，应拆分而不是扩大当前工单。

### 依赖图

```mermaid
flowchart LR
    A["Wave 0<br/>契约与架构护栏"] --> B["Wave 1<br/>Durable Work / Outbox / Lineage"]
    B --> C["Wave 2<br/>版式先行"]
    C --> C2["Wave 2A<br/>Storyboard 1.1 逐页政策"]
    C2 --> D["Wave 3<br/>结构化多角色"]
    D --> E["Wave 4<br/>候选 / 质检 / 接受 / 导出"]
    E --> F["Wave 5<br/>迁移、破坏测试、真实 P0 证明"]
    F --> G["Wave 6<br/>Token 感知文本流水线"]
```

MM-068～MM-071 因修改 Storyboard 公共契约，按编号串行并优先于新的候选闭环开发；完成后恢复 MM-038。MM-033/MM-056、MM-037/MM-057、MM-040/MM-064、MM-042/MM-065、MM-050/MM-061 可在共享契约冻结后分别开发后端与 UI；MM-041 只依赖 ReviewDecision 后端，不等待审片 UI。MM-047～MM-050 只依赖 Mock P0 门禁 MM-060/MM-063，不等待付费验收 MM-046；真实 P1 仍由 MM-051 单独阻断。不得为了并行而复制状态或绕开公开契约。

## v0.3 P0 / 架构与可靠性底座

### MM-023 v0.3 契约、ADR 与文档基线

- 优先级 / 状态 / 规模：`P0 / Done / 1–2d`。
- 对应：PRD V03-P0-01～03、V03-P1-01、AC-01；技术架构 §5～7、§17、§20。
- 目标：把新版 PRD 和技术架构转成无自相矛盾、可追踪的实施边界。
- 交付：模块所有权表、依赖白名单、表/目录所有权草案、ADR-010～018、v0.2→v0.3 fixture 清单、官方 Swagger/mapping 快照元数据和文档一致性报告。
- 验收：
  - README、PRD、技术架构和本文对“已实现/未实现”、P0/P1、术语和真实调用状态一致；
  - PRD 每个 FR-20～23、AC-09～12 和架构 DoD 条目能反查至少一张实现/验收工单；
  - 每个 v0.3 对象、表和工作区目录有唯一 owning module，跨模块依赖符合白名单；
  - ADR 明确选择、拒绝方案、兼容期、回滚路径和删除条件，不把方向性文档写成已实现；
  - 文档敏感信息扫描为 0，Swagger URL/hash/mapping version 可追溯。
- 不包含：Canonical Schema/fixture 实现、模块搬迁、数据库迁移或真实模型调用。

完成证据（2026-08-13）：`docs/architecture/V03_IMPLEMENTATION_BASELINE.md`、`docs/adr/ADR-010-018.md` 与 `tests/test_v03_document_baseline.py`；定向测试 4 项通过，Ruff 通过。真实服务未调用。

### MM-052 v0.3 Canonical Schema 与契约 fixture

- 优先级 / 状态 / 规模：`P0 / Done / 1–2d`；依赖 MM-023。
- 对应：PRD §11、NFR-06、AC-09～12；技术架构 §5.6、§6、§16.2。
- 目标：把 v0.3 核心对象冻结为后端、前端、迁移和测试共用的单一契约基线。
- 交付：PageLayoutDraft/PromptPlan/ProviderExecutionSpec/Candidate/Finding/Review/PageApproval/TextStageRun JSON Schema 与 Pydantic DTO；canonical JSON/hash 规则；consumer fixture。
- 验收：
  - 每个对象声明 schema version、稳定 ID/version/hash、必填字段、枚举和向后兼容策略；
  - PageLayoutDraft fixture 包含 page profile、frame hierarchy、shot scale、阅读顺序、焦点、人物位置、文字和 crop-safe zone；
  - PromptPlan 单/双/三角色 fixture 包含独立正负区块、order/center、每角色动作和跨角色 relationship action；
  - Review fixture 覆盖两个候选、blocker/warning/info、接受/拒绝/待修复、Finding 豁免和 stale PageApproval；
  - canonical bytes 在 Python/TypeScript fixture 中得到同一 SHA-256；敏感字段和绝对路径 Schema 校验失败。
- 不包含：业务状态机、数据库表、UI 或供应商请求。

完成证据（2026-08-13）：`backend/app/modules/*/contracts.py`、`contracts/schemas/v0.3/`、`contracts/fixtures/v0.3/`、`tests/contracts/test_v03_contracts.py` 与 `frontend/src/generated/api/v03Canonical.test.ts`；后端 20 项契约测试、前端 2 项跨端哈希测试、Ruff、Mypy 和 production build 通过。真实服务未调用。

### MM-024 v0.2 行为刻画与迁移 fixture

- 优先级 / 状态 / 规模：`P0 / Done / 1–2d`；依赖 MM-023、MM-052。
- 对应：AC-02～08 的继承回归；技术架构 §5.8、§16.6。
- 目标：先锁定重构前真实行为，避免“目录升级”破坏已完成闭环。
- 交付：`main@40f2cb9` 基线测试报告、v0.2 schema 16 数据库 fixture、工程包 v1.4 fixture、关键 HTTP/错误/审计 characterization tests。
- 验收：
  - 复跑后端测试、前端测试、Ruff、Mypy 和 production build，并记录精确数量与结果；
  - 固定 adaptation、bibles、prompting、generation、pages、exports、recovery 的成功、关键失败、幂等与 revision 冲突行为；
  - fixture 覆盖单角色、双角色 flat prompt、历史 PageVersion、AssetVersion、reroll/inpaint、工程包恢复；
  - 测试不依赖真实文本模型或 NovelAI，不读取真实凭证；
  - 任何基线失败先单独修复，不能在架构工单中顺手改变产品语义。
- 不包含：新模块或新表。

完成证据（2026-08-13）：`tests/fixtures/v0.2/`、`tests/characterization/test_v02_baseline_fixtures.py` 与 `docs/acceptance/V02_BASELINE_REPORT.md`；后端 137 项、前端 22 项、Ruff、Mypy 和 production build 通过。fixture 仅使用 Stub/Mock，无真实凭证和真实服务调用。

### MM-025 模块骨架、shared kernel 与 typed AppContainer

- 优先级 / 状态 / 规模：`P0 / Done / 2–3d`；依赖 MM-024。
- 对应：PRD §12；技术架构 §5.2～5.5、§5.8、ADR-016/018。
- 目标：建立纵向模块的真实组装边界，同时让 v0.2 路径原样运行。
- 交付：`bootstrap/`、`shared_kernel/`、`platform/`、`modules/`、`workflows/` 骨架；typed `AppContainer`；clock/ID/hash/error/ArtifactRef 原语；module installer 生命周期。
- 验收：
  - `main.py` 只创建容器、安装模块和路由，不继续新增具体 service 构造逻辑；
  - AppContainer 显式声明依赖，真实 adapter 只在 composition root 创建；
  - shared kernel 不出现业务 DTO、BaseService、GenericRepository 或可变全局状态；
  - 旧 service 通过明确命名的 compatibility binding 接入，MM-024 行为测试全部保持；
  - `/health`、启动 reconciliation、generation executor shutdown 和 vault lock 生命周期不退化。
- 不包含：一次性移动全部旧 service、改变数据库 schema 或前端 UI。

完成证据（2026-08-13）：`backend/app/bootstrap/`、`backend/app/shared_kernel/`、`backend/app/platform/`、`backend/app/modules/`、`backend/app/workflows/` 与 `tests/bootstrap/test_app_container.py`；Ruff、Mypy、全量后端测试通过，旧 URL、恢复与生命周期测试保持。

### MM-026 后端公开 facade 与路由注入

- 优先级 / 状态 / 规模：`P0 / Done / 1–2d`；依赖 MM-025、MM-052。
- 对应：技术架构 §5.5～5.8、架构 DoD 4～6。
- 目标：让后端新能力只能通过最小公开契约接入，停止继续扩大旧的 `app.state` 入口。
- 交付：各模块 `public.py`/`contracts.py`、legacy facade adapter、FastAPI `Depends` provider 和一个旧用例的垂直迁移样例。
- 验收：
  - v0.3 新 route 不读取 `request.app.state.*`，不导入其他模块内部 service/repository；
  - 公开 Command/Query/Snapshot/Event DTO 为不可变、版本化对象，不暴露 SQLite row、FastAPI Request 或供应商 payload；
  - 现有 route 可继续走 compatibility provider，行为与 URL 不变；
  - 至少一个旧用例通过 facade + `Depends` 运行，成功、错误、幂等和 revision 冲突与 MM-024 一致；
  - compatibility provider 有精确 allowlist、owner 和删除条件，不成为新的 service locator。
- 不包含：前端目录迁移、删除所有 legacy 入口或生成完整 OpenAPI client。

完成证据（2026-08-13）：各模块 `contracts.py`/`public.py`、`backend/app/bootstrap/dependencies.py`、`modules/composition/adapters/legacy.py` 与 `tests/modules/composition/test_legacy_facade.py`；页面 revision 的成功、错误、幂等、revision conflict 和零外部调用保持，Ruff、Mypy、全量后端测试通过。

### MM-053 前端 feature 边界与 feature-local client

- 优先级 / 状态 / 规模：`P0 / Done / 1–2d`；依赖 MM-025、MM-052。
- 对应：技术架构 §5.1、§5.5、§16.4，架构 DoD 3～4。
- 目标：为 v0.3 UI 建立可独立演进的 feature 边界，不继续把领域状态和请求堆进根目录与全局 `api.ts`。
- 交付：`frontend/src/app/`、`features/`、`shared/ui/`、`generated/api/` 或等价生成 DTO 边界；feature public entry；fixture-backed client test harness。
- 验收：
  - feature 只能导入自身文件、`shared/ui`、生成 DTO/client 和 app 提供的公开 workflow；
  - v0.3 业务规则和请求不新增到 `frontend/src/api.ts`，旧 API 继续作为显式 compatibility seam；
  - 跨 feature 流程由 `app/` 组合公开入口，不能直接导入另一 feature 的 component/store；
  - fixture client 可在后端未完成时提供与 MM-052 一致的成功/错误/revision conflict 响应；
  - 现有前端测试和 production build 不退化。
- 不包含：Layout、Prompt 或 Review 的具体页面。

完成证据（2026-08-13）：`frontend/src/app/`、`features/`、`shared/ui/`、`generated/api/` 与 Layout fixture client；成功、not found、validation、revision conflict 共 5 项定向测试，全量前端 17 个文件/27 项与 production build 通过，legacy `api.ts` 明确标记迁移缝。

### MM-027 架构适应性函数与表/迁移所有权

- 优先级 / 状态 / 规模：`P0 / Done / 2–3d`；依赖 MM-025、MM-026、MM-053。
- 对应：技术架构 §5.4～5.8、§16.3～16.4、架构 DoD 2～6。
- 目标：把高内聚、低耦合从评审约定变成 CI 硬门禁。
- 交付：`tests/architecture/`、依赖白名单、循环检测、公开入口检测、domain 纯净检测、table/migration registry、模块迁移 runner、Port contract harness、前端 import 检查。
- 验收：
  - 业务模块循环依赖为 0，跨模块 import 只能进入 `public`/`contracts`；
  - 新增跨模块写 SQL、跨模块 cascade、未登记表/迁移或重复表所有者会使测试失败并打印完整依赖链；
  - v0.3 新增 `app.state` service lookup、模块内部真实 HTTP client 和前端跨 feature 内部 import 会被阻断；
  - 空库、v0.2 fixture 前向、重复执行和未知更高 schema version 均有迁移测试；
  - 临时豁免必须包含 owner、原因、影响、删除条件和 ADR，永久 ignore 不通过。
- 不包含：为了让门禁变绿而批量豁免现有代码；legacy 只能使用精确 allowlist。

完成证据（2026-08-13）：`tests/architecture/`、`backend/app/platform/persistence/`、模块
`migrations/` 入口、精确 compatibility exemption registry 与共享 Composition Port contract
harness；架构/契约/模块门禁 44 项、全量后端 165 项、全量前端 17 个文件/27 项、Ruff、
Mypy 和 production build 通过。空库、schema 16 fixture、重复迁移与未知 999 schema 均已
回归；未调用真实文本模型或 NovelAI。

### MM-028 Durable Work 存储与幂等事务

- 优先级 / 状态 / 规模：`P0 / Done / 1–2d`；依赖 MM-027。
- 对应：NFR-01、FR-11；技术架构 §5.3、§7.1、§9.2.1、ADR-013。
- 目标：先在领域事务中持久化工作意图；进程内 task 不再是真源。
- 交付：`work_items`、attempt/handler receipt 表；durable work Port；UnitOfWork 接线；幂等键；`requires_user_action`、`not_before`、attempt limit 和 last-safe-error 字段。
- 验收：
  - 领域提交与 work item 在同一个 SQLite 事务中完成，事务失败时两者都不存在；
  - 同一幂等键重复提交只返回原结果，不创建第二项工作；
  - payload 只保存版本化 command ref/hash，不保存 Token、完整正文、完整 Prompt 或图片字节；
  - 状态转移使用 revision/CAS 条件，重复完成、取消后完成和超 attempt limit 均失败关闭；
  - 同一 contract suite 同时验证 in-memory fake 和 SQLite adapter。
- 不包含：worker 循环、租约、SSE、外部 broker 或付费调用。

完成证据（2026-08-13）：schema 17 的 `work_items`、`work_attempts`、
`work_handler_receipts` migration，`backend/app/platform/durable_work/` Port、fake、SQLite
adapter 与 typed UnitOfWork；同一 contract suite 验证幂等、CAS、完成/取消/失败和 attempt
limit，另有领域状态与工作意图同事务回滚、not-before、人工动作及敏感载荷门禁。全量后端
169 项、架构/契约/模块门禁 48 项、前端 17 个文件/27 项、Ruff、Mypy 与 production build
通过；未启动 worker，未调用真实文本模型或 NovelAI。

### MM-054 Durable Worker、租约与重试策略

- 优先级 / 状态 / 规模：`P0 / Done / 1–2d`；依赖 MM-028。
- 对应：NFR-01、FR-11；技术架构 §9.2.1、§10、ADR-013。
- 目标：安全领取已持久化工作，并把本地可重试、永久失败和需要人工处理严格分开。
- 交付：单写者 worker runtime、CAS 短租约、续租/过期、handler registry、重试/退避策略和 wakeup adapter。
- 验收：
  - 两个 worker 竞争时同一 work item 只能有一个有效租约，租约过期后可由另一 owner 领取；
  - 纯本地幂等失败按 `not_before` 和 attempt limit 重试，永久失败不重试；
  - `requires_user_action=true` 或可能已外发的 attempt 不因 worker 启动、唤醒或租约过期自动执行；
  - pause/cancel 后不领取新工作，在途本地 handler 结束后写入可解释终态；
  - `asyncio.create_task` 只唤醒 worker，丢失 task 后 work item 仍存在。
- 不包含：Outbox/SSE 和启动 reconciliation。

完成证据（2026-08-13）：schema 18 `worker_leases`、CAS claim/renew/expiry、执行安全
级别、handler registry、确定性退避、进程内 wakeup 与单写者 `DurableWorker`；真实双线程
竞争仅一个租约，本地租约过期可换 owner，可能外发的 attempt 进入 `needs_review`，人工动作、
暂停、取消及丢失 wake signal 均不产生越权执行。全量后端 177 项、架构/契约/模块门禁 56
项、前端 17 个文件/27 项、Ruff、Mypy 与 production build 通过。当前 runtime 已交付可复用
worker 机制和恢复验证，但尚无业务 work kind/handler 接入，因此 lifespan 不启动空 worker；
待首个业务 durable command 接入时同票注册 handler 并启动 serve loop。未执行真实供应商调用。

### MM-029 Outbox 与 SSE replay

- 优先级 / 状态 / 规模：`P0 / Done / 1–2d`；依赖 MM-028。
- 对应：NFR-01/04/05、FR-11；技术架构 §5.7、§9.2.1、§9.3、架构 DoD 10/16。
- 目标：让已提交状态和前端进度保持一致，不靠内存事件或轮询猜测。
- 交付：`outbox_events`、project sequence、幂等 publisher、`Last-Event-ID` replay、handler receipt 和 SSE API。
- 验收：
  - HTTP 命令只返回已提交状态；SSE 断线重连能补发缺失序列且不重复应用副作用；
  - publisher 失败不回滚领域事务，重启后可幂等重放；
  - 同一 project sequence 单调递增且无重复；跨项目不能订阅到其他项目事件；
  - event handler 按 `(event_id, handler_version)` 幂等，重复投递只返回首次 receipt；
  - 事件和恢复日志不包含正文、完整 Prompt、Token、图片字节或绝对工作区路径。
- 不包含：启动 reconciliation、WebSocket 或云端事件总线。

完成证据（2026-08-13）：schema 19 `outbox_events`/project sequence/`handled_events`，
事务内 append、幂等 publisher、同事务 handler receipt 与按项目 `Last-Event-ID` SSE replay；
并发写入序列 1～12 无重号，publisher 失败后跨实例重放不回滚领域事实，重复 handler 只执行
一次，跨项目事件不可见。事件只保存版本、引用、哈希和安全标量。全量后端 183 项、架构/
契约/模块门禁 62 项、前端 17 个文件/27 项、Ruff、Mypy 与 production build 通过。SSE replay
端点已接入；现有 legacy 业务命令尚未迁移为 outbox 写入者，前端 EventSource 消费随首个业务
事件接入交付。未接入 WebSocket/云端总线，未执行真实供应商调用。

### MM-055 Durable Work/Outbox 重启恢复

- 优先级 / 状态 / 规模：`P0 / Done / 1–2d`；依赖 MM-029、MM-054。
- 对应：NFR-01/04、FR-11；技术架构 §9.3、架构 DoD 10/16。
- 目标：根据持久事实恢复工作，不因重启猜测供应商结果或自动继续付费操作。
- 交付：lease/outbox reconciliation、模块 IntegrityProbe 聚合、脱敏恢复摘要和用户恢复命令。
- 验收：
  - 纯本地幂等工作可按策略重新排队；可能已外发的 attempt 一律进入 `needs_review`；
  - queued 工作重启后不自动开始，running 且无在途 attempt 的 Job 恢复为 paused；
  - 过期租约、已发布未确认 outbox、完成结果未发事件三个断点均可幂等修复；
  - Recovery coordinator 只调用模块 probe/repair command，不直接写任意业务私表；
  - 查看范围、已用预算和未知结果后，用户必须显式恢复；启动本身产生的外部请求数为 0。
- 不包含：业务模块特有的 layout/review 文件修复。

完成证据（2026-08-13）：schema 20 Outbox delivery attempt 与 schema 21 owner-directed
recovery report/finding/receipt，typed `IntegrityProbe` 聚合、脱敏 API、确认后 repair command；
重启会暂停 queued/local orphan，外发未知进入 `needs_review`，未确认发布和缺失完成事件可幂等
修复。启动与恢复外部请求数为 0，恢复 coordinator 只写 recovery 私表，业务修复由
`durable_work` owner probe 执行。全量后端 185 项、架构/契约/模块/恢复门禁 64 项、前端
17 个文件/27 项、Ruff、Mypy 与 production build 通过；未执行真实供应商调用。

### MM-030 Artifact Dependency Graph 与最小失效

- 优先级 / 状态 / 规模：`P0 / Done / 2–3d`；依赖 MM-027、MM-029。
- 对应：FR-06/07/10/14/15、NFR-01；技术架构 §5.2、§5.7、§6.4、ADR-015。
- 目标：集中计算 Storyboard → Layout → Bible/Tags → Prompt → Spec → Candidate/Review → PageApproval → Export 的精确失效范围。
- 交付：ArtifactRef、依赖边、边类型白名单、cycle guard、stale event、影响查询、解释文本和 lineage public contract。
- 验收：
  - 图为有向无环；非法边、重复冲突边和跨项目边失败关闭；
  - 修改一个 frame 只返回该 frame 的 Prompt/Spec/Review/PageApproval 影响，不使无关页面失效；
  - 修改一个 CharacterTagSet 只影响引用该角色/造型的面板；Storyboard 变化正确级联到相关 Layout；
  - 失效记录保存原因、起点、依赖路径和事件 ID，但不复制业务文档；
  - 重放同一失效事件幂等，旧版本和历史决定保留且可解释为 stale。
- 不包含：lineage 直接写其他模块私表或自行决定业务边是否合法。

完成证据（2026-08-13）：schema 22 `artifact_versions`、typed dependency edges、
`invalidation_events/impacts`，边类型白名单、跨项目/冲突/cycle guard、确定性最短完整路径与
幂等 stale 传播；单 frame、单 CharacterTagSet 和 Storyboard 三类最小影响 fixture 均不波及
无关页面/角色，重复 source event 不重复写，后续事件保留历史并记录 `marked_stale=false`。
全量后端 190 项、架构/契约/模块/恢复门禁 69 项、前端 17 个文件/27 项、Ruff、Mypy 与
production build 通过；lineage 仅保存引用、哈希、原因与路径，未写其他模块私表。

## v0.3 P0 / V03-P0-01 版式先行

### MM-031 PageLayoutDraft 领域、Schema 与持久化

- 优先级 / 状态 / 规模：`P0 / Done / 2–3d`；依赖 MM-027、MM-030。
- 对应：FR-06/10/13/20、AC-04/09；技术架构 §6.1～6.2、§7.5、ADR-010。
- 目标：让页面版式成为 Prompt 和 GenerationSpec 的上游版本对象，而不是出图后的临时 UI 状态。
- 交付：layout module；PageLayoutDraft/FrameSpec/LayoutApproval/DimensionSelection 契约；模块迁移和 workspace 版本快照；draft/save/get/list/approve command/query。
- 验收：
  - 页面 profile/尺寸、frame parent/child hierarchy、shot scale、panel ID、0–1 坐标、order、aspect ratio、focal point、character positions、text/crop safe zone 可往返；
  - 每个已批准 Storyboard panel 恰好映射一个叶子 frame；frame hierarchy 无孤儿、重复 panel 或循环；
  - 保存和审批均创建不可变版本与规范化 SHA-256，不覆盖旧版；
  - LayoutApproval 绑定精确 Storyboard/Layout 内容哈希，修改后旧审批可查询但标记 stale；
  - v0.2 页面几何只能创建 `imported_legacy` draft，不能自动审批；
  - API/存储不包含 NovelAI 字段、供应商尺寸或图片 Token。
- 不包含：尺寸选择算法、画布 UI、图像请求。

完成证据（2026-08-13）：schema 23 新增 layout 自有的不可变版本、审批与
`dimension_selections` 基线；PageLayoutDraft 全字段以规范化 JSON/SHA-256 同步写入 SQLite
索引和 workspace 快照，读取时双向验哈希。保存使用乐观 revision，同内容重放不增版本，
内容或 Storyboard 绑定变化才追加版本；审批精确绑定两侧内容哈希，旧审批保留并按固定原因
查询为 stale。`imported_legacy` 导入不产生审批，先绑定已批准 Storyboard 才可人工审批；
layout 表与快照无 NovelAI、凭证或图片 Token 字段，所有命令
`external_requests_started = 0`。全量后端 196 项、架构/契约/模块门禁 77 项、前端 17 个
文件/27 项、Ruff、Mypy（172 source files）与 production build 通过；未执行真实供应商调用。

### MM-032 LayoutValidator 与 DimensionSelector

- 优先级 / 状态 / 规模：`P0 / Done / 1–2d`；依赖 MM-031。
- 对应：FR-13/20、AC-09；技术架构 §7.5、OPEN-07。
- 目标：在付费生成前确定格框合法性、阅读顺序和可满足的目标尺寸。
- 交付：纯函数 LayoutValidator、DimensionCapabilitySet、DimensionSelector、规则版本、黄金 fixture 和可解释选择结果。
- 验收：
  - 检查 panel/frame 全集、有限坐标、画布边界、面积、gutter、非法重叠、阅读顺序环、人物/安全区范围；
  - 按“宽高比误差 → crop-safe 风险 → 目标像素 → 成本/固定键”稳定排序，完全相同输入得到相同选择与哈希；
  - 横格、竖格、近方格、六格页和不可满足的 crop-safe fixture 均有明确结果；
  - capability 变化会使契约测试失败或产生新规则/mapping version，不静默改旧结果；
  - 算法不导入 production/NovelAI 类型，也不访问网络或凭证库。
- 不包含：真实供应商尺寸 smoke；该证据归 MM-046。

完成证据（2026-08-13）：纯函数 `LayoutValidator` 覆盖 panel/frame 全集、规范化/有限几何、
全画布 root、最小面积、重叠、gutter、人物/文字/crop-safe 范围和 reading-order DAG，并返回
稳定 code/path。provider-neutral `DimensionCapabilitySet` 与 `DimensionSelector` 按宽高比误差、
crop-safe 风险、目标像素、成本、固定 key 稳定排序，结果和失败均带规则版本与规范化哈希；
能力候选变化而哈希未变会失败关闭。横格、竖格、近方格、六格页及不可满足 crop-safe 黄金
fixture 通过。全量后端 209 项、前端 17 个文件/27 项、Ruff、Mypy（174 source files）与
production build 通过；算法未导入 production/NovelAI、未访问网络或凭证库。

### MM-033 Layout API 与审批命令

- 优先级 / 状态 / 规模：`P0 / Done / 1–2d`；依赖 MM-026、MM-031、MM-032。
- 对应：PRD §8.5、FR-06/13/20、AC-09。
- 目标：为版式编辑和审批提供强校验、可恢复的后端命令面。
- 交付：draft/get/list/save/approve/impact API、revision/Idempotency-Key、LayoutValidator/DimensionSelector 接线和 OpenAPI 契约。
- 验收：
  - 保存使用乐观 revision；两个并发写只有一个成功，失败方收到当前 revision 且不覆盖另一版本；
  - approve 同步复验 Storyboard、page profile、hierarchy、shot scale、frame 和 DimensionSelection hash；非法项返回精确 frame/path；
  - impact 查询返回将 stale 的具体 ArtifactRef/路径，但审批命令不直接写下游私表；
  - 同一 Idempotency-Key 重复提交返回同一版本/审批，不产生重复行；
  - 所有命令只写本地状态，`external_requests_started = 0`。
- 不包含：Layout Workbench UI、Prompt 编译和 NovelAI 调用。

完成证据（2026-08-13）：schema 24 增加 layout 自有的 command receipt 和审批-尺寸绑定；
draft/get/list/save/validate/approve/impact 路由进入 OpenAPI，写命令要求本地 session、CSRF、
`Idempotency-Key`，相同请求跨 revision 变化仍返回原资源，不重复落行。并发 revision 回归中
两个写只有一个 201，另一方 409 且返回 `current_revision=2`；只允许从当前父版本继续修订。
approve 从已批准 Storyboard 页面重新取得 panel 集，复验 profile/canvas、层级/格框、shot
scale 契约和每个 leaf 的 DimensionSelection 哈希，缺失 frame/path 精确返回；layout 版本通过
公开 lineage facade 注册边，impact 只读返回 ArtifactRef/完整路径，审批不改下游私表。
全量后端 214 项、前端 17 个文件/27 项、Ruff、Mypy（177 source files）、production build
及 `git diff --check` 通过，所有响应 `external_requests_started = 0`，未调用真实供应商。

### MM-056 Layout Workbench 与影响预览

- 优先级 / 状态 / 规模：`P0 / Done / 1–2d`；依赖 MM-053、MM-031、MM-032。
- 对应：PRD §8.5、FR-06/13/20、AC-09。
- 目标：让用户在 Prompt 和出图前看见、编辑并明确批准页面节奏与每格约束。
- 交付：layout feature client、Layout Workbench、1–6 格模板、格框拖拽/拆分/合并、page profile、frame hierarchy、shot scale、阅读顺序、焦点、人物位置、安全区和尺寸/裁切预览。
- 验收：
  - 刷新或重启后从后端 fixture/API 恢复草稿/已批准版本，前端 store 不是项目真源；
  - 画布可编辑横格、竖格、近方格和六格页，清楚显示层级、景别、实际比例、合法尺寸和 crop-safe 风险；
  - 审批前展示受影响 Prompt/Spec/Review/PageApproval、候选数和成本摘要；非法 frame 的批准按钮禁用并定位问题；
  - revision conflict 保留本地草稿并提供重新加载，不静默覆盖；
  - 前端测试覆盖模板、拆分/合并、层级、shot scale、键盘阅读顺序、审批失效和零外部请求。
- 不包含：后端状态机或图像请求。

完成证据（2026-08-13）：新增 feature-local Layout HTTP/fixture client 与可恢复工作台，启动、
刷新及章节/页面切换均从后端 current snapshot、approval、impact 重建状态。1～6 格模板保持
Storyboard panel 与叶子 frame 一一映射；拖拽和方向键使用页面绝对坐标，层级拆分/合并可逆且
不制造空 panel，page profile 会同步重算嵌套 frame 比例。Inspector 支持景别、焦点、人物位置、
文字/crop-safe 区，校验后显示合法尺寸、预计裁切与风险；审批前展示 Prompt/Spec/Review/
PageApproval 影响、候选数 0 与图像成本 0。revision conflict 保留本地草稿，须显式重载；修改
已批准版本会明确显示审批失效。前端 20 个文件/36 项、全量后端 214 项、Ruff、Mypy（178
source files）、production build 与 `git diff --check` 通过，全部 layout 响应
`external_requests_started = 0`，未发起图像请求。

### MM-034 版式生成门禁、冻结与旧入口收口

- 优先级 / 状态 / 规模：`P0 / Done / 2–3d`；依赖 MM-030、MM-033。
- 对应：FR-06/10/11/13/20、AC-04/09；技术架构 §6.4、§8、§9.1。
- 目标：让任何新 PromptPackage、GenerationSpec 和 Job 都实际依赖已批准版式，关闭新项目“先统一竖图、后裁切”的旧捷径。
- 交付：LayoutSnapshot 查询、生成预检门禁、frame/DimensionSelection 冻结、计划指纹扩展、lineage 边、旧入口提示和 compatibility 条件。
- 验收：
  - 无 LayoutApproval、审批 stale、frame 非法或尺寸不可满足时，不创建 PromptPackage/GenerationSpec，不读取供应商凭证；
  - GenerationPlan/Job/Spec 冻结 layout ID/version/hash、frame hash、尺寸选择、expected crop ratio 和规则版本；
  - 修改单格 layout 只失效对应 Prompt/Spec/Review/PageApproval，旧素材不删除、不自动重抽；
  - 新项目无法走固定统一尺寸旧入口；旧 v0.2 工程仍可只读查看并收到明确迁移提示；
  - 回归证明文字/气泡/布局本地编辑仍不发图像请求。
- 不包含：多角色供应商映射和候选接受。

完成证据（2026-08-13）：新增逐章 `ApprovedChapterLayoutSnapshot` 查询和生成前
fail-closed 门禁；PromptPackage 1.1、GenerationPlan、JobItem 与 GenerationSpec 1.3
逐格冻结 layout/approval/frame/DimensionSelection 的 ID、版本、哈希、尺寸、预计裁切和
规则版本。执行器在保存 Spec 和读取本地凭证前复验当前审批，版式变化时调用数保持 0；
frame lineage 只失效对应 PromptPackage/Spec 路径，既有素材不删除且不自动重抽。新建项目
标记 `v03`，schema 16 工程标记 `legacy_v02`，可读但生成入口返回明确迁移提示。数据库
schema 26 的空库、v0.2 fixture 前向和重复迁移通过；后端 217 项、前端 20 文件/36 项、
Ruff、Mypy（178 source files）、production build、`git diff --check` 与凭证明文扫描通过，
全部外部调用使用 Stub/Mock。

## v0.3 P0 / V03-P0-02 结构化多角色契约

### MM-035 PromptPlan v2 与固定 Tags 结构化编译

- 优先级 / 状态 / 规模：`P0 / Done / 2–3d`；依赖 MM-034。
- 对应：FR-05/07/10/12/21、AC-04/10；技术架构 §7.3、§8.1/8.3/8.4、ADR-011。
- 目标：把 base、每个角色正负区块、顺序、坐标和关系动作保留为领域真源，禁止先扁平化。
- 交付：PromptPlan v2/PromptPackage v2 Schema、compiler、冲突/覆盖校验、固定 Tags 确定性注入、legacy flat prompt reader、单/双/三角色 fixture。
- 验收：
  - 每个目标角色恰好出现一次，order 连续唯一、center 在 0–1，角色正负区块不合并；
  - 每个角色保留自己的 action/pose，base 保留跨角色 `relationship_action`；编译后可从 fixture 逐字段反查，不能只剩共享自然语言；
  - `fixed_tags` 与已批准 CharacterTagSet 的有序内容和哈希完全一致，模型不能改写、漏掉、重排或串角色；
  - 固定/可变/负向冲突、未知角色、缺角色、空区块和 layout 角色位置不一致均本地阻断；
  - 相同输入版本得到相同 PromptPlan 和哈希，变更 mapping 不改写历史 PromptPackage；
  - 多角色 `legacy_flat_prompt` 只可查看旧素材，不能用于新 Job；单角色也必须显式重新批准后才能进入 v0.3 路径。
- 不包含：把 PromptPlan 转为 NovelAI 私有字段。

完成证据（2026-08-14）：`backend/app/modules/prompting/compiler.py` 以
`PromptPlan 2.0` / `PromptPackage 2.0` 保留 base、每角色正负区块、action、连续 order、
已批准 layout center、关系动作与固定 Tags 的有序内容/哈希；现有 Prompt API 以 bundle
schema 1.2 持久化该结构，flat 字符串仅保留为 v0.2 UI 兼容投影，不能反向重建角色。
compiler 对角色/版式覆盖、坐标、空区块、固定/可变/负向冲突和跨角色串扰失败关闭，
Executor 再验 v2 内容哈希与冻结 TagSet。`legacy_flat_prompt` 查询明确返回只读、需重新生成、
不可创建新 Job；单/双/三角色、确定性哈希、冲突和 legacy 阻断均有模块/API/生成回归。
架构/契约/模块测试、Prompt/Queue/Executor/revision 定向测试、前端 20 文件/36 项、Ruff、
Mypy、TypeScript、production build 与 `git diff --check` 通过；未调用真实文本或图像服务。

### MM-036 ProviderExecutionSpec 与 NovelAI 多角色映射

- 优先级 / 状态 / 规模：`P0 / Done / 2–3d`；依赖 MM-035。
- 对应：FR-09/11/12/21、AC-10；技术架构 §8.2/8.4、§16.1～16.2、OPEN-03/08。
- 目标：用版本化 anti-corruption mapper 把稳定 PromptPlan 转成当前 NovelAI V5 Full 载荷。
- 交付：ProviderExecutionSpec、mapping version、capability fixture、base/正向角色 captions/负向角色 captions/坐标映射、canonical payload hash、Swagger diff 契约测试。
- 验收：
  - 单/双/三角色 fixture 的正负 captions 与坐标数量一致、顺序稳定、非空，base 不混入角色固定 Tags；
  - 每角色 action/pose 映射到自己的 caption，跨角色 relationship action 只进入规定的 base/关系字段；交换角色顺序时 caption/坐标/action 同步交换而不串扰；
  - 角色遗漏、空 `char_captions`、错序、负向数量不匹配或 capability 不支持时，在构造 Authorization 前失败；
  - 相同 PromptPlan/GenerationSpec 得到相同 ProviderExecutionSpec payload hash；mapping 升级产生新版本；
  - mock 覆盖当前成功载荷、400/422、401/403、429、5xx、损坏响应和未知结果，不读取真实 Token；
  - 运行代码只发送冻结 ProviderExecutionSpec，不从 flat prompt 反推角色。
- 不包含：真实双角色付费调用；归 MM-046。

完成证据（2026-08-14；2026-08-29 升级复核）：`production` 模块提供严格 Pydantic
结构化 DTO 与版本化 anti-corruption mapper，当前 mapping 固定为
`novelai-image-2026-08-29.4-v5-full-1`；
单/双/三角色和交换顺序 fixture 逐字段验证 base、正负 captions、order、center、action
及 canonical payload hash。角色缺失、空区块、数量/顺序错误、不支持模型或过期 mapping
均在凭证读取前失败关闭；当前官方 Swagger 快照的 113,758 bytes 与 SHA-256 进入契约测试。
queue、revision 和 executor 只消费冻结 ProviderExecutionSpec/payload，不从 flat prompt 反推，
Mock 覆盖供应商成功及现有错误分类；未读取真实 Token、未发出付费请求。

### MM-037 Prompt 审批与 Job 冻结

- 优先级 / 状态 / 规模：`P0 / Done / 1–2d`；依赖 MM-033、MM-036。
- 对应：PRD §8.6、FR-10～12/21、AC-10。
- 目标：把结构化 PromptPlan 和供应商载荷纳入可失效的生成批准与冻结指纹。
- 交付：Prompt approve/impact API、GenerationApproval/Plan/Job/Spec 扩展、生成预检和 executor 发送前复验。
- 验收：
  - Job 指纹冻结 PromptPlan/ProviderExecutionSpec/Layout/CharacterTagSet/model/mapping/rule 版本和每格候选数；
  - 上游任一哈希变化都会使批准 stale 并要求重新估算，不静默沿用；
  - approve/Job create 使用 Idempotency-Key；重复提交不创建第二个批准、Job 或成本预留；
  - executor 在读取凭证前复验全部冻结哈希；失败时外部请求数为 0；
  - executor 只发送冻结 ProviderExecutionSpec，不再次调用文本模型、自由重写 Prompt 或退回 flat prompt。
- 不包含：Prompt Inspector UI、候选质量判断和页面批准。

完成证据（2026-08-14）：schema 28/29 增加 Prompt 审批幂等快照和
GenerationApproval，schema 30 将幂等键唯一性安全迁移到审批对象作用域；Job/Item 原子冻结 PromptPlan/PromptPackage/CharacterTagSet、
ProviderExecutionSpec/payload、Layout/frame/dimension、模型、mapping/contract、seed、参考图
provenance、候选数和质量规则版本，并只对外返回审计 ID/哈希而不返回完整 payload。
approve/Job create 重放不创建第二份批准或 Job；executor 在读取凭证前重算并复验全部哈希，
篡改回归返回 `GENERATION_APPROVAL_STALE`，凭证读取和 provider 调用均为 0。reroll/inpaint
同样冻结映射与父版本输入；旧 flat prompt 仍失败关闭。

### MM-057 Prompt Inspector 与脱敏载荷预览

- 优先级 / 状态 / 规模：`P0 / Done / 1–2d`；依赖 MM-053、MM-035、MM-036。
- 对应：PRD §8.6、FR-10～12/21、AC-10。
- 目标：用户在批准前能逐格核对领域 PromptPlan 与实际供应商映射，不接受黑盒拼接。
- 交付：Prompt Inspector feature、结构化角色编辑/对照、mapping 预览、候选数/成本摘要和 stale 影响提示。
- 验收：
  - UI 分区显示 base、每个角色固定/可变/负向 Tags、action、relationship action、order/center、layout 约束和供应商映射；
  - 单/双/三角色 fixture 的角色区块数量和顺序在领域/载荷两栏一一对应，缺失或空区块高亮且不能批准；
  - payload 预览只展示 allowlist 字段，不包含 Token、请求头、完整章节、base64 或其他项目数据；
  - 修改结构化字段后显示新 PromptPlan/payload hash、受影响对象和重新估算要求；
  - 前端测试覆盖固定 Tags 只读边界、动作/关系保留、载荷脱敏、stale 和 revision conflict。
- 不包含：后端 Job 创建或真实供应商调用。

完成证据（2026-08-14）：feature-local client/Inspector 逐格显示 base、固定/可变/负向
Tags、action、relationship、order/center、layout 与实际 NovelAI 映射；固定 Tags 只读，
可变结构修改后立即进入未保存状态、清空可批准哈希并阻止审批，保存后重新读取服务端
snapshot。payload 后端采用字段 allowlist，明确排除 Token、header、章节原文、图片/mask/
reference base64；界面展示 mapping/model/hash、影响对象、候选数/预计调用和“需生成预估”
成本边界。前后端测试覆盖角色对齐、只读边界、编辑保留、脱敏、stale conflict 和审批门禁；
预览外部请求数始终为 0。

### MM-066 Opus 零 Anlas 有界生成与 10 页回归

- 优先级 / 状态 / 规模：`P0 / Done / 1–2d`；依赖 MM-037、MM-057。
- 对应：FR-09～11/18、AC-05/06；技术架构 §2.4、§8.7、§9、§11。
- 目标：默认使用官方 Opus 免费载荷，同时保留用户显式选择标准计费的能力，并让全部外部请求和未知成本可审计。
- 交付：零 Anlas 请求 profile、逐图订阅核验、schema 31 验证调用计数、ZIP 响应安全解码、seed 来源标记、初次/整本计费模式选择和 10 页 Mock E2E。
- 验收：
  - 零模式固定单张、普通尺寸、28 steps、无基础图/参考图，并在出图前原子预留和执行 Opus 订阅核验；
  - 用户把最大出图调用扩大时，界面动态展示同量订阅核验与两倍外部请求硬上限；标准模式仍需显式选择和成本确认；
  - 资格核验不冒充账单：供应商未回传逐次扣费时 `unverified_cost_calls` 保留，整本视图同样提示未知成本；
  - 10 页零模式断言 10 次出图、10 次订阅核验、20 次外部请求、0 Anlas 本地预留和 10 次未核实成本记录；
  - revision 不接受 0 Anlas 上限；旧模型明确标记为仅标准计费；ZIP 未返回 seed 时 `response_seed=null`、`seed_source=request`。

完成证据（2026-08-15）：新增 schema 31 迁移与 30→31 回归；队列在任何订阅网络请求前原子检查出图/核验上限，且零 Anlas 出图必须先完成同一 attempt 的订阅核验。所有已发出的图像请求（成功、失败、重试或启动恢复）在供应商未回传费用时均累计为未核实。NovelAI `200 application/octet-stream` 单 PNG ZIP 经条目、路径、大小、压缩比、PNG 与尺寸门禁后登记；JSON 兼容路径保留供应商 seed。前端默认零 Anlas 资格载荷但明确说明本地 0 预留不是账单保证；可显式选择标准计费，修改最终调用/成本上限会清除确认。完整 10 页 Mock 流程覆盖页面合成和四格式导出，不执行真实 NovelAI 付费请求。

### MM-067 整本重试历史调用与成本累计

- 优先级 / 状态 / 规模：`P1 / Done / 1–2d`；依赖 MM-066、MM-102。
- 目标：整本计划重置章节任务后，仍按历史 Job 累计出图、订阅核验、外部请求和未核实/可验证成本。
- 验收：章节与全部历史 Job 保持不可变关联；重试幂等键包含 retry 序号；整本摘要与导出审计不因清空当前 `generation_job_id` 丢失旧调用。

完成证据（2026-08-15）：`book.chapter_job_created` 的不可变审计关联用于汇总同一章节的全部历史 Job；整本与章节摘要累计历史出图、订阅核验、外部请求、分配成本、已核实成本及未核实请求。章节重试使用独立幂等域，新 Job 只能获得章节生命周期剩余调用/成本额度；额度不足以重建完整有界任务时返回 `BOOK_CHAPTER_RETRY_BUDGET_EXHAUSTED`，不会取消当前任务或重置授权。回归覆盖一次失败重试后仅分配剩余额度、再次耗尽后拒绝重试以及历史未知成本不丢失。

## v0.3 P0 / Storyboard 1.1 逐页分镜政策

### MM-068 Storyboard 1.1 契约、页面政策与模型修复

- 优先级 / 状态 / 规模：`P0 / Done / 1–2d`；依赖 MM-009、MM-031。
- 对应：PRD §8.4/§11、AC-04；技术架构 §6.4、§8.4、ADR-019、DoD 20。
- 目标：让文本模型对每一页显式给出页型和完整分镜，并由本地域规则而非 Prompt 猜测决定是否合法。
- 交付：Storyboard schema 1.1、`PageType`、版本化 `StoryboardPagePolicyValidator`、机器可读 Finding、生成 Prompt 约束及最多两次结构修复。
- 验收：
  - `standard` 3/6 格通过，1/2/7 格失败；`cover/splash/special` 1/2/6 格通过；空页、缺失或未知页型失败；
  - 每项失败包含 `page_id`、JSON path、实际页型/格数和允许范围，政策版本可审计；
  - 格数或页型错误与 JSON Schema 错误一样进入最多两次修复，超过上限不持久化 Storyboard；
  - Storyboard 1.0 可解析但不被默认补页型，也不按既有格数反推分类。
- 不包含：审批/API/Layout 门禁或前端展示。

### MM-069 分镜校验/审批、1.0 升级与 Layout 门禁

- 优先级 / 状态 / 规模：`P0 / Done / 1–2d`；依赖 MM-068、MM-033、MM-034。
- 对应：PRD §8.4/§8.5、AC-04/09；技术架构 §7.4、§8.5、§18.1。
- 目标：在每个下游入口重复执行 Storyboard 1.1 政策，防止旧版或违规草稿绕过分镜审批进入版式。
- 交付：本地 validate API、版本响应中的政策状态、审批错误契约、Storyboard 1.0 只读升级门禁、legacy adaptation facade 与 Page/Layout consumer 复验、模板兼容元数据。
- 验收：
  - validate 不启动外部请求并返回逐页 Findings；违规审批返回 `STORYBOARD_PAGE_POLICY_INVALID`；1.0 审批/修订/Layout 返回 `STORYBOARD_UPGRADE_REQUIRED`；
  - 人工编辑可保存合法的 1.1 新版本，但不合法格数不能保存或审批；旧 1.0 历史内容保持原样可读；
  - Layout 创建只接受已批准、当前、政策有效的 1.1 页面，且批准 Panel 与叶子 Frame 严格一一对应；
  - 1–2 格模板元数据只声明兼容 `cover/splash/special`，3–6 格模板兼容全部页型。
- 不包含：用户界面或真实模型调用。

### MM-070 改编工作台页型状态与模板兼容体验

- 优先级 / 状态 / 规模：`P0 / Done / 1–2d`；依赖 MM-053、MM-069。
- 对应：PRD §8.4/§8.5、AC-04/09；技术架构 §4.3、§8.5。
- 目标：让用户在审批前直接看到模型自动分类、格数和违规原因，并只看到与页型兼容的版式模板。
- 交付：前端 Storyboard 1.1 类型、页型标签/说明、逐页格数状态、审批禁用原因、Layout 页面选择摘要与模板过滤。
- 验收：
  - 每页显示 `standard/cover/splash/special` 的中文标签、格数及合法/违规状态，不要求用户填写例外理由；
  - 普通页 1–2 格、未知/缺失页型、旧 1.0 在界面明确提示且审批按钮禁用；
  - 特殊页 1–2 格可选择相同格数模板，普通页只显示 3–6 格模板；无兼容模板时不创建草稿；
  - 保留已有文本模型配置刷新、编辑和审批交互回归。
- 不包含：新增页型人工编辑入口。

### MM-071 Storyboard 1.1 回归、迁移与 E2E 门禁

- 优先级 / 状态 / 规模：`P0 / Release Gate / Done / 1–2d`；依赖 MM-068～MM-070。
- 对应：PRD §15、AC-04；技术架构 §16、§18、DoD 20。
- 目标：用契约、API、consumer 和产品流证据证明新政策已落地，同时保护既有 NovelAI V5 与历史 Storyboard。
- 交付：领域/文本修复/API/前端/Layout/页面服务测试；Storyboard 1.0 只读 fixture；既有 Mock/授权验收 fixture 升级；逐页分镜 E2E。
- 验收：
  - 覆盖普通页 3/6 成功和 1/2/7/空页失败，特殊页 1/2/6 成功，缺失/未知类型两次修复后停止；
  - E2E 至少包含一个自动判定的 1–2 格特殊页，其他普通页均为 3–6 格，每个 Panel 恰好映射一个叶子 Frame；
  - Storyboard 1.0 可读但不能创建新审批/Layout；已有 V5 验收改动不丢失；
  - 后端定向测试、Ruff、mypy、前端定向测试与生产构建通过；全量测试若存在无关基线失败须逐项记录，不能冒充全绿。
- 不包含：真实文本模型或额外 NovelAI 付费请求。

完成证据（2026-08-29）：Storyboard 1.1、自动 `page_type`、普通页 3–6 格与特殊页
1–6 格政策已接入模型结构修复、持久化、人工修改、审批、设定生成、Prompt/Generation、
页面草稿和 Layout 门禁；1.0 保持原文档形状可读但不能修改、重新审批或进入新生产。
Mock E2E 生成并审批 1 格 `splash` 与 3 格 `standard`，逐页批准 PageLayoutDraft，并断言
每个 Panel 恰好对应一个叶子 Frame。全量后端测试、48 项前端测试、Ruff、mypy 和 production
build 通过。本票未调用真实文本模型或 NovelAI。

## v0.3 P0 / V03-P0-03 候选、质检、接受与发布

### MM-038 PanelCandidateSet 与生成结果接入

- 优先级 / 状态 / 规模：`P0 / Todo / 2–3d`；依赖 MM-030、MM-037、MM-055。
- 对应：FR-10/11/14/22、AC-06/11；技术架构 §6.1～6.2、§9.5、ADR-012。
- 目标：把“文件生成成功”与“素材被采用”彻底分离。
- 交付：review module 基线；PanelCandidateSet/候选状态契约和表；Generation target hash；AssetVersion ready 事件 handler；多候选 Job 接入。
- 验收：
  - 供应商成功响应只登记不可变 AssetVersion，并加入唯一的 `panel_id + generation_target_sha256` CandidateSet，初始为 `qc_pending`；
  - 同一事件重复投递不重复加入候选；同一目标支持两个以上候选且保留各自 seed/provenance/成本；
  - 不自动改变 Panel 当前素材、PageVersion 或任何 accepted 状态；
  - GenerationJob completed 只表示有可用文件，不创建 ReviewDecision/PageApproval；
  - 文件落盘、候选登记和事件发布任一断点都可 reconciliation，未登记文件不会冒充候选。
- 不包含：质量规则、人工接受 UI。

### MM-039 QualityRun/Finding 框架与确定性规则

- 优先级 / 状态 / 规模：`P0 / Todo / 1–2d`；依赖 MM-032、MM-038。
- 对应：FR-18/22、AC-11；技术架构 §9.5、§15.3、§16.2、OPEN-10/11。
- 目标：建立可解释、可复跑的质量状态机，并先落地确定性文件/版式规则。
- 交付：QualityRun/Finding 状态机、规则注册表和版本；`FILE_DECODE_BLOCKER`、`BLANK_IMAGE_BLOCKER`、`DIMENSION_MISMATCH_BLOCKER`、`LOW_RESOLUTION_WARNING`、`CROP_SAFE_ZONE_BLOCKER`、`DUPLICATE_CANDIDATE_SHA256_WARNING`；resolve/waive 契约。
- 验收：
  - 规则输入固定候选文件哈希、layout frame、必要 Bible/continuity 摘要和规则版本，默认不调用云模型；
  - 同一输入/规则重复运行结果幂等，规则升级创建新 run，不改写旧 Finding；
  - blocker/warning 包含 rule ID/version、区域、证据、置信口径和状态；规则执行失败进入 `qc_failed`，候选仍保留；
  - 关闭或豁免必须由明确用户动作完成并保留理由；规则永远不能创建 accepted 决定；
  - 六条命名规则各有 pass/fail fixture；纯白、纯黑或像素方差低于版本化阈值的 fixture 必出 `BLANK_IMAGE_BLOCKER`；两个字节相同候选必出 duplicate warning，损坏图和越过 crop-safe 的图必出 blocker。
- 不包含：随机文字、角色数量、服装/道具和页面文字溢出检查；归 MM-062。

### MM-062 视觉质量检查清单与金标 fixture

- 优先级 / 状态 / 规模：`P0 / Todo / 1–2d`；依赖 MM-039。
- 对应：FR-18/22、AC-11；技术架构 §15.3、§16.2、OPEN-10/11。
- 目标：完整覆盖 PRD 点名的 P0 质量项；可靠性不足的视觉项必须显式进入人工检查，不能用空规则冒充自动识别。
- 交付：`RANDOM_TEXT_REVIEW`、`CHARACTER_COUNT_REVIEW`、`CLOTHING_PROP_VISIBILITY_REVIEW`、`PAGE_TEXT_OVERFLOW_BLOCKER`；授权/合成金标 fixture；规则能力登记和抽样报告模板。
- 验收：
  - 每个候选都生成随机文字、目标角色数量、固定服装/标志道具三项明确检查项，用户必须记录 `pass/fail/not_applicable + note` 后才能 PageApproval；
  - 在本地 detector 未在每类至少 50 个授权/合成标注样本上达到 `precision ≥ 0.90` 且 `recall ≥ 0.80` 前，上述三项标记 `manual_check_required`，不得显示“自动通过”；引入 detector 时须单列版本、误报/漏报和禁用开关；
  - `PAGE_TEXT_OVERFLOW_BLOCKER` 对每个 PageDocument 实际测量文字边界，任一像素越界或最小字号违规必出 blocker；
  - blank、duplicate、损坏、crop-risk、随机文字、角色数、服装/道具、溢出八类 fixture 均产生预期 rule ID/severity/status；
  - 规则/人工检查只能生成或关闭 Finding，不能自动接受候选或批准页面。
- 不包含：云端视觉模型、通用美学评分或自动豁免。

### MM-040 ReviewDecision 状态机与 API

- 优先级 / 状态 / 规模：`P0 / Todo / 1–2d`；依赖 MM-039。
- 对应：PRD §8.7、FR-14/22、AC-06/11；NFR-02/05。
- 目标：让用户动作能对精确 AssetVersion 创建可追溯、可失效的决定，且 API 不替用户推断接受结果。
- 交付：accepted/rejected/needs_fix 追加式 ReviewDecision、幂等 command/query API、current-decision projection、Finding resolve/waive 接线和审片指标事件。
- 验收：
  - 一个 target 最多一个当前有效 accepted 决定，但历史决定全部保留；
  - 决定绑定精确 asset/version/target/dependency hash/user action；lineage 变化只把当前决定标记 stale，不改写历史事件；
  - accept/reject/needs_fix 和 Finding resolve/waive 均使用 Idempotency-Key；重复提交返回相同事件 ID；
  - 接受未 ready、跨 target、stale 或 blocker 未处置的候选失败关闭并返回稳定错误码；
  - 查询可在重启后重建 current decision、历史和指标，API 不根据“最新候选”自动推断 accepted。
- 不包含：候选审片 UI 或从决定直接发布页面。

### MM-064 候选审片台与质量证据 UI

- 优先级 / 状态 / 规模：`P0 / Todo / 1–2d`；依赖 MM-040、MM-053、MM-062。
- 对应：PRD §8.7、FR-14/22、AC-06/11；NFR-02/05。
- 目标：让用户并排比较同一目标候选，查看完整证据后明确接受、拒绝或要求修复。
- 交付：Candidate Review feature、联系表/并排视图、Finding/人工检查面板、备注/豁免交互、指标采集和 revision conflict 处理。
- 验收：
  - 用户能对两个以上候选接受一个、拒绝一个、标记待修复，并看到 PromptPlan、seed、参考图、成本和八类质量证据；
  - random-text/角色数/服装道具人工项未填写时接受按钮禁用；blocker 豁免必须输入理由并二次确认；
  - 50 个候选使用懒加载或虚拟化，本地缓存命中时目标切换 200 ms 内有视觉反馈；
  - 刷新/SSE 重连后决定和 Finding 从后端恢复，前端不根据卡片顺序或最新时间推断 accepted；
  - revision conflict 保留用户备注并提示重载，不能覆盖另一审片动作。
- 不包含：ReviewDecision 后端状态机、reroll/inpaint 执行或页面批准。

### MM-041 reroll/inpaint 候选回环

- 优先级 / 状态 / 规模：`P0 / Todo / 1–2d`；依赖 MM-040。
- 对应：FR-14、AC-06/11；技术架构 §8.6、§9.5。
- 目标：让修改结果回到同一个候选/质检/接受流程，而不是绕过审片自动替换页面。
- 交付：从选中候选发起 reroll/inpaint 的 target/parent lineage；revision 完成事件接入 CandidateSet；旧页面兼容策略。
- 验收：
  - reroll/inpaint 冻结父 AssetVersion、PromptPlan/Spec、mask、用户动作和预算，父文件不覆盖；
  - 新结果进入 `qc_pending`，不会自动撤销旧 accepted 决定或生成当前 PageVersion；
  - 上游依赖变化会使旧 accepted stale；仅新增同 target 候选时旧 accepted 可继续有效，除非用户改选；
  - 单格 revision 不改变其他格，整页 revision 不改变其他页；
  - 外部调用仍遵守两次人工确认、串行、暂停/取消和 unknown outcome 边界。
- 不包含：Focused Inpainting 或自动选择最佳候选。

### MM-042 PageApproval 与页面状态机

- 优先级 / 状态 / 规模：`P0 / Todo / 1–2d`；依赖 MM-040、MM-062。
- 对应：FR-13/15/17/22、AC-06/07/11；技术架构 §9.5、§12、ADR-012。
- 目标：把“页面可编辑”和“页面可发布”分离，形成精确、可失效的页面批准快照。
- 交付：draft/ready_for_review/approved/changes_requested/stale 状态；PageApproval 契约和表；从 accepted assets 合成 PageVersion 的公开流程；approve/request-changes API。
- 验收：
  - 只有每格存在有效 accepted 候选且渲染成功时进入 ready_for_review；
  - PageApproval 冻结 PageVersion、ordered accepted assets、Finding/豁免快照、renderer/font hash 和依赖摘要；
  - blocker 未关闭/未显式豁免、accepted stale、文字/版式/素材变化时无法批准或使旧批准 stale；
  - Job completed、PageVersion rendered 或 Finding=0 都不能自动生成批准；
  - 恢复旧 PageVersion 不删除分支，也不自动恢复一个已失效批准。
- 不包含：页面批准 UI 或导出格式编码。

### MM-065 页面批准与状态导航 UI

- 优先级 / 状态 / 规模：`P0 / Todo / 1–2d`；依赖 MM-042、MM-053、MM-064。
- 对应：PRD §8.8～8.9、FR-13/17/22、AC-06/07/11。
- 目标：让用户清楚看到页面为何不可批准、批准绑定什么，以及上游变化后该去哪里修复。
- 交付：Page Approval feature、页面状态徽标、accepted asset/Finding/renderer 摘要、approve/request-changes、stale dependency 深链和历史批准浏览。
- 验收：
  - 页面缩略图准确区分缺候选、待质检、待接受、有 blocker、可审批、已审批和 stale；状态完全来自后端 Snapshot；
  - approve 前展示 ordered accepted assets、八类质量状态/豁免、renderer/font hash 和 dependency hash；缺任一项按钮禁用；
  - request changes 能定位具体 panel/Finding 并保留备注，不触发外部生成；
  - 上游变化通过 SSE 将 approved 页面转 stale，并提供 Layout/Prompt/Candidate Review 的精确深链；
  - 前端 fixture 测试覆盖 approve、changes_requested、stale、历史浏览和 revision conflict。
- 不包含：PageApproval 后端状态机、ExportPreflight 或发布格式。

### MM-043 ExportPreflight 引擎与 TOCTOU 门禁

- 优先级 / 状态 / 规模：`P0 / Todo / 1–2d`；依赖 MM-042。
- 对应：FR-16～18/22、AC-07/11；技术架构 §12.3、§13、§15、OPEN-06/11。
- 目标：让正式 PNG/PDF/CBZ 只消费预检通过的精确 PageApproval 清单，修复假门禁。
- 交付：ExportPreflight 计算器、blocker/warning 模型、preflight token/hash、TOCTOU 复验和 ExportRevision/PageApproval 绑定。
- 验收：
  - 实际计算缺页、未批准/stale 页面、未接受候选、开放 blocker、分辨率、危险裁切、文字溢出、阅读顺序和字体许可；
  - `POST /exports` 在同一事务前复验固定 PageApproval/依赖/preflight hash，状态变化后旧预检不可复用；
  - 正式 PNG/PDF/CBZ 遇任一 blocker 失败关闭；工程备份仍可导出，但 manifest 明确 `incomplete_project`；
  - 导出失败不污染上次成功版本，页序、哈希、秘密扫描和派生物元数据通过；
  - 预检只报告问题，不自动修改页面、豁免 Finding 或触发生成。
- 不包含：Export Center UI、新增发布格式或自动上传。

### MM-058 Export Center 预检交互与问题定位

- 优先级 / 状态 / 规模：`P0 / Todo / 1–2d`；依赖 MM-043、MM-053、MM-065。
- 对应：PRD §8.9、FR-17/18/22、AC-07/11。
- 目标：让用户看懂哪一页、哪一格、哪个批准或 Finding 阻止发布，并能回到正确工作台处理。
- 交付：Export Center feature、preflight summary、按 page/panel/rule 分组、问题深链、预检过期提示、正式导出/未完成工程包区分。
- 验收：
  - fixture 中 9 类 blocker/warning 均显示稳定 rule/error code、page/panel 和可操作入口；
  - preflight hash 过期后正式导出按钮立即禁用，重新预检前不能复用旧结果；
  - 正式 PNG/PDF/CBZ 与 `incomplete_project` 工程包使用不同文案、确认和结果状态；
  - 页面状态变化通过 SSE 后只刷新受影响预检，不触发生成或自动豁免；
  - 前端测试覆盖 blocker→修复→同页通过、stale PageApproval、字体许可和导出失败保留上次成功版本。
- 不包含：后端预检计算、发布上传或新格式。

## v0.3 P0 / 迁移、回归与真实发布门禁

### MM-044 v0.2→v0.3 数据迁移与只读恢复

- 优先级 / 状态 / 规模：`P0 / Todo / 1–2d`；依赖 MM-034、MM-037、MM-043。
- 对应：FR-16、AC-07/09～11；技术架构 §16.6、架构 DoD 10/14。
- 目标：让 schema 16 项目安全进入 v0.3 的“待确认”状态，不伪造版式、Prompt 或审片批准。
- 交付：迁移前数据库备份、追加式数据库迁移、artifact 边补登记、legacy layout/prompt/candidate 转换、失败时只读恢复和 integrity probes。
- 验收：
  - 既有 Storyboard/PageVersion 建立可证明的 lineage，无法证明的边标记 `legacy_unknown`；
  - PageVersion 几何只生成 `imported_legacy` LayoutDraft；flat prompt 标记 legacy；ready asset 可加入 legacy CandidateSet，但不自动 accepted；
  - 旧 PageVersion 没有 PageApproval，仍可作为历史工程恢复；正式 v0.3 发布前必须重新质检和批准；
  - 空库、schema 16 fixture、重复迁移、未知更高版本、磁盘故障和中途崩溃均有测试；
  - 迁移前备份完成哈希/quick_check 后才写新 schema；失败时原备份和不可变文件哈希保持，应用进入只读恢复。
- 不包含：工程包升级、ID 重映射、反向迁移或自动替用户接受旧素材。

### MM-059 工程包升级、恢复与 v0.2 回滚演练

- 优先级 / 状态 / 规模：`P0 / Todo / 1–2d`；依赖 MM-044。
- 对应：FR-16、AC-07/09～11；技术架构 §13、§16.6、架构 DoD 10/14。
- 目标：让 v0.3 新对象可移植，并证明代码回滚时能安全回到迁移前备份，而不是尝试破坏性降级数据库。
- 交付：工程包新 schema、导出/恢复顺序、ID 重映射、v1.4 compatibility reader、pre-migration backup rollback drill 和恢复报告。
- 验收：
  - v0.3 工程包包含非秘密 layout/prompt/candidate/finding/review/PageApproval/lineage/workflow 记录及逐文件哈希；凭证继续替换为重新配置占位符；
  - 在空工作区恢复后对象计数、依赖边、当前指针、PageApproval、页序和不可变素材 SHA-256 与源工程一致；
  - v1.4 fixture 可导入并得到与 MM-044 相同的 legacy 待确认状态，ID 冲突整体重映射；
  - 回滚演练使用 MM-044 的迁移前 schema 16 备份和 `main@40f2cb9` 兼容代码，只读打开项目、恢复 v1.4 包并核对全部历史素材哈希；
  - 回滚报告明确 v0.3 新写入数据不会反向写入 schema 16；切换前保留 v0.3 数据副本，禁止就地降级或删除新表。
- 不包含：自动逆向迁移、覆盖现有项目或外部备份服务。

阶段证据（2026-08-14）：工程包 schema v1.5 已加入 layout、lineage、ProviderExecutionSpec、
GenerationApproval 记录和 `layouts/` 文件，完成同库 ID 冲突全量重映射与 v0.3 round-trip；
v1.4 compatibility reader 继续通过。schema 16 迁移前备份、旧主版本只读回滚演练尚依赖
MM-044，因此本工单保持 In Progress，不标记 Done。

### MM-045 P0 架构与契约测试门禁

- 优先级 / 状态 / 规模：`P0 / Release Gate / Todo / 1–2d`；依赖 MM-027、MM-036、MM-039、MM-043、MM-044。
- 对应：AC-01～11；技术架构 §16、§20 除真实调用项。
- 目标：在进入全链 E2E 前证明模块边界、Schema、Port 和供应商映射没有结构性漏洞。
- 交付：Port/consumer/Schema 契约套件、architecture CI、table ownership/migration checks、NovelAI mapping contract 和静态秘密扫描。
- 验收：
  - 架构循环、禁止 import、跨表写入、未版本化事件、新 `app.state` lookup 和前端跨 feature import 均为 0；
  - PageLayoutDraft、PromptPlan、ProviderExecutionSpec、Candidate/Finding/Review/PageApproval 在 Python/TypeScript/JSON fixture 上兼容；
  - 每个真实 adapter 和 fake 通过同一 Port contract suite；event consumer fixture 覆盖首次、重复、依赖未就绪和永久失败；
  - 单/双/三角色 mapping、正负区块、坐标、action/relationship 和 payload hash 契约通过；
  - 未登记 migration/table、跨模块 cascade、供应商 DTO 泄漏和敏感字段进入工程包 fixture 均会使门禁失败。
- 不包含：全链 E2E、崩溃注入、UI 回归或真实调用。

### MM-060 P0 Mock 产品 E2E 与 v0.2 回归

- 优先级 / 状态 / 规模：`P0 / Release Gate / Todo / 1–2d`；依赖 MM-034、MM-037、MM-041、MM-056、MM-057、MM-058、MM-059、MM-062、MM-064、MM-065。
- 对应：AC-01～11；技术架构 §16、§20 除真实调用项。
- 目标：用完整用户旅程证明 v0.3 P0 产品行为闭环，并确认 v0.2 没有回归。
- 交付：v0.3 Mock E2E、v0.2 全量回归、迁移/工程包 round trip 和 Mock 产品验收报告。
- 验收：
  - TXT → Storyboard → LayoutApproval → PromptPlan → 多候选 → QC → ReviewDecision → PageApproval → 工程包/PNG/PDF/CBZ 全链通过；
  - 横/竖/方格/六格、两个以上候选、八类质量检查、Finding 关闭/豁免、reroll/inpaint 和 stale 路径均有断言；
  - v0.2 全量回归、迁移幂等、schema 16 回滚、空工作区恢复、Ruff、Mypy、前端测试/build 和秘密扫描通过并记录精确数量；
  - Mock 报告逐项勾选 AC-01～11，明确真实文本/NovelAI 请求仍为 0，不冒充 AC-08/10/11 真实证据。
- 不包含：崩溃注入、未知远端结果或任何真实付费调用。

### MM-063 崩溃注入、恢复与未知结果矩阵

- 优先级 / 状态 / 规模：`P0 / Release Gate / Todo / 1–2d`；依赖 MM-030、MM-043、MM-055、MM-059。
- 对应：NFR-01/04、AC-05/07/11；技术架构 §9.3、§16.2、架构 DoD 10/14/16。
- 目标：证明每个跨 SQLite/文件/事件边界都能从中断恢复，且可能计费的请求绝不盲目重发。
- 交付：确定性 fault injector、提交断点矩阵、startup reconciliation 报告和逐断点恢复断言。
- 验收：
  - 在 durable work/outbox、lineage、文件 staging→ready、AssetVersion→CandidateSet、QualityRun、ReviewDecision、PageApproval、preflight→ExportRevision 每个边界前后各注入一次崩溃；
  - 纯本地幂等步骤重启后最多重放一次并收敛到相同 hash；已可能外发的 attempt 全部进入 `needs_review`，新增供应商请求为 0；
  - 部分文件、过期租约、已提交未发布事件、stale approval 和中断导出均进入所属模块的可解释恢复结果；
  - schema 16 备份回滚与 v0.3 工程包恢复后，不可变素材 SHA-256、页序和历史决定保持；
  - 同一断点连续执行 20 次，重复对象、丢失事件和自动付费调用均为 0。
- 不包含：随机 kill 造成的不可复现实验或真实供应商故障注入。

### MM-046 真实 smoke、授权章节与 v0.3 P0 报告

- 优先级 / 状态 / 规模：`P0 / Release Gate / Blocked / 1–2d`；依赖 MM-045、MM-060、MM-063 和用户对服务、预算、素材的单独授权。
- 对应：AC-08、AC-10/11 的真实部分；PRD DoD 4～8/12～14；技术架构 DoD 17～19。
- 目标：证明真实服务和真实创作流程可用，并把质量、成本与未完成项诚实记录下来。
- 交付：最小真实文本阶段、单角色/双角色 NovelAI smoke、代表性授权章节、v0.3 P0 acceptance report、README/PRD/工单状态回写。
- 验收：
  - 开始前由用户明确确认端点、模型、Token 来源、授权章节、请求上限和预算；未经确认保持 Blocked；
  - 双人格人工检查身份串扰、服装/标志物、相对位置和互动动作；至少一个目标比较两个以上候选；
  - 至少一个 accepted 候选完成 reroll 或 inpaint 后重新质检/接受，全部页面经 PageApproval 后导出；
  - 报告 token、图像调用、估算/可验证实际成本、P50/P95、失败/重试、首轮接受率、候选数、审片时间和 blocker/warning 密度；
  - 401/余额不足等破坏场景继续用 Mock，不为测试故意损坏账户或浪费额度；
  - 任何未通过项显式列出，短 smoke 不替代授权章节闭环。
- 不包含：无人值守生产、提高并发或发布到外部平台。

部分完成证据（2026-08-29）：用户已授权 NovelAI V5 Full、零 Anlas 上限与附件《沙王》
素材；真实完成 12 页初版、双人第 8 页、四臂第 12 页和 4 次定向 reroll，四格式导出、
provider payload、秘密扫描与逐页视觉审片通过。证据见 `docs/sandkings-v5-acceptance.md`
及本机 `workspace/acceptance/sandkings-v5/20260829-130820-reroll-1/`。工单仍为 Blocked，
因为 MM-045/MM-060/MM-063、多候选接受/PageApproval、真实外部文本模型和完整 v0.3 P0
报告尚未完成；本次结果不冒充整票 Done。

## v0.3 P1 / V03-P1-01 分层、Token 感知文本流水线

### MM-047 ModelCapabilitySnapshot 与 TokenBudget

- 优先级 / 状态 / 规模：`P1 / Todo / 2–3d`；依赖 Mock P0 门禁 MM-060、MM-063，不依赖付费验收 MM-046。
- 对应：FR-04/05/23、AC-03/12；技术架构 §7.2～7.3、OPEN-09。
- 目标：在请求前知道模型能力来源、输入/Schema/输出/安全余量如何占用上下文，并对裁剪作出可解释决定。
- 交付：显式 capability probe、ModelCapabilitySnapshot、tokenizer/保守估算 Port、TokenBudgetPlanner、TruncationReport、能力来源等级和只读查询契约。
- 验收：
  - probe 是独立用户动作，不能只凭 `/models` 成功猜测上下文、结构化输出或 token 计量能力；
  - snapshot 区分 `provider_reported / probed / conservative_default / unknown` 并被 TextStageRun 冻结；
  - must-retain StoryBeat/SourceAnchor、角色身份/造型、Layout 硬约束和输出 Schema 永不静默裁剪；
  - 可裁剪项按版本策略产生原哈希、原因和替代摘要；硬约束仍超限时缩小 shard，不发请求；
  - 本地预算预检目标 500 ms 内完成，不通过额外外部请求估算；未知 tokenizer 使用保守上界并清楚标注。
- 不包含：真正执行 stage 或改写 Storyboard。

### MM-048 TextStageRun、checkpoint 与 durable 执行

- 优先级 / 状态 / 规模：`P1 / Todo / 2–3d`；依赖 MM-029、MM-047、MM-054。
- 对应：FR-05/23、NFR-01/04/05、AC-12；技术架构 §6.1、§7.2～7.3。
- 目标：让每次文本阶段拥有精确输入、预算、尝试、结果、错误和可恢复检查点。
- 交付：TextStageRun/checkpoint/token ledger/cache metadata 表和契约；TextModelProvider `execute_stage`/repair Port；durable handler；错误归一化和隐私默认值。
- 验收：
  - run 冻结 stage、profile revision、capability snapshot、template/Schema、ordered input hashes、TokenBudget 和幂等键；
  - 空 content、finish_reason 截断、上下文超限、Schema 不完整、证据失败和可修复 JSON 分别归类；
  - 只有“响应完整但格式可修复”才最多修复两次，修复也有独立预算/attempt；
  - 通过 Schema、来源和业务不变量后才提交 checkpoint 并解锁下游；重启只恢复未完成的安全工作；
  - 默认只保存结构化结果、原始响应哈希和错误摘要，不保存完整章节、完整供应商响应或密钥。
- 不包含：阶段 DAG 和业务 UI。

### MM-049 Stage DAG、shard、缓存与最小重跑

- 优先级 / 状态 / 规模：`P1 / Todo / 2–3d`；依赖 MM-030、MM-048。
- 对应：FR-05/23、AC-12；技术架构 §7.3、ADR-014。
- 目标：把一次超长调用改成 `chapter_plan → scene_plan → page_plan → panel_plan → bible/tag → prompt_plan` 的可恢复 DAG。
- 交付：process manager、稳定 shard key、依赖/检查点图、缓存键和 invalidation policy、失败补偿、最小重跑查询。
- 验收：
  - 每个 shard 只消费精确上游 ArtifactRef/hash，不能读取整本未选文本；
  - 相同 stage/profile/capability/template/Schema/ordered input/token policy 命中缓存，任一版本变化使对应最小范围失效；
  - scene/page/panel 中途失败只重跑未完成 shard，已校验的其他页面不重复调用；
  - 重复事件、乱序到达、依赖未就绪和永久失败均有幂等/人工处置路径；
  - workflow 只保存步骤和关联 ID，不复制 Storyboard、PromptPlan 或 Bible 真源。
- 不包含：真实长章节调用。

### MM-050 分层改编/设定/Prompt 后端接线

- 优先级 / 状态 / 规模：`P1 / Todo / 1–2d`；依赖 MM-049。
- 对应：PRD §8.3/8.10、FR-04/05/07/23、AC-03/04/12。
- 目标：把 adaptation、world_bible 和 prompting 的写路径切到阶段流水线，同时保留可控的 legacy compatibility。
- 交付：业务 stage handlers、公开 facade 接线、legacy adapter 切换条件、lineage/cache invalidation 和后端阶段查询/重试 API。
- 验收：
  - 结构化改编、CharacterTagSet 和 PromptPlan 使用同一激活 TextModelProfile revision，不静默切换；
  - 配置变化、上游修改或 cache policy 变化只使必要 stage stale，并展示影响范围；
  - AC-12 通过前，旧一次性真实生产路径默认关闭；compatibility 入口清楚标记 legacy，不能失败后静默回退；
  - stage 查询返回状态、上游版本、预算、用量、裁剪、checkpoint、cache 和可恢复 command，不泄露完整正文/响应；
  - 后端 Mock 覆盖长输入、空 content、截断、错误 JSON、证据失败和局部恢复。
- 不包含：阶段 UI 或真实模型验收。

### MM-061 TextStage 阶段 UI 与 Token 报告

- 优先级 / 状态 / 规模：`P1 / Todo / 1–2d`；依赖 MM-049、MM-053。
- 对应：PRD §8.3/8.10、FR-04/05/23、AC-03/12。
- 目标：让用户看见每个文本阶段为何运行、裁掉什么、失败在哪里，以及恢复会重跑多少范围。
- 交付：Adaptation Workbench 阶段树、设置页 capability 来源摘要、TokenBudget/Truncation/usage/cache 视图、checkpoint 重试、影响预览和 SSE 状态更新。
- 验收：
  - UI 展示 chapter/scene/page/panel/bible/prompt 的状态、上游版本、输入/Schema/预留输出/安全余量、供应商/估算用量和裁剪项；
  - 空 content、截断、上下文超限、JSON 可修复、证据失败使用不同错误码和操作文案；
  - 重试前显示将重跑的 shard 数与已缓存阶段，用户可取消且不会产生外部请求；
  - 配置/模板/Schema/上游变化后只刷新受影响分支，cache hit/miss 原因可解释；
  - 前端 fixture 测试覆盖局部失败恢复、SSE 重连、revision conflict 和凭证脱敏。
- 不包含：后端 stage 执行或真实模型调用。

### MM-051 长章节与真实文本流水线验收

- 优先级 / 状态 / 规模：`P1 / Blocked / 1–2d`；依赖 MM-050、MM-061 和用户对真实文本模型与授权章节的单独授权。
- 对应：AC-12、PRD DoD 10/12/14；技术架构实施顺序 11。
- 目标：证明 Token 预算、分片、检查点和最小重跑在代表性长章节及真实服务上成立。
- 交付：超限/截断/中途失败 fixture 报告、用户授权长章节真实运行、P1 验收报告、legacy 路径去留决定。
- 验收：
  - fixture 证明 must-retain 内容和 Schema 不被静默裁剪，所有可裁剪项进入 TruncationReport；
  - 在中间 stage 注入失败后，只重跑最小未完成 shard，已校验上游和其他页面调用数不增加；
  - 真实运行记录 profile/capability/template/Schema、供应商与估算 token、缓存命中、裁剪、墙钟、失败和重试；
  - 配置或上游版本变化准确失效缓存；无可靠 tokenizer/上下文能力时保守阻断，不冒险发送；
  - AC-12 未全部通过时保留显式 legacy 兼容，但继续禁止其作为默认真实生产入口；通过后才按 ADR 删除或限定旧路径。
- 不包含：额外文本供应商、自动提示词压缩或无限重试。

## v0.2 已完成功能点

| 功能域 | 功能点 | 验收结果 |
|---|---|---|
| 文本模型设置 | 设置表单包含备注名称（可选）、URL、Key/Password、Request Model；首次一次保存，后续修改非秘密字段可留空保留原 Key/Password | 非敏感配置进入本地 SQLite，Key/Password 只写入已解锁的应用加密凭证库；任何读取响应不返回秘密 |
| 统一文本模型来源 | 结构化改编、角色/风格设定草拟、角色固定 Tags、NovelAI Prompt 与结构修复使用同一当前配置版本 | 每个模型产物记录配置版本、模型、端点主机、模板版本、token 与耗时，不静默回退 |
| 模型化角色与风格草拟 | 使用配置的文本模型，从已审批 Storyboard 生成结构化 CharacterBible 与 StyleBible | 通过版本化 Schema、来源版本校验、人工编辑和独立审批后才能继续 |
| 角色固定 Tags | 为每个角色/造型生成有序固定 Tags 与负向 Tags，区分逐格可变 Tags | CharacterTagSet 独立版本化和审批；固定 Tags 变更只使相关 Prompt 失效 |
| NovelAI PromptPackage | 文本模型为每格生成基础画面、角色区块、可变 Tags、风格 Tags 和负向 Tags | PromptPackage 通过 Schema、角色覆盖和冲突检查，可预览、修改、批准和追溯 |
| 确定性 Prompt 编译 | 本地编译器把批准的固定 Tags 原样注入对应角色区块 | 相同输入得到相同 Prompt/哈希；模型不能改写、漏掉或混合不同角色的固定 Tags |
| 生成门禁与冻结 | 图像任务冻结 Storyboard、Bibles、CharacterTagSet、PromptPackage、文本模型来源和 NovelAI 配置 | 缺失、过期、哈希不符或未批准时在本地阻止请求；执行期不再临时调用文本模型改写 Prompt |
| 成本、审计与可移植性 | 文本 token 按任务分类，Prompt/Tag 来源进入审计和工程包，密钥仍保持零泄露 | 工程包可恢复新对象与引用；日志、工程包和成品导出均不含密钥原文 |

## v0.2 已完成执行顺序

| 顺序 | 工单 | 优先级 | 状态 | 依赖 |
|---:|---|---|---|---|
| 1 | MM-001 Git 与仓库基线 | P0 | Done | 无 |
| 2 | MM-002 FastAPI 本地应用骨架 | P0 | Done | MM-001 |
| 3 | MM-003 SQLite、迁移与单写者 | P0 | Done | MM-002 |
| 4 | MM-004 应用本地加密凭证库 | P0 | Done | MM-002 |
| 5 | MM-005 React 本地前端骨架 | P0 | Done | MM-002 |
| 6 | MM-006 项目创建与工作区 | P1 | Done | MM-003 |
| 7 | MM-007 TXT 预检与编码确认 | P1 | Done | MM-006 |
| 8 | MM-008 章节修正、SourceAnchor 与 StoryBeat | P1 | Done | MM-007 |
| 9 | MM-009 文本模型适配与结构化分镜 | P1 | Done | MM-008、MM-004 |
| 10 | MM-010 角色表、风格板与审批门禁 | P1 | Done | MM-009 |
| 11 | MM-011 NovelAI 契约、Mock 与连接测试 | P1 | Done | MM-004、MM-010 |
| 12 | MM-012 有界串行生成队列 | P1 | Done | MM-003、MM-011 |
| 13 | MM-013 NovelAI 逐格生成与素材版本 | P1 | Done | MM-012 |
| 14 | MM-014 页面编辑与确定性合成 | P1 | Done | MM-013、MM-005 |
| 15 | MM-015 reroll、inpaint 与版本恢复 | P1 | Done | MM-014 |
| 16 | MM-016 工程包、PNG、PDF、CBZ | P1 | Done | MM-014、MM-015 |
| 17 | MM-017 崩溃恢复、安全与单章验收 | P1 | Done | MM-006–MM-016 |
| 18 | MM-101 跨章节状态账本 | P2 | Done | MM-017 |
| 19 | MM-102 整本预算、按章队列与恢复 | P2 | Done | MM-101 |
| 20 | MM-201 高级版式、彩色与条漫 | P3 | Done | MM-102 |
| 21 | MM-018 文本模型四字段配置 | P1 | Done | MM-004、MM-009 |
| 22 | MM-019 模型化设定与 CharacterTagSet | P1 | Done | MM-018、MM-010 |
| 23 | MM-020 PromptPackage 与确定性编译 | P1 | Done | MM-019、MM-011 |
| 24 | MM-021 生成冻结、导出与恢复升级 | P1 | Done | MM-020、MM-012–MM-017 |
| 25 | MM-022 v0.2 前端闭环与回归验收 | P1 | Done | MM-018–MM-021 |

## v0.2 P0 / Blocker 工单

### MM-001 Git 与仓库基线

- 目标：建立可审计、可测试、不会提交运行数据和密钥的仓库。
- 交付：`main` 分支、`.gitignore`、工程说明、测试入口、初始提交。
- 验收：
  - Git 仓库位于 `Manga Maker/.git`；
  - `.venv`、`node_modules`、应用数据、工作区、凭证库、缓存和构建结果被忽略；
  - `git status` 不包含任何凭证或运行数据；
  - 使用真实本机 Git 身份完成初始提交，不伪造提交者信息。
- 完成证据：Git 已初始化为 `main` 分支，忽略规则和密钥扫描已通过，并使用用户提供的仓库本地 Git 身份完成初始提交。

### MM-002 FastAPI 本地应用骨架

- 目标：提供只监听 loopback 的本地后端和稳定测试入口。
- 交付：Python 3.12 项目、FastAPI app factory、配置、`/health`、错误模型、测试。
- 验收：
  - 应用拒绝非 loopback host 配置；
  - `/health` 返回版本、环境和数据库状态，不暴露路径或密钥；
  - 所有测试可由一条命令运行；
  - README 明确实际可运行与尚未实现的边界。
- 完成证据：`/health`、loopback 配置校验、统一错误模型、pytest/ruff/mypy 已通过。

### MM-003 SQLite、迁移与单写者

- 目标：建立项目元数据、版本与任务状态的持久基础。
- 交付：SQLite connection manager、schema migration、事务、单写者锁、reconciliation 入口。
- 验收：
  - foreign keys、WAL 和 busy timeout 生效；
  - 重复迁移幂等；
  - 两个并发写命令不会破坏 revision；
  - 重启后 schema 与数据可恢复。
- 完成证据：migration 幂等、foreign keys/WAL、20 路并发写入测试通过。

### MM-004 应用本地加密凭证库

- 目标：不使用 Keychain，在软件本地安全保存 LLM 和 NovelAI Token。
- 交付：Argon2id + XChaCha20-Poly1305 vault、创建/解锁/锁定/保存/读取/删除/重置接口。
- 验收：
  - vault 位于应用数据目录，不在项目或 SQLite；
  - 主密码、派生密钥和 Token 不写日志或前端；
  - 错误密码、密文篡改、截断文件均失败关闭；
  - 原子更新失败不会破坏上一个可用 vault；
  - 忘记密码只能重置凭证，不删除项目。
- 完成证据：创建/解锁/锁定/增删凭证、权限、错误密码、篡改和可恢复 reset 测试通过。

### MM-005 React 本地前端骨架

- 目标：建立无第三方远程资源的本地项目壳。
- 交付：React/TypeScript/Vite、路由、API client、错误边界、健康页、基础测试。
- 验收：
  - 所有脚本、字体和样式本地提供；
  - 前端不保存模型 Token；
  - 后端不可用时显示可操作错误；
  - build 和测试通过。
- 完成证据：本地健康页、离线错误状态、TypeScript build 和 7 项前端测试通过。

## v0.2 P1 / High 工单

### MM-006 项目创建与工作区

- 建立 UUIDv7 项目、稳定目录、manifest、磁盘预检和安全路径解析。
- 创建失败不留下半成品；项目删除默认可恢复。
- 完成证据：项目 API、本地会话/CSRF、稳定工作区、staging/orphan 故障边界与项目列表界面均已通过测试。

### MM-007 TXT 预检与编码确认

- 支持 UTF-8、UTF-8 BOM、GB18030/GBK 候选、换行规范化和异常预览。
- 置信度不足时禁止静默确认；源文件复制后保存 SHA-256。
- 完成证据：10 MB 上限、二进制拒绝、编码候选/预览、显式确认、原始字节与规范化 UTF-8 双份保存均已通过接口测试并接入界面。

### MM-008 章节修正、SourceAnchor 与 StoryBeat

- 识别中文章节标题，支持拆分、合并、重命名和边界修正。
- 创建不可变 SourceChapter；SourceAnchor offset、摘录与哈希可往返验证。
- 完成证据：章节新版本、光标拆分/相邻合并/重命名、过期章节拒绝、本地 StoryBeat 草拟和逐项 SourceAnchor 往返验证均已通过测试。

### MM-009 文本模型适配与结构化分镜

- 实现 OpenAI-compatible adapter、Storyboard JSON Schema 和最多两次结构修复。
- StoryBeat 处理率必须 100%，`unresolved` 阻止审批。
- 完成证据：已交付 OpenAI-compatible 配置与显式连接测试、只读取所选章节和 StoryBeat 的适配链路、场景→页→格版本化契约、100% 来源覆盖与最多两次修复、不可变分镜版本、可视化编辑工作台、过期来源/未解决节拍审批门禁，以及模型/参数/hash/token/耗时等非秘密 provenance。32 项后端测试、6 项前端测试、ruff、mypy 和生产构建通过；仅使用 Mock，未执行真实模型调用。

### MM-010 角色表、风格板与审批门禁

- 生成/编辑 CharacterBible、StyleBible、参考图和审批版本。
- 任一输入变化使相关审批和生成估算失效。
- 完成证据：已交付基于已审批 Storyboard 的本地确定性草拟、角色与黑白风格可视化编辑、不可变独立版本/审批哈希、修改后受影响面板清单、角色与风格双审批生成门禁，以及参考图授权确认、PNG/JPEG/WebP 真实解码、10 MB/尺寸/像素限制、内容寻址保存和会话保护读取。新增参考图或上游分镜变化都会使当前生成就绪失效；36 项后端测试、7 项前端测试、ruff、mypy 和生产构建通过，未执行真实模型或 NovelAI 调用。

### MM-011 NovelAI 契约、Mock 与连接测试

- 固定官方 Swagger 哈希、能力 profile、错误分类和本地 mock。
- 连接测试由用户点击触发，不进行隐藏图片生成。
- 完成证据：已于 2026-08-29 固定官方 `docs/doc.json` 的 URL、113,758-byte 大小、SHA-256 和 V5 映射版本，并明确排除错误的 Observability `/openapi.json`；交付 8 个模型 capability（V5 Full 为唯一推荐默认）、固定 NovelAI host/path、应用本地加密 Token profile、项目非敏感配置、标签建议与订阅/使用额度连接测试、本地 Mock，以及认证、权限、余额、使用额度、限流、参数、网络、5xx 和异常响应分类。连接测试不自动重试并返回 `generated_images = 0`；真实生成结果另见沙王 V5 验收清单。

### MM-012 有界串行生成队列

- Job 固定 panel 清单、调用/成本上限和用户动作；默认串行。
- 暂停/取消后不领取新项；重启不自动恢复付费调用。
- 完成证据：已交付基于已审批 Storyboard/CharacterBible/StyleBible 和已验证 NovelAI 配置的确定性计划指纹，冻结有序 panel、模型映射版本、契约哈希、用户动作、每格保守成本预留、总成本与最多三倍 panel 数的调用上限；SQLite Job/Item/Attempt 状态机通过 partial unique index 与单写者事务保证全应用单在途，支持 revision 冲突保护、开始、暂停、恢复、取消、成本超限转人工审阅及启动 reconciliation。queued 项重启不自动开始，running 无在途转 paused，在途转 needs_review；55 项后端测试、10 项前端测试、ruff、mypy 和生产构建通过，图像执行器尚未接入且外部请求数为 0。

### MM-013 NovelAI 逐格生成与素材版本

- 支持生成、参考图预处理、响应校验、原始 PNG 和 provenance sidecar。
- 真实调用必须在 mock 全部通过且用户单独确认后执行。
- 完成证据：已交付手工 allowlist 的 `POST /ai/generate-image` JSON 映射、固定 host/模型/契约、每格不可变 GenerationSpec、供应商要求的 6 位字母数字 correlation ID 与本地 UUIDv7 attempt ID、最多一张经 EXIF 修正和黑边 padding 的 Precise Reference、严格 200/201、JSON/base64 或安全 ZIP、PNG/尺寸/seed 来源校验，以及 `original.png` + provenance + AssetVersion 原子登记。ZIP 不提供供应商 seed 时只记录请求 seed 为 effective seed，不伪装成响应值。发送前读取加密凭证并消耗冻结上限；明确连接失败/5xx 最多重试两次，发送后结果不明立即转人工审阅且不重放。界面要求在启动 Job 后再次勾选确认才调度，并轮询进度、预览本地素材；75 项后端测试、11 项前端测试、ruff、mypy 和生产构建通过。真实 NovelAI 付费请求为 0，尚未执行用户批准的低成本 smoke。

### MM-014 页面编辑与确定性合成

- 1–6 格模板、裁切、气泡、中文文字、旁白、音效和页码。
- 后端生成 2048×3072 规范 PNG；文字/布局修改不调用图片 API。
- 完成证据：已交付六种 1–6 格固定模板、素材焦点/缩放裁切、对白气泡/旁白框/描边音效字/页码、本机 CJK 字体哈希和 Pillow 固定参数渲染；服务端以乐观锁创建不可变 PageVersion 和 2048 × 3072 黑白 PNG，旧文件不覆盖，重复内容按哈希幂等。页面编辑器支持模板、格框、裁切和文字图层修改，并明确标示为仅本地操作；页面接口与审计固定 `external_requests_started = 0`。测试覆盖模板边界、确定性中文渲染、旧版本保留、冲突/溢出拒绝和前端保存不访问 NovelAI；80 项后端测试、12 项前端测试、ruff、mypy、生产构建、diff/密钥扫描均通过，真实 NovelAI 请求为 0。

### MM-015 reroll、inpaint 与版本恢复

- 单格、整页和蒙版重绘均创建不可变版本。
- 恢复只切换指针，不删除分支或产生外部调用。
- 完成证据：已交付单格/整页 reroll 与 PNG 蒙版 inpaint 的范围预检、有界 Job 和两层人工确认，精确冻结父 PageVersion/AssetVersion、蒙版/父图哈希、局部说明、强度、模型和成本上限；官方 `infill` allowlist 映射经 Mock 契约测试，Focused Inpainting 明确排除。结果追加父子 AssetVersion，并自动派生 PageVersion；并发页面修改时保留非当前分支。素材和页面历史激活使用冲突保护，只切换指针、不删除文件，审计外部请求数为 0。蒙版拒绝非 PNG、尺寸不符、空选、全图选中和不匹配父素材；Mock 验证黑色未选区域像素保持。84 项后端测试、13 项前端测试、ruff、mypy、生产构建与 diff 检查通过，真实 NovelAI 请求为 0。

### MM-016 工程包、PNG、PDF、CBZ

- 导出绑定固定 PageVersion 清单；失败不污染上次成功结果。
- 工程包支持 dry-run、哈希、Zip Slip/解压炸弹防护和空工作区恢复。
- 完成证据：已交付冻结完整 PageVersion 页序与哈希的 ExportRevision、逐页零填充 2048 × 3072 PNG、同源多页 PDF、含 `ComicInfo.xml` 的 CBZ，以及用非秘密“需重新配置”占位符替换凭证引用并移除源机工作区路径的 `.manga-maker.zip`。四种结果在 staging 中完成页数/页序/尺寸/打开/SHA-256 校验后才原子发布；注入 PDF 失败证明旧成功版本不变。工程包 dry-run 在零项目写入下检查 schema、对象计数、逐文件哈希、磁盘空间、文件数/大小/总展开量/压缩比，并拒绝 Zip Slip、符号链接和重复路径；确认恢复时创建新工作区，ID 冲突整体重映射且页面哈希和当前指针保持。后端与前端自动化测试、ruff、mypy 和生产构建通过，外部图像请求新增数为 0。

### MM-017 崩溃恢复、安全与单章验收

- 覆盖所有两阶段提交断点、未知计费、磁盘不足、日志脱敏和秘密扫描。
- 完成一个授权章节的 mock 闭环；真实调用与真实单章分别取得用户确认。
- 完成证据：schema v12 增加持久化恢复摘要与导出秘密扫描结果；启动时将未知计费任务转人工审阅、中断导出失败关闭并保留项目/素材半成品，且不会自动调用供应商。生成与导出磁盘耗尽分别归一化并保持旧版本；诊断递归脱敏，默认关闭 access log；已解锁凭证字节会扫描普通文件和 ZIP 条目，命中不回显且阻止发布。自有合成章节完成 TXT 到四格式导出的 Mock 闭环与单格 reroll/恢复，96 项后端、15 项前端测试、ruff、mypy 和生产构建通过。详见 `P0_ACCEPTANCE_REPORT.md`。真实 NovelAI 请求仍为 0，付费 smoke 与代表性授权章节真实生产明确未执行。

### MM-018 文本模型四字段配置

- 目标：把文本模型配置收敛为用户可一次完成的四字段设置，并允许不重复提交秘密地修改非秘密字段。
- 交付：`备注名称（可选）`、`URL`、`Key/Password`、`Request Model` 四字段 API 与界面；固定项目级本地凭证引用；配置版本和脱敏状态。
- 验收：
  - 保存时只做本地校验和持久化，不隐式发出网络请求；
  - 备注名称、URL 和 Request Model 保存在本地数据库，Key/Password 只写入已解锁加密凭证库；
  - Key/Password 不进入响应、SQLite、日志、工程包、前端状态或浏览器存储；
  - 首次保存要求 Key/Password；保存后清空输入，后续留空则保留原凭证，应用重启后显示脱敏状态；
  - 缺字段、凭证库锁定、非 loopback 明文 HTTP 与任务期间配置变化均失败关闭。
- 完成证据：设置界面与 API 已收敛为备注名称（可选）、URL、Key/Password、Request Model 四项；schema 32 保存可空备注并从 schema 31 无损迁移。Key/Password 写入项目稳定引用的本地加密 vault，更新非秘密字段时无需重发，保存响应、SQLite 与审计不含原文。旧 `provider_api_url`/`base_url`、`model_name`/`model`、`api_key` 请求别名仅保留兼容；配置修订在模型调用结束前再次核对。

### MM-019 模型化设定与 CharacterTagSet

- 目标：让当前文本模型承担设定草拟和固定角色标签任务，同时保留人工创作控制。
- 交付：模型生成 CharacterBible/StyleBible；CharacterTagSet Bundle Schema、版本、编辑、审批、来源与失效规则。
- 验收：
  - 只有已审批且未过期的 Storyboard 能触发模型设定草拟；
  - 每个角色恰好有一个默认造型 CharacterTagSet，固定 Tags 有序、去重并计算哈希；
  - 固定 Tags 与动作、姿势、表情、镜头等逐格变量分离；
  - 模型输出缺角色、增加未知角色、复用 ID 或违反 Schema 时最多修复两次后停止；
  - 用户修改 Tags 创建新版本，不覆盖旧版本，并使依赖 PromptPackage 失效。
- 完成证据：CharacterBible/StyleBible 改为由当前文本模型结构化生成，并记录模型、端点、模板、token、耗时、修复次数和配置修订；schema v16 增加 CharacterTagSet Bundle 版本和审批，严格校验角色全集、稳定 ID、有序单项 Tags 与固定部分 SHA-256。人工修改创建新版本并使已有 PromptPackage 失效。

### MM-020 PromptPackage 与确定性编译

- 目标：在出图前生成、预览并批准每格 NovelAI Prompt，避免执行期临时自由拼接。
- 交付：PromptDraft/PromptPackage Schema、文本模型生成、结构化编辑、独立审批、本地确定性编译与哈希。
- 验收：
  - 当前文本模型为每个已审批面板恰好生成一个 Prompt 草案；
  - 每个角色使用独立 Prompt 区块并引用明确 CharacterTagSet；
  - 本地编译器忽略模型返回的固定 Tags，始终从批准版本原样注入；
  - 固定/可变 Tags 冲突、角色缺失、未知面板或超长 Prompt 均在本地阻断；
  - 相同输入版本重复编译得到完全相同的 Prompt 与 SHA-256。
- 完成证据：文本模型为每格输出 Prompt 草案组件；服务端校验面板全集和逐格角色区块后，从已审批 CharacterTagSet 本地注入固定 Tags，去重编译正负 Prompt 并计算 SHA-256。API 和界面支持组件修改、重新编译、最终 Prompt 预览与独立审批。

### MM-021 生成冻结、导出与恢复升级

- 目标：把 v0.2 的 Tags、Prompt 与文本模型来源纳入现有有界任务和可恢复工程。
- 交付：schema v16、新对象表、GenerationJob/GenerationSpec 扩展、队列门禁、工程包 v1.4 和 ID 重映射恢复。
- 验收：
  - 估算和任务指纹冻结 CharacterTagSet、PromptPackage、文本配置版本与哈希；
  - NovelAI 执行器只读取冻结 Prompt，不在发送前调用文本模型；
  - reroll/inpaint 保持固定 Tags，局部修改只能追加到可变 Prompt；
  - 旧任务缺少 v0.2 快照时失败关闭并提示重新创建；
  - 工程包包含非秘密来源和新版本对象，恢复后凭证仍要求重新录入。
- 完成证据：GenerationPlan/Job/Spec 已冻结 TagSet、PromptPackage、文本配置修订、最终 Prompt 和哈希；NovelAI 执行器只读取并复验冻结 Prompt，inpaint 只追加局部变量说明。工程包升级为 v1.4、最低数据库 schema v16，按依赖顺序导出和 ID 重映射恢复六张新表，凭证仍替换为重新配置占位符。

### MM-022 v0.2 前端闭环与回归验收

- 目标：让用户在现有本地 Web 工作流中完成配置、设定、Tags、Prompt、审批和生成预检。
- 交付：三项模型设置、模型外发确认、Tags 编辑审批、逐格 Prompt 预览审批、生成预检摘要、自动化测试与状态文档。
- 验收：
  - 界面不再要求用户先到另一个区域创建“文本模型凭证引用”；
  - 用户可看到固定 Tags 与逐格变量的边界，以及最终发送给 NovelAI 的 Prompt；
  - 修改上游版本后，界面明确显示过期原因并阻止生成；
  - 后端测试、前端测试、ruff、mypy、生产构建、迁移幂等和秘密扫描全部通过；
  - Mock 通过不冒充真实文本模型或真实 NovelAI 验收。
- 完成证据：前端已交付三字段文本模型配置、每次模型外发确认、模型化设定、固定 Tags 编辑审批、逐格 PromptPackage 编辑/最终预览/审批，以及生成预检中的逐格冻结 Prompt。109 项后端测试、20 项前端测试、ruff、mypy、TypeScript、生产构建、迁移幂等、工程包恢复、稳定对象 ID 回归与 diff 检查通过；全部外部模型测试使用 Stub/Mock，未执行真实文本模型或 NovelAI 付费调用。

## v0.2 P2 / P3 工单

### MM-101 跨章节状态账本

- 角色、服装、道具、场景和剧情状态跨章版本化；变更提供影响分析。
- 完成证据：schema v13 增加项目级 ContinuityLedger、不可变版本和独立审批；账本只能按当前章节集顺序、从已审批 Storyboard 与 CharacterBible 在本机草拟，五类状态保留稳定 entry ID、来源章节与分格。手工编辑先计算 added/changed/removed，再定位未来已审批章节的受影响分格；上游来源变化阻止审批，未批准版本阻止推进下一章。可视化工作台支持分组编辑、预览影响、保存与批准，工程包 v1.1 可整体备份和 ID 重映射恢复。99 项后端、16 项前端测试、ruff、mypy 和生产构建通过；新增外部请求为 0。

### MM-102 整本预算、按章队列与恢复

- 全书页数/成本规划，按章审批和断点续跑；不引入无限并发。
- 完成证据：schema v14 增加不可变整本生产计划与逐章预算快照；估算要求当前章节集、逐章生成就绪状态和贯穿末章的已批准连续性账本。用户确认全书硬上限后仍须逐章审批，启动后每次人工“推进”最多创建一个现有有界 GenerationJob，且图像执行继续要求生成控制台的独立二次确认；不会定时推进、后台创建下一章或并发出图。暂停、恢复和取消同步到当前本地任务；重启将活动计划暂停，未知计费章节转人工复核，失败章节只在显式复核后重置。整本计划进入工程包 schema v1.2；102 项后端、17 项前端测试、ruff、mypy 和生产构建通过，新增真实 NovelAI 请求为 0。

### MM-201 高级版式、彩色与条漫

- 彩色分页、右到左、竖向条漫、更多模板和可复用素材库。
- 完成证据：schema v15 增加项目级可复用素材库，引用现有不可变 AssetVersion，可编辑元数据、归档/恢复并跨面板用于后续 PageVersion，不复制文件、不出图；工程包 schema v1.3 完整保存并重映射这些引用。PageDocument v2 在兼容 v1 默认黑白页的同时支持黑白/彩色、左到右/右到左/从上到下、可选底色和最高 16,000 px 的有界画布；Pillow renderer v2 按页面文档确定性保留 RGB 或转灰度。模板从六种基础分页扩展到 16 种，包含左右对开、主镜头、RTL 与 1–6 格竖向条漫。PNG/PDF/CBZ 按每页真实尺寸导出，CBZ 写入 RTL 元数据；界面可切换页面 profile、模板并管理/复用素材。106 项后端、17 项前端测试、ruff、mypy 和生产构建通过；新增真实 NovelAI 请求为 0。

## 工单变更规则

- 每次开发开始将一个工单设为 `In Progress`，同一时间只允许一个最高优先级主工单。
- 同一波次只有在文件边界和依赖无重叠时才并行；共享 migration、公开契约或状态机的改动先由上游工单合并。
- 发现新工作时先归属现有工单；范围独立才新增编号。
- 优先级变化必须写明原因、依赖和对产品 P0 验收的影响。
- PRD 的 FR/AC、技术架构 ADR/DoD 或对象所有权变化时，同一变更必须更新追踪矩阵和受影响工单。
- 单张工单超过 3 个开发日、需要两个独立回滚点或同时改动两个无公开契约的业务模块时，停止并继续拆分。
- `Blocked` 的真实验收工单只有在用户明确批准服务、模型、预算和素材范围后才能转为 `In Progress`。
- 真实 NovelAI 调用、永久删除、发布或外部部署不因工单存在而自动获得授权。
