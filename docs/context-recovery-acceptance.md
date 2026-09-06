# 长任务上下文恢复：实现与验收

日期：2026-09-06。关联：[PRD 第 25 节](../PRD.md#long-task-context-recovery)（FR-24 / NFR-07 / AC-13）、[技术架构 §23](../TECHNICAL_ARCHITECTURE.md#long-task-context-recovery)、[MM-072～078](../WORK_ITEMS.md#context-recovery-tickets)。

## 本次交付

实现覆盖应用拥有的状态、受控工具返回和整页生产脚本。检查点、恢复计划和交接文件不依赖模型可用性；它们不替换外部 Codex 的历史。

| Ticket | 实现入口 | 验证范围 |
|---|---|---|
| MM-072 | `platform/context_budget` | 水位、输出预留、未知/滞后观测、增量去重、图片数、有界分页与失败总数 |
| MM-073 | `workflows/book_production/store.py`、migration 34 | 不可变文件、事务指针、租约、revision、fencing、提交边界中断、损坏/越界引用 |
| MM-074 | `host.py`、`coordinator.py` | 无宿主能力降级、两次失败后交接、退避、重启继承、新窗口与压缩后用量核验 |
| MM-075 | `api/workflow_context.py`、`bootstrap/context_reader.py` | session/CSRF、项目隔离、来源/冻结输入/图像/排版核对、审查记录失效、恢复 CAS |
| MM-076 | `scripts/production_context.py`、`scripts/workflow_context.py`、前端 `features/workflowContext` | 5 页批次、跨进程清单锁、逐页检查点、完整清单登记、摘要/交接和三个独立进度 |
| MM-077 | `tests/workflows/`、本报告 | 故障注入、100 页模拟断连恢复、完整回归和静态检查 |
| MM-078 | 尚未集成真实宿主 | Codex 强制压缩/历史替换仍待验收；真实整书产物部分已另见[100 页验收报告](canticle-100-acceptance.md)，不能用本轮模拟结果代替 |

## 关键行为

- 预算使用版本 `context-budget-1`，50% 保存、60% 请求压缩、70% 停止追加，输出预留取 `max(16,000, 10% 窗口, 计划输出)`；输入和预留合计不得超过已确认窗口的 80%。无有效读数时不报告精确比例。
- 证据列表整体使用 6,000 UTF-8 字节作为保守 token 上界。超大单条保留索引，内容可用条目/offset 分块补读；分页结束返回空游标。失败数量独立于返回页，结构化敏感字段、session/CSRF、Bearer 和图片 data URL 在证据保存前处理。
- TaskSnapshot 不超过 1 MB，产物引用使用项目/任务内 UUID 和 SHA256。后续检查点不能改写目标、来源、约束、验收标准或丢失页码；生产刷新保留已有审查记录，不静默替换已有生成 ID。
- 检查点发布采用唯一临时文件、文件/目录 fsync、不可覆盖发布和 SQLite 事务指针。文件发布期间租约到期也拒绝提交；已发布但未提交的文件不会成为当前检查点。损坏的最新检查点阻断恢复并列出有效历史，不自动回退或删除证据。
- `ReviewRecord` 有效性绑定原图/排版/冻结计划哈希、审查范围、`page-review-1` 规则、renderer/font 和同任务证据。修改、重排或证据损坏会使相关记录失效；有效记录也不是 PageApproval。
- 默认 `LocalHandoffHost` 的 usage、compaction、context_replacement 均为 false。模拟宿主测试验证状态机；没有尝试操纵真实 Codex 历史，也没有修改 Codex 全局配置。
- `resume` 重算恢复计划后只恢复本地状态，明确返回 `external_requests_started=0`、`host_context_replaced=false` 和仍需生成审批。`needs_review`、在途/失败请求、源或文件变化、过期 revision/plan_hash 均阻止恢复。

## 使用方式

**前端**：在有章节的项目工作台找到“检查点与交接”。“记录当前冻结页面”只记录当前已有整页计划；完整 100 页目标应通过生产清单登记，使尚未准备的页也保留在总范围内。生成、排版、有效审查记录分别计数；导出以 ExportCenter 的实际验收为准。

**清单登记**：使用运行中的本地应用对应 session 文件，避免复制其中的凭据到命令或文档。

```bash
.venv/bin/python -m scripts.workflow_context /path/to/production.json register \
  --objective '完成完整 100 页漫画，保留全部章节并逐页及整书验收' \
  --session-file /path/to/current-local-session.json
.venv/bin/python -m scripts.workflow_context /path/to/production.json summary \
  --session-file /path/to/current-local-session.json
.venv/bin/python -m scripts.workflow_context /path/to/production.json handoff \
  --session-file /path/to/current-local-session.json
```

登记在清单中保存 `workflow_context.run_id`。此后原生产命令每页领取/续租、保存应用检查点并预检；`prepare/generate` 全程持有非阻塞文件锁，每批生成最多 5 页。旧清单无需迁移也会有本地 `.context/<manifest-name>/CURRENT.json` 与 `RECOVERY.json`；未登记清单的状态机控制尚未接入应用，摘要标注需要重新核对。

交接包默认保存为 `.context/<manifest-name>/RECOVERY-BUNDLE.json`，应用另存不可变副本；内容保留完整目标、限制和验收标准，详细页表按 checkpoint/证据引用读取。不能容纳完整限制的包明确失败，不静默删减目标。下载/读取恢复包后仍要核对当前领域状态。

## 故障注入与证据

| 场景 | 测试与结果 |
|---|---|
| 多结果合计超限、失败项位于末尾 | `test_context_budget.py`、`test_workflow_context_api.py` 验证 envelope 上限、精确失败数、完整分块补读及脱敏 |
| staging/发布/数据库提交前中断 | `test_context_checkpoints.py` 验证旧检查点与 revision 保持，孤立文件不成为进度 |
| 第二执行者、过期租约、晚提交 | SQLite 双实例/并发领取、文件锁跨进程测试、发布期间租约到期测试 |
| 宿主压缩两次失败与重启 | `test_context_host.py` 验证首次退避、第二次停止、次数不随重启或 checkpoint 更新清零 |
| 源文/原图/文字/字体/规则变化 | `test_context_reader.py` 从磁盘重新取哈希，识别后续采用的页和陈旧审查记录；冻结依赖仍经领域公开方法核对 |
| 100 页恢复，模拟第 26 页响应丢失 | `test_production_context.py` 通过真实生产脚本 + 应用工作流 API + 模拟图像传输执行 20 批。第 26 页服务端完成后丢失响应，重启只读已有结果；100 页各提交一次，100 个不同页号文件，总目标保持 100 页，已审查仍为 0 |
| 多阶段恢复后保持范围 | `test_workflow_context_api.py` 以 5 页为单位推进，在 25/50/75 页核对恢复，验证总范围、下一页与不产生审批 |
| CLI 登记、摘要、交接 | `test_context_cli.py` 通过真实应用工作流 API 验证完整页码、目标保真、0600 权限和无生成调用 |
| UI | 4 项交互测试覆盖独立进度、无可靠用量、交接保真、恢复核对和异常时释放租约 |

## 恢复功能开发阶段回归

| 检查 | 结果 |
|---|---|
| 后端全量 pytest | **411 passed**，237.72 秒；含上下文恢复与已有业务/架构回归 |
| 最后调整后的领域对应关系回归 | **13 passed**，5.24 秒；覆盖原图对应的生成 ID、冻结输入、文件和审查失效 |
| 前端全量 Vitest | **54 passed / 24 files**；其中新增 4 项恢复交互测试 |
| Ruff | backend、tests 及三个生产/恢复脚本全部通过 |
| mypy | 213 个后端源文件检查通过 |
| 前端构建 | TypeScript 检查与 Vite 生产构建通过 |
| `git diff --check` | 通过 |

后端存在一条既有 TestClient/httpx 弃用提醒，不影响本轮用例结果。所有运行使用隔离测试数据或模拟传输；没有发起真实图像/文本请求，没有操作正在进行的真实生产。

```bash
.venv/bin/ruff check backend tests scripts/produce_authored_manga.py scripts/production_context.py scripts/workflow_context.py
.venv/bin/mypy backend
.venv/bin/pytest -o addopts='' -q --tb=short
pnpm --dir frontend test
pnpm --dir frontend build
git diff --check
```

<a id="v022-release-validation"></a>

## v0.2.2 合并前回归（2026-09-06）

在整页工作台增加章节集 ID 替换、同 ID 版本变化和旧预览迟到三项回归后，再次验证：后端 **411 passed**（232.51 秒），前端 **57 passed / 24 files**；Ruff、mypy（213 个源文件）、TypeScript / Vite 构建、`uv lock --check --offline` 和 `git diff --check` 通过。三项新增用例均先在旧代码复现失败，再验证修复后通过；上表保留恢复功能开发时的历史数字。

离线命令 `python -m scripts.run_storyboard_manga_acceptance` 另行复验通过：逐格和整页两种模式各 2 页、包含三格和四格，Mock 图像调用分别为 7 / 2 次，真实 NovelAI 请求为 0；每种模式的四格式合计 5 个导出文件，实际哈希与报告逐项一致。

本机运行记录为 `/tmp/mm-ship-backend.log`、`/tmp/mm-ship-frontend.log`、`/tmp/mm-ship-build.log`，离线 CLI 报告位于 `/private/tmp/mm-ship-storyboard/20260906T043932.574585Z/report.json`。这些临时记录不随仓库分发；上述命令和测试保留为可重复验证入口。本次回归未启动真实图像或文本生产，也不代表真实宿主历史替换已完成。

## 未覆盖边界

1. 真实 Codex 的当前上下文读取、请求前强制拦截、压缩和历史替换尚无可用宿主实现；不能称原 Codex 上下文问题已被彻底修复。当前交付的是本地恢复基础和手动交接路径。
2. 本报告的 100 页恢复证据使用隔离测试数据和模拟图像传输，新增真实文本/NovelAI 请求为 0。真实视觉质量、连续性、四类导出与重开的独立成品验收已记录于[100 页验收报告](canticle-100-acceptance.md)；它完成 MM-078 的整书产物部分，不能替代真实宿主集成。
3. 当前领域适配器接入整页生产；FR-23 分层文本流水线、通用代码任务专属上下文契约、v0.3 候选接受/PageApproval 闭环保持各自工单边界。
4. 本轮没有重启正在生产的应用、迁移其运行数据库或切换已有真实清单。运行旧脚本不会自动获得新文件锁，须在当前工作结束且在途项核对后启动新版；迁移 34 已在隔离启动/升级测试中验证。
5. 检查点与证据本轮保持保留，不自动清理；损坏最新检查点只提供历史核对信息，不提供静默回退。打包导出继续采用既有业务工程包边界，workflow 运行检查点不冒充业务页面或审批数据。
