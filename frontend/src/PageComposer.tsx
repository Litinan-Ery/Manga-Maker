import { useEffect, useMemo, useState } from "react";

import {
  ApiError,
  type ChapterSet,
  type ComicPageVersion,
  type PageDocument,
  type PagePanelPlacement,
  type PageTemplate,
  type PageTextLayer,
  draftComicPages,
  getComicPageImage,
  listComicPages,
  listPageTemplates,
  saveComicPageRevision,
} from "./api";
import { RevisionWorkbench } from "./RevisionWorkbench";

interface PageComposerProps {
  projectId: string;
  chapterSet: ChapterSet;
  onError: (message: string) => void;
}

export function PageComposer({ projectId, chapterSet, onError }: PageComposerProps) {
  const [chapterId, setChapterId] = useState(chapterSet.chapters[0]?.chapter_id ?? "");
  const [templates, setTemplates] = useState<PageTemplate[]>([]);
  const [pages, setPages] = useState<ComicPageVersion[]>([]);
  const [selectedPageId, setSelectedPageId] = useState("");
  const [document, setDocument] = useState<PageDocument | null>(null);
  const [pagesLoading, setPagesLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const selectedPage = useMemo(
    () => pages.find((page) => page.page_id === selectedPageId) ?? pages[0] ?? null,
    [pages, selectedPageId],
  );

  useEffect(() => {
    if (!chapterSet.chapters.some((chapter) => chapter.chapter_id === chapterId)) {
      setChapterId(chapterSet.chapters[0]?.chapter_id ?? "");
    }
  }, [chapterId, chapterSet]);

  useEffect(() => {
    if (!chapterId) return;
    let active = true;
    setPagesLoading(true);
    setPages([]);
    setDocument(null);
    Promise.all([listPageTemplates(projectId), listComicPages(projectId, chapterId)])
      .then(([availableTemplates, currentPages]) => {
        if (!active) return;
        setTemplates(availableTemplates);
        setPages(currentPages);
        setSelectedPageId(currentPages[0]?.page_id ?? "");
      })
      .catch((error: unknown) => {
        if (active) onError(errorMessage(error));
      })
      .finally(() => {
        if (active) setPagesLoading(false);
      });
    return () => {
      active = false;
    };
  }, [chapterId, onError, projectId]);

  useEffect(() => {
    if (!selectedPage) {
      setDocument(null);
      return;
    }
    setSelectedPageId(selectedPage.page_id);
    setDocument(cloneDocument(selectedPage.document));
  }, [selectedPage?.page_id, selectedPage?.page_version_id]);

  async function handleDraft() {
    if (!chapterId) return;
    await run(async () => {
      const drafted = await draftComicPages(projectId, chapterId);
      setPages(drafted);
      setSelectedPageId(drafted[0]?.page_id ?? "");
      setMessage("已用当前面板素材建立规范页面；未调用任何图像 API。");
    });
  }

  async function handleSave() {
    if (!selectedPage || !document) return;
    await run(async () => {
      const saved = await saveComicPageRevision(projectId, selectedPage, document);
      setPages((current) =>
        current.map((page) => (page.page_id === saved.page_id ? saved : page)),
      );
      setMessage("新页面版本已在本地确定性渲染，文字和布局修改没有产生 NovelAI 请求。");
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

  function applyTemplate(templateId: string) {
    if (!document) return;
    const pageTemplate = templates.find(
      (item) => item.template_id === templateId && item.panel_count === document.panels.length,
    );
    if (!pageTemplate) return;
    setDocument({
      ...document,
      template_id: templateId,
      panels: document.panels.map((panel, index) => ({
        ...panel,
        frame: { ...pageTemplate.frames[index] },
      })),
    });
  }

  function updatePanel(index: number, next: PagePanelPlacement) {
    if (!document) return;
    setDocument({
      ...document,
      panels: document.panels.map((panel, itemIndex) =>
        itemIndex === index ? next : panel,
      ),
    });
  }

  function updateTextLayer(index: number, next: PageTextLayer) {
    if (!document) return;
    setDocument({
      ...document,
      text_layers: document.text_layers.map((layer, itemIndex) =>
        itemIndex === index ? next : layer,
      ),
    });
  }

  function addTextLayer() {
    if (!document) return;
    const panel = document.panels[0];
    const width = Math.min(620, panel.frame.width - 48);
    const height = 220;
    setDocument({
      ...document,
      text_layers: [
        ...document.text_layers,
        {
          layer_id: crypto.randomUUID(),
          panel_id: panel.panel_id,
          kind: "dialogue",
          text: "新对白",
          speaker: null,
          bounds: {
            x: panel.frame.x + 24,
            y: panel.frame.y + 24,
            width,
            height,
          },
          font_size: 42,
          align: "center",
        },
      ],
    });
  }

  return (
    <section className="page-composer" aria-label="本地页面编辑器">
      <div className="workspace-heading compact">
        <div>
          <p className="section-kicker">第八步</p>
          <h2>本地排版与漫画页</h2>
        </div>
        <span>{pages.length ? `${pages.length} 页` : "尚未建立"}</span>
      </div>
      <p className="panel-description">
        面板图像作为不可变素材，格框、裁切、对白、旁白、音效和页码在本机合成为 2048 × 3072 PNG。
      </p>
      <div className="page-composer-toolbar">
        <label>
          <span>章节</span>
          <select value={chapterId} onChange={(event) => setChapterId(event.target.value)}>
            {chapterSet.chapters.map((chapter) => (
              <option key={chapter.chapter_id} value={chapter.chapter_id}>
                {chapter.ordinal}. {chapter.title}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          disabled={busy || pagesLoading || !chapterId}
          onClick={() => void handleDraft()}
        >
          从当前素材建立漫画页
        </button>
      </div>

      {pages.length > 0 && selectedPage && document && (
        <div className="page-composer-workspace">
          <aside>
            <label>
              <span>当前页</span>
              <select value={selectedPage.page_id} onChange={(event) => setSelectedPageId(event.target.value)}>
                {pages.map((page) => (
                  <option key={page.page_id} value={page.page_id}>
                    第 {page.page_number} 页 · v{page.version}
                  </option>
                ))}
              </select>
            </label>
            <PagePreview projectId={projectId} page={selectedPage} />
            <code>{selectedPage.render_sha256.slice(0, 16)}</code>
          </aside>
          <div className="page-controls">
            <label>
              <span>1–6 格模板</span>
              <select value={document.template_id} onChange={(event) => applyTemplate(event.target.value)}>
                {templates
                  .filter((item) => item.panel_count === document.panels.length)
                  .map((item) => (
                    <option key={item.template_id} value={item.template_id}>{item.label}</option>
                  ))}
              </select>
            </label>

            <div className="page-panel-controls">
              {document.panels.map((panel, index) => (
                <PanelControls
                  key={panel.panel_id}
                  index={index}
                  panel={panel}
                  onChange={(next) => updatePanel(index, next)}
                />
              ))}
            </div>

            <div className="page-text-controls">
              <div className="bible-section-heading">
                <h3>本地文字图层</h3>
                <button type="button" className="quiet-button" onClick={addTextLayer}>
                  添加文字图层
                </button>
              </div>
              {document.text_layers.map((layer, index) => (
                <TextLayerControls
                  key={layer.layer_id}
                  layer={layer}
                  onChange={(next) => updateTextLayer(index, next)}
                  onRemove={() =>
                    setDocument({
                      ...document,
                      text_layers: document.text_layers.filter((_, itemIndex) => itemIndex !== index),
                    })
                  }
                />
              ))}
            </div>
            <label className="confirmation-row">
              <input
                type="checkbox"
                checked={document.show_page_number}
                onChange={(event) => setDocument({ ...document, show_page_number: event.target.checked })}
              />
              <span>显示页码</span>
            </label>
            <button type="button" disabled={busy} onClick={() => void handleSave()}>
              保存并重新渲染页面（仅本地）
            </button>
            <RevisionWorkbench
              projectId={projectId}
              page={selectedPage}
              onPageChange={(next) => {
                setPages((current) =>
                  current.map((item) => (item.page_id === next.page_id ? next : item)),
                );
                setSelectedPageId(next.page_id);
              }}
              onError={onError}
            />
          </div>
        </div>
      )}
      {message && <p className="success-message" role="status">{message}</p>}
    </section>
  );
}

function PanelControls({
  index,
  panel,
  onChange,
}: {
  index: number;
  panel: PagePanelPlacement;
  onChange: (panel: PagePanelPlacement) => void;
}) {
  return (
    <article>
      <strong>面板 {index + 1}</strong>
      <div className="compact-field-grid">
        {(["x", "y", "width", "height"] as const).map((field) => (
          <NumberField
            key={field}
            label={{ x: "X", y: "Y", width: "宽", height: "高" }[field]}
            value={panel.frame[field]}
            min={field === "x" || field === "y" ? 0 : 64}
            max={field === "x" || field === "width" ? 2048 : 3072}
            onChange={(value) => onChange({ ...panel, frame: { ...panel.frame, [field]: value } })}
          />
        ))}
        <NumberField label="焦点 X" value={panel.focal_x} min={0} max={1} step={0.05} onChange={(value) => onChange({ ...panel, focal_x: value })} />
        <NumberField label="焦点 Y" value={panel.focal_y} min={0} max={1} step={0.05} onChange={(value) => onChange({ ...panel, focal_y: value })} />
        <NumberField label="放大" value={panel.zoom} min={1} max={4} step={0.05} onChange={(value) => onChange({ ...panel, zoom: value })} />
      </div>
    </article>
  );
}

function TextLayerControls({
  layer,
  onChange,
  onRemove,
}: {
  layer: PageTextLayer;
  onChange: (layer: PageTextLayer) => void;
  onRemove: () => void;
}) {
  return (
    <article>
      <label>
        <span>类型</span>
        <select value={layer.kind} onChange={(event) => onChange({ ...layer, kind: event.target.value as PageTextLayer["kind"] })}>
          <option value="dialogue">对白气泡</option>
          <option value="narration">旁白框</option>
          <option value="sfx">音效字</option>
        </select>
      </label>
      <label className="page-text-value">
        <span>文字</span>
        <textarea rows={3} value={layer.text} onChange={(event) => onChange({ ...layer, text: event.target.value })} />
      </label>
      <div className="compact-field-grid">
        {(["x", "y", "width", "height"] as const).map((field) => (
          <NumberField
            key={field}
            label={{ x: "X", y: "Y", width: "宽", height: "高" }[field]}
            value={layer.bounds[field]}
            min={field === "x" || field === "y" ? 0 : 64}
            max={field === "x" || field === "width" ? 2048 : 3072}
            onChange={(value) => onChange({ ...layer, bounds: { ...layer.bounds, [field]: value } })}
          />
        ))}
        <NumberField label="字号" value={layer.font_size} min={20} max={180} onChange={(value) => onChange({ ...layer, font_size: value })} />
      </div>
      <button type="button" className="quiet-button" onClick={onRemove}>移除图层</button>
    </article>
  );
}

function NumberField({
  label,
  value,
  min,
  max,
  step = 1,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (value: number) => void;
}) {
  return (
    <label>
      <span>{label}</span>
      <input type="number" value={value} min={min} max={max} step={step} onChange={(event) => onChange(Number(event.target.value))} />
    </label>
  );
}

function PagePreview({ projectId, page }: { projectId: string; page: ComicPageVersion }) {
  const [source, setSource] = useState("");
  useEffect(() => {
    let active = true;
    let objectUrl = "";
    getComicPageImage(projectId, page.page_id, page.page_version_id)
      .then((blob) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setSource(objectUrl);
      })
      .catch(() => setSource(""));
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [page.page_id, page.page_version_id, projectId]);
  return source ? <img src={source} alt={`第 ${page.page_number} 页漫画预览`} /> : <div className="page-preview-placeholder">本地页面</div>;
}

function cloneDocument(document: PageDocument): PageDocument {
  return JSON.parse(JSON.stringify(document)) as PageDocument;
}

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "页面编辑操作失败。";
}
