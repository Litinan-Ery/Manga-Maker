# Manga Maker 开发工单

| 项目 | 内容 |
|---|---|
| 版本 | v0.1 |
| 日期 | 2026-08-09 |
| 状态 | 开发中 |
| 产品范围 | 以 README、PRD、TECHNICAL_ARCHITECTURE 为准 |

## 优先级定义

| 优先级 | 含义 | 调度规则 |
|---|---|---|
| P0 / Blocker | 后续工单无法安全开展的基础能力 | 当前优先完成，不被功能性工作抢占 |
| P1 / High | 产品 P0 单章闭环的必要能力 | 依赖满足后按编号执行 |
| P2 / Medium | 产品 P1 整本小说能力 | 产品 P0 验收后启动 |
| P3 / Low | 产品 P2 高级创作能力 | 产品 P1 稳定后评估 |

状态统一为 `Todo / In Progress / Blocked / Done`。`Done` 必须同时满足代码、测试、文档和验收证据，只有方向性实现不得标记完成。

## 执行顺序

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

## P0 / Blocker 工单

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

## P1 / High 工单

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
- 完成证据：已固定 2026-08-09 官方 `docs/doc.json` 的 URL、112,680-byte 大小、SHA-256 和映射版本，并明确排除错误的 Observability `/openapi.json`；交付 6 个模型 capability、固定 NovelAI host/path、应用本地加密 Token profile、项目非敏感配置、单次标签建议连接测试、本地 Mock，以及认证、权限、余额、限流、参数、网络、5xx 和异常响应分类。连接测试不自动重试并返回 `generated_images = 0`；49 项后端测试、9 项前端测试、ruff、mypy、契约复验、密钥扫描和生产构建通过，未执行真实 NovelAI 调用。

### MM-012 有界串行生成队列

- Job 固定 panel 清单、调用/成本上限和用户动作；默认串行。
- 暂停/取消后不领取新项；重启不自动恢复付费调用。
- 完成证据：已交付基于已审批 Storyboard/CharacterBible/StyleBible 和已验证 NovelAI 配置的确定性计划指纹，冻结有序 panel、模型映射版本、契约哈希、用户动作、每格保守成本预留、总成本与最多三倍 panel 数的调用上限；SQLite Job/Item/Attempt 状态机通过 partial unique index 与单写者事务保证全应用单在途，支持 revision 冲突保护、开始、暂停、恢复、取消、成本超限转人工审阅及启动 reconciliation。queued 项重启不自动开始，running 无在途转 paused，在途转 needs_review；55 项后端测试、10 项前端测试、ruff、mypy 和生产构建通过，图像执行器尚未接入且外部请求数为 0。

### MM-013 NovelAI 逐格生成与素材版本

- 支持生成、参考图预处理、响应校验、原始 PNG 和 provenance sidecar。
- 真实调用必须在 mock 全部通过且用户单独确认后执行。
- 完成证据：已交付手工 allowlist 的 `POST /ai/generate-image` JSON 映射、固定 host/模型/契约、每格不可变 GenerationSpec 与 UUIDv7 correlation ID、最多一张经 EXIF 修正和黑边 padding 的 Precise Reference、严格 201/JSON/base64/PNG/尺寸/seed 校验，以及 `original.png` + provenance + AssetVersion 原子登记。发送前读取加密凭证并消耗冻结上限；明确连接失败/5xx 最多重试两次，发送后结果不明立即转人工审阅且不重放。界面要求在启动 Job 后再次勾选确认才调度，并轮询进度、预览本地素材；75 项后端测试、11 项前端测试、ruff、mypy 和生产构建通过。真实 NovelAI 付费请求为 0，尚未执行用户批准的低成本 smoke。

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

## P2 / P3 工单

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
- 发现新工作时先归属现有工单；范围独立才新增编号。
- 优先级变化必须写明原因、依赖和对产品 P0 验收的影响。
- 真实 NovelAI 调用、永久删除、发布或外部部署不因工单存在而自动获得授权。
