import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { GenerationConsole } from "./GenerationConsole";
import { clearLocalSession, consumeLocalSession } from "./api";

afterEach(() => {
  cleanup();
  clearLocalSession();
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
});

it("requires local estimate and confirmation before controlling the bounded queue", async () => {
  window.history.replaceState(null, "", "/#session=session-test&csrf=csrf-test");
  consumeLocalSession();
  const fetchMock = vi.fn((path: RequestInfo | URL, init?: RequestInit) => {
    const url = String(path);
    if (url.endsWith("/generation/jobs") && !init?.method) {
      return Promise.resolve(jsonResponse([]));
    }
    if (url.endsWith("/generation/assets") && !init?.method) {
      return Promise.resolve(jsonResponse([]));
    }
    if (url.endsWith("/generation/estimate") && init?.method === "POST") {
      return Promise.resolve(jsonResponse(estimate));
    }
    if (url.endsWith("/generation/jobs") && init?.method === "POST") {
      return Promise.resolve(jsonResponse(job("queued", 1), 201));
    }
    if (url.endsWith("/start") && init?.method === "POST") {
      return Promise.resolve(jsonResponse(job("running", 2)));
    }
    if (url.endsWith("/pause") && init?.method === "POST") {
      return Promise.resolve(jsonResponse(job("paused", 3)));
    }
    return Promise.reject(new Error(`unexpected request: ${url}`));
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    <GenerationConsole
      projectId="project-1"
      chapterSet={{
        source_file_id: "source-1",
        chapter_set_id: "set-1",
        chapter_set_version: 1,
        chapters: [
          {
            chapter_id: "chapter-1",
            version: 1,
            ordinal: 1,
            title: "第一章",
            start_offset: 0,
            end_offset: 20,
            text_sha256: "a".repeat(64),
          },
        ],
      }}
      onError={vi.fn()}
    />,
  );

  fireEvent.click(await screen.findByRole("button", { name: "生成本地范围与预算预检" }));
  expect(await screen.findByText("≤ 0 Anlas")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "创建有界队列（不出图）" })).toBeDisabled();

  fireEvent.click(screen.getByLabelText(/最多 2 次出图、2 次订阅核验、4 次外部请求/));
  fireEvent.change(screen.getByLabelText("最大出图调用次数"), { target: { value: "3" } });
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  expect(screen.getByRole("button", { name: "创建有界队列（不出图）" })).toBeDisabled();
  fireEvent.click(screen.getByLabelText(/最多 3 次出图、3 次订阅核验、6 次外部请求/));
  fireEvent.click(screen.getByRole("button", { name: "创建有界队列（不出图）" }));
  expect(await screen.findByText(/面板清单和上限已冻结/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "由我启动队列" }));
  expect(await screen.findByText(/尚未发出 NovelAI 请求/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "执行冻结队列（将调用 NovelAI）" })).toBeDisabled();
  expect(fetchMock.mock.calls.some(([path]) => String(path).endsWith("/execute"))).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "暂停领取新面板" }));
  expect(await screen.findByText(/不会领取新面板/)).toBeInTheDocument();

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(6));
  const writeCalls = fetchMock.mock.calls.filter(([, init]) => init?.method);
  const estimateCall = writeCalls.find(([path]) => String(path).endsWith("/generation/estimate"));
  expect(JSON.parse(String(estimateCall?.[1]?.body)).per_panel_cost_ceiling_anlas).toBe(0);
  expect(writeCalls.every(([, init]) => new Headers(init?.headers).has("X-CSRF-Token"))).toBe(true);
});

