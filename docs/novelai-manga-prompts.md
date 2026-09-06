# NovelAI 日式多格漫画 Prompt 指南

更新：2026-09-06。适用于本应用的 NovelAI V5 Full 整页模式。

## 在应用里操作

1. 批准分镜、版式、角色固定 Tags 与风格板。普通页保持 3–6 格，在版式工作台设置 `rtl_ttb` 阅读方向。
2. 选择“图像生成方式 → V5 整页多格”。文字策略推荐“本地文字”，每格只写一个可画出的瞬间。
3. 预览完整 Prompt，检查所有分格、镜头、角色与文字都已覆盖，再确认生成一次。
4. 检查成图实际格数、顺序和人物，再采用。依据实际格框调整本地文字位置，核对对白后导出。严格格框、可单格修改和精确文字位置优先选择逐格生成与本地排版。

2026-09-06 已实际完成三格、四格两页的 V5 Full 成图与本地日语排字。模型文字试验仍出现串格、额外音效；即便文字放在末尾并逐格重复，也不能保证精确绑定。模型文字因此标为实验能力，不用它交付要求准确的对白。成品与逐次证据见 [真实验收报告](../.manga-maker/storyboard-real-final/README.md)。

已有工程若使用旧 NovelAI 映射，需要重新保存图像模型配置、重新预览和确认。
本轮不自动改写旧审批哈希，也不把已有一页一幅插图视为多格效果验收。

## Prompt 的四层结构

**全页 Tags → 阅读顺序与格框 → 每格单一画面 → 末尾实际文字。**

- 全页层明确 `manga, comic, monochrome, greyscale, screentone`，直接写“上排左右两格，下排通栏一格”等行列关系；仅列百分比坐标的首次真实图把三格画成了四条横格。
- 每格用短句写“谁、在哪里、做什么、镜头看见什么”。“接近门 → 开门 → 转头”分别放进三个格。手部插入镜头要说清脸在画面外。
- 相同角色使用同一姓名、固定外观及具体服装；“服装保持一致”不能代替服装说明。手部特写只呈现可见部位。人物位置是格内位置，与格框在整页中的位置分开。
- 选择模型文字时，所有实际字符串统一放到 Prompt 最末尾的 `Text:` 后，用空行分隔；此前用全页方位文字表和逐格内联字符串关联。避免数字格号和“文字第几项”间接引用。不要在 `Text:` 后继续写画面说明。

NovelAI 官方说明 V5 支持自然语言、多语言和文字；角色框用于角色定位，不是漫画分格控制器。
参考：[模型说明](https://docs.novelai.net/en/image/models/)、[多角色提示](https://docs.novelai.net/en/image/multiplecharacters/)、[文字渲染](https://docs.novelai.net/en/image/textrendering/)。

## 可直接输入 NovelAI 的三格示例

选择 V5 Full，关闭自动质量标签，Undesired Content Preset 选择 None。
下面是推荐的无字底图结构示例；生成后在本地加日语对白。完整实际请求保存在验收报告目录的 `page-1-local-prompt.txt` 和 `page-2-local-prompt.txt`。

```text
manga, comic, monochrome, greyscale, screentone, ink lineart, no text

One complete Japanese manga page with exactly three panels. Read right to left, then top to bottom. Two panels occupy the top row; a wide third panel spans the bottom. Clear black panel borders and white gutters. Keep all gutters empty, without labels or numbering. One instant per panel; do not add extra inset panels.

Lin Xia is an adult woman with shoulder-length black hair and a small beauty mark beneath her left eye. She wears the same light shirt and dark trousers throughout the page. Only draw the body parts visible in each shot.

Panel at the upper right: Wide shot. Lin Xia stands in the doorway of an old house at night, holding a closed umbrella in her left hand. Rain is visible behind her. Leave the upper part visually quiet for later lettering.

Panel at the upper left: Extreme close-up of Lin Xia's right hand holding a brass key beside the door lock. Only the hand, cuff, key and lock are visible. Her face is outside the panel. Leave space above the hand for a later sound effect.

Panel across the bottom: Close-up of Lin Xia's face as she looks toward a light inside the house. Her mouth is slightly open. Keep her hair and beauty mark consistent. Leave some light space beside her face for later lettering.

Keep lettering areas empty. Do not draw letters or speech balloons.
```

对应的 Undesired Content：

```text
lowres, bad anatomy, bad hands, jpeg artifacts, watermark, logo, color, panel number, page number, text, letters, speech bubble
```

本地对白可分别填写 `ただいま。`、`カチャ`、`誰かいる？`。说话人单独保存，不打印“林夏:”前缀；音效可改为 SFX 图层。竖排短句可逐字换行，并在实际成图上调整文字框。

如果要试模型文字，移除上述文字禁令，正向加入 `text, speech bubble`，用方位文字表及逐格描述重复实际字符串，并在最末尾追加：

```text
Text: ただいま。

カチャ

誰かいる？
```

这符合官方输入格式，但本次真实四格页仍有文字串格；需要逐张审阅，不能把“请求格式正确”当成“文字准确”。

## 常见冲突与实际边界

| 写法/设置 | 后果与处理 |
|---|---|
| 正向要求漫画，负向仍有 `manga, anime, panel border` | 要求互相抵消；先修改并重新批准风格板 |
| 整页仍写 `single panel` 或 `single full-page comic illustration` | 容易变成单幅插图；改为具体格数与逐格画面 |
| 要画对白却保留 `no text` 或负向 `text` | 移除文字禁令，关闭自动质量标签 |
| 使用抑制 `screentone, halftone, multiple views` 的预设 | 不利于黑白多格漫画；使用 None 和显式负面提示 |
| 把多个 Character Prompt 或 `\|` 当作分格 | 它们不是分镜语法；按整页自然语言描述各格 |
| 一格塞入连续三件事 | 模型难以明确时间；拆成不同格，每格保留一个瞬间 |
| 将坐标当成精确布局控制 | 坐标只是文本参考；严格尺寸和版式用本地拼页 |

自动质量标签包含 `no text`，部分负向预设包含网点/多视图抑制项，详见
[质量标签](https://docs.novelai.net/en/image/qualitytags/)与[负向预设](https://docs.novelai.net/en/image/undesiredcontent/)。
本应用整页请求使用 `qualityToggle=false`、`tag_hint_qt=0`、`tag_hint_uc_preset=0`；
V5 逐格请求也改为显式负面提示。旧的质量策略仅用于不要求模型文字的逐格路径。

V5 Full 的模型文字上限是 750 字符，Curated 为 374；本应用整页入口当前只接 Full。
画面提示另有本地字符预算，尚未接入官方 Token 计数器。少量短句与较短对白更容易检查，
不能靠加重权重保证格数、阅读顺序或准确文字。功能链路和模型视觉效果需分别验收。

## 重复执行本地用例

```bash
uv run pytest tests/test_storyboard_composition.py tests/test_storyboard_prompt_semantics.py tests/test_fullpages_api.py tests/e2e/test_storyboard_manga.py
uv run python -m scripts.run_storyboard_manga_acceptance
```

脚本保留三格/四格来源、最终 Prompt、请求哈希、调用数、页面、工程包、PNG、PDF 和 CBZ。
产物在 `.manga-maker/storyboard-acceptance/<时间>/`，使用隔离 Mock 和假凭证，真实 NovelAI 请求为零。
完整分层证据见 [分镜修复验收记录](storyboard-repair-cases.md)。
