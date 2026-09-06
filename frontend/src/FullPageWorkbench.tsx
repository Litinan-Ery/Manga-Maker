import { useEffect, useState } from "react";
import { type ChapterSet, listComicPages } from "./api";
import {
  adoptFullPage, fullPageHistory, fullPageImage, fullPageSources, generateFullPage, previewFullPage,
  type FullPageGeneration, type FullPageOptions, type FullPageSource,
} from "./fullPages";

interface Props {
  projectId: string;
  chapterSet: ChapterSet;
  onError: (message: string) => void;
  onAdopted: () => void;
}
const labels = { draft: "待确认", running: "生成中", ready: "待审阅成图", failed: "失败", needs_review: "结果待核查" };

export function FullPageWorkbench(props: Props) {
  const { projectId, chapterSet } = props;
  return <FullPageEditor
    key={`${projectId}:${chapterSet.chapter_set_id}:${chapterSet.chapter_set_version}`}
    {...props}
  />;
}

function FullPageEditor({ projectId, chapterSet, onError, onAdopted }: Props) {
  const [chapterId, setChapterId] = useState(chapterSet.chapters[0]?.chapter_id ?? "");
  const [sources, setSources] = useState<FullPageSource[]>([]);
  const [pageNumber, setPageNumber] = useState(1);
  const [policy, setPolicy] = useState<"local" | "model">("local");
  const [seed, setSeed] = useState(42);
  const [ceiling, setCeiling] = useState(0);
  const [descriptions, setDescriptions] = useState<Record<string, string>>({});
  const [texts, setTexts] = useState<Record<string, string[]>>({});
  const [characters, setCharacters] = useState<Record<string, string[]>>({});
  const [plan, setPlan] = useState<FullPageGeneration | null>(null);
  const [history, setHistory] = useState<FullPageGeneration[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const [reviewed, setReviewed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [refresh, setRefresh] = useState(0);
  const source = sources.find((page) => page.page_number === pageNumber);

  useEffect(() => {
    let active = true;
    setPlan(null); setConfirmed(false); setReviewed(false); setDescriptions({}); setTexts({});
    setSources([]); setHistory([]); setCharacters({});
    void Promise.allSettled([fullPageSources(projectId, chapterId), fullPageHistory(projectId, chapterId)])
      .then(([sourceResult, historyResult]) => {
        if (!active) return;
        if (sourceResult.status === "fulfilled") {
          setSources(sourceResult.value.pages); setMessage("");
          setPageNumber((current) => sourceResult.value.pages.some((page) => page.page_number === current)
            ? current : sourceResult.value.pages[0]?.page_number ?? 1);
        } else setMessage(String(sourceResult.reason?.message ?? "请先批准分镜、版式、角色与风格。"));
        if (historyResult.status === "fulfilled") setHistory(historyResult.value);
        else onError(String(historyResult.reason?.message ?? "无法读取整页历史。"));
      });
    return () => { active = false; };
  }, [projectId, chapterId, refresh, onError]);

  function invalidate() { setPlan(null); setConfirmed(false); setReviewed(false); }
  async function run(action: () => Promise<void>) {
    setBusy(true); setMessage("");
    try { await action(); } catch (error) { onError(error instanceof Error ? error.message : String(error)); }
    finally { setBusy(false); }
  }
  function currentOptions(): FullPageOptions {
    const ids = new Set(source?.panels.map((panel) => panel.panel_id) ?? []);
    return { chapter_id: chapterId, page_number: pageNumber, text_policy: policy, seed,
      cost_ceiling_anlas: ceiling,
      panel_descriptions: Object.fromEntries(Object.entries(descriptions).filter(([id, value]) => ids.has(id) && value.trim())),
      panel_texts: policy === "model" ? Object.fromEntries(Object.entries(texts).filter(([id]) => ids.has(id))) : {},
      panel_characters: Object.fromEntries(Object.entries(characters).filter(([id]) => ids.has(id))),
    };
  }
  function selectHistory(item: FullPageGeneration) {
    setPlan(item); setPageNumber(item.page_number); setPolicy(item.options.text_policy);
    setSeed(item.options.seed); setCeiling(item.options.cost_ceiling_anlas);
    setDescriptions(item.options.panel_descriptions); setTexts(item.options.panel_texts);
    setCharacters(item.options.panel_characters ?? {});
    setConfirmed(false); setReviewed(false);
  }

  return <section className="full-page-workbench" aria-label="整页多格生成">
    <h2>V5 整页日式漫画</h2>
    <p>一页生成一张带格框的图。逐格写清一个瞬间，保留角色外观；成图后检查格数和顺序，再采用为页面。</p>
    <fieldset disabled={busy}>
      <div className="full-page-options">
        <label>整页章节<select value={chapterId} onChange={(event) => setChapterId(event.target.value)}>
          {chapterSet.chapters.map((chapter) => <option key={chapter.chapter_id} value={chapter.chapter_id}>{chapter.title}</option>)}
        </select></label>
        <label>待生成页<select value={pageNumber} onChange={(event) => { setPageNumber(Number(event.target.value)); invalidate(); }}>
          {sources.map((page) => <option key={page.page_id} value={page.page_number}>第 {page.page_number} 页 · {page.panels.length} 格</option>)}
        </select></label>
        <label>文字策略<select value={policy} onChange={(event) => { setPolicy(event.target.value as "local" | "model"); invalidate(); }}>
          <option value="local">本地文字（推荐，可编辑）</option><option value="model">模型文字（实验，画入图中）</option>
        </select></label>
        <label>整页 seed<input type="number" min={0} max={4294967287} value={seed} onChange={(event) => { setSeed(Number(event.target.value)); invalidate(); }} /></label>
        <label>本页预算预留（Anlas）<input type="number" min={0} max={100} value={ceiling} onChange={(event) => { setCeiling(Number(event.target.value)); invalidate(); }} /></label>
      </div>
      <p>{policy === "model"
        ? "模型文字可能串格、重复或漏字。需要精确对白时请选择本地文字；模型文字成图须逐格检查后采用。"
        : "对白在本地准确排字，可修改内容和位置。采用成图后请核对文字框与实际格框，按需调整。"}</p>
      <p>{ceiling === 0 ? "零 Anlas 模式：每次先核验 Opus 与 V5 可用额度，再生成 1 张；不符合条件会停止。" : "按本页预算预留规划 1 次生成；本地预留不是供应商扣费上限，实际费用需核对 NovelAI 账单。"}</p>
      <button type="button" className="quiet-button" onClick={() => setRefresh((value) => value + 1)}>刷新分镜与生成记录</button>
      {source?.panels.map((panel, index) => <details key={panel.panel_id}>
        <summary>分镜 {index + 1} 的画面与文字</summary>
        {!!panel.characters?.length && <fieldset>
          <legend>本格出镜角色 {index + 1}</legend>
          <p>只保留实际出镜的人物。物件或手部特写可全部取消，并在画面描述中写清细节。</p>
          {panel.characters.map((name) => <label key={name}>
            <input type="checkbox" checked={(characters[panel.panel_id] ?? panel.characters ?? []).includes(name)}
              onChange={(event) => {
                const current = characters[panel.panel_id] ?? panel.characters ?? [];
                setCharacters({ ...characters, [panel.panel_id]: event.target.checked
                  ? [...current, name] : current.filter((item) => item !== name) });
                invalidate();
              }} />{`分镜 ${index + 1} 出镜：${name}`}
          </label>)}
        </fieldset>}
        <label>画面描述 {index + 1}<textarea maxLength={800} rows={3} value={descriptions[panel.panel_id] ?? panel.visual_description}
          onChange={(event) => { setDescriptions({ ...descriptions, [panel.panel_id]: event.target.value }); invalidate(); }} /></label>
        {policy === "model" && <label>画入图中的文字 {index + 1}（空行分隔；可直接输入日语）
          <textarea rows={3} value={(texts[panel.panel_id] ?? panel.texts).join("\n\n")}
            onChange={(event) => { setTexts({ ...texts, [panel.panel_id]: event.target.value.split(/\n\s*\n/).map((text) => text.trim()).filter(Boolean) }); invalidate(); }} />
        </label>}
      </details>)}
      <button type="button" disabled={!source} onClick={() => void run(async () => {
        const next = await previewFullPage(projectId, currentOptions()); setPlan(next); setConfirmed(false); setReviewed(false);
        setHistory((items) => [next, ...items]);
      })}>预览本页完整 Prompt</button>
    </fieldset>
    {plan && <div className="full-page-preview">
      <h3>第 {plan.page_number} 页 · {plan.panel_count} 格 · {labels[plan.status]}</h3>
      <p>{plan.provider_payload.model} · {plan.provider_payload.parameters.width} × {plan.provider_payload.parameters.height} · 图像调用 {plan.generation_calls} 次 · 额度核验 {plan.verification_calls} 次</p>
      <label>整页最终正向 Prompt<textarea readOnly rows={12} value={plan.provider_payload.input} /></label>
      <label>整页最终负向 Prompt<textarea readOnly rows={3} value={plan.provider_payload.parameters.negative_prompt} /></label>
      <p>自动质量标签与负面预设已关闭。{plan.options.text_policy === "model" ? "Text: 位于提示末尾，文字随图生成。" : "文字将在本地添加，模型预留留白。"}</p>
      {!!plan.removed_conflicting_tags.length && <p>本模式移除的冲突提示：{plan.removed_conflicting_tags.join("、")}</p>}
      <p className="field-help">格框位置是模型参考，仍需检查成图。提示长度受本地字符预算约束，未声称已核算官方 Token。</p>
      {plan.status === "draft" && <>
        <label className="confirmation-row"><input type="checkbox" checked={confirmed} disabled={busy} onChange={(event) => setConfirmed(event.target.checked)} />我确认发送以上提示，本页最多生成 1 次，预算预留 {plan.cost_ceiling_anlas} Anlas</label>
        <button type="button" disabled={!confirmed || busy} onClick={() => void run(async () => {
          setMessage("正在生成本页，重复请求不会再次出图。");
          const result = await generateFullPage(projectId, plan); setPlan(result); setConfirmed(false);
          setHistory((items) => items.map((item) => item.generation_id === result.generation_id ? result : item));
          setMessage(result.status === "ready" ? "成图已保存，请检查后采用。" : "生成已停止，请查看状态。重新尝试需新建预览并确认。");
        })}>确认并生成本页一次</button>
      </>}
      {plan.error_code && <p role="alert">{plan.error_code}。不会自动重试；核对 NovelAI 记录后，可重新预览发起新尝试。</p>}
      {plan.status === "ready" && <>
        <FullPageImage projectId={projectId} generationId={plan.generation_id} onError={onError} />
        <label className="confirmation-row"><input type="checkbox" checked={reviewed} disabled={busy} onChange={(event) => setReviewed(event.target.checked)} />已检查格数、阅读顺序、人物与文字</label>
        <button type="button" disabled={!reviewed || busy} onClick={() => void run(async () => {
          const pages = await listComicPages(projectId, chapterId);
          const current = pages.find((page) => page.page_id === plan.page_id);
          await adoptFullPage(projectId, plan.generation_id, current?.page_revision ?? 0);
          setReviewed(false); setMessage(plan.options.text_policy === "model"
            ? "已采用为新的漫画页版本，可导出；图中模型文字需通过重新生成修改。"
            : "已采用为新的漫画页版本，可在下方编辑本地文字或导出。"); onAdopted();
        })}>采用成图为漫画页</button>
      </>}
    </div>}
    {history.length > 0 && <details><summary>本章整页历史（{history.length}）</summary>
      <div className="full-page-history">{history.map((item) => <button key={item.generation_id} type="button" className="quiet-button" disabled={busy} onClick={() => selectHistory(item)}>
        第 {item.page_number} 页 · {labels[item.status]} · seed {item.options.seed} · {item.created_at}
      </button>)}</div>
    </details>}
    {message && <p role="status">{message}</p>}
  </section>;
}

function FullPageImage({ projectId, generationId, onError }: { projectId: string; generationId: string; onError: (message: string) => void }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    let active = true; let nextUrl = ""; setUrl("");
    void fullPageImage(projectId, generationId).then((blob) => {
      if (!active) return;
      nextUrl = URL.createObjectURL(blob); setUrl(nextUrl);
    }).catch((error: unknown) => { if (active) onError(error instanceof Error ? error.message : String(error)); });
    return () => { active = false; if (nextUrl) URL.revokeObjectURL(nextUrl); };
  }, [projectId, generationId, onError]);
  return url ? <img className="full-page-image" src={url} alt="待审阅的整页漫画成图" /> : <p>正在读取成图…</p>;
}
