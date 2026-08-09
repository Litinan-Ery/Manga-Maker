import { useEffect, useMemo, useState } from "react";

import {
  ApiError,
  type ChapterSet,
  type ContinuityDocument,
  type ContinuityImpact,
  type ContinuityKind,
  type ContinuityVersion,
  analyzeContinuityImpact,
  approveContinuity,
  draftContinuity,
  getContinuity,
  reviseContinuity,
} from "./api";

interface ContinuityWorkbenchProps {
  projectId: string;
  chapterSet: ChapterSet;
  onError: (message: string) => void;
}

const KIND_LABELS: Record<ContinuityKind, string> = {
  character: "角色",
  outfit: "服装",
  prop: "道具",
  location: "场景",
  plot: "剧情状态",
};

export function ContinuityWorkbench({
  projectId,
  chapterSet,
  onError,
}: ContinuityWorkbenchProps) {
  const [ledger, setLedger] = useState<ContinuityVersion | null>(null);
  const [document, setDocument] = useState<ContinuityDocument | null>(null);
  const [chapterId, setChapterId] = useState(chapterSet.chapters[0]?.chapter_id ?? "");
  const [impact, setImpact] = useState<ContinuityImpact | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    let active = true;
    setLedger(null);
    setDocument(null);
    getContinuity(projectId)
      .then((current) => {
        if (!active) return;
        applyLedger(current);
        const next = chapterSet.chapters.find(
          (chapter) => chapter.ordinal === current.through_chapter_ordinal + 1,
        );
        if (next) setChapterId(next.chapter_id);
      })
      .catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 404) return;
        if (active) onError(errorMessage(error));
      });
    return () => {
      active = false;
    };
  }, [chapterSet.chapters, onError, projectId]);

  const dirty = Boolean(
    ledger && document && JSON.stringify(ledger.document) !== JSON.stringify(document),
  );
  const grouped = useMemo(() => {
    const groups = new Map<ContinuityKind, ContinuityDocument["entries"]>();
    for (const entry of document?.entries ?? []) {
      groups.set(entry.kind, [...(groups.get(entry.kind) ?? []), entry]);
    }
    return groups;
  }, [document]);

  function applyLedger(next: ContinuityVersion) {
    setLedger(next);
    setDocument(structuredClone(next.document));
    setImpact(next.impact);
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

  async function draft() {
    if (!chapterId) return;
    await run(async () => {
      const result = await draftContinuity(projectId, chapterId);
      applyLedger(result);
      setMessage("已从该章获批分镜与角色设定草拟新账本版本，没有调用外部模型。");
    });
  }

  async function analyze() {
    if (!ledger || !document) return;
    await run(async () => {
      const result = await analyzeContinuityImpact(
        projectId,
        ledger.continuity_version_id,
        document,
      );
      setImpact(result);
      setMessage("影响分析已完成；只读取本地已审批分镜。");
    });
  }

  async function save() {
    if (!ledger || !document) return;
    await run(async () => {
      const result = await reviseContinuity(
        projectId,
        ledger.continuity_version_id,
        document,
      );
      applyLedger(result);
      setMessage("修改已保存为不可变账本版本，旧版本与审批均被保留。");
    });
  }

  async function approve() {
    if (!ledger || dirty) return;
    await run(async () => {
      const result = await approveContinuity(
        projectId,
        ledger.continuity_version_id,
      );
      applyLedger(result);
      setMessage("当前跨章节状态已批准，可以继续推进下一章。");
    });
  }

  function updateEntry(entryId: string, patch: { status?: string; notes?: string }) {
    setDocument((current) =>
      current
        ? {
            ...current,
            entries: current.entries.map((entry) =>
              entry.entry_id === entryId ? { ...entry, ...patch } : entry,
            ),
          }
        : current,
    );
    setImpact(null);
  }

  return (
    <section className="continuity-workbench" aria-label="跨章节连续性账本">
      <div className="workspace-heading compact">
        <div>
          <p className="section-kicker">跨章连续性</p>
          <h2>角色、服装与剧情状态账本</h2>
        </div>
        <span>
          {ledger
            ? `v${ledger.version} · ${statusLabel(ledger.approval_status)}`
            : "尚未建立"}
        </span>
      </div>
      <p className="panel-description">
        每章从已批准的分镜与角色设定更新一次。改动会先指出后续章节中可能受影响的分格，不会触发图像生成。
      </p>

      <div className="continuity-toolbar">
        <label>
          <span>要写入账本的章节</span>
          <select value={chapterId} onChange={(event) => setChapterId(event.target.value)}>
            {chapterSet.chapters.map((chapter) => (
              <option key={chapter.chapter_id} value={chapter.chapter_id}>
                {chapter.ordinal}. {chapter.title}
              </option>
            ))}
          </select>
        </label>
        <button type="button" disabled={busy || !chapterId} onClick={() => void draft()}>
          {ledger ? "推进到所选章节" : "从第一章建立账本"}
        </button>
      </div>

      {ledger && document && (
        <>
          <div className="continuity-summary">
            <strong>已记录到第 {ledger.through_chapter_ordinal} 章</strong>
            <span>{document.entries.length} 个状态项</span>
            <span>外部请求 0</span>
          </div>
          {[...grouped.entries()].map(([kind, entries]) => (
            <section className="continuity-group" key={kind}>
              <h3>{KIND_LABELS[kind]} · {entries.length}</h3>
              <div className="continuity-grid">
                {entries.map((entry) => (
                  <article key={entry.entry_id}>
                    <strong>{entry.name}</strong>
                    <label>
                      <span>当前状态</span>
                      <input
                        value={entry.status}
                        maxLength={100}
                        onChange={(event) =>
                          updateEntry(entry.entry_id, { status: event.target.value })
                        }
                      />
                    </label>
                    {Object.entries(entry.attributes).map(([key, value]) => (
                      <p key={key}><span>{key}</span>{value}</p>
                    ))}
                    <label>
                      <span>创作备注</span>
                      <textarea
                        value={entry.notes}
                        maxLength={2000}
                        onChange={(event) =>
                          updateEntry(entry.entry_id, { notes: event.target.value })
                        }
                      />
                    </label>
                  </article>
                ))}
              </div>
            </section>
          ))}
          <div className="continuity-actions">
            <button type="button" className="quiet-button" disabled={busy || !dirty} onClick={() => void analyze()}>
              预览后续影响
            </button>
            <button type="button" disabled={busy || !dirty} onClick={() => void save()}>
              保存为新版本
            </button>
            <button
              type="button"
              disabled={busy || dirty || ledger.approval_status === "approved"}
              onClick={() => void approve()}
            >
              批准当前状态
            </button>
          </div>
        </>
      )}

      {impact && impact.changed_entries.length > 0 && (
        <div className={`impact-report ${impact.requires_future_review ? "warning" : ""}`}>
          <strong>{impact.changed_entries.length} 个状态变化</strong>
          <span>
            {impact.requires_future_review
              ? `影响 ${impact.affected_chapters.length} 个后续章节、${impact.affected_panel_ids.length} 个分格`
              : "尚未命中后续已审批分镜"}
          </span>
          {impact.affected_chapters.map((chapter) => (
            <p key={chapter.chapter_id}>
              {chapter.ordinal}. {chapter.title} · {chapter.panel_count} 格
            </p>
          ))}
        </div>
      )}
      {message && <p className="success-message" role="status">{message}</p>}
    </section>
  );
}

function statusLabel(status: ContinuityVersion["approval_status"]): string {
  if (status === "approved") return "已批准";
  if (status === "stale") return "来源已变化";
  return "待批准";
}

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "跨章节账本操作失败。";
}
