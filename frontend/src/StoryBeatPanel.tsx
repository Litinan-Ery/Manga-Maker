import { useEffect, useState } from "react";

import {
  ApiError,
  type ChapterSet,
  type StoryBeatSet,
  draftStoryBeats,
  getStoryBeats,
} from "./api";

interface StoryBeatPanelProps {
  projectId: string;
  chapterSet: ChapterSet;
  onError: (message: string) => void;
  onChanged?: () => void;
}

export function StoryBeatPanel({ projectId, chapterSet, onError, onChanged }: StoryBeatPanelProps) {
  const [chapterId, setChapterId] = useState(chapterSet.chapters[0]?.chapter_id ?? "");
  const [beatSet, setBeatSet] = useState<StoryBeatSet | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const nextChapterId = chapterSet.chapters[0]?.chapter_id ?? "";
    setChapterId(nextChapterId);
    setBeatSet(null);
  }, [chapterSet]);

  useEffect(() => {
    if (!chapterId) return;
    let active = true;
    setBeatSet(null);
    getStoryBeats(projectId, chapterId)
      .then((result) => {
        if (active) setBeatSet(result);
      })
      .catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 404) return;
        if (active) onError(error instanceof Error ? error.message : "无法读取剧情节拍。");
      });
    return () => {
      active = false;
    };
  }, [chapterId, onError, projectId]);

  async function createDraft() {
    if (!chapterId) return;
    setBusy(true);
    try {
      setBeatSet(await draftStoryBeats(projectId, chapterId));
      onChanged?.();
    } catch (error) {
      onError(error instanceof Error ? error.message : "无法建立剧情节拍。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="story-beats">
      <div className="workspace-heading compact">
        <div>
          <p className="section-kicker">第三步</p>
          <h2>建立来源覆盖账本</h2>
        </div>
        {beatSet && <span>节拍版本 {beatSet.beat_set_version}</span>}
      </div>
      <p className="editor-help">
        先在本机按原文段落建立可追溯剧情节拍。每项都保存字符范围和哈希，此步骤不调用模型。
      </p>
      <div className="beat-toolbar">
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
        <button type="button" disabled={busy || !chapterId} onClick={() => void createDraft()}>
          {beatSet ? "重新建立节拍" : "建立剧情节拍"}
        </button>
      </div>
      {beatSet ? (
        <ol>
          {beatSet.beats.map((beat) => (
            <li key={beat.beat_id}>
              <span>{String(beat.ordinal).padStart(2, "0")}</span>
              <p>{beat.source_excerpt}</p>
              <small>待改编映射</small>
            </li>
          ))}
        </ol>
      ) : (
        <p className="empty-state">该章节尚未建立剧情节拍。</p>
      )}
    </section>
  );
}
