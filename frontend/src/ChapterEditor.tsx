import { useEffect, useState } from "react";

import {
  type ChapterBoundaryInput,
  type ChapterSet,
  getChapterText,
  replaceChapters,
} from "./api";

interface ChapterEditorProps {
  projectId: string;
  chapterSet: ChapterSet;
  onSaved: (chapterSet: ChapterSet) => void;
  onError: (message: string) => void;
}

interface SplitDraft {
  index: number;
  text: string;
  cursor: number;
}

export function ChapterEditor({
  projectId,
  chapterSet,
  onSaved,
  onError,
}: ChapterEditorProps) {
  const [drafts, setDrafts] = useState<ChapterBoundaryInput[]>(() => toDrafts(chapterSet));
  const [splitDraft, setSplitDraft] = useState<SplitDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const changed = JSON.stringify(drafts) !== JSON.stringify(toDrafts(chapterSet));

  useEffect(() => {
    setDrafts(toDrafts(chapterSet));
    setSplitDraft(null);
  }, [chapterSet]);

  function rename(index: number, title: string) {
    setDrafts((current) =>
      current.map((chapter, chapterIndex) =>
        chapterIndex === index ? { ...chapter, title } : chapter,
      ),
    );
  }

  function mergeWithPrevious(index: number) {
    if (index === 0) return;
    setDrafts((current) => {
      const merged = [...current];
      merged[index - 1] = { ...merged[index - 1], end_offset: merged[index].end_offset };
      merged.splice(index, 1);
      return merged;
    });
    setSplitDraft(null);
  }

  async function openSplit(index: number) {
    const persisted = chapterSet.chapters.find(
      (chapter) =>
        chapter.start_offset === drafts[index].start_offset &&
        chapter.end_offset === drafts[index].end_offset,
    );
    if (!persisted) {
      onError("请先保存当前章节调整，再继续拆分。");
      return;
    }
    setBusy(true);
    try {
      const result = await getChapterText(projectId, persisted.chapter_id);
      setSplitDraft({ index, text: result.text, cursor: 0 });
    } catch (error) {
      onError(error instanceof Error ? error.message : "无法读取章节文本。");
    } finally {
      setBusy(false);
    }
  }

  function applySplit() {
    if (!splitDraft || splitDraft.cursor <= 0 || splitDraft.cursor >= splitDraft.text.length) return;
    setDrafts((current) => {
      const next = [...current];
      const chapter = next[splitDraft.index];
      const splitAt = chapter.start_offset + splitDraft.cursor;
      next.splice(
        splitDraft.index,
        1,
        { ...chapter, end_offset: splitAt },
        {
          title: `${chapter.title}（续）`,
          start_offset: splitAt,
          end_offset: chapter.end_offset,
        },
      );
      return next;
    });
    setSplitDraft(null);
  }

  async function save() {
    if (drafts.some((chapter) => chapter.title.trim().length === 0)) {
      onError("章节标题不能为空。");
      return;
    }
    setBusy(true);
    try {
      const saved = await replaceChapters(projectId, chapterSet.source_file_id, drafts);
      onSaved(saved);
    } catch (error) {
      onError(error instanceof Error ? error.message : "无法保存章节调整。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="chapter-editor">
      <p className="editor-help">可直接重命名；需要修正边界时，合并相邻章节或在原文光标处拆分。</p>
      <ol>
        {drafts.map((chapter, index) => (
          <li key={`${chapter.start_offset}-${chapter.end_offset}`}>
            <span>{String(index + 1).padStart(2, "0")}</span>
            <label>
              <span className="sr-only">第 {index + 1} 章标题</span>
              <input
                value={chapter.title}
                maxLength={200}
                onChange={(event) => rename(index, event.target.value)}
              />
            </label>
            <small>{chapter.end_offset - chapter.start_offset} 字符</small>
            <div className="chapter-actions">
              <button type="button" className="quiet-button" disabled={busy} onClick={() => void openSplit(index)}>
                从光标拆分
              </button>
              {index > 0 && (
                <button type="button" className="quiet-button" disabled={busy} onClick={() => mergeWithPrevious(index)}>
                  与上一章合并
                </button>
              )}
            </div>
          </li>
        ))}
      </ol>

      {splitDraft && (
        <div className="split-panel">
          <strong>在原文中点击要拆分的位置</strong>
          <textarea
            value={splitDraft.text}
            readOnly
            rows={12}
            onSelect={(event) =>
              setSplitDraft({ ...splitDraft, cursor: event.currentTarget.selectionStart })
            }
          />
          <p>当前光标：本章第 {splitDraft.cursor} 个字符</p>
          <div>
            <button type="button" className="quiet-button" onClick={() => setSplitDraft(null)}>
              取消
            </button>
            <button
              type="button"
              disabled={splitDraft.cursor <= 0 || splitDraft.cursor >= splitDraft.text.length}
              onClick={applySplit}
            >
              在这里拆分
            </button>
          </div>
        </div>
      )}

      <div className="editor-footer">
        <span>{changed ? "有尚未保存的调整" : "章节边界已保存"}</span>
        <button type="button" disabled={!changed || busy} onClick={() => void save()}>
          保存章节调整
        </button>
      </div>
    </div>
  );
}

function toDrafts(chapterSet: ChapterSet): ChapterBoundaryInput[] {
  return chapterSet.chapters.map((chapter) => ({
    title: chapter.title,
    start_offset: chapter.start_offset,
    end_offset: chapter.end_offset,
  }));
}