it("keeps standard billing available only through an explicit mode choice", async () => {
  window.history.replaceState(null, "", "/#session=session-test&csrf=csrf-test");
  consumeLocalSession();
  const fetchMock = vi.fn((path: RequestInfo | URL, init?: RequestInit) => {
    const url = String(path);
    if (url.endsWith("/generation/jobs") && !init?.method) {
      return Promise.resolve(jsonResponse([]));
    }
    if (url.endsWith("/generation/assets") && !init?.method) {
      return Promise.resolve(jsonResponse([]));
    }
    if (url.endsWith("/generation/estimate") && init?.method === "POST") {
      return Promise.resolve(jsonResponse({
        ...estimate,
        per_panel_cost_ceiling_anlas: 10,
        estimated_verification_calls: 0,
        estimated_external_requests: 2,
        estimated_cost_upper_anlas: 20,
        billing_mode: "standard",
        cost_basis: "user_confirmed_per_panel_ceiling",
      }));
    }
    return Promise.reject(new Error(`unexpected request: ${url}`));
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<GenerationConsole projectId="project-1" chapterSet={chapterSet} onError={vi.fn()} />);
  await screen.findByText(/默认使用 Opus 零 Anlas/);
  fireEvent.change(screen.getByLabelText("计费模式"), { target: { value: "standard" } });
  expect(screen.getByLabelText("每格 Anlas 硬上限")).toHaveValue(10);
  fireEvent.click(screen.getByRole("button", { name: "生成本地范围与预算预检" }));
  expect(await screen.findByText("≤ 20 Anlas")).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText(/标准计费成本硬上限/));
  expect(screen.getByRole("button", { name: "创建有界队列（不出图）" })).toBeEnabled();
  fireEvent.change(screen.getByLabelText("最大 Anlas 硬上限"), {
    target: { value: "30" },
  });
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  expect(screen.getByRole("button", { name: "创建有界队列（不出图）" })).toBeDisabled();

  const call = fetchMock.mock.calls.find(([path]) => String(path).endsWith("/generation/estimate"));
  expect(JSON.parse(String(call?.[1]?.body)).per_panel_cost_ceiling_anlas).toBe(10);
});

