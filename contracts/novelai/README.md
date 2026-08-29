# NovelAI 图像接口契约基线

状态：NovelAI Diffusion V5 的审计基线；不是由 Manga Maker 托管的官方接口定义。

## 固定版本

- 官方 Swagger：<https://image.novelai.net/docs/doc.json>
- 抓取日期：2026-08-29
- 大小：113,758 bytes
- SHA-256：`2bd3c5fcd491016e1951f5a3f347d0207d49d4add153899405224e21fd1dc684`
- Swagger / 标题 / 版本：`2.0` / `Omegalaser API` / `1.0`
- Manga Maker 映射版本：`novelai-image-2026-08-29.4-v5-full-1`（默认模型切换为
  `nai-diffusion-5-full`，固定 V5 的 23 steps、CFG 7、Karras、Euler Ancestral 与
  `params_version=4`，并保留已审计的 `infill` 组合）

`https://image.novelai.net/openapi.json` 当前是标题为 `Observability API` 的另一份
OpenAPI 3.1 契约，只含错误追踪能力，不能作为图像接口生成或验收依据。实现只允许显式列入
`image-api.contract.json` 的图像路径；上游哈希变化时先人工审计差异，再升级映射版本。

## 使用边界

- 连接测试只在用户点击后调用 `GET /ai/generate-image/suggest-tags` 与
  `GET /user/subscription`，固定发送无敏感内容的 `manga` 查询并核验 Opus 层级；不生成图片、
  不产生隐藏付费任务、不自动重试。
- 出图、重绘和放大必须由后续有界队列承接，绑定明确用户动作和调用上限。
- V5 的 Opus 单图免 Anlas 条件带有会恢复的使用额度；额度耗尽后官方服务可能改扣 Anlas。
  订阅层级核验只能证明 Opus 有效，不能证明当下仍有免费额度。
- P0 局部重绘仍使用固定的 `POST /ai/generate-image`，根操作为 `infill`，只发送经哈希冻结的父图、黑白蒙版、提示词与 allowlist 参数；Focused Inpainting 不在当前稳定契约内。
- 应用接收用户已有的 Persistent API Token；不收集 NovelAI 邮箱/密码，也不调用登录或
  Token 创建接口。
- Token 只从应用本地加密凭证库按需读取，不进入项目、SQLite、日志、接口响应或导出物。

## 能力来源

- 模型能力、22 角色上限、V5 文字渲染与 Precise Reference 限制以 NovelAI 官方文档为依据：
  <https://docs.novelai.net/en/image/models/>、
  <https://docs.novelai.net/en/image/multiplecharacters/>、
  <https://docs.novelai.net/en/image/textrendering/>、
  <https://docs.novelai.net/en/image/precisereference/>。
- 官方 Swagger 没有枚举模型字符串。`nai-diffusion-5-full`、
  `nai-diffusion-5-curated` 及其 inpaint 标识由 NovelAI 官方网页客户端交叉核验；这些字符串仍
  受本地 allowlist 和 Mock 契约测试约束。
- V5 当前不启用 Precise Reference 或 Vibe Transfer；前者的官方文档目前只列 V4.5。
- Opus 使用额度与采样上限依据 <https://docs.novelai.net/en/faq/#opus-usage-limits> 和
  <https://docs.novelai.net/en/image/stepsguidance/>。

## 更新流程

1. 人工下载官方 `docs/doc.json`，核对来源主机、标题、版本、大小和 SHA-256。
2. 比较 allowlist 路径、鉴权、请求字段、响应类型与错误状态。
3. 用 Mock 完成成功、认证、权限、余额、限流、参数、网络和异常响应回归。
4. 更新映射版本、文档和测试后提交。应用启动时不会自动联网刷新契约。
