import { useState } from "react";
import { request, type ChapterSet } from "./api";

const endpoints = {
  storyboard: "adaptation/storyboards/import",
  bibles: "bibles/import",
  tags: "prompting/character-tags/import",
};

export function LocalDraftImport({ projectId, chapterSet, onChanged, onError }: {
  projectId: string;
  chapterSet: ChapterSet;
  onChanged: () => void;
  onError: (message: string) => void;
}) {
  const [kind, setKind] = useState<keyof typeof endpoints>("storyboard");
  const [chapterId, setChapterId] = useState(chapterSet.chapters[0]?.chapter_id ?? "");
  const [budget, setBudget] = useState(35);
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [source, setSource] = useState("");

  async function getSource() {
    setBusy(true);
    try {
      const snapshot = await request(`/api/v1/projects/${projectId}/adaptation/storyboards/source?chapter_id=${encodeURIComponent(chapterId)}&page_budget=${budget}`, {}, false);
      setSource(JSON.stringify(snapshot, null, 2));
    } catch (error) {
      onError(error instanceof Error ? error.message : "无法读取来源快照。");
    } finally { setBusy(false); }
  }

  async function importDraft() {
    setBusy(true);
    setMessage("");
    try {
      const document: unknown = JSON.parse(body);
      if (!document || typeof document !== "object" || Array.isArray(document)) {
        throw new Error("请粘贴一个 JSON 对象。");
      }
      await request(`/api/v1/projects/${projectId}/${endpoints[kind]}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(document),
      }, true);
      setBody("");
      setMessage("已保存本地草稿。请在对应工作台核对并审批，再进行下一步。");
      onChanged();
    } catch (error) {
      onError(error instanceof Error ? error.message : "本地草稿导入失败。");
    } finally { setBusy(false); }
  }

  return <details className="workspace-section">
    <summary>导入自编分镜与设定（JSON）</summary>
    <p>适用于已经编写好的脚本。依次导入并审批分镜、角色与画风、固定 tags；仅保存本地版本，不调用文本模型或生成图片。</p>
    <label>来源章节<select value={chapterId} disabled={busy} onChange={e => { setChapterId(e.target.value); setSource(""); }}>
      {chapterSet.chapters.map(chapter => <option key={chapter.chapter_id} value={chapter.chapter_id}>{chapter.title}</option>)}
    </select></label>
    <label>章内页数上限<input type="number" min={1} max={64} value={budget} disabled={busy} onChange={e => setBudget(Number(e.target.value))} /></label>
    <button type="button" disabled={busy || !chapterId || budget < 1 || budget > 64} onClick={() => void getSource()}>读取来源锚点与版本快照</button>
    {source && <label>来源快照（供编写分镜使用）<textarea readOnly value={source} rows={10} /></label>}
    <label>草稿类型<select disabled={busy} value={kind} onChange={e => { setKind(e.target.value as keyof typeof endpoints); setBody(""); setMessage(""); }}>
      <option value="storyboard">分镜</option><option value="bibles">角色与画风设定</option><option value="tags">角色固定 tags</option>
    </select></label>
    <label>本地草稿 JSON<textarea rows={10} value={body} disabled={busy} onChange={e => setBody(e.target.value)} /></label>
    <p>分镜需将上方 source_fingerprint 填入 expected_source_fingerprint，并提供 chapter_id、page_budget、来源说明 source_note 和 document。来源已变化或引用不匹配时，会拒绝导入并保留现有版本。</p>
    <button type="button" disabled={busy || !body.trim()} onClick={() => void importDraft()}>保存本地草稿</button>
    {message && <p role="status">{message}</p>}
  </details>;
}
