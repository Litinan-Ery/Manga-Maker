import { useEffect, useState } from "react";

import {
  ApiError,
  type ChapterSet,
  type GenerationEstimate,
  type GenerationJob,
  createGenerationJob,
  estimateGeneration,
  listGenerationJobs,
  transitionGenerationJob,
} from "./api";

interface GenerationConsoleProps {
  projectId: string;
  chapterSet: ChapterSet;
  onError: (message: string) => void;
}

export function GenerationConsole({ projectId, chapterSet, onError }: GenerationConsoleProps) {
  const [chapterId, setChapterId] = useState(chapterSet.chapters[0]?.chapter_id ?? "");
  const [perPanelCeiling, setPerPanelCeiling] = useState(10);
  const [estimate, setEstimate] = useState<GenerationEstimate | null>(null);
  const [maxCalls, setMaxCalls] = useState(1);
  const [maxCost, setMaxCost] = useState(10);
  const [confirmed, setConfirmed] = useState(false);
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    let active = true;
    setEstimate(null);
    setConfirmed(false);
    listGenerationJobs(projectId)
      .then((jobs) => {
        if (active) setJob(jobs[0] ?? null);
      })
      .catch((error: unknown) => {
        if (active) onError(errorMessage(error));
      });
    return () => {
      active = false;
    };
  }, [projectId, onError]);

  useEffect(() => {
    if (!chapterSet.chapters.some((chapter) => chapter.chapter_id === chapterId)) {
      setChapterId(chapterSet.chapters[0]?.chapter_id ?? "");
    }
  }, [chapterSet, chapterId]);

  async function handleEstimate() {
    if (!chapterId) return;
    await run(async () => {
      const nextEstimate = await estimateGeneration(projectId, chapterId, perPanelCeiling);
      setEstimate(nextEstimate);
      setMaxCalls(nextEstimate.estimated_calls);
      setMaxCost(nextEstimate.estimated_cost_upper_anlas);
      setConfirmed(false);
      setMessage("估算只固定本地版本和预算，没有创建外部请求。请核对后确认。 ");
    });
  }

  async function handleCreate() {
    if (!estimate || !confirmed) return;
    await run(async () => {
      const created = await createGenerationJob(projectId, estimate, maxCalls, maxCost);
      setJob(created);
      setMessage("有界队列已创建，面板清单和上限已冻结；尚未启动外部请求。 ");
    });
  }

  async function handleTransition(action: "start" | "pause" | "resume" | "cancel") {
    if (!job) return;
    await run(async () => {
      const updated = await transitionGenerationJob(
        projectId,
        job.job_id,
        action,
        job.revision,
      );
      setJob(updated);
      setMessage(transitionMessage(action));
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

  return (
    <section className="generation-console" aria-label="有界生成队列">
      <div className="workspace-heading compact">
        <div>
          <p className="section-kicker">第七步</p>
          <h2>确认范围与生成上限</h2>
        </div>
        <span>{job ? statusLabel(job.status) : "尚未创建"}</span>
      </div>
      <p className="panel-description">
        当前工单只建立安全的串行队列状态机，不会调用 NovelAI 出图。下一阶段接入执行器后，仍须由你点击启动。
      </p>

      <div className="settings-form generation-plan-form">
        <label>
          <span>章节</span>
          <select
            value={chapterId}
            onChange={(event) => {
              setChapterId(event.target.value);
              setEstimate(null);
              setConfirmed(false);
            }}
          >
            {chapterSet.chapters.map((chapter) => (
              <option key={chapter.chapter_id} value={chapter.chapter_id}>
                {chapter.ordinal}. {chapter.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>每格保守预留上限（Anlas）</span>
          <input
            type="number"
            min={0}
            max={100000}
            value={perPanelCeiling}
            onChange={(event) => {
              setPerPanelCeiling(Number(event.target.value));
              setEstimate(null);
              setConfirmed(false);
            }}
          />
        </label>
        <button type="button" disabled={busy || !chapterId} onClick={() => void handleEstimate()}>
          生成本地范围与预算预检
        </button>
      </div>

      {estimate && (
        <div className="generation-estimate">
          <div className="facts queue-facts">
            <QueueFact label="页面" value={String(estimate.page_count)} />
            <QueueFact label="面板 / 最少调用" value={`${estimate.panel_count} / ${estimate.estimated_calls}`} />
            <QueueFact label="成本预留" value={`≤ ${estimate.estimated_cost_upper_anlas} Anlas`} />
          </div>
          <p className="field-note">{estimate.cost_notice}</p>
          <div className="settings-form">
            <label>
              <span>最大调用次数</span>
              <input
                type="number"
                min={estimate.panel_count}
                max={estimate.panel_count * 3}
                value={maxCalls}
                onChange={(event) => setMaxCalls(Number(event.target.value))}
              />
            </label>
            <label>
              <span>最大成本预留（Anlas）</span>
              <input
                type="number"
                min={estimate.estimated_cost_upper_anlas}
                max={10000000}
                value={maxCost}
                onChange={(event) => setMaxCost(Number(event.target.value))}
              />
            </label>
            <label className="confirmation-row">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(event) => setConfirmed(event.target.checked)}
              />
              <span>我已核对页数、面板清单、调用上限和成本预留</span>
            </label>
            <button
              type="button"
              disabled={busy || !confirmed || maxCalls < estimate.panel_count || maxCost < estimate.estimated_cost_upper_anlas}
              onClick={() => void handleCreate()}
            >
              创建有界队列（不出图）
            </button>
          </div>
        </div>
      )}

      {job && (
        <div className="queue-status-card">
          <div>
            <strong>{statusLabel(job.status)}</strong>
            <span>
              已开始 {job.calls_started}/{job.max_calls} 次 · 已完成 {job.calls_completed}/{job.panel_count} 格 ·
              已记录 {job.recorded_cost_anlas}/{job.max_cost_anlas} Anlas
            </span>
          </div>
          <div className="button-row">
            {job.status === "queued" && (
              <button type="button" disabled={busy} onClick={() => void handleTransition("start")}>
                由我启动队列
              </button>
            )}
            {job.status === "running" && (
              <button type="button" disabled={busy} onClick={() => void handleTransition("pause")}>
                暂停领取新面板
              </button>
            )}
            {job.status === "paused" && (
              <button type="button" disabled={busy} onClick={() => void handleTransition("resume")}>
                由我恢复队列
              </button>
            )}
            {["queued", "running", "paused", "needs_review", "failed"].includes(job.status) && (
              <button
                type="button"
                className="quiet-button"
                disabled={busy}
                onClick={() => void handleTransition("cancel")}
              >
                取消未开始面板
              </button>
            )}
          </div>
        </div>
      )}
      {message && <p className="success-message" role="status">{message}</p>}
    </section>
  );
}

function QueueFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="fact">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function statusLabel(status: GenerationJob["status"]): string {
  return {
    draft: "草稿",
    awaiting_approval: "等待确认",
    queued: "已排队，未启动",
    running: "运行中",
    paused: "已暂停",
    needs_review: "需要人工审阅",
    failed: "失败",
    completed: "已完成",
    canceled: "已取消",
  }[status];
}

function transitionMessage(action: "start" | "pause" | "resume" | "cancel"): string {
  return {
    start: "队列已由你启动；当前版本没有图像执行器，因此不会发出 NovelAI 请求。",
    pause: "已暂停，当前在途项可收尾，但不会领取新面板。",
    resume: "队列已由你恢复；应用重启不会自动执行付费请求。",
    cancel: "已取消所有尚未开始的面板；历史和在途结算记录仍保留。",
  }[action];
}

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "生成队列操作失败。";
}
