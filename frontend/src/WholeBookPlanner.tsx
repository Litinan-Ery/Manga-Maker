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
  const [billingMode, setBillingMode] = useState<"opus_zero_anlas" | "standard">("opus_zero_anlas");
  const [perPanelCost, setPerPanelCost] = useState(0);
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
        {plan && plan.per_panel_cost_ceiling_anlas > 0
          ? "当前整本计划使用标准计费；继续推进可能创建会消耗 Anlas 的章节队列。"
          : billingMode === "opus_zero_anlas"
            ? "先冻结全书 Opus 零 Anlas 资格载荷和本地 0 Anlas 预留，再逐章审批。每次最多建立一个本地任务；真正出图仍需在生成控制台确认并逐张核验，资格核验不是账单回执。"
            : "标准计费允许参考图并按已冻结参数执行，但可能消耗 Anlas；先冻结整本调用与成本硬上限，再逐章审批。"}
      </p>

      {!plan && (
        <div className="book-plan-setup">
          <label>
            <span>计费模式</span>
            <select
              value={billingMode}
              onChange={(event) => {
                const nextMode = event.target.value as "opus_zero_anlas" | "standard";
                setBillingMode(nextMode);
                setPerPanelCost(nextMode === "opus_zero_anlas" ? 0 : 10);
                setEstimate(null);
                setConfirmed(false);
              }}
            >
              <option value="opus_zero_anlas">Opus 零 Anlas（默认）</option>
              <option value="standard">标准计费（允许参考图）</option>
            </select>
          </label>
          <label>
            <span>{billingMode === "opus_zero_anlas" ? "每格本地 Anlas 预留" : "每格 Anlas 硬上限"}</span>
            <input
              type="number"
              min={billingMode === "opus_zero_anlas" ? 0 : 1}
              max={billingMode === "opus_zero_anlas" ? 0 : 100_000}
              value={perPanelCost}
              readOnly={billingMode === "opus_zero_anlas"}
              onChange={(event) => {
                setPerPanelCost(Number(event.target.value));
                setEstimate(null);
                setConfirmed(false);
              }}
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
            <Metric label="分格 / 出图调用" value={`${estimate.estimated_panel_count} / ${estimate.estimated_calls}`} />
            <Metric label="预计订阅核验" value={estimate.estimated_verification_calls} />
            <Metric label="预计外部请求" value={estimate.estimated_external_requests} />
            <Metric label="成本预留" value={`${estimate.estimated_cost_upper_anlas} Anlas`} />
          </div>
          <p>{estimate.cost_notice}</p>
          <div className="book-limit-grid">
            <label>
              <span>整本出图调用硬上限</span>
              <input
                type="number"
                min={estimate.estimated_calls}
                max={estimate.estimated_calls * 3}
                value={maxCalls}
                onChange={(event) => {
                  setMaxCalls(Number(event.target.value));
                  setConfirmed(false);
                }}
              />
            </label>
            <label>
              <span>{estimate.billing_mode === "opus_zero_anlas" ? "整本本地 Anlas 预留" : "整本 Anlas 硬上限"}</span>
              <input
                type="number"
                min={estimate.estimated_cost_upper_anlas}
                max={estimate.billing_mode === "opus_zero_anlas" ? 0 : 100_000_000}
                value={maxCost}
                readOnly={estimate.billing_mode === "opus_zero_anlas"}
                onChange={(event) => {
                  setMaxCost(Number(event.target.value));
                  setConfirmed(false);
                }}
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
            <span>
              {estimate.billing_mode === "opus_zero_anlas"
                ? `我已核对章节范围、分镜、最多 ${maxCalls} 次出图、${maxCalls} 次订阅核验、${maxCalls * 2} 次外部请求和 0 Anlas 本地预留；我理解资格核验不是账单保证，逐次实际费用可能保持未核实。`
                : `我已核对章节范围、分镜、最多 ${maxCalls} 次出图与外部请求，以及标准计费成本硬上限。`}
            </span>
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
              <Metric label="出图调用" value={`${plan.calls_started} / ${plan.max_calls}`} />
              <Metric label="订阅核验" value={`${plan.verification_calls_started} / ${plan.max_verification_calls}`} />
              <Metric label="供应商已核实成本" value={`${plan.recorded_cost_anlas} / ${plan.max_cost_anlas} Anlas`} />
              <Metric label="外部请求" value={`${plan.external_requests_started} / ${plan.max_external_requests}`} />
            </div>
            {plan.unverified_cost_calls > 0 && (
              <p className="field-note">
                {plan.per_panel_cost_ceiling_anlas === 0
                  ? `${plan.unverified_cost_calls} 次出图虽已完成资格核验，但供应商未回传逐次实际扣费，不能把 0 视为已核实账单。`
                  : `${plan.unverified_cost_calls} 次出图的实际扣费未由供应商回传，保留为未核实记录。`}
              </p>
            )}
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
