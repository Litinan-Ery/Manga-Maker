# NovelAI 图像接口契约基线

状态：MM-011 的审计基线；不是由 Manga Maker 托管的官方接口定义。

## 固定版本

- 官方 Swagger：<https://image.novelai.net/docs/doc.json>
- 抓取日期：2026-08-09
- 大小：112,680 bytes
- SHA-256：`f43ea4feff0d390dc65e5ed704d4cf7e75af741bb413b86981f465fb8fb556f8`
- Swagger / 标题 / 版本：`2.0` / `Omegalaser API` / `1.0`
- Manga Maker 映射版本：`novelai-image-2026-08-09.2`（加入经审计的 P0 `infill` 字段组合）

`https://image.novelai.net/openapi.json` 当前是标题为 `Observability API` 的另一份
OpenAPI 3.1 契约，只含错误追踪能力，不能作为图像接口生成或验收依据。实现只允许显式列入
`image-api.contract.json` 的图像路径；上游哈希变化时先人工审计差异，再升级映射版本。

## P0 使用边界

- 连接测试只在用户点击后调用 `GET /ai/generate-image/suggest-tags`，固定发送无敏感内容的
  `manga` 查询，不生成图片、不产生隐藏付费任务、不自动重试。
- 出图、重绘和放大必须由后续有界队列承接，绑定明确用户动作和调用上限。
- P0 局部重绘仍使用固定的 `POST /ai/generate-image`，根操作为 `infill`，只发送经哈希冻结的父图、黑白蒙版、提示词与 allowlist 参数；Focused Inpainting 不在当前稳定契约内。
- 应用接收用户已有的 Persistent API Token；不收集 NovelAI 邮箱/密码，也不调用登录或
  Token 创建接口。
- Token 只从应用本地加密凭证库按需读取，不进入项目、SQLite、日志、接口响应或导出物。

## 能力来源

- 当前模型和 Precise Reference 限制以 NovelAI 官方模型、Precise Reference 文档为产品依据。
- 官方 Swagger 没有枚举模型字符串。模型和 inpaint 标识使用社区实现交叉核对：
  `Nya-Foundation/NekoAI-API` commit
  `58e595d6f1a07aafc510eb946377df8066ade0bb`（AGPL-3.0）。Manga Maker 没有复制其代码、
  引入其依赖或继承其重试策略；这些字符串仍受本地 allowlist 和 Mock 契约测试约束。
- 其他设计参考：`LlmKira/novelai-python`（Apache-2.0，已停止活跃维护）、
  `raspie10032/ComfyUI_RS_NAI_API_Request`（GPL-3.0）、`ststoryweaver/NAIWeaver`（MIT）。

## 更新流程

1. 人工下载官方 `docs/doc.json`，核对来源主机、标题、版本、大小和 SHA-256。
2. 比较 allowlist 路径、鉴权、请求字段、响应类型与错误状态。
3. 用 Mock 完成成功、认证、权限、余额、限流、参数、网络和异常响应回归。
4. 更新映射版本、文档和测试后提交。应用启动时不会自动联网刷新契约。
