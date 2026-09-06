import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WorkflowContextPanel } from "./WorkflowContextPanel";
import type { ContextClient, ContextSummary } from "./client";

afterEach(cleanup);

function fixture() {
  const summary: ContextSummary = {
    runtime: { run_id: "run-1", revision: 4, state: "handoff_required", compaction_attempts: 2, latest_checkpoint_id: "checkpoint", observation: { measurement: "actual", current_tokens: 90000, context_limit: 100000 } },
    capabilities: { usage: false, compaction: false, context_replacement: false },
    objective: "完成完整 100 页，保留三个时代", counts: { total: 100, generated: 40, lettered: 30, reviewed: 20, needs_review: 0, failed: 0 },
    can_resume: true, next_action: "审查第 21 页",
  };
  const lease = { writer_id: "ui-1", revision: 4, fencing_token: 2, expires_at: 999 };
  const client: ContextClient = {
    list: vi.fn(async () => ({ runs: [summary.runtime] })), context: vi.fn(async () => summary),
    capture: vi.fn(async () => summary), claim: vi.fn(async () => lease), release: vi.fn(async () => ({})),
    plan: vi.fn(async () => ({ can_resume: true, revision: 4, plan_hash: "hash" })),
    bundle: vi.fn(async () => ({ artifact_id: "bundle", host_context_replaced: false, bundle: { objective: summary.objective, constraints: ["完整 100 页"], counts: summary.counts } })),
    resume: vi.fn(async () => ({ runtime: { run_id: "run-1", state: "collecting", revision: 5 } })),
  };
  return { summary, client, lease };
}

it("separates progress stages and does not claim actual host usage from client reports", async () => {
  const { client } = fixture();
  render(<WorkflowContextPanel projectId="p" client={client} />);
  await screen.findByText("完成完整 100 页，保留三个时代");
  const panel = screen.getByRole("region", { name: "长任务进度与恢复" });
  expect(within(panel).getByText("40 / 100 页")).toBeVisible();
  expect(within(panel).getByText("30 / 100 页")).toBeVisible();
  expect(within(panel).getByText("20 / 100 页")).toBeVisible();
  expect(within(panel).getByText(/宿主未提供可靠读数/)).toBeVisible();
  expect(within(panel).queryByText(/90%/)).toBeNull();
  expect(client.resume).not.toHaveBeenCalled();
});

it("exports the preserved goal and releases the lease without resuming", async () => {
  const { client, lease } = fixture();
  render(<WorkflowContextPanel projectId="p" client={client} />);
  const button = await screen.findByRole("button", { name: "导出交接包" });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  const content = await screen.findByLabelText("交接内容");
  expect((content as HTMLTextAreaElement).value).toContain("完成完整 100 页，保留三个时代");
  await waitFor(() => expect(client.release).toHaveBeenCalledWith("p", "run-1", lease, 4));
  expect(client.resume).not.toHaveBeenCalled();
});

it("reconciles a fresh plan and releases the resulting revision", async () => {
  const { client, lease } = fixture();
  render(<WorkflowContextPanel projectId="p" client={client} />);
  const button = await screen.findByRole("button", { name: "核对并准备继续" });
  await waitFor(() => expect(button).toBeEnabled()); fireEvent.click(button);
  await screen.findByText(/本地任务已核对/);
  expect(client.resume).toHaveBeenCalledOnce();
  expect(client.release).toHaveBeenCalledWith("p", "run-1", lease, 5);
});

it("stops when reconciliation fails and releases ownership", async () => {
  const { client, lease } = fixture();
  vi.mocked(client.plan).mockResolvedValue({ can_resume: false, revision: 4, plan_hash: "changed" });
  render(<WorkflowContextPanel projectId="p" client={client} />);
  const button = await screen.findByRole("button", { name: "核对并准备继续" });
  await waitFor(() => expect(button).toBeEnabled()); fireEvent.click(button);
  expect(await screen.findByRole("alert")).toHaveTextContent("页面或来源需要先核对");
  expect(client.resume).not.toHaveBeenCalled();
  expect(client.release).toHaveBeenCalledWith("p", "run-1", lease, 4);
});
