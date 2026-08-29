import {
  type KeyboardEvent,
  type PointerEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import type { FrameSpec, NormalizedRect, PageLayoutDraft, ShotScale } from "../../generated/api/v03Types";
import { ContractApiError } from "../../generated/api/contractError";
import { LOCAL_DIMENSION_CAPABILITIES } from "./capabilities";
import type {
  ApprovedStoryboardSummary,
  DimensionOutcome,
  DimensionSelection,
  LayoutApproval,
  LayoutApprovalValidation,
  LayoutClient,
  LayoutImpact,
  LayoutVersionSnapshot,
  StoryboardPageSummary,
} from "./client";
import { leafFrames } from "./client";
import {
  LAYOUT_TEMPLATES,
  absoluteFrameRect,
  applyTemplate,
  changePageProfile,
  createLayoutDraft,
  frameDepth,
  mergeFrame,
  moveReadingOrder,
  splitFrame,
  templatesForPage,
  updateFrame,
  updateFrameAbsoluteRect,
} from "./templates";

export interface LayoutChapterSummary {
  chapter_id: string;
  title: string;
}

export interface LayoutWorkbenchProps {
  projectId: string;
  chapters: LayoutChapterSummary[];
  client: LayoutClient;
  onError: (message: string) => void;
}

export function LayoutWorkbench({ projectId, chapters, client, onError }: LayoutWorkbenchProps) {
  const [chapterId, setChapterId] = useState(chapters[0]?.chapter_id ?? "");
  const [storyboard, setStoryboard] = useState<ApprovedStoryboardSummary | null>(null);
  const [pageId, setPageId] = useState("");
  const [snapshot, setSnapshot] = useState<LayoutVersionSnapshot | null>(null);
  const [approval, setApproval] = useState<LayoutApproval | null>(null);
  const [draft, setDraft] = useState<PageLayoutDraft | null>(null);
  const [selectedFrameId, setSelectedFrameId] = useState("");
  const [validation, setValidation] = useState<LayoutApprovalValidation | null>(null);
  const [impact, setImpact] = useState<LayoutImpact | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [conflict, setConflict] = useState(false);
  const [approvalConfirmed, setApprovalConfirmed] = useState(false);
  const [approvalInvalidated, setApprovalInvalidated] = useState(false);

  const page = storyboard?.pages.find((item) => item.page_id === pageId) ?? null;
  const selectedFrame = draft?.frames.find((frame) => frame.frame_id === selectedFrameId) ?? null;
  const dirty = Boolean(snapshot && draft && JSON.stringify(draft) !== JSON.stringify(snapshot.layout));
  const hasUnmappedLeaf = draft ? leafFrames(draft).some((frame) => frame.panel_id === null) : false;
  const dimensionByFrame = useMemo(
    () => new Map(validation?.dimension_outcomes.map((outcome) => [outcome.frame_id, outcome]) ?? []),
    [validation],
  );
  const canApprove = Boolean(
    snapshot &&
      validation?.valid &&
      !dirty &&
      !hasUnmappedLeaf &&
      approval?.state !== "active" &&
      approvalConfirmed,
  );

  useEffect(() => {
    setChapterId((current) =>
      chapters.some((chapter) => chapter.chapter_id === current)
        ? current
        : (chapters[0]?.chapter_id ?? ""),
    );
  }, [chapters]);

  useEffect(() => {
    if (!chapterId) return;
    let active = true;
    setStoryboard(null);
    setSnapshot(null);
    setDraft(null);
    setApproval(null);
    setValidation(null);
    setImpact(null);
    Promise.all([client.getApprovedStoryboard(projectId, chapterId), client.listCurrent(projectId, chapterId)])
      .then(async ([nextStoryboard, layouts]) => {
        if (!active) return;
        setStoryboard(nextStoryboard);
        const firstPage = nextStoryboard.pages[0];
        setPageId(firstPage?.page_id ?? "");
        const restored = layouts.find((item) => item.layout.page_id === firstPage?.page_id) ?? null;
        if (restored) await loadSnapshot(restored, active);
      })
      .catch((error: unknown) => active && onError(errorMessage(error)));
    return () => {
      active = false;
    };
  }, [chapterId, client, onError, projectId]);

  async function loadPage(nextPageId: string) {
    setPageId(nextPageId);
    setValidation(null);
    setImpact(null);
    setApprovalConfirmed(false);
    setApprovalInvalidated(false);
    const layouts = await client.listCurrent(projectId, chapterId);
    const restored = layouts.find((item) => item.layout.page_id === nextPageId) ?? null;
    if (restored) {
      await loadSnapshot(restored);
    } else {
      setSnapshot(null);
      setApproval(null);
      setDraft(null);
      setSelectedFrameId("");
    }
  }

  async function loadSnapshot(next: LayoutVersionSnapshot, active = true) {
    const [nextApproval, nextImpact] = await Promise.all([
      client.getApproval(projectId, next.page_layout_draft_version_id),
      client.getImpact(projectId, next),
    ]);
    if (!active) return;
    setSnapshot(next);
    setDraft(structuredClone(next.layout));
    setSelectedFrameId(leafFrames(next.layout)[0]?.frame_id ?? "");
    setApproval(nextApproval);
    setImpact(nextImpact);
    setValidation(null);
    setConflict(false);
    setApprovalInvalidated(false);
  }

  async function createFromTemplate(templateId: string) {
    if (!storyboard || !page || storyboard.approval_status !== "approved") return;
    const template = LAYOUT_TEMPLATES.find(
      (item) => item.id === templateId && item.rects.length === page.panels.length,
    );
    if (!template) return;
    await run(async () => {
      const created = await client.createDraft(
        projectId,
        chapterId,
        storyboard.storyboard_version_id,
        createLayoutDraft(page, template),
        idempotencyKey("create", page.page_id),
      );
      await loadSnapshot(created);
      setMessage("版式草稿已写入本地工程，未启动任何图像请求。");
    });
  }

  async function save() {
    if (!snapshot || !draft || !storyboard) return;
    await run(async () => {
      try {
        const invalidatesApproval = approval?.state === "active" || approvalInvalidated;
        const saved = await client.saveDraft(
          projectId,
          snapshot.page_layout_draft_version_id,
          storyboard.storyboard_version_id,
          draft,
          snapshot.revision,
          idempotencyKey("save", snapshot.page_layout_draft_version_id),
        );
        await loadSnapshot(saved);
        setApprovalInvalidated(invalidatesApproval);
        setMessage(`版式已保存为不可变版本 ${saved.revision}。`);
      } catch (error) {
        if (error instanceof ContractApiError && error.status === 409) {
          setConflict(true);
          setMessage("后端已有更新；本地草稿仍保留，请重新加载后再决定如何合并。");
          return;
        }
        throw error;
      }
    });
  }

  async function reload() {
    if (!snapshot) return;
    await run(async () => {
      const layouts = await client.listCurrent(projectId, chapterId);
      const current = layouts.find(
        (item) => item.layout.page_layout_draft_id === snapshot.layout.page_layout_draft_id,
      );
      if (!current) throw new Error("后端未返回当前版式版本。");
      await loadSnapshot(current);
      setMessage("已重新加载后端当前版本；之前的本地草稿已丢弃。");
    });
  }

  async function validate() {
    if (!snapshot || dirty) return;
    await run(async () => {
      const result = await client.validate(projectId, snapshot, LOCAL_DIMENSION_CAPABILITIES);
      setValidation(result);
      setApprovalConfirmed(false);
      const firstInvalid = result.layout.findings[0]?.frame_ids[0];
      if (firstInvalid) setSelectedFrameId(firstInvalid);
      setMessage(
        result.valid ? "本地版式与尺寸校验通过，可以查看影响后审批。" : "校验发现问题，已定位第一处非法格框。",
      );
    });
  }

  async function approve() {
    if (!snapshot || !validation || !canApprove) return;
    const selections = validation.dimension_outcomes.filter(
      (outcome): outcome is DimensionSelection => outcome.status === "selected",
    );
    await run(async () => {
      const nextApproval = await client.approve(
        projectId,
        snapshot,
        LOCAL_DIMENSION_CAPABILITIES,
        selections,
        idempotencyKey("approve", snapshot.page_layout_draft_version_id),
      );
      setApproval(nextApproval);
      setApprovalInvalidated(false);
      setApprovalConfirmed(false);
      setMessage("当前版式与每格合法尺寸已批准；仍未发出图像请求。");
    });
  }

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setMessage("");
    try {
      await action();
    } catch (error) {
      onError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  function changeDraft(next: PageLayoutDraft, preferredFrameId?: string) {
    if (approval?.state === "active") setApprovalInvalidated(true);
    setDraft(next);
    setValidation(null);
    setApprovalConfirmed(false);
    if (preferredFrameId) setSelectedFrameId(preferredFrameId);
  }

  return (
    <section className="layout-workbench" aria-labelledby="layout-workbench-title">
      <div className="workspace-heading compact">
        <div>
          <p className="section-kicker">第五步</p>
          <h2 id="layout-workbench-title">版式工作台</h2>
        </div>
        <span>{snapshot ? `版式版本 ${snapshot.revision}` : "先批准版式，再编译 Prompt"}</span>
      </div>

      {!storyboard && <p className="warning-inline">正在读取已批准分镜与本地版式。</p>}
      {storyboard?.approval_status !== "approved" && (
        <p className="warning-inline">请先批准当前 Storyboard；版式不会调用文本模型或图像模型。</p>
      )}
      {storyboard && (
        <div className="layout-source-pickers">
          <label>
            <span>选择章节</span>
            <select value={chapterId} onChange={(event) => setChapterId(event.target.value)}>
              {chapters.map((chapter) => (
                <option key={chapter.chapter_id} value={chapter.chapter_id}>
                  {chapter.title}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>选择分镜页面</span>
            <select value={pageId} onChange={(event) => void loadPage(event.target.value)}>
              {storyboard.pages.map((item) => (
                <option key={item.page_id} value={item.page_id}>
                  第 {item.page_number} 页 · {layoutPageTypeLabel(item.page_type)} · {item.panels.length} 格 · {item.turning_point}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      {!snapshot && page && (
        <div className="layout-template-picker" aria-label="1 到 6 格版式模板">
          <h3>选择起始节奏</h3>
          <p>模板只建立本地格框；每个 Storyboard panel 会映射到一个叶子 frame。</p>
          <div>
            {templatesForPage(page).map(
              (template) => (
                <button
                  type="button"
                  key={template.id}
                  disabled={busy || storyboard?.approval_status !== "approved"}
                  onClick={() => void createFromTemplate(template.id)}
                >
                  {template.label}
                </button>
              ),
            )}
          </div>
          {templatesForPage(page).length === 0 && (
            <p className="warning-inline" role="alert">
              当前页型与分镜数量不符合规则，不能创建版式草稿。
            </p>
          )}
        </div>
      )}

      {draft && snapshot && page && (
        <>
          <div className="layout-toolbar">
            <label>
              <span>页面规格</span>
              <select
                value={draft.page_profile}
                onChange={(event) => {
                  const profile = event.target.value as PageLayoutDraft["page_profile"];
                  changeDraft(changePageProfile(draft, profile));
                }}
              >
                <option value="print_portrait_2_3">印刷竖版 2:3</option>
                <option value="digital_portrait_2_3">数字竖版 2:3</option>
                <option value="vertical_strip">竖向条漫</option>
              </select>
            </label>
            <label>
              <span>阅读方向</span>
              <select
                value={draft.reading_direction}
                onChange={(event) => changeDraft({ ...draft, reading_direction: event.target.value as PageLayoutDraft["reading_direction"] })}
              >
                <option value="ltr_ttb">从左到右、从上到下</option>
                <option value="rtl_ttb">从右到左、从上到下</option>
                <option value="ttb">从上到下</option>
              </select>
            </label>
            <label>
              <span>重套模板</span>
              <select
                aria-label="重套模板"
                defaultValue=""
                onChange={(event) => {
                  const template = LAYOUT_TEMPLATES.find((item) => item.id === event.target.value);
                  if (template) changeDraft(applyTemplate(draft, page, template));
                  event.currentTarget.value = "";
                }}
              >
                <option value="" disabled>选择相同格数模板</option>
                {templatesForPage(page).map(
                  (template) => <option key={template.id} value={template.id}>{template.label}</option>,
                )}
              </select>
            </label>
          </div>

          <div className="layout-stage">
            <FrameTree
              draft={draft}
              selectedFrameId={selectedFrameId}
              onSelect={setSelectedFrameId}
              onMove={(frameId, delta) => changeDraft(moveReadingOrder(draft, frameId, delta), frameId)}
            />
            <LayoutCanvas
              draft={draft}
              selectedFrameId={selectedFrameId}
              outcomes={dimensionByFrame}
              onSelect={setSelectedFrameId}
              onChange={(frameId, nextRect) =>
                changeDraft(updateFrameAbsoluteRect(draft, frameId, nextRect), frameId)
              }
            />
            {selectedFrame && (
              <FrameInspector
                draft={draft}
                frame={selectedFrame}
                outcome={dimensionByFrame.get(selectedFrame.frame_id)}
                onChange={(patch) => changeDraft(updateFrame(draft, selectedFrame.frame_id, patch), selectedFrame.frame_id)}
                onSplit={(orientation) => changeDraft(splitFrame(draft, selectedFrame.frame_id, orientation), selectedFrame.frame_id)}
                onMerge={() => changeDraft(mergeFrame(draft, selectedFrame.frame_id), selectedFrame.frame_id)}
              />
            )}
          </div>

          {validation && !validation.valid && (
            <section className="layout-findings" role="alert">
              <h3>需要修复 {validation.failure_paths.length} 项</h3>
              <ol>
                {validation.layout.findings.map((finding) => (
                  <li key={`${finding.code}-${finding.path}`}>
                    <button type="button" onClick={() => finding.frame_ids[0] && setSelectedFrameId(finding.frame_ids[0])}>
                      {finding.code} · {finding.path}
                    </button>
                    <span>{finding.message}</span>
                  </li>
                ))}
              </ol>
            </section>
          )}

          <ImpactPreview impact={impact} />

          {conflict && (
            <div className="layout-conflict" role="alert">
              <p>检测到 revision conflict。本地草稿未被覆盖。</p>
              <button type="button" onClick={() => void reload()}>重新加载后端当前版本</button>
            </div>
          )}
          <div className="layout-actions">
            <span>
              {approval?.state === "active"
                ? dirty || approvalInvalidated
                  ? "修改后旧审批将失效"
                  : "当前版本已批准"
                : approvalInvalidated
                  ? "旧审批已因新版本失效"
                : approval?.state === "stale"
                  ? "旧审批已失效"
                  : dirty
                    ? "有未保存修改"
                    : "当前版本尚未批准"}
            </span>
            <div>
              <button type="button" disabled={busy || !dirty} onClick={() => void save()}>
                保存为新版本
              </button>
              <button type="button" className="quiet-button" disabled={busy || dirty} onClick={() => void validate()}>
                校验版式与尺寸
              </button>
            </div>
          </div>
          <label className="layout-approval-confirmation">
            <input
              type="checkbox"
              checked={approvalConfirmed}
              disabled={!validation?.valid || dirty || approval?.state === "active"}
              onChange={(event) => setApprovalConfirmed(event.target.checked)}
            />
            <span>我已核对受影响对象、每格合法尺寸与裁切风险；本次审批不会启动图像请求。</span>
          </label>
          <button type="button" className="approval-button" disabled={busy || !canApprove} onClick={() => void approve()}>
            批准当前版式
          </button>
        </>
      )}
      {message && <p className="success-message" role="status">{message}</p>}
    </section>
  );
}

function FrameTree({
  draft,
  selectedFrameId,
  onSelect,
  onMove,
}: {
  draft: PageLayoutDraft;
  selectedFrameId: string;
  onSelect: (frameId: string) => void;
  onMove: (frameId: string, delta: -1 | 1) => void;
}) {
  return (
    <nav className="frame-tree" aria-label="格框层级与阅读顺序">
      <h3>层级与顺序</h3>
      <ol>
        {[...draft.frames]
          .sort((first, second) => frameDepth(draft, first.frame_id) - frameDepth(draft, second.frame_id) || (first.order ?? 0) - (second.order ?? 0))
          .map((frame) => (
            <li key={frame.frame_id} style={{ paddingInlineStart: `${frameDepth(draft, frame.frame_id) * 14}px` }}>
              <button
                type="button"
                className={frame.frame_id === selectedFrameId ? "selected" : ""}
                onClick={() => onSelect(frame.frame_id)}
                onKeyDown={(event) => {
                  if (!frame.panel_id || !event.altKey) return;
                  if (event.key === "ArrowUp") onMove(frame.frame_id, -1);
                  else if (event.key === "ArrowDown") onMove(frame.frame_id, 1);
                  else return;
                  event.preventDefault();
                }}
              >
                {frame.panel_id ? `格 ${frame.order}` : "容器"} · {frame.shot_scale}
              </button>
              {frame.panel_id && (
                <span>
                  <button type="button" aria-label={`格 ${frame.order} 前移`} onClick={() => onMove(frame.frame_id, -1)}>↑</button>
                  <button type="button" aria-label={`格 ${frame.order} 后移`} onClick={() => onMove(frame.frame_id, 1)}>↓</button>
                </span>
              )}
            </li>
          ))}
      </ol>
    </nav>
  );
}

function LayoutCanvas({
  draft,
  selectedFrameId,
  outcomes,
  onSelect,
  onChange,
}: {
  draft: PageLayoutDraft;
  selectedFrameId: string;
  outcomes: Map<string, DimensionOutcome>;
  onSelect: (frameId: string) => void;
  onChange: (frameId: string, rect: NormalizedRect) => void;
}) {
  const leaves = leafFrames(draft);
  const drag = useRef<{
    pointerId: number;
    frameId: string;
    startX: number;
    startY: number;
    canvas: DOMRect;
    rect: NormalizedRect;
  } | null>(null);

  function keyboardMove(event: KeyboardEvent<HTMLButtonElement>, frame: FrameSpec) {
    const step = event.shiftKey ? 0.05 : 0.01;
    const patch = { ...absoluteFrameRect(draft, frame.frame_id) };
    if (event.key === "ArrowLeft") patch.x = clamp(patch.x - step, 0, 1 - patch.width);
    else if (event.key === "ArrowRight") patch.x = clamp(patch.x + step, 0, 1 - patch.width);
    else if (event.key === "ArrowUp") patch.y = clamp(patch.y - step, 0, 1 - patch.height);
    else if (event.key === "ArrowDown") patch.y = clamp(patch.y + step, 0, 1 - patch.height);
    else return;
    event.preventDefault();
    onChange(frame.frame_id, patch);
  }

  function startDrag(event: PointerEvent<HTMLButtonElement>, frame: FrameSpec) {
    if (event.button !== 0) return;
    const canvas = event.currentTarget.parentElement?.getBoundingClientRect();
    if (!canvas || canvas.width === 0 || canvas.height === 0) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    drag.current = {
      pointerId: event.pointerId,
      frameId: frame.frame_id,
      startX: event.clientX,
      startY: event.clientY,
      canvas,
      rect: absoluteFrameRect(draft, frame.frame_id),
    };
    onSelect(frame.frame_id);
  }

  function continueDrag(event: PointerEvent<HTMLButtonElement>) {
    const active = drag.current;
    if (!active || active.pointerId !== event.pointerId) return;
    const x = clamp(
      active.rect.x + (event.clientX - active.startX) / active.canvas.width,
      0,
      1 - active.rect.width,
    );
    const y = clamp(
      active.rect.y + (event.clientY - active.startY) / active.canvas.height,
      0,
      1 - active.rect.height,
    );
    onChange(active.frameId, { ...active.rect, x, y });
  }

  function stopDrag(event: PointerEvent<HTMLButtonElement>) {
    if (drag.current?.pointerId === event.pointerId) drag.current = null;
  }

  return (
    <div className="layout-canvas-wrap">
      <div className="layout-canvas" style={{ aspectRatio: `${draft.canvas.width} / ${draft.canvas.height}` }} aria-label="版式画布">
        {leaves.map((frame) => {
          const outcome = outcomes.get(frame.frame_id);
          const frameRect = absoluteFrameRect(draft, frame.frame_id);
          return (
            <button
              type="button"
              key={frame.frame_id}
              className={`layout-frame ${selectedFrameId === frame.frame_id ? "selected" : ""} ${outcome?.status === "unsatisfied" ? "invalid" : ""}`}
              style={{ left: `${frameRect.x * 100}%`, top: `${frameRect.y * 100}%`, width: `${frameRect.width * 100}%`, height: `${frameRect.height * 100}%` }}
              onClick={() => onSelect(frame.frame_id)}
              onKeyDown={(event) => keyboardMove(event, frame)}
              onPointerDown={(event) => startDrag(event, frame)}
              onPointerMove={continueDrag}
              onPointerUp={stopDrag}
              onPointerCancel={stopDrag}
            >
              <span>#{frame.order}</span>
              <small>{frame.shot_scale}</small>
              <i style={{ left: `${frame.focal_point.x * 100}%`, top: `${frame.focal_point.y * 100}%` }} />
            </button>
          );
        })}
      </div>
      <p>{draft.canvas.width} × {draft.canvas.height} · 键盘方向键微调，Shift 加速</p>
    </div>
  );
}

function FrameInspector({
  draft,
  frame,
  outcome,
  onChange,
  onSplit,
  onMerge,
}: {
  draft: PageLayoutDraft;
  frame: FrameSpec;
  outcome?: DimensionOutcome;
  onChange: (patch: Partial<FrameSpec>) => void;
  onSplit: (orientation: "horizontal" | "vertical") => void;
  onMerge: () => void;
}) {
  const childCount = draft.frames.filter((candidate) => candidate.parent_frame_id === frame.frame_id).length;
  const children = draft.frames.filter((candidate) => candidate.parent_frame_id === frame.frame_id);
  const canSplit = frame.panel_id === null && childCount >= 2;
  const canMerge = childCount === 2 && children.every((child) => child.panel_id === null);
  return (
    <aside className="frame-inspector">
      <h3>{frame.panel_id ? `格 ${frame.order} 属性` : "容器属性"}</h3>
      <label>
        <span>景别</span>
        <select value={frame.shot_scale} onChange={(event) => onChange({ shot_scale: event.target.value as ShotScale })}>
          <option value="extreme_close_up">极特写</option>
          <option value="close_up">特写</option>
          <option value="medium">中景</option>
          <option value="full">全身</option>
          <option value="wide">远景</option>
          <option value="establishing">建立镜头</option>
        </select>
      </label>
      <RectEditor label="父容器内格框 0–1" rect={frame.rect} onChange={(rect) => onChange({ rect })} />
      <div className="point-editor">
        <span>焦点</span>
        <NumberField label="X" value={frame.focal_point.x} onChange={(x) => onChange({ focal_point: { ...frame.focal_point, x } })} />
        <NumberField label="Y" value={frame.focal_point.y} onChange={(y) => onChange({ focal_point: { ...frame.focal_point, y } })} />
      </div>
      <RectEditor label="裁切保护区" rect={frame.crop_safe_rect} onChange={(crop_safe_rect) => onChange({ crop_safe_rect })} />
      {frame.text_safe_zones[0] && (
        <RectEditor
          label="文字安全区"
          rect={frame.text_safe_zones[0].rect}
          onChange={(rect) => onChange({ text_safe_zones: [{ ...frame.text_safe_zones[0], rect }, ...frame.text_safe_zones.slice(1)] })}
        />
      )}
      {frame.character_positions.map((position, index) => (
        <div className="point-editor" key={position.character_id}>
          <span>人物 {index + 1} 位置</span>
          <NumberField label="X" value={position.center.x} onChange={(x) => onChange({ character_positions: frame.character_positions.map((item, itemIndex) => itemIndex === index ? { ...item, center: { ...item.center, x } } : item) })} />
          <NumberField label="Y" value={position.center.y} onChange={(y) => onChange({ character_positions: frame.character_positions.map((item, itemIndex) => itemIndex === index ? { ...item, center: { ...item.center, y } } : item) })} />
        </div>
      ))}
      <dl className="dimension-preview">
        <div><dt>实际比例</dt><dd>{frame.aspect_ratio.toFixed(3)}</dd></div>
        <div><dt>合法尺寸</dt><dd>{outcome?.status === "selected" ? `${outcome.selected.width} × ${outcome.selected.height}` : outcome ? "不可满足" : "待校验"}</dd></div>
        <div><dt>预计裁切</dt><dd>{outcome?.status === "selected" ? `${(outcome.expected_crop_ratio * 100).toFixed(1)}%` : "—"}</dd></div>
        <div><dt>裁切风险</dt><dd>{outcome?.ranked_candidates[0] ? `${(outcome.ranked_candidates[0].crop_safe_risk * 100).toFixed(1)}%` : "—"}</dd></div>
      </dl>
      <div className="frame-structure-actions">
        <button type="button" className="quiet-button" disabled={!canSplit} onClick={() => onSplit("horizontal")}>按上下拆分层级</button>
        <button type="button" className="quiet-button" disabled={!canSplit} onClick={() => onSplit("vertical")}>按左右拆分层级</button>
        <button type="button" className="quiet-button" disabled={!canMerge} onClick={onMerge}>合并子层级</button>
      </div>
    </aside>
  );
}

function RectEditor({ label, rect, onChange }: { label: string; rect: NormalizedRect; onChange: (rect: NormalizedRect) => void }) {
  function change(key: keyof NormalizedRect, value: number) {
    const next = { ...rect, [key]: value };
    next.width = clamp(next.width, 0.01, 1 - next.x);
    next.height = clamp(next.height, 0.01, 1 - next.y);
    next.x = clamp(next.x, 0, 1 - next.width);
    next.y = clamp(next.y, 0, 1 - next.height);
    onChange(next);
  }
  return (
    <fieldset className="rect-editor">
      <legend>{label}</legend>
      {(["x", "y", "width", "height"] as const).map((key) => (
        <NumberField key={key} label={key} value={rect[key]} onChange={(value) => change(key, value)} />
      ))}
    </fieldset>
  );
}

function NumberField({ label, value, onChange }: { label: string; value: number; onChange: (value: number) => void }) {
  return (
    <label>
      <span>{label}</span>
      <input type="number" min={0} max={1} step={0.01} value={round(value)} onChange={(event) => onChange(clamp(Number(event.target.value), 0, 1))} />
    </label>
  );
}

function ImpactPreview({ impact }: { impact: LayoutImpact | null }) {
  const groups = new Map<string, number>();
  for (const item of impact?.impacts ?? []) {
    groups.set(item.artifact.artifact_type, (groups.get(item.artifact.artifact_type) ?? 0) + 1);
  }
  const label = (type: string) => ({ prompt_plan: "Prompt", generation_spec: "Spec", review_decision: "Review", page_approval: "PageApproval" }[type] ?? type);
  return (
    <section className="layout-impact-preview">
      <h3>审批影响预览</h3>
      {groups.size ? (
        <ul>{[...groups].map(([type, count]) => <li key={type}><strong>{label(type)}</strong><span>{count} 个将变为 stale</span></li>)}</ul>
      ) : <p>当前尚无下游 Prompt / Spec / Review / PageApproval。</p>}
      <div><span>候选数：0（本票不创建候选）</span><span>图像成本：0（仅本地审批）</span></div>
    </section>
  );
}

function idempotencyKey(kind: string, resourceId: string): string {
  return `layout-${kind}-${resourceId}-${crypto.randomUUID()}`;
}

function layoutPageTypeLabel(pageType: StoryboardPageSummary["page_type"]): string {
  if (pageType === "standard") return "普通页";
  if (pageType === "cover") return "封面";
  if (pageType === "splash") return "通页大场面";
  if (pageType === "special") return "特殊页";
  return "未分类";
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

function round(value: number): number {
  return Math.round(value * 1000) / 1000;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "版式操作失败。";
}
