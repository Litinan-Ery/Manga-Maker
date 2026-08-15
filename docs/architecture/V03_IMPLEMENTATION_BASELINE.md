# Manga Maker v0.3 实施基线

| 项目 | 内容 |
|---|---|
| 状态 | MM-023 实施基线；不代表 v0.3 功能已交付 |
| 决策日期 | 2026-08-13 |
| 需求真源 | `PRD.md` |
| 架构真源 | `TECHNICAL_ARCHITECTURE.md` |
| 排期真源 | `WORK_ITEMS.md` |
| 代码兼容基线 | `main@40f2cb9`（v0.2） |

## 1. 模块与对象所有权

跨模块只能导入提供方的 `public.py` 或 `contracts.py`。对象正文、状态机、私有表和工作区目录只允许 owning module 写入。

| owning module | v0.3 核心对象 | 公开消费方 |
|---|---|---|
| `project_source` | Project、SourceFile、SourceChapter、SourceAnchor | adaptation、world_bible、asset_catalog |
| `text_execution` | TextModelProfile 引用、ModelCapabilitySnapshot、TokenBudget、TextStageRun、checkpoint、token ledger | adaptation、world_bible、prompting |
| `adaptation` | StoryBeat、Storyboard、StoryboardApproval | world_bible、layout、prompting |
| `world_bible` | CharacterBible、CharacterTagSet、StyleBible、ContinuityLedger 及批准 | prompting、production、review |
| `layout` | PageLayoutDraft、FrameSpec、LayoutApproval、DimensionSelection | prompting、production、review、composition |
| `prompting` | PromptPlan、PromptPackage、PromptApproval | production |
| `production` | GenerationApproval/Spec/Job/Attempt、ProviderExecutionSpec、AssetVersion、MaskAsset | review、composition、asset_catalog |
| `review` | PanelCandidateSet、QualityRun/Finding、ReviewDecision、PageApproval | composition、exporting |
| `composition` | PageVersion、页面图层、规范渲染派生物 | exporting |
| `asset_catalog` | AssetLibraryItem | UI/query aggregator |
| `exporting` | ExportPreflight、ExportRevision、package manifest | UI/workflow |
| `lineage` | ArtifactRef、artifact dependency、invalidation event | production、review、composition、exporting、workflow |
| `chapter_workflow` / `book_workflow` | process step、checkpoint、compensation、人工门禁状态 | app/API composition |
| `durable_work`（platform） | work item、outbox、lease、handled event | module adapters；不解释业务 payload |
| `observability`（platform） | allowlist audit event、correlation metadata | 所有模块经 port 写入 |

## 2. 编译期依赖白名单

`shared_kernel` 与 platform ports 对所有业务模块可见；platform/shared kernel 不反向依赖业务模块。未列出的业务模块依赖一律拒绝。

| 消费模块 | 允许依赖的业务公开契约 |
|---|---|
| `project_source`、`text_execution`、`lineage` | 无 |
| `adaptation` | `project_source`、`text_execution` |
| `world_bible` | `project_source`、`adaptation`、`text_execution` |
| `layout` | `adaptation` |
| `prompting` | `adaptation`、`world_bible`、`layout`、`text_execution` |
| `production` | `prompting`、`world_bible`、`layout`、`lineage` |
| `review` | `production`、`world_bible`、`layout`、`lineage` |
| `composition` | `production`、`review`、`layout`、`lineage` |
| `asset_catalog` | `project_source`、`production` |
| `exporting` | `composition`、`review`、`lineage` |
| workflows | 上述模块的 public facade；业务模块不得反向依赖 workflow |

## 3. SQLite 表所有权草案

| owner | tables |
|---|---|
| `project_source` | `projects`、`source_files`、`source_chapters`、`source_anchors` |
| `text_execution` | `text_model_profiles`、`model_capability_snapshots`、`text_stage_runs`、`text_stage_checkpoints`、`token_ledgers` |
| `adaptation` | `story_beats`、`storyboards`、`storyboard_versions`、`storyboard_approvals` |
| `world_bible` | `character_bibles`、`character_tag_sets`、`style_bibles`、`continuity_ledgers` 与对应 approvals |
| `layout` | `page_layout_drafts`、`layout_approvals`、`dimension_selections` |
| `prompting` | `prompt_packages`、`prompt_approvals` |
| `production` | `generation_specs`、`provider_execution_specs`、`generation_jobs`、`generation_items`、`provider_attempts`、`asset_versions`、`mask_assets`、`cost_estimates`、`cost_records` |
| `review` | `panel_candidate_sets`、`quality_runs`、`quality_findings`、`review_decisions`、`page_approvals` |
| `composition` | `comic_pages`、`page_versions` |
| `asset_catalog` | `asset_library_items` |
| `lineage` | `artifact_versions`、`artifact_dependencies`、`invalidation_events` |
| `durable_work` | `work_items`、`outbox_events`、`worker_leases`、`handled_events` |
| `observability` | `audit_events` |
| `exporting` | `export_preflights`、`export_revisions`、`export_files`、`package_manifests` |

旧 schema 16 的历史表暂由 `backend/app/database.py` compatibility seam 管理。MM-027 建立可执行 registry 后，新增 migration 必须位于 owner 的 `migrations/`；MM-044 完成迁移且所有消费方切换前，不删除旧 migration runner。

## 4. 工作区目录所有权草案

