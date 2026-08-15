import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { WholeBookPlanner } from "./WholeBookPlanner";
import {
  type BookEstimate,
  type BookPlan,
  clearLocalSession,
  consumeLocalSession,
} from "./api";

afterEach(() => {
  clearLocalSession();
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
});

it("freezes a bounded plan, approves every chapter and only creates one local job on explicit advance", async () => {
  window.history.replaceState(null, "", "/#session=session-test&csrf=csrf-test");
  consumeLocalSession();
  let plan = makePlan("awaiting_approval");
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    if (path.endsWith("/plans/current") && method === "GET") {
      return Promise.resolve(jsonResponse({ error: { message: "尚未规划" } }, 404));
    }
    if (path.endsWith("/estimate")) return Promise.resolve(jsonResponse(estimate));
    if (path.endsWith("/plans")) return Promise.resolve(jsonResponse(plan, 201));
    if (path.includes("chapters/book-chapter-1/approve")) {
      plan = {
        ...plan,
        revision: 2,
        chapters: plan.chapters.map((chapter, index) =>
          index === 0 ? { ...chapter, status: "approved" as const } : chapter,
        ),
      };
      return Promise.resolve(jsonResponse(plan));
    }
    if (path.includes("chapters/book-chapter-2/approve")) {
      plan = {
        ...plan,
        revision: 3,
        status: "ready",
        chapters: plan.chapters.map((chapter) => ({
          ...chapter,
          status: "approved" as const,
        })),
      };
      return Promise.resolve(jsonResponse(plan));
    }
    if (path.endsWith("/start")) {
      plan = { ...plan, revision: 4, status: "active" };
      return Promise.resolve(jsonResponse(plan));
    }
    if (path.endsWith("/advance")) {
      plan = {
        ...plan,
        revision: 5,
        chapters: plan.chapters.map((chapter, index) =>
          index === 0
            ? {
                ...chapter,
                status: "job_created" as const,
                generation_job_id: "generation-job-1",
                generation_job_status: "queued" as const,
              }
            : chapter,
        ),
      };
      return Promise.resolve(jsonResponse(plan));
    }
    return Promise.reject(new Error(`unexpected request: ${method} ${path}`));
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<WholeBookPlanner projectId="project-1" onError={vi.fn()} />);
  expect(await screen.findByText("尚未规划")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "计算整本预算" }));
  expect(await screen.findByText("2. 第二章")).toBeInTheDocument();
  expect(screen.getAllByText(/0 Anlas/).length).toBeGreaterThan(0);
  expect(screen.getByText(/尚未创建队列或外部请求/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.change(screen.getByLabelText("整本出图调用硬上限"), { target: { value: "3" } });
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  expect(screen.getByRole("button", { name: "冻结整本计划" })).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "冻结整本计划" }));
  expect(await screen.findByText(/请逐章核对并批准/)).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "核对并批准本章" })[0]);
  await waitFor(() => expect(screen.getAllByText("已批准").length).toBeGreaterThan(0));
  fireEvent.click(screen.getByRole("button", { name: "核对并批准本章" }));
  expect(await screen.findByRole("button", { name: "启动整本计划" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "启动整本计划" }));
  expect(await screen.findByRole("button", { name: "创建下一章本地队列" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "创建下一章本地队列" }));
  expect(await screen.findByText(/生成控制台中再次确认/)).toBeInTheDocument();
  expect(screen.getByText(/本地任务 generati/)).toBeInTheDocument();

  const writes = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
  expect(writes).toHaveLength(6);
  const estimateCall = writes.find(([path]) => String(path).endsWith("/estimate"));
  expect(JSON.parse(String(estimateCall?.[1]?.body)).per_panel_cost_ceiling_anlas).toBe(0);
  for (const [, init] of writes) {
    const headers = new Headers(init?.headers);
    expect(headers.get("X-Manga-Maker-Session")).toBe("session-test");
    expect(headers.get("X-CSRF-Token")).toBe("csrf-test");
  }
});

const estimate: BookEstimate = {
  schema_version: "1.0",
  project_id: "project-1",
  source_chapter_set_id: "chapter-set-1",
  continuity_version_id: "continuity-2",
  per_panel_cost_ceiling_anlas: 0,
  chapters: [1, 2].map((ordinal) => ({
    chapter_id: `chapter-${ordinal}`,
    ordinal,
    title: ordinal === 1 ? "第一章" : "第二章",
    storyboard_version_id: `storyboard-${ordinal}`,
    character_bible_version_id: `characters-${ordinal}`,
    style_bible_version_id: `style-${ordinal}`,
    generation_plan_fingerprint: String(ordinal).repeat(64),
    page_count: 1,
    panel_count: 1,
    estimated_calls: 1,
    estimated_verification_calls: 1,
    estimated_external_requests: 2,
    estimated_cost_upper_anlas: 0,
  })),
  chapter_count: 2,
  estimated_page_count: 2,
  estimated_panel_count: 2,
  estimated_calls: 2,
  estimated_verification_calls: 2,
  estimated_external_requests: 4,
  estimated_cost_upper_anlas: 0,
  billing_mode: "opus_zero_anlas",
  cost_basis: "opus_zero_anlas_official_limits_v1",
  cost_notice: "整本计划已冻结为 Opus 零 Anlas 模式。",
  plan_fingerprint: "f".repeat(64),
  external_request_created: false,
};

function makePlan(status: BookPlan["status"]): BookPlan {
  return {
    book_plan_id: "book-plan-1",
    project_id: "project-1",
    version: 1,
    source_chapter_set_id: "chapter-set-1",
    continuity_version_id: "continuity-2",
    status,
    per_panel_cost_ceiling_anlas: 0,
    estimated_page_count: 2,
    estimated_panel_count: 2,
    estimated_calls: 2,
    estimated_cost_upper_anlas: 0,
    max_calls: 2,
    max_cost_anlas: 0,
    plan_fingerprint: "f".repeat(64),
    revision: 1,
    is_current: true,
    created_at: "2026-08-09 00:00:00",
    updated_at: "2026-08-09 00:00:00",
    completed_at: null,
    chapters: [1, 2].map((ordinal) => ({
      book_chapter_plan_id: `book-chapter-${ordinal}`,
      chapter_id: `chapter-${ordinal}`,
      ordinal,
      title: ordinal === 1 ? "第一章" : "第二章",
      storyboard_version_id: `storyboard-${ordinal}`,
      character_bible_version_id: `characters-${ordinal}`,
      style_bible_version_id: `style-${ordinal}`,
      generation_plan_fingerprint: String(ordinal).repeat(64),
      page_count: 1,
      panel_count: 1,
      estimated_cost_upper_anlas: 0,
      max_calls: 1,
      max_cost_anlas: 0,
      status: "awaiting_approval" as const,
      generation_job_id: null,
      generation_job_status: null,
      calls_started: 0,
      calls_completed: 0,
      verification_calls_started: 0,
      verification_calls_completed: 0,
      allocated_cost_anlas: 0,
      recorded_cost_anlas: 0,
      unverified_cost_calls: 0,
      external_requests_started: 0,
      external_requests_completed: 0,
      retry_count: 0,
      approved_at: null,
    })),
    calls_started: 0,
    calls_completed: 0,
    verification_calls_started: 0,
    verification_calls_completed: 0,
    allocated_cost_anlas: 0,
    recorded_cost_anlas: 0,
    unverified_cost_calls: 0,
    external_requests_started: 0,
    external_requests_completed: 0,
    max_verification_calls: 2,
    max_external_requests: 4,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
