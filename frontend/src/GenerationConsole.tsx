import { useEffect, useState } from "react";

import {
  ApiError,
  type ChapterSet,
  type GenerationAsset,
  type GenerationEstimate,
  type GenerationJob,
  createGenerationJob,
  estimateGeneration,
  executeGenerationJob,
  getGenerationAssetImage,
  getGenerationJob,
  listGenerationAssets,
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
  const [billingMode, setBillingMode] = useState<"opus_zero_anlas" | "standard">("opus_zero_anlas");
  const [perPanelCeiling, setPerPanelCeiling] = useState(0);
  const [estimate, setEstimate] = useState<GenerationEstimate | null>(null);
  const [maxCalls, setMaxCalls] = useState(1);
  const [maxCost, setMaxCost] = useState(0);
  const [confirmed, setConfirmed] = useState(false);
  const [executeConfirmed, setExecuteConfirmed] = useState(false);
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [assets, setAssets] = useState<GenerationAsset[]>([]);
  const [executionScheduled, setExecutionScheduled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const jobIsZeroAnlas = job?.cost_basis === "opus_zero_anlas_official_limits_v1";

  useEffect(() => {
    let active = true;
    setEstimate(null);
    setConfirmed(false);
    Promise.all([listGenerationJobs(projectId), listGenerationAssets(projectId)])
      .then(([jobs, currentAssets]) => {
        if (active) {
          setJob(jobs[0] ?? null);
          setAssets(currentAssets);
        }
      })
      .catch((error: unknown) => {
        if (active) onError(errorMessage(error));
      });
    return () => {
      active = false;
    };
  }, [projectId, onError]);

  useEffect(() => {
    if (!job || job.status !== "running") {
      setExecutionScheduled(false);
      return;
    }
    if (!executionScheduled && job.calls_started === job.calls_completed) return;
    let active = true;
    const refresh = async () => {
      try {
        const [updated, currentAssets] = await Promise.all([
          getGenerationJob(projectId, job.job_id),
          listGenerationAssets(projectId),
        ]);
        if (active) {
          setJob(updated);
          setAssets(currentAssets);
        }
      } catch (error) {
        if (active) onError(errorMessage(error));
      }
    };
    const timer = window.setInterval(() => void refresh(), 750);
    void refresh();
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [executionScheduled, job?.calls_completed, job?.calls_started, job?.job_id, job?.status, onError, projectId]);

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
      setExecuteConfirmed(false);
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
      setExecuteConfirmed(false);
      setMessage(transitionMessage(action));
    });
  }

  async function handleExecute() {
    if (!job || !executeConfirmed || job.status !== "running") return;
    await run(async () => {
      const result = await executeGenerationJob(projectId, job.job_id, job.revision);
      setExecuteConfirmed(false);
      setExecutionScheduled(true);
      setMessage(
        result.status === "scheduled"
          ? "已由你确认执行。队列将严格串行处理，可随时暂停领取新面板。"
          : "该队列已在执行，未启动重复请求。",
      );
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
        {job && !jobIsZeroAnlas
          ? "当前队列使用标准计费；执行可能消耗 Anlas，请按已冻结的成本上限单独确认。"
          : billingMode === "opus_zero_anlas"
            ? "默认使用 Opus 零 Anlas 资格载荷：本地预留为 0，单次 1 张、普通尺寸、28 步且不带基础图或参考图。每张图发出前都会实时核验有效 Opus；资格核验不是账单回执。"
            : "标准计费允许参考图并按已冻结参数执行，但可能消耗 Anlas；创建前必须明确冻结成本硬上限。"}
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
          <span>计费模式</span>
          <select
            value={billingMode}
            onChange={(event) => {
              const nextMode = event.target.value as "opus_zero_anlas" | "standard";
              setBillingMode(nextMode);
              setPerPanelCeiling(nextMode === "opus_zero_anlas" ? 0 : 10);
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
            value={perPanelCeiling}
            readOnly={billingMode === "opus_zero_anlas"}
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
            <QueueFact label="面板 / 出图调用" value={`${estimate.panel_count} / ${estimate.estimated_calls}`} />
            <QueueFact label="预计订阅核验" value={String(estimate.estimated_verification_calls)} />
            <QueueFact label="预计外部请求" value={String(estimate.estimated_external_requests)} />
            <QueueFact label="成本预留" value={`≤ ${estimate.estimated_cost_upper_anlas} Anlas`} />
          </div>
          <p className="field-note">{estimate.cost_notice}</p>
          <div className="prompt-preview-list">
            <h3>冻结前最终 Prompt 预览</h3>
            {estimate.panels.map((panel) => (
              <details key={panel.prompt_package_id}>
                <summary>第 {panel.page_number} 页 · 面板 {panel.panel_order}</summary>
                <strong>正向</strong>
                <code>{panel.compiled_prompt}</code>
                <strong>负向</strong>
                <code>{panel.compiled_negative_prompt}</code>
                <small>SHA-256 {panel.compiled_prompt_sha256}</small>
              </details>
            ))}
          </div>
          <div className="settings-form">
            <label>
              <span>最大出图调用次数</span>
              <input
                type="number"
                min={estimate.panel_count}
                max={estimate.panel_count * 3}
                value={maxCalls}
                onChange={(event) => {
                  setMaxCalls(Number(event.target.value));
                  setConfirmed(false);
                }}
              />
            </label>
            <label>
              <span>{estimate.billing_mode === "opus_zero_anlas" ? "最大本地 Anlas 预留" : "最大 Anlas 硬上限"}</span>
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
            <label className="confirmation-row">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(event) => setConfirmed(event.target.checked)}
              />
              <span>
                {estimate.billing_mode === "opus_zero_anlas"
                  ? `我已核对页数、面板清单、最多 ${maxCalls} 次出图、${maxCalls} 次订阅核验、${maxCalls * 2} 次外部请求和 0 Anlas 本地预留；我理解资格核验不是账单保证，逐次实际费用可能保持未核实`
                  : `我已核对页数、面板清单、最多 ${maxCalls} 次出图与外部请求，以及标准计费成本硬上限`}
              </span>
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
              出图 {job.calls_started}/{job.max_calls} · 订阅核验 {job.verification_calls_started}/{job.max_verification_calls} ·
              外部请求 {job.external_requests_started}/{job.max_external_requests} · 已完成 {job.calls_completed}/{job.panel_count} 格 ·
              已分配 {job.allocated_cost_anlas}/{job.max_cost_anlas} Anlas
            </span>
            {job.unverified_cost_calls > 0 && (
              <small>
                {jobIsZeroAnlas
                  ? `${job.unverified_cost_calls} 次请求已核验 Opus 资格，但供应商未回传逐次实际扣费，仍保留为未核实。`
                  : `${job.unverified_cost_calls} 次请求的实际费用未由接口回传，保留为未核实记录。`}
              </small>
            )}
          </div>
          <div className="button-row">
            {job.status === "queued" && (
              <button type="button" disabled={busy} onClick={() => void handleTransition("start")}>
                由我启动队列
              </button>
            )}
            {job.status === "running" && (
              <>
                <label className="confirmation-row execution-confirmation">
                  <input
                    type="checkbox"
                    checked={executeConfirmed}
                    onChange={(event) => setExecuteConfirmed(event.target.checked)}
                  />
                  <span>
                    {jobIsZeroAnlas
                      ? "我确认执行 Opus 零 Anlas 资格队列；若资格条件不满足，必须在出图前停止，供应商未回传的实际费用仍为未核实"
                      : "我确认执行旧版标准计费队列；这可能消耗 NovelAI Anlas"}
                  </span>
                </label>
                <button type="button" disabled={busy || !executeConfirmed} onClick={() => void handleExecute()}>
                  执行冻结队列（将调用 NovelAI）
                </button>
                <button type="button" disabled={busy} onClick={() => void handleTransition("pause")}>
                  暂停领取新面板
                </button>
              </>
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
      {assets.length > 0 && (
        <div className="generation-assets" aria-label="已生成面板">
          <h3>已生成面板</h3>
          <div>
            {assets.map((asset) => (
              <GeneratedPanel key={asset.asset_version_id} projectId={projectId} asset={asset} />
            ))}
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
    start: "队列已进入可执行状态；尚未发出 NovelAI 请求，请再完成一次明确确认。",
    pause: "已暂停，当前在途项可收尾，但不会领取新面板。",
    resume: "队列已恢复为可执行状态；仍需再次明确确认，应用重启也不会自动请求。",
    cancel: "已取消所有尚未开始的面板；历史和在途结算记录仍保留。",
  }[action];
}

function GeneratedPanel({ projectId, asset }: { projectId: string; asset: GenerationAsset }) {
  const [source, setSource] = useState("");
  useEffect(() => {
    let active = true;
    let objectUrl = "";
    getGenerationAssetImage(projectId, asset.asset_version_id)
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
  }, [asset.asset_version_id, projectId]);
  return (
    <article>
      {source ? <img src={source} alt={`面板 ${asset.panel_id}`} /> : <div className="asset-placeholder">本地素材</div>}
      <strong>面板 {asset.panel_id.slice(0, 8)}</strong>
      <span>v{asset.version} · {asset.width} × {asset.height} · seed {asset.seed}</span>
      <code>{asset.image_sha256.slice(0, 12)}</code>
    </article>
  );
}

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "生成队列操作失败。";
}