| owner | project-relative directory/file |
|---|---|
| `project_source` | `manifest.json`（项目身份字段）、`source/` |
| `adaptation` | `storyboard/versions/` |
| `layout` | `layouts/versions/` |
| `world_bible` | `bibles/characters/`、`bibles/styles/`、`assets/references/` |
| `prompting` | `prompts/versions/` |
| `text_execution` | `text-runs/` |
| `production` | `assets/panels/`、`assets/masks/`、`assets/staging/` |
| `review` | `quality/candidate-sets/`、`quality/findings/` |
| `composition` | `pages/` |
| `exporting` | `exports/`、`manifest.json` 的导出清单字段 |
| `observability` | `audit/` |

共享 `manifest.json` 通过版本化 manifest writer port 合并 owner 片段；模块不能直接覆盖其他 owner 的片段。绝对路径不得出现在公开契约、SQLite 业务记录或工程包中。

## 5. v0.2 → v0.3 fixture 清单

| fixture | 证明内容 | 主工单 |
|---|---|---|
| schema 16 最小数据库 | 空库迁移、重复迁移、只读恢复 | MM-024、MM-044 |
| schema 16 完整单章数据库 | adaptation、bibles、prompting、generation、pages、exports、audit 兼容 | MM-024、MM-044 |
| 工程包 v1.4 | dry-run、ID 重映射、文件哈希、恢复到空工作区 | MM-024、MM-059 |
| 单角色 PromptPackage v1 | legacy flat prompt 可读、不可冒充 PromptPlan v2 | MM-024、MM-035 |
| 双角色 flat prompt v1 | 迁移后标记 `legacy_flat_prompt`，禁止新 Job 静默扁平回退 | MM-024、MM-035 |
| 历史 AssetVersion/PageVersion | 不可变文件和恢复指针保持；不会自动 accepted/approved | MM-024、MM-044 |
| reroll/inpaint 历史链 | parent/mask/provenance 可读，迁移不覆盖父文件 | MM-024、MM-044 |
| 失败中的 generation/revision | 重启进入人工审阅，不自动重放付费动作 | MM-024、MM-055、MM-063 |

fixture 不包含真实凭证、真实小说全文或供应商响应中的敏感元数据。

## 6. 需求与验收追踪

| 需求/验收 | 实现工单 | 验收工单 |
|---|---|---|
| FR-20 / AC-09 版式先行 | MM-031～MM-034、MM-056 | MM-045、MM-060、MM-063、MM-046 |
| FR-21 / AC-10 多角色契约 | MM-035～MM-037、MM-057 | MM-045、MM-060、MM-046 |
| FR-22 / AC-11 候选闭环 | MM-038～MM-043、MM-058、MM-062、MM-064～MM-065 | MM-045、MM-060、MM-063、MM-046 |
| FR-23 / AC-12 Token 感知 | MM-047～MM-050、MM-061 | MM-051 |
| 架构 DoD 1～8 | MM-023～MM-027、MM-052～MM-053 | MM-045、MM-060 |
| 架构 DoD 9～16 | MM-028～MM-045、MM-054～MM-059、MM-062～MM-065 | MM-045、MM-060、MM-063 |
| 架构 DoD 17～19 | 不自动实现真实调用 | MM-046（等待单独授权） |

## 7. NovelAI 契约快照

| 字段 | 固定值 |
|---|---|
| source URL | `https://image.novelai.net/docs/doc.json` |
| fetched on | 2026-08-09 |
| bytes | 112,680 |
| SHA-256 | `f43ea4feff0d390dc65e5ed704d4cf7e75af741bb413b86981f465fb8fb556f8` |
| Swagger/title/version | `2.0` / `Omegalaser API` / `1.0` |
| Manga Maker mapping | `novelai-image-2026-08-09.3-v03-opus-zero-anlas-1` |
| machine-readable metadata | `contracts/novelai/image-api.contract.json` |

哈希变化只触发人工 diff，不会在启动时联网升级。MM-036 已把 ProviderExecutionSpec
mapping 固定为 `novelai-image-2026-08-09.3-v03-opus-zero-anlas-1`；本表不声称
2026-08-09 快照仍是最新上游契约。

## 8. 文档一致性报告

| 检查 | 结果 | 边界 |
|---|---|---|
| README / PRD / architecture / tickets 的状态 | 通过 | v0.2 已实现与 v0.3 待实现分开陈述 |
| P0/P1 术语 | 通过 | 产品优先级与开发调度优先级在 `WORK_ITEMS.md` 分开定义 |
| FR-20～23、AC-09～12 追踪 | 通过 | 本文 §6 和 `WORK_ITEMS.md` 可反查 |
| owner 唯一性 | 通过（设计基线） | MM-027 将其变成可执行架构门禁 |
| ADR 完整性 | 通过 | `docs/adr/ADR-010-018.md` 记录兼容、回滚和删除条件 |
| Swagger 元数据 | 通过 | 本文 §7 与 machine-readable contract 一致 |
| 敏感信息 | 通过（文档静态检查） | 文档中无真实 Token、主密码、请求头值或用户凭证；MM-045 将加入 CI 扫描 |
| 真实服务 | 未执行 | 仍需 MM-046/MM-051 的用户单独授权 |

本报告只确认设计可实施和文档可追踪。Canonical Schema、代码边界、迁移、状态机、UI、恢复和真实生产分别由后续工单验收。
