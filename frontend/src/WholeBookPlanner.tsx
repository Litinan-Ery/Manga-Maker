import { useEffect, useState } from "react";

import {
  ApiError,
  type BookEstimate,
  type BookPlan,
  type BookPlanChapter,
  approveBookPlanChapter,
  createBookPlan,
  estimateBookProduction,
  getCurrentBookPlan,
  retryBookPlanChapter,
  transitionBookPlan,
} from "./api";

interface WholeBookPlannerProps {
  projectId: string;
  onError: (message: string) => void;
}

export function WholeBookPlanner({ projectId, onError }: WholeBookPlannerProps) {
  const [estimate, setEstimate] = useState<BookEstimate | null>(null);
  const [plan, setPlan] = useState<BookPlan | null>(null);
  const [perPanelCost, setPerPanelCost] = useState(10);
  const [maxCalls, setMaxCalls] = useState(0);
  const [maxCost, setMaxCost] = useState(0);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    let active = true;
    setEstimate(null);
    setPlan(null);
    setConfirmed(false);
    getCurrentBookPlan(projectId)
      .then((current) => {
        if (active) setPlan(current);
      })
      .catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 404) return;
        if (active) onError(errorMessage(error));
      });
    return () => {
      active = false;
    };
  }, [onError, projectId]);

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

  async function calculate() {
    await run(async () => {
      const result = await estimateBookProduction(projectId, perPanelCost);
      setEstimate(result);
      setMaxCalls(result.estimated_calls);
      setMaxCost(result.estimated_cost_upper_anlas);
      setConfirmed(false);
      setMessage("整本预算已在本地计算，尚未创建队列或外部请求。");
    });
  }

  async function create() {
    if (!estimate || !confirmed) return;
    await run(async () => {
      const result = await createBookPlan(projectId, estimate, maxCalls, maxCost);
      setPlan(result);
      setEstimate(null);
      setConfirmed(false);
      setMessage("整本计划已冻结。请逐章核对并批准后再启动。");
    });
  }

  async function approve(chapter: BookPlanChapter) {
    if (!plan) return;
    await run(async () => {
      const result = await approveBookPlanChapter(
        projectId,
        plan,
        chapter.book_chapter_plan_id,
      );
      setPlan(result);
      setMessage(`第 ${chapter.ordinal} 章已批准；其预算和分镜指纹已冻结。`);
    });
  }

  async function retry(chapter: BookPlanChapter) {
    if (!plan) return;
    await run(async () => {
      const result = await retryBookPlanChapter(
        projectId,
        plan,
        chapter.book_chapter_plan_id,
      );
      setPlan(result);
      setMessage("旧任务已保留并取消；该章可由你再次创建一个新的本地任务。");
    });
  }

  async function transition(
    action: "start" | "advance" | "pause" | "resume" | "cancel",
  ) {
    if (!plan) return;
    await run(async () => {
      const result = await transitionBookPlan(projectId, plan, action);
      setPlan(result);
      setMessage(actionMessage(action, result));
    });
  }

  return (
    <section className="whole-book-planner" aria-label="整本生产计划">
      <div className="workspace-heading compact">
        <div>
          <p className="section-kicker">整本生产</p>
          <h2>有界、逐章、人工推进</h2>
        </div>
        <span>{plan ? statusLabel(plan.status) : "尚未规划"}</span>
      </div>
      <p className="panel-description">
        先冻结全书预算，再逐章审批。每次“创建下一章本地队列”最多只建立一个任务；图像生成仍需在生成控制台单独确认，不会自动连续付费。
      </p>

      {!plan && (
        <div className="book-plan-setup">
          <label>
            <span>每格成本预留上限（Anlas）</span>
            <input
              type="number"
              min={0}
              max={100000}
              value={perPanelCost}
              onChange={(event) => setPerPanelCost(Number(event.target.value))}
            />
          </label>
          <button type="button" disabled={busy} onClick={() => void calculate()}>
            计算整本预算
          </button>
        </div>
      )}

      {estimate && !plan && (
        <div className="book-estimate">
          <div className="book-metrics">
            <Metric label="章节" value={estimate.chapter_count} />
            <Metric label="页数" value={estimate.estimated_page_count} />
            <Metric label="分格 / 最少调用" value={estimate.estimated_panel_count} />
            <Metric label="成本预留" value={`${estimate.estimated_cost_upper_anlas} Anlas`} />
          </div>
          <p>{estimate.cost_notice}</p>
          <div className="book-limit-grid">
            <label>
              <span>整本调用硬上限</span>
              <input
                type="number"
                min={estimate.estimated_calls}
                max={estimate.estimated_calls * 3}
                value={maxCalls}
                onChange={(event) => setMaxCalls(Number(event.target.value))}
              />
            </label>
            <label>
              <span>整本成本硬上限（Anlas）</span>
              <input
                type="number"
                min={estimate.estimated_cost_upper_anlas}
                value={maxCost}
                onChange={(event) => setMaxCost(Number(event.target.value))}
              />
            </label>
          </div>
          <ol className="book-estimate-chapters">
            {estimate.chapters.map((chapter) => (
              <li key={chapter.chapter_id}>
                <strong>{chapter.ordinal}. {chapter.title}</strong>
                <span>{chapter.page_count} 页 · {chapter.panel_count} 格 · {chapter.estimated_cost_upper_anlas} Anlas</span>
              </li>
            ))}
          </ol>
          <label className="confirmation-row">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(event) => setConfirmed(event.target.checked)}
            />
            <span>我已核对章节范围、分镜、调用上限与成本预留。</span>
          </label>
          <button
            type="button"
            disabled={busy || !confirmed || maxCalls < estimate.estimated_calls || maxCost < estimate.estimated_cost_upper_anlas}
            onClick={() => void create()}
          >
            冻结整本计划
          </button>
        </div>
      )}

      {plan && (
        <>
          <div className="book-plan-summary">
            <div className="book-metrics">
              <Metric label="计划状态" value={statusLabel(plan.status)} />
              <Metric label="调用" value={`${plan.calls_started} / ${plan.max_calls}`} />
              <Metric label="已记录成本" value={`${plan.recorded_cost_anlas} / ${plan.max_cost_anlas} Anlas`} />
              <Metric label="外部请求" value={plan.external_requests_started} />
            </div>
            <div className="book-plan-actions">
              {plan.status === "ready" && (
                <button type="button" disabled={busy} onClick={() => void transition("start")}>启动整本计划</button>
              )}
              {plan.status === "active" && (
                <>
                  <button type="button" disabled={busy} onClick={() => void transition("advance")}>创建下一章本地队列</button>
                  <button type="button" className="quiet-button" disabled={busy} onClick={() => void transition("pause")}>暂停计划</button>
                </>
              )}
              {plan.status === "paused" && (
                <button type="button" disabled={busy} onClick={() => void transition("resume")}>恢复计划</button>
              )}
              {!['completed', 'canceled'].includes(plan.status) && (
                <button type="button" className="quiet-button" disabled={busy} onClick={() => void transition("cancel")}>取消计划</button>
              )}
            </div>
          </div>
          <ol className="book-plan-chapters">
            {plan.chapters.map((chapter) => (
              <li key={chapter.book_chapter_plan_id}>
                <div>
                  <strong>{chapter.ordinal}. {chapter.title}</strong>
                  <span>{chapter.page_count} 页 · {chapter.panel_count} 格 · 上限 {chapter.max_calls} 次 / {chapter.max_cost_anlas} Anlas</span>
                  {chapter.generation_job_id && (
                    <small>本地任务 {chapter.generation_job_id.slice(0, 8)} · {jobStatusLabel(chapter.generation_job_status)}</small>
                  )}
                </div>
                <span className={`book-chapter-status status-${chapter.status}`}>{chapterStatusLabel(chapter.status)}</span>
                {chapter.status === "awaiting_approval" && (
                  <button type="button" disabled={busy} onClick={() => void approve(chapter)}>核对并批准本章</button>
                )}
                {chapter.status === "needs_review" && (
                  <button type="button" disabled={busy} onClick={() => void retry(chapter)}>人工复核后重置本章</button>
                )}
              </li>
            ))}
          </ol>
        </>
      )}
      {message && <p className="success-message" role="status">{message}</p>}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div><span>{label}</span><strong>{value}</strong></div>;
}