it("sends the frozen queue only after a second explicit execution confirmation", async () => {
  window.history.replaceState(null, "", "/#session=session-test&csrf=csrf-test");
  consumeLocalSession();
  const fetchMock = vi.fn((path: RequestInfo | URL, init?: RequestInit) => {
    const url = String(path);
    if (url.endsWith("/generation/jobs") && !init?.method) {
      return Promise.resolve(jsonResponse([job("running", 2)]));
    }
    if (url.endsWith("/generation/assets") && !init?.method) {
      return Promise.resolve(jsonResponse([]));
    }
    if (url.endsWith("/execute") && init?.method === "POST") {
      return Promise.resolve(jsonResponse({
        status: "scheduled",
        job_id: "job-1",
        bounded_user_action_id: "action-1",
      }, 202));
    }
    if (url.endsWith("/generation/jobs/job-1") && !init?.method) {
      return Promise.resolve(jsonResponse({
        ...job("running", 3),
        status: "completed",
        calls_started: 2,
        calls_completed: 2,
      }));
    }
    return Promise.reject(new Error(`unexpected request: ${url}`));
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    <GenerationConsole
      projectId="project-1"
      chapterSet={{
        source_file_id: "source-1",
        chapter_set_id: "set-1",
        chapter_set_version: 1,
        chapters: [{
          chapter_id: "chapter-1",
          version: 1,
          ordinal: 1,
          title: "第一章",
          start_offset: 0,
          end_offset: 20,
          text_sha256: "a".repeat(64),
        }],
      }}
      onError={vi.fn()}
    />,
  );

  const execute = await screen.findByRole("button", {
    name: "执行冻结队列（将调用 NovelAI）",
  });
  expect(execute).toBeDisabled();
  expect(fetchMock.mock.calls.some(([path]) => String(path).endsWith("/execute"))).toBe(false);

  fireEvent.click(screen.getByLabelText(/我确认执行 Opus 零 Anlas 资格队列/));
  fireEvent.click(execute);
  expect(await screen.findByText(/已由你确认执行/)).toBeInTheDocument();

  const executeCall = fetchMock.mock.calls.find(([path]) => String(path).endsWith("/execute"));
  expect(executeCall).toBeDefined();
  expect(JSON.parse(String(executeCall?.[1]?.body))).toEqual({
    expected_revision: 2,
    confirmation: "I_CONFIRM_NOVELAI_IMAGE_GENERATION",
  });
  await waitFor(() => expect(screen.getAllByText("已完成")).toHaveLength(2));
});

it("keeps legacy standard-cost jobs behind an explicit paid warning", async () => {
  window.history.replaceState(null, "", "/#session=session-test&csrf=csrf-test");
  consumeLocalSession();
  const standardJob = job("running", 2, "user_confirmed_per_panel_ceiling");
  vi.stubGlobal(
    "fetch",
    vi.fn((path: RequestInfo | URL) => {
      const url = String(path);
      if (url.endsWith("/generation/jobs")) return Promise.resolve(jsonResponse([standardJob]));
      if (url.endsWith("/generation/assets")) return Promise.resolve(jsonResponse([]));
      return Promise.reject(new Error(`unexpected request: ${url}`));
    }),
  );

  render(
    <GenerationConsole
      projectId="project-1"
      chapterSet={{
        source_file_id: "source-1",
        chapter_set_id: "set-1",
        chapter_set_version: 1,
        chapters: [{
          chapter_id: "chapter-1",
          version: 1,
          ordinal: 1,
          title: "第一章",
          start_offset: 0,
          end_offset: 20,
          text_sha256: "a".repeat(64),
        }],
      }}
      onError={vi.fn()}
    />,
  );

  expect(await screen.findByText(/旧版标准计费队列/)).toBeInTheDocument();
  expect(screen.getByLabelText(/可能消耗 NovelAI Anlas/)).not.toBeChecked();
  expect(screen.getByRole("button", { name: "执行冻结队列（将调用 NovelAI）" })).toBeDisabled();
});

const estimate = {
  project_id: "project-1",
  chapter_id: "chapter-1",
  storyboard_version_id: "storyboard-v1",
  character_bible_version_id: "character-v1",
  style_bible_version_id: "style-v1",
  novelai_config_revision: 1,
  provider_model_id: "nai-diffusion-5-full",
  mapping_version: "novelai-image-2026-08-29.4-v5-full-1",
  contract_sha256: "c".repeat(64),
  page_count: 1,
  panel_count: 2,
  estimated_calls: 2,
  estimated_verification_calls: 2,
  estimated_external_requests: 4,
  per_panel_cost_ceiling_anlas: 0,
  estimated_cost_upper_anlas: 0,
  billing_mode: "opus_zero_anlas",
  cost_basis: "opus_zero_anlas_official_limits_v1",
  cost_notice: "执行前逐张核验 Opus 免费条件。",
  plan_fingerprint: "f".repeat(64),
  panels: [],
  external_request_created: false,
} as const;

const chapterSet = {
  source_file_id: "source-1",
  chapter_set_id: "set-1",
  chapter_set_version: 1,
  chapters: [{
    chapter_id: "chapter-1",
    version: 1,
    ordinal: 1,
    title: "第一章",
    start_offset: 0,
    end_offset: 20,
    text_sha256: "a".repeat(64),
  }],
};

function job(
  status: "queued" | "running" | "paused",
  revision: number,
  costBasis: "user_confirmed_per_panel_ceiling" | "opus_zero_anlas_official_limits_v1" =
    "opus_zero_anlas_official_limits_v1",
) {
  const zeroAnlas = costBasis === "opus_zero_anlas_official_limits_v1";
  return {
    job_id: "job-1",
    project_id: "project-1",
    chapter_id: "chapter-1",
    storyboard_version_id: "storyboard-v1",
    character_bible_version_id: "character-v1",
    style_bible_version_id: "style-v1",
    novelai_config_revision: 1,
    provider_model_id: "nai-diffusion-5-full",
    mapping_version: "novelai-image-2026-08-29.4-v5-full-1",
    contract_sha256: "c".repeat(64),
    credential_profile_id: "novelai",
    timeout_seconds: 30,
    plan_fingerprint: "f".repeat(64),
    status,
    user_action_id: "action-1",
    page_count: 1,
    panel_count: 2,
    max_calls: 2,
    max_cost_anlas: zeroAnlas ? 0 : 20,
    estimated_cost_upper_anlas: zeroAnlas ? 0 : 20,
    cost_basis: costBasis,
    calls_started: 0,
    calls_completed: 0,
    verification_calls_started: 0,
    verification_calls_completed: 0,
    max_verification_calls: zeroAnlas ? 2 : 0,
    max_external_requests: zeroAnlas ? 4 : 2,
    items_claimed: 0,
    allocated_cost_anlas: 0,
    recorded_cost_anlas: 0,
    unverified_cost_calls: 0,
    revision,
    created_at: "2026-08-09 12:00:00",
    updated_at: "2026-08-09 12:00:00",
    started_at: null,
    paused_at: null,
    completed_at: null,
    items: [],
    external_requests_started: 0,
    external_requests_completed: 0,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
