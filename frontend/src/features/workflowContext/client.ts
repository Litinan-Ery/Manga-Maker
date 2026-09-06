export interface ContextLease {
  writer_id: string;
  fencing_token: number;
  expires_at: number;
  revision: number;
}

export interface ContextRun {
  run_id: string;
  revision: number;
  state: string;
}

export interface ContextSummary {
  runtime: ContextRun & {
    compaction_attempts: number;
    latest_checkpoint_id: string | null;
    observation: { measurement: string; current_tokens: number | null; context_limit: number | null } | null;
  };
  capabilities: { usage: boolean; compaction: boolean; context_replacement: boolean };
  objective?: string;
  next_action?: string;
  counts?: { total: number; generated: number; lettered: number; reviewed: number; needs_review: number; failed: number };
  can_resume: boolean;
  problem_count?: number;
  problems?: Array<{ code: string; unit_id?: string }>;
  error_message?: string;
}

export interface RecoveryPlan { revision: number; plan_hash: string; can_resume: boolean }
export interface RecoveryBundle { bundle: Record<string, unknown>; artifact_id: string; host_context_replaced: boolean }
export interface ContextClient {
  list(project: string, signal?: AbortSignal): Promise<{ runs: ContextRun[] }>;
  context(project: string, run: string, signal?: AbortSignal): Promise<ContextSummary>;
  capture(project: string, objective: string): Promise<ContextSummary>;
  claim(project: string, run: string): Promise<ContextLease>;
  release(project: string, run: string, lease: ContextLease, revision: number): Promise<unknown>;
  plan(project: string, run: string): Promise<RecoveryPlan>;
  bundle(project: string, run: string, lease: ContextLease, revision: number): Promise<RecoveryBundle>;
  resume(project: string, run: string, lease: ContextLease, plan: RecoveryPlan): Promise<{ runtime: ContextRun }>;
}

export function createContextClient(credentials: { session: string; csrf: string }): ContextClient {
  async function request<T>(project: string, suffix: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    const response = await fetch(`/api/v1/projects/${encodeURIComponent(project)}/workflows${suffix}`, {
      method: body === undefined ? "GET" : "POST",
      signal,
      headers: {
        Accept: "application/json", "Content-Type": "application/json",
        "X-Manga-Maker-Session": credentials.session, "X-CSRF-Token": credentials.csrf,
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error?.message ?? result.message ?? `任务状态请求失败 (${response.status})`);
    return result as T;
  }
  const path = (run: string) => `/${encodeURIComponent(run)}`;
  return {
    list: (project, signal) => request(project, "", undefined, signal),
    context: (project, run, signal) => request(project, path(run) + "/context", undefined, signal),
    capture: (project, objective) => request(project, "/from-project", { objective }),
    claim: (project, run) => request(project, path(run) + "/leases", { writer_id: `ui-${crypto.randomUUID()}`, ttl: 600 }),
    release: (project, run, lease, revision) => request(project, path(run) + "/leases/release", { lease, expected_revision: revision }),
    plan: (project, run) => request(project, path(run) + "/recovery-plan", {}),
    bundle: (project, run, lease, revision) => request(project, path(run) + "/recovery-bundles", { lease, expected_revision: revision }),
    resume: (project, run, lease, plan) => request(project, path(run) + "/resume", { lease, expected_revision: plan.revision, plan_hash: plan.plan_hash }),
  };
}
