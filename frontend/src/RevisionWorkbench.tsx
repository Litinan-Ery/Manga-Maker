import { useEffect, useMemo, useState } from "react";

import {
  ApiError,
  type ComicPageVersion,
  type GenerationJob,
  type RevisionEstimate,
  type RevisionOperation,
  activatePageVersion,
  createRevisionJob,
  estimateRevision,
  executeGenerationJob,
  getGenerationJob,
  listPageVersions,
  transitionGenerationJob,
  uploadRevisionMask,
} from "./api";

interface RevisionWorkbenchProps {
  projectId: string;
  page: ComicPageVersion;
  onPageChange: (page: ComicPageVersion) => void;
  onError: (message: string) => void;
}

const TERMINAL = new Set(["completed", "failed", "needs_review", "canceled"]);

export function RevisionWorkbench({
  projectId,
  page,
  onPageChange,
  onError,
}: RevisionWorkbenchProps) {
  const [versions, setVersions] = useState<ComicPageVersion[]>([]);
  const [historyVersionId, setHistoryVersionId] = useState("");
  const [operation, setOperation] = useState<RevisionOperation>("panel_reroll");
  const [panelId, setPanelId] = useState(page.document.panels[0]?.panel_id ?? "");
  const [maskFile, setMaskFile] = useState<File | null>(null);
  const [editPrompt, setEditPrompt] = useState("");
  const [strength, setStrength] = useState(0.65);
  const [costCeiling, setCostCeiling] = useState(10);
  const [estimate, setEstimate] = useState<RevisionEstimate | null>(null);
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [scopeConfirmed, setScopeConfirmed] = useState(false);
  const [executeConfirmed, setExecuteConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const targetPanel = useMemo(
    () => page.document.panels.find((panel) => panel.panel_id === panelId) ?? null,
    [page.document.panels, panelId],
  );

  useEffect(() => {
    let active = true;
    setPanelId(page.document.panels[0]?.panel_id ?? "");
    setEstimate(null);
    setJob(null);
    listPageVersions(projectId, page.page_id)
      .then((items) => {
        if (!active) return;
        setVersions(items);
        setHistoryVersionId(
          items.find((item) => item.page_version_id !== page.page_version_id)
            ?.page_version_id ?? page.page_version_id,
        );
      })
      .catch((error: unknown) => {
        if (active) onError(errorMessage(error));
      });
    return () => {
      active = false;
    };
  }, [onError, page.page_id, page.page_version_id, projectId]);

  function resetPlan(nextOperation: RevisionOperation = operation) {
    setOperation(nextOperation);
    setEstimate(null);
    setJob(null);
    setScopeConfirmed(false);
    setExecuteConfirmed(false);
    setMessage("");
  }

  async function handleEstimate() {
    if (operation !== "page_reroll" && !targetPanel) {
      onError("请选择当前页中的目标面板。");
      return;
    }
    if (operation === "inpaint" && (!maskFile || !editPrompt.trim())) {
      onError("局部重绘需要与父素材同尺寸的 PNG 蒙版和修改说明。");
      return;
    }
    await run(async () => {
      const mask =
        operation === "inpaint" && targetPanel && maskFile
          ? await uploadRevisionMask(
              projectId,
              targetPanel.panel_id,
              targetPanel.asset_version_id,
              maskFile,
            )
          : null;
      const result = await estimateRevision(projectId, {
        operation,
        page_id: page.page_id,
        panel_id: operation === "page_reroll" ? null : panelId,
        mask_asset_id: mask?.mask_asset_id ?? null,
        edit_prompt: operation === "inpaint" ? editPrompt.trim() : null,
        inpaint_strength: operation === "inpaint" ? strength : null,
        per_panel_cost_ceiling_anlas: costCeiling,
      });
      setEstimate(result);
      setScopeConfirmed(false);
      setMessage("范围和保守成本已在本机冻结预览；尚未创建任务或调用 NovelAI。");
    });
  }

  async function handleCreate() {
    if (!estimate || !scopeConfirmed) return;
    await run(async () => {
      const created = await createRevisionJob(projectId, estimate);
      setJob(created);
      setMessage("不可变 revision 队列已建立，但尚未启动或发出图像请求。");
    });
  }

  async function handleStart() {
    if (!job) return;
    await run(async () => {
      const started = await transitionGenerationJob(
        projectId,
        job.job_id,
        "start",
        job.revision,
      );
      setJob(started);
      setMessage("队列已就绪；仍需第二次明确确认才会调用 NovelAI。");
    });
  }

  async function handleExecute() {
    if (!job || !executeConfirmed) return;
    await run(async () => {
      await executeGenerationJob(projectId, job.job_id, job.revision);
      setMessage("已按你的明确操作执行冻结队列；可以暂停或取消后续领取。");
      const completed = await pollJob(projectId, job.job_id, setJob);
      if (completed.status === "completed" && completed.result_page_version_id) {
        const pageVersions = await listPageVersions(projectId, page.page_id);
        setVersions(pageVersions);
        const current = pageVersions.find((version) => version.is_current);
        if (current) onPageChange(current);
      }
    });
  }

  async function handleRestore() {
    if (!historyVersionId) return;
    await run(async () => {
      const restored = await activatePageVersion(projectId, page, historyVersionId);
      onPageChange(restored);
      setMessage("历史页面已恢复为当前版本；没有发出 NovelAI 请求，也没有删除分支。");
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
    <section className="revision-workbench" aria-label="重绘与版本恢复">
      <div className="bible-section-heading">
        <div>
          <h3>重绘与版本恢复</h3>
          <p>任何出图都创建新素材和页面分支；恢复历史只切换本地指针。</p>
        </div>
      </div>

      <div className="revision-history">
        <label>
          <span>页面历史</span>
          <select
            value={historyVersionId}
            onChange={(event) => setHistoryVersionId(event.target.value)}
          >
            {versions.map((version) => (
              <option key={version.page_version_id} value={version.page_version_id}>
                v{version.version}{version.is_current ? " · 当前" : ""}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="quiet-button"
          disabled={busy || !historyVersionId || historyVersionId === page.page_version_id}
          onClick={() => void handleRestore()}
        >
          恢复为当前页面（仅本地）
        </button>
      </div>

      <div className="revision-fields">
        <label>
          <span>操作</span>
          <select
            value={operation}
            onChange={(event) => resetPlan(event.target.value as RevisionOperation)}
          >
            <option value="panel_reroll">单格 reroll</option>
            <option value="page_reroll">整页 reroll</option>
            <option value="inpaint">PNG 蒙版局部重绘</option>
          </select>
        </label>
        {operation !== "page_reroll" && (
          <label>
            <span>目标面板</span>
            <select value={panelId} onChange={(event) => setPanelId(event.target.value)}>
              {page.document.panels.map((panel, index) => (
                <option key={panel.panel_id} value={panel.panel_id}>面板 {index + 1}</option>
              ))}
            </select>
          </label>
        )}
        <NumberInput
          label="每格保守预留（Anlas）"
          value={costCeiling}
          min={1}
          max={100000}
          onChange={setCostCeiling}
        />
        {operation === "inpaint" && (
          <>
            <label>
              <span>PNG 蒙版（白色重绘，黑色保留）</span>
              <input
                type="file"
                accept="image/png"
                onChange={(event) => setMaskFile(event.target.files?.[0] ?? null)}
              />
            </label>
            <label>
              <span>局部修改说明</span>
              <textarea
                rows={3}
                value={editPrompt}
                onChange={(event) => setEditPrompt(event.target.value)}
              />
            </label>
            <NumberInput
              label="重绘强度"
              value={strength}
              min={0.1}
              max={1}
              step={0.05}
              onChange={setStrength}
            />
          </>
        )}
      </div>
      <button type="button" disabled={busy} onClick={() => void handleEstimate()}>
        预检目标与保守成本（不出图）
      </button>

      {estimate && !job && (
        <div className="revision-approval">
          <p>
            {estimate.panel_count} 格 · 最多 {estimate.estimated_calls} 次请求 ·
            保守预留 ≤ {estimate.estimated_cost_upper_anlas} Anlas
          </p>
          <label className="confirmation-row">
            <input
              type="checkbox"
              checked={scopeConfirmed}
              onChange={(event) => setScopeConfirmed(event.target.checked)}
            />
            <span>我已核对目标、父版本、蒙版和成本上限</span>
          </label>
          <button
            type="button"
            disabled={busy || !scopeConfirmed}
            onClick={() => void handleCreate()}
          >
            创建有界 revision 队列（不出图）
          </button>
        </div>
      )}

      {job && (
        <div className="revision-approval">
          <p>队列：{job.status} · 已发出 {job.calls_started}/{job.max_calls} 次</p>
          {job.status === "queued" && (
            <button type="button" disabled={busy} onClick={() => void handleStart()}>
              由我启动 revision 队列
            </button>
          )}
          {job.status === "running" && (
            <>
              <label className="confirmation-row">
                <input
                  type="checkbox"
                  checked={executeConfirmed}
                  onChange={(event) => setExecuteConfirmed(event.target.checked)}
                />
                <span>我确认现在执行，这可能产生 NovelAI 费用</span>
              </label>
              <button
                type="button"
                disabled={busy || !executeConfirmed}
                onClick={() => void handleExecute()}
              >
                执行冻结 revision 队列（将调用 NovelAI）
              </button>
            </>
          )}
        </div>
      )}
      {message && <p className="success-message" role="status">{message}</p>}
    </section>
  );
}

function NumberInput({
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
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}

async function pollJob(
  projectId: string,
  jobId: string,
  onUpdate: (job: GenerationJob) => void,
): Promise<GenerationJob> {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    await new Promise((resolve) => window.setTimeout(resolve, 1000));
    const job = await getGenerationJob(projectId, jobId);
    onUpdate(job);
    if (TERMINAL.has(job.status)) return job;
  }
  throw new ApiError("等待生成结果超时；任务仍保留，可刷新后继续查看。", 408);
}

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "重绘或版本操作失败。";
}
