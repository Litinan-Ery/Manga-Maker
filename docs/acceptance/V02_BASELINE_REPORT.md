# Manga Maker v0.2 重构基线报告

| 项目 | 结果 |
|---|---|
| 工单 | MM-024 |
| 代码兼容基线 | `main@40f2cb9` |
| 复跑日期 | 2026-08-13 |
| 运行环境 | macOS，Python 3.12，Node/pnpm 由仓库锁文件解析 |
| 外部服务 | 未调用；文本和 NovelAI 均使用仓库内 Stub/Mock |

## 门禁结果

| 门禁 | 结果 |
|---|---|
| `uv run ruff check backend tests scripts` | 通过 |
| `uv run mypy backend` | 通过 |
| `uv run pytest -q` | 137 项通过 |
| `pnpm --dir frontend test` | 15 个文件、22 项通过 |
| `pnpm --dir frontend build` | TypeScript + Vite production build 通过 |

测试数包含 MM-023/MM-052/MM-024 新增的治理、契约和 fixture 测试；它们不改写 v0.2 产品语义。全量后端仅有 Starlette TestClient 对 `httpx` 的弃用提示，没有失败或跳过。

## 冻结迁移样本

| 文件 | 内容与边界 |
|---|---|
| `tests/fixtures/v0.2/schema16.db.fixture` | schema 16、46 张表；单角色初次生成、panel reroll、inpaint、3 个 AssetVersion、3 个 PageVersion、MaskAsset、PromptPackage v1 与审计事件 |
| `tests/fixtures/v0.2/project-v1.4.manga-maker.zip` | 工程包 v1.4；相同生成/版本链、相对文件路径、哈希清单、无凭证；可 dry-run 和恢复 |
| `tests/fixtures/v0.2/fixture-metadata.json` | 两个二进制 fixture 的固定 SHA-256、大小、表/对象计数和安全声明 |

样本通过真实 v0.2 API 和 Mock provider 生成，再将临时工作区绝对路径规范化为 `/MANGA_MAKER_PROJECT`、凭证引用改为 `restore-required`。不包含真实密钥、主密码、真实小说或真实供应商响应。

当前样本的 Storyboard 是单角色，而工程包内 PromptPackage v1 明确为 flat compiled prompt，后续多角色迁移使用 `contracts/fixtures/v0.3/prompt-plan-double.json` 与旧 flat shape 的对照，不把 v1 推断成结构化 v2。

## 被刻画的兼容行为

- schema 16 可只读打开，`quick_check`、foreign key、16 条 migration 记录保持有效；现有 migration 重复执行幂等。
- PromptPackage v1 有 `compiled_prompt`，没有 `prompt_plan`；迁移必须显式标记 legacy，不得静默冒充 v2。
- 初次生成、panel reroll、inpaint 都有独立 GenerationJob/Spec/Attempt；父素材、MaskAsset、AssetVersion 和 PageVersion 历史保留。
- 工程包预检不恢复写入，必须确认后恢复；首次保留 ID，同一工程再次恢复整体重映射 ID。
- 工程包拒绝未知 schema、绝对/越界路径、损坏哈希和凭证字段；冻结 fixture 本身通过逐文件哈希校验。
- 既有 API 的成功、缺少确认、revision conflict、stale plan、幂等重复、崩溃转人工审阅和失败关闭仍由原测试套件持续覆盖。

## 解释

这是架构重构前的行为护栏，不是 v0.3 功能验收。后续模块化、迁移和新状态机必须让本报告的 v0.2 测试与 fixture 持续通过；如果需要改变产品语义，应单独更新 PRD、兼容策略和迁移验收，不能混入目录重构。