function statusLabel(status: BookPlan["status"]): string {
  return {
    awaiting_approval: "逐章待批准",
    ready: "已批准，待启动",
    active: "进行中",
    paused: "已暂停",
    needs_review: "需要人工复核",
    completed: "已完成",
    canceled: "已取消",
  }[status];
}

function chapterStatusLabel(status: BookPlanChapter["status"]): string {
  return {
    awaiting_approval: "待批准",
    approved: "已批准",
    job_created: "任务已创建",
    running: "任务进行中",
    paused: "已暂停",
    needs_review: "需要复核",
    completed: "已完成",
    canceled: "已取消",
  }[status];
}

function jobStatusLabel(status: BookPlanChapter["generation_job_status"]): string {
  if (!status) return "尚未创建";
  return {
    draft: "草稿",
    awaiting_approval: "待批准",
    queued: "等待启动",
    running: "进行中",
    paused: "已暂停",
    needs_review: "需要复核",
    failed: "失败",
    completed: "已完成",
    canceled: "已取消",
  }[status];
}

function actionMessage(action: string, plan: BookPlan): string {
  if (action === "start") return "整本计划已启动；尚未创建任何外部请求。";
  if (action === "advance" && plan.status === "completed") return "全部章节的本地任务均已完成。";
  if (action === "advance") return "已创建或同步当前章节任务；请在生成控制台中再次确认后执行。";
  if (action === "pause") return "整本计划及当前本地任务已暂停。";
  if (action === "resume") return "整本计划已恢复，仍不会自动执行或推进。";
  return "整本计划已取消，历史任务与结果均已保留。";
}

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "整本生产计划操作失败。";
}
