import { useEffect, useMemo, useState } from "react";

import {
  ApiError,
  type BeatResolution,
  type ChapterSet,
  type DialogueLine,
  type StoryBeatSet,
  type StoryboardDocument,
  type StoryboardPanel,
  type StoryboardScene,
  type StoryboardVersion,
  type TextModelConfiguration,
  type VaultStatus,
  approveStoryboard,
  generateStoryboard,
  getCurrentStoryboard,
  getStoryBeats,
  getTextModelConfiguration,
  reviseStoryboard,
  testTextModelConfiguration,
} from "./api";

interface StoryboardWorkbenchProps {
  projectId: string;
  chapterSet: ChapterSet;
  vaultStatus: VaultStatus | null;
  onError: (message: string) => void;
  refreshKey: number;
  textModelRefreshKey: number;
  onChanged?: () => void;
}

export function StoryboardWorkbench({
  projectId,
  chapterSet,
  vaultStatus,
  onError,
  refreshKey,
  textModelRefreshKey,
  onChanged,
}: StoryboardWorkbenchProps) {
  const [chapterId, setChapterId] = useState(chapterSet.chapters[0]?.chapter_id ?? "");
  const [beatSet, setBeatSet] = useState<StoryBeatSet | null>(null);
  const [configuration, setConfiguration] = useState<TextModelConfiguration | null>(null);
  const [pageBudget, setPageBudget] = useState(8);
  const [preferences, setPreferences] = useState("");
  const [confirmedDataSend, setConfirmedDataSend] = useState(false);
  const [storyboard, setStoryboard] = useState<StoryboardVersion | null>(null);
  const [draft, setDraft] = useState<StoryboardDocument | null>(null);
  const [busy, setBusy] = useState(false);
  const [connectionMessage, setConnectionMessage] = useState("");

  const selectedChapter = chapterSet.chapters.find((chapter) => chapter.chapter_id === chapterId);
  const dirty = Boolean(
    storyboard && draft && JSON.stringify(draft) !== JSON.stringify(storyboard.document),
  );
  const draftUnresolvedCount =
    draft?.beat_resolutions.filter((resolution) => resolution.status === "unresolved").length ?? 0;
  const pagePolicyFindings = useMemo(() => {
    if (!draft) return [];
    if (draft.schema_version !== "1.1") {
      return ["这是 1.0 历史分镜，只能只读；请重新生成 1.1 分镜。"];
    }
    return draft.pages.flatMap((page) => {
      if (!page.page_type) return [`第 ${page.page_number} 页缺少页面类型。`];
      const minimum = page.page_type === "standard" ? 3 : 1;
      return page.panels.length < minimum || page.panels.length > 6
        ? [
            `第 ${page.page_number} 页为 ${pageTypeLabel(page.page_type)}，需要 ${minimum}–6 格，当前为 ${page.panels.length} 格。`,
          ]
        : [];
    });
  }, [draft]);
  const sourceByBeatId = useMemo(
    () => new Map(beatSet?.beats.map((beat) => [beat.beat_id, beat.source_excerpt]) ?? []),
    [beatSet],
  );
  useEffect(() => {
    const firstChapter = chapterSet.chapters[0]?.chapter_id ?? "";
    setChapterId(firstChapter);
    setBeatSet(null);
    setStoryboard(null);
    setDraft(null);
  }, [chapterSet]);

  useEffect(() => {
    let active = true;
    setConfiguration(null);
    getTextModelConfiguration(projectId)
      .then((result) => {
        if (!active) return;
        setConfiguration(result);
      })
      .catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 404) return;
        if (active) onError(error instanceof Error ? error.message : "无法读取文本模型配置。");
      });
    return () => {
      active = false;
    };
  }, [onError, projectId, textModelRefreshKey]);

  useEffect(() => {
    if (!chapterId) return;
    let active = true;
    setBeatSet(null);
    setStoryboard(null);
    setDraft(null);
    Promise.all([
      getStoryBeats(projectId, chapterId).catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }),
      getCurrentStoryboard(projectId, chapterId).catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }),
    ])
      .then(([nextBeatSet, nextStoryboard]) => {
        if (!active) return;
        setBeatSet(nextBeatSet);
        setStoryboard(nextStoryboard);
        setDraft(nextStoryboard ? cloneDocument(nextStoryboard.document) : null);
        if (nextStoryboard) setPageBudget(nextStoryboard.page_budget);
      })
      .catch((error: unknown) => {
        if (active) onError(error instanceof Error ? error.message : "无法读取改编工作台。 ");
      });
    return () => {
      active = false;
    };
  }, [chapterId, onError, projectId, refreshKey]);

  async function testConnection() {
    await run(async () => {
      const result = await testTextModelConfiguration(projectId);
      setConnectionMessage(`连接正常：${result.endpoint_host} · ${result.model}`);
    });
  }

  async function generate() {
    if (!chapterId || !confirmedDataSend) return;
    await run(async () => {
      const generated = await generateStoryboard(
        projectId,
        chapterId,
        pageBudget,
        preferences
          .split("\n")
          .map((item) => item.trim())
          .filter(Boolean),
      );
      setStoryboard(generated);
      setDraft(cloneDocument(generated.document));
      setConfirmedDataSend(false);
      setConnectionMessage("结构化分镜已生成并保存为不可变版本。");
      onChanged?.();
    });
  }

  async function saveRevision() {
    if (!storyboard || !draft) return;
    await run(async () => {
      const revised = await reviseStoryboard(
        projectId,
        storyboard.storyboard_version_id,
        draft,
      );
      setStoryboard(revised);
      setDraft(cloneDocument(revised.document));
      setConnectionMessage(`人工修改已保存为分镜版本 ${revised.version}。`);
      onChanged?.();
    });
  }

  async function approve() {
    if (!storyboard || dirty || pagePolicyFindings.length > 0) return;
    await run(async () => {
      const approved = await approveStoryboard(projectId, storyboard.storyboard_version_id);
      setStoryboard(approved);
      setDraft(cloneDocument(approved.document));
      setConnectionMessage("分镜已审批；角色表和风格板仍需在下一阶段单独确认。");
      onChanged?.();
    });
  }

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setConnectionMessage("");
    try {
      await action();
    } catch (error) {
      onError(error instanceof Error ? error.message : "改编操作失败。");
    } finally {
      setBusy(false);
    }
  }

  function updateResolution(index: number, patch: Partial<BeatResolution>) {
    setDraft((current) => {
      if (!current) return current;
      const resolutions = current.beat_resolutions.map((resolution, resolutionIndex) =>
        resolutionIndex === index ? { ...resolution, ...patch } : resolution,
      );
      return { ...current, beat_resolutions: resolutions };
    });
  }

  function changeResolutionStatus(index: number, status: BeatResolution["status"]) {
    setDraft((current) => {
      if (!current) return current;
      const currentResolution = current.beat_resolutions[index];
      const beatId = currentResolution.beat_id;
      const nextResolution: BeatResolution = {
        ...currentResolution,
        status,
        page_numbers:
          status === "represented" || status === "condensed"
            ? currentResolution.page_numbers.length
              ? currentResolution.page_numbers
              : [1]
            : [],
        reason: status === "omitted" ? currentResolution.reason ?? "" : null,
      };
      let scenes = current.scenes;
      if (status === "omitted") {
        scenes = scenes.map((scene) => ({
          ...scene,
          beat_ids: scene.beat_ids.filter((candidate) => candidate !== beatId),
        }));
      } else if (currentResolution.status === "omitted" && scenes[0]) {
        scenes = [
          { ...scenes[0], beat_ids: [...scenes[0].beat_ids, beatId] },
          ...scenes.slice(1),
        ];
      }
      return {
        ...current,
        beat_resolutions: current.beat_resolutions.map((resolution, resolutionIndex) =>
          resolutionIndex === index ? nextResolution : resolution,
        ),
        scenes,
      };
    });
  }

  function updateScene(index: number, patch: Partial<StoryboardScene>) {
    setDraft((current) => {
      if (!current) return current;
      return {
        ...current,
        scenes: current.scenes.map((scene, sceneIndex) =>
          sceneIndex === index ? { ...scene, ...patch } : scene,
        ),
      };
    });
  }

  function updatePage(pageIndex: number, turningPoint: string) {
    setDraft((current) => {
      if (!current) return current;
      return {
        ...current,
        pages: current.pages.map((page, index) =>
          index === pageIndex ? { ...page, turning_point: turningPoint } : page,
        ),
      };
    });
  }

  function updatePanel(
    pageIndex: number,
    panelIndex: number,
    patch: Partial<StoryboardPanel>,
  ) {
    setDraft((current) => {
      if (!current) return current;
      return {
        ...current,
        pages: current.pages.map((page, currentPageIndex) =>
          currentPageIndex === pageIndex
            ? {
                ...page,
                panels: page.panels.map((panel, currentPanelIndex) =>
                  currentPanelIndex === panelIndex ? { ...panel, ...patch } : panel,
                ),
              }
            : page,
        ),
      };
    });
  }

  return (
    <section className="adaptation-workbench">
      <div className="workspace-heading compact">
        <div>
          <p className="section-kicker">第四步</p>
          <h2>结构化改编工作台</h2>
        </div>
        <span>{storyboard ? `分镜版本 ${storyboard.version}` : "尚无分镜"}</span>
      </div>

      <div className="model-settings">
        <h3>文本模型</h3>
        <p className="panel-description">
          配置入口已统一到页面上方的“模型凭证库”；这里仅显示当前项目配置并执行显式连接测试。
        </p>
        <div className="button-row">
          <button
            type="button"
            className="quiet-button"
            disabled={busy || !configuration || !vaultStatus?.unlocked}
            onClick={() => void testConnection()}
          >
            由我触发连接测试
          </button>
        </div>
        {configuration ? (
          <p className="configuration-summary">
            当前：{configuration.remark_name ? `${configuration.remark_name} · ` : ""}
            {configuration.endpoint_host} · {configuration.request_model} · Key/Password {configuration.credential_fingerprint ?? "已保存"} · 配置版本 {configuration.revision}
          </p>
        ) : (
          <p className="warning-inline">尚未配置文本模型，请先在页面上方保存四字段配置。</p>
        )}
      </div>

      <div className="generation-setup">
        <label>
          <span>选择章节</span>
          <select value={chapterId} onChange={(event) => setChapterId(event.target.value)}>
            {chapterSet.chapters.map((chapter) => (
              <option key={chapter.chapter_id} value={chapter.chapter_id}>
                {chapter.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>页数上限</span>
          <input
            type="number"
            min={1}
            max={64}
            value={pageBudget}
            onChange={(event) => setPageBudget(Number(event.target.value))}
          />
        </label>
        <label className="preferences-field">
          <span>改编偏好（每行一项，可选）</span>
          <textarea
            rows={3}
            value={preferences}
            onChange={(event) => setPreferences(event.target.value)}
            placeholder="例如：突出悬疑感\n保留关键对白"
          />
        </label>
      </div>
      <label className="consent-row">
        <input
          type="checkbox"
          checked={confirmedDataSend}
          onChange={(event) => setConfirmedDataSend(event.target.checked)}
        />
        <span>
          我确认把“{selectedChapter?.title ?? "所选章节"}”正文、{beatSet?.beats.length ?? 0}
          个剧情节拍及改编偏好发送到当前文本模型；不会发送整本 TXT 或 NovelAI 凭证。
        </span>
      </label>
      <button
        type="button"
        className="generate-button"
        disabled={
          busy ||
          !configuration ||
          !vaultStatus?.unlocked ||
          !beatSet ||
          !confirmedDataSend ||
          pageBudget < 1
        }
        onClick={() => void generate()}
      >
        {busy ? "正在处理…" : storyboard ? "重新生成分镜版本" : "生成结构化分镜"}
      </button>
      {!beatSet && <p className="warning-inline">请先为所选章节建立剧情节拍。</p>}

      {connectionMessage && <p className="success-message" role="status">{connectionMessage}</p>}

      {storyboard && draft && (
        <div className="storyboard-editor">
          <div className="storyboard-status">
            <div>
              <strong>{statusLabel(storyboard.approval_status)}</strong>
              <span>
                {draft.pages.length} 页 · {draft.pages.reduce((sum, page) => sum + page.panels.length, 0)} 格 · {draftUnresolvedCount} 个未解决节拍 · {pagePolicyFindings.length} 个页型/格数异常
              </span>
            </div>
            <code>{storyboard.source_fingerprint.slice(0, 12)}</code>
          </div>
          {storyboard.approval_status === "stale" && (
            <p className="warning-inline">来源章节或剧情节拍已变化；请重新生成，当前版本不能审批。</p>
          )}

          <section className="coverage-editor">
            <h3>来源覆盖</h3>
            <ol>
              {draft.beat_resolutions.map((resolution, index) => (
                <li key={resolution.beat_id}>
                  <p>{sourceByBeatId.get(resolution.beat_id) ?? resolution.beat_id}</p>
                  <label>
                    <span>处理方式</span>
                    <select
                      value={resolution.status}
                      onChange={(event) =>
                        changeResolutionStatus(index, event.target.value as BeatResolution["status"])
                      }
                    >
                      <option value="represented">直接呈现</option>
                      <option value="condensed">合并压缩</option>
                      <option value="omitted">明确省略</option>
                      <option value="unresolved">尚未解决</option>
                    </select>
                  </label>
                  {(resolution.status === "represented" || resolution.status === "condensed") && (
                    <label>
                      <span>对应页码</span>
                      <input
                        value={resolution.page_numbers.join(",")}
                        onChange={(event) =>
                          updateResolution(index, { page_numbers: parsePageNumbers(event.target.value) })
                        }
                      />
                    </label>
                  )}
                  {resolution.status === "omitted" && (
                    <label className="reason-field">
                      <span>省略理由</span>
                      <input
                        value={resolution.reason ?? ""}
                        onChange={(event) => updateResolution(index, { reason: event.target.value })}
                      />
                    </label>
                  )}
                </li>
              ))}
            </ol>
          </section>

          <section className="scenes-editor">
            <h3>场景结构</h3>
            <div>
              {draft.scenes.map((scene, index) => (
                <article key={scene.scene_id} className="scene-card">
                  <header>
                    <strong>场景 {scene.order}</strong>
                    <span>{scene.beat_ids.length} 个剧情节拍</span>
                  </header>
                  <div>
                    <label>
                      <span>场景名称</span>
                      <input
                        value={scene.title}
                        onChange={(event) => updateScene(index, { title: event.target.value })}
                      />
                    </label>
                    <label>
                      <span>地点</span>
                      <input
                        value={scene.location}
                        onChange={(event) => updateScene(index, { location: event.target.value })}
                      />
                    </label>
                    <label>
                      <span>时间</span>
                      <input
                        value={scene.time_of_day}
                        onChange={(event) => updateScene(index, { time_of_day: event.target.value })}
                      />
                    </label>
                    <label className="scene-summary">
                      <span>场景摘要</span>
                      <textarea
                        rows={3}
                        value={scene.summary}
                        onChange={(event) => updateScene(index, { summary: event.target.value })}
                      />
                    </label>
                  </div>
                </article>
              ))}
            </div>
          </section>

          <section className="pages-editor">
            <h3>页面与分格</h3>
            {draft.pages.map((page, pageIndex) => (
              <article key={page.page_id} className="page-card">
                <header>
                  <strong>第 {page.page_number} 页</strong>
                  <span className={pagePolicyFindingForPage(page) ? "page-policy-invalid" : "page-policy-valid"}>
                    {page.page_type ? pageTypeLabel(page.page_type) : "未分类"} · {page.panels.length} 格 · {pagePolicyFindingForPage(page) ? "格数异常" : "符合规则"}
                  </span>
                  <label>
                    <span>本页叙事功能</span>
                    <input
                      value={page.turning_point}
                      onChange={(event) => updatePage(pageIndex, event.target.value)}
                    />
                  </label>
                </header>
                {page.panels.map((panel, panelIndex) => (
                  <PanelEditor
                    key={panel.panel_id}
                    panel={panel}
                    onChange={(patch) => updatePanel(pageIndex, panelIndex, patch)}
                  />
                ))}
              </article>
            ))}
          </section>

          {pagePolicyFindings.length > 0 && (
            <div className="warning-inline storyboard-policy-warning" role="alert">
              <strong>页面策略尚未满足</strong>
              <ul>
                {pagePolicyFindings.map((finding) => <li key={finding}>{finding}</li>)}
              </ul>
            </div>
          )}

          <div className="editor-footer storyboard-footer">
            <span>{dirty ? "有尚未保存的分镜修改" : "当前版本已保存"}</span>
            <div className="button-row">
              <button
                type="button"
                disabled={busy || !dirty || draft.schema_version !== "1.1"}
                onClick={() => void saveRevision()}
              >
                保存为新版本
              </button>
              <button
                type="button"
                className="approval-button"
                disabled={
                  busy ||
                  dirty ||
                  storyboard.approval_status !== "draft" ||
                  draft.beat_resolutions.some((resolution) => resolution.status === "unresolved") ||
                  pagePolicyFindings.length > 0
                }
                onClick={() => void approve()}
              >
                审批当前分镜
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function pageTypeLabel(pageType: NonNullable<StoryboardDocument["pages"][number]["page_type"]>) {
  return {
    standard: "普通页",
    cover: "封面",
    splash: "通页大场面",
    special: "特殊页",
  }[pageType];
}

function pagePolicyFindingForPage(
  page: StoryboardDocument["pages"][number],
): string | null {
  if (!page.page_type) return "缺少页面类型";
  const minimum = page.page_type === "standard" ? 3 : 1;
  if (page.panels.length < minimum || page.panels.length > 6) {
    return `${minimum}–6 格`;
  }
  return null;
}

function PanelEditor({
  panel,
  onChange,
}: {
  panel: StoryboardPanel;
  onChange: (patch: Partial<StoryboardPanel>) => void;
}) {
  function updateDialogue(index: number, patch: Partial<DialogueLine>) {
    onChange({
      dialogue: panel.dialogue.map((line, lineIndex) =>
        lineIndex === index ? { ...line, ...patch } : line,
      ),
    });
  }

  return (
    <section className="panel-card">
      <h4>第 {panel.order} 格</h4>
      <div className="panel-fields">
        <label>
          <span>剧情目的</span>
          <textarea rows={2} value={panel.purpose} onChange={(event) => onChange({ purpose: event.target.value })} />
        </label>
        <label>
          <span>镜头</span>
          <input value={panel.shot} onChange={(event) => onChange({ shot: event.target.value })} />
        </label>
        <label>
          <span>角色（逗号分隔）</span>
          <input
            value={panel.characters.join(", ")}
            onChange={(event) => onChange({ characters: splitList(event.target.value, /[,，]/) })}
          />
        </label>
        <label>
          <span>旁白（每行一项）</span>
          <textarea
            rows={2}
            value={panel.narration.join("\n")}
            onChange={(event) => onChange({ narration: splitList(event.target.value, /\n/) })}
          />
        </label>
        <label>
          <span>音效（每行一项）</span>
          <textarea
            rows={2}
            value={panel.sfx.join("\n")}
            onChange={(event) => onChange({ sfx: splitList(event.target.value, /\n/) })}
          />
        </label>
        <label className="prompt-field">
          <span>画面提示词</span>
          <textarea rows={3} value={panel.visual_prompt} onChange={(event) => onChange({ visual_prompt: event.target.value })} />
        </label>
        <label className="prompt-field">
          <span>负面提示词</span>
          <textarea rows={2} value={panel.negative_prompt} onChange={(event) => onChange({ negative_prompt: event.target.value })} />
        </label>
      </div>
      <div className="dialogue-editor">
        <strong>对白</strong>
        {panel.dialogue.map((line, index) => (
          <div key={`${index}-${line.speaker}`}>
            <input
              aria-label={`第 ${panel.order} 格对白 ${index + 1} 说话人`}
              value={line.speaker}
              onChange={(event) => updateDialogue(index, { speaker: event.target.value })}
            />
            <input
              aria-label={`第 ${panel.order} 格对白 ${index + 1} 内容`}
              value={line.text}
              onChange={(event) => updateDialogue(index, { text: event.target.value })}
            />
            <button
              type="button"
              className="quiet-button"
              onClick={() => onChange({ dialogue: panel.dialogue.filter((_, lineIndex) => lineIndex !== index) })}
            >
              删除
            </button>
          </div>
        ))}
        <button
          type="button"
          className="quiet-button"
          onClick={() => onChange({ dialogue: [...panel.dialogue, { speaker: "角色", text: "新对白" }] })}
        >
          添加对白
        </button>
      </div>
      <small>来源锚点：{panel.source_anchor_ids.length} 个</small>
    </section>
  );
}

function parsePageNumbers(value: string): number[] {
  return [...new Set(value.split(/[,，\s]+/).map(Number).filter((item) => Number.isInteger(item) && item > 0))];
}

function splitList(value: string, separator: RegExp): string[] {
  return value
    .split(separator)
    .map((item) => item.trim())
    .filter(Boolean);
}

function cloneDocument(document: StoryboardDocument): StoryboardDocument {
  return structuredClone(document);
}

function statusLabel(status: StoryboardVersion["approval_status"]): string {
  if (status === "approved") return "分镜已审批";
  if (status === "stale") return "来源已变化";
  return "分镜待审批";
}
