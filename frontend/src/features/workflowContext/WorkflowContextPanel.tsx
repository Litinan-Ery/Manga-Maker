import { useEffect, useState } from "react";
import type { ContextClient, ContextLease, ContextRun, ContextSummary } from "./client";

const states: Record<string, string> = {
  collecting: "可继续核对", checkpointing: "需要检查点", compacting: "正在整理上下文",
  backoff: "等待重试", handoff_required: "需要交接", rehydrating: "等待恢复核对",
};

export function WorkflowContextPanel({ projectId, client }: { projectId: string; client: ContextClient }) {
  const [runs, setRuns] = useState<ContextRun[]>([]);
  const [summary, setSummary] = useState<ContextSummary | null>(null);
  const [objective, setObjective] = useState("完成当前冻结页面的生产、逐页审查与整书验收");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [bundle, setBundle] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    client.list(projectId, controller.signal).then(async ({ runs: items }) => {
      if (controller.signal.aborted) return;
      setRuns(items);
      if (items[0]) {
        const value = await client.context(projectId, items[0].run_id, controller.signal);
        if (!controller.signal.aborted) setSummary(value);
      }
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(message(reason));
    }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [client, projectId]);

  async function action(work: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await work(); } catch (reason) { setError(message(reason)); } finally { setBusy(false); }
  }

  async function refresh(run = summary?.runtime.run_id) {
    const items = await client.list(projectId);
    setRuns(items.runs);
    const selected = run ?? items.runs[0]?.run_id;
    setSummary(selected ? await client.context(projectId, selected) : null);
  }

  async function recover(kind: "bundle" | "resume") {
    if (!summary) return;
    const run = summary.runtime.run_id;
    let lease: ContextLease | null = null;
    let revision = summary.runtime.revision;
    try {
      lease = await client.claim(projectId, run);
      revision = lease.revision;
      if (kind === "bundle") {
        const result = await client.bundle(projectId, run, lease, revision);
        setBundle(JSON.stringify(result.bundle, null, 2));
        setNotice("交接文件已保存在本地项目中，可下载后用于新上下文。");
      } else {
        const plan = await client.plan(projectId, run);
        if (!plan.can_resume) throw new Error("页面或来源需要先核对，尚未恢复。");
        if (plan.revision !== revision) throw new Error("任务状态已变化，请刷新后重试。");
        const result = await client.resume(projectId, run, lease, plan);
        revision = result.runtime.revision;
        setNotice("本地任务已核对，可按原有审批流程继续生产。");
      }
    } finally {
      if (lease) await client.release(projectId, run, lease, revision);
    }
    await refresh(run);
  }

  function download() {
    const url = URL.createObjectURL(new Blob([bundle], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url; link.download = `workflow-${summary?.runtime.run_id}-recovery.json`;
    link.click(); URL.revokeObjectURL(url);
  }

  const counts = summary?.counts;
  const observation = summary?.runtime.observation;
  const known = summary?.capabilities.usage && observation?.measurement === "actual" && observation.current_tokens !== null && Boolean(observation.context_limit);
  return <section className="workflow-context" aria-label="长任务进度与恢复">
    <div className="workflow-context-heading">
      <div><p className="section-kicker">长任务进度</p><h2>检查点与交接</h2></div>
      <button type="button" className="quiet-button" disabled={busy} onClick={() => void action(() => refresh())}>刷新任务状态</button>
    </div>
    {runs.length > 0 && <label>已记录任务
      <select value={summary?.runtime.run_id ?? ""} disabled={busy} onChange={(event) => { setBundle(""); void action(() => refresh(event.target.value)); }}>
        {runs.map(run => <option key={run.run_id} value={run.run_id}>任务 {run.run_id.slice(-8)} · 修订 {run.revision}</option>)}
      </select>
    </label>}
    {summary && <>
      <p>{summary.objective}</p>
      <p role="status">{states[summary.runtime.state] ?? "状态待核对"} · 修订 {summary.runtime.revision}</p>
      {counts && <dl className="workflow-context-counts">
        <div><dt>已生成</dt><dd>{counts.generated} / {counts.total} 页</dd></div>
        <div><dt>已排版</dt><dd>{counts.lettered} / {counts.total} 页</dd></div>
        <div><dt>审查记录有效</dt><dd>{counts.reviewed} / {counts.total} 页</dd></div>
      </dl>}
      <p>上下文用量：{known ? `${Math.round(observation!.current_tokens! / observation!.context_limit! * 100)}%` : "宿主未提供可靠读数"}</p>
      {!summary.capabilities.context_replacement && <p>当前宿主支持通过交接文件在新上下文继续。生成和导出仍需完成各自的审批与验收。</p>}
      {summary.next_action && <p>下一步：{summary.next_action}</p>}
      {Boolean(summary.problem_count) && <p>有 {summary.problem_count} 项来源或页面状态需要核对。</p>}
      {summary.error_message && <p role="alert">{summary.error_message}</p>}
      <div className="workflow-context-actions">
        <button type="button" disabled={busy || !summary.runtime.latest_checkpoint_id} onClick={() => void action(() => recover("bundle"))}>导出交接包</button>
        <button type="button" disabled={busy || !summary.can_resume} onClick={() => void action(() => recover("resume"))}>核对并准备继续</button>
      </div>
    </>}
    <details>
      <summary>记录当前冻结页面</summary>
      <p>记录范围为当前已冻结的整页计划。完整生产清单可通过工作流命令登记全部页码。</p>
      <label>任务目标<input value={objective} maxLength={2000} onChange={(event) => setObjective(event.target.value)} /></label>
      <button type="button" disabled={busy || !objective.trim()} onClick={() => void action(async () => {
        const value = await client.capture(projectId, objective.trim());
        setBundle(""); await refresh(value.runtime.run_id);
      })}>保存当前计划检查点</button>
    </details>
    {bundle && <div><label>交接内容<textarea readOnly value={bundle} rows={8} /></label><button type="button" onClick={download}>下载交接文件</button></div>}
    {notice && <p role="status">{notice}</p>}
    {error && <p className="action-error" role="alert">{error}</p>}
  </section>;
}

function message(reason: unknown): string { return reason instanceof Error ? reason.message : "任务状态暂时不可用，请刷新。"; }
