import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { RevisionWorkbench } from "./RevisionWorkbench";
import { type ComicPageVersion, clearLocalSession, consumeLocalSession } from "./api";

afterEach(() => {
  cleanup();
  clearLocalSession();
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
});

it("keeps revision execution behind two confirmations and restores history locally", async () => {
  window.history.replaceState(null, "", "/#session=session-test&csrf=csrf-test");
  consumeLocalSession();
  const current = pageVersion(2, true);
  const old = pageVersion(1, false);
  const fetchMock = vi.fn((path: RequestInfo | URL, init?: RequestInit) => {
    const url = String(path);
    const method = init?.method ?? "GET";
    if (url.endsWith("/pages/page-1/versions") && method === "GET") {
      return Promise.resolve(jsonResponse([current, old]));
    }
    if (url.endsWith("/versions/page-version-1/activate") && method === "POST") {
      return Promise.resolve(jsonResponse({ ...old, is_current: true, page_revision: 4 }));
    }
    if (url.endsWith("/generation/revisions/estimate") && method === "POST") {
      return Promise.resolve(jsonResponse(revisionEstimate));
    }
    if (url.endsWith("/generation/revisions/jobs") && method === "POST") {
      return Promise.resolve(jsonResponse(generationJob("queued", 1), 201));
    }
    if (url.endsWith("/generation/jobs/job-1/start") && method === "POST") {
      return Promise.resolve(jsonResponse(generationJob("running", 2)));
    }
    return Promise.reject(new Error(`unexpected request: ${method} ${url}`));
  });
  vi.stubGlobal("fetch", fetchMock);
  const onPageChange = vi.fn();

  render(
    <RevisionWorkbench
      projectId="project-1"
      page={current}
      onPageChange={onPageChange}
      onError={vi.fn()}
    />,
  );

  fireEvent.click(await screen.findByRole("button", { name: "恢复为当前页面（仅本地）" }));
  expect(await screen.findByText(/没有发出 NovelAI 请求/)).toBeInTheDocument();
  expect(onPageChange).toHaveBeenCalledTimes(1);
  expect(fetchMock.mock.calls.some(([path]) => String(path).includes("/execute"))).toBe(false);

  fireEvent.click(screen.getByRole("button", { name: "预检目标与保守成本（不出图）" }));
  expect(await screen.findByText(/保守预留 ≤ 10 Anlas/)).toBeInTheDocument();
  const create = screen.getByRole("button", { name: "创建有界 revision 队列（不出图）" });
  expect(create).toBeDisabled();
  fireEvent.click(screen.getByLabelText("我已核对目标、父版本、蒙版和成本上限"));
  fireEvent.click(create);
  expect(await screen.findByText(/尚未启动或发出图像请求/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "由我启动 revision 队列" }));
  expect(await screen.findByText(/仍需第二次明确确认/)).toBeInTheDocument();
  const execute = screen.getByRole("button", {
    name: "执行冻结 revision 队列（将调用 NovelAI）",
  });
  expect(execute).toBeDisabled();
  expect(fetchMock.mock.calls.some(([path]) => String(path).includes("/execute"))).toBe(false);
  fireEvent.click(screen.getByLabelText("我确认现在执行，这可能产生 NovelAI 费用"));
  expect(execute).toBeEnabled();

  const writeCalls = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
  await waitFor(() => expect(writeCalls.length).toBe(4));
  expect(
    writeCalls.every(([, init]) => new Headers(init?.headers).has("X-CSRF-Token")),
  ).toBe(true);
});

const revisionEstimate = {
  operation: "panel_reroll",
  project_id: "project-1",
  chapter_id: "chapter-1",
  page_id: "page-1",
  page_version_id: "page-version-2",
  page_number: 1,
  provider_model_id: "nai-diffusion-4-5-full",
  panel_count: 1,
  estimated_calls: 1,
  estimated_cost_upper_anlas: 10,
  cost_basis: "user_confirmed_per_panel_ceiling",
  cost_notice: "保守预留",
  plan_fingerprint: "f".repeat(64),
  targets: [
    {
      ordinal: 1,
      page_id: "page-1",
      page_number: 1,
      panel_id: "panel-1",
      panel_order: 1,
      parent_asset_version_id: "asset-version-2",
      mask_asset_id: null,
      edit_prompt: null,
      inpaint_strength: null,
      cost_ceiling_anlas: 10,
    },
  ],
  external_request_created: false,
} as const;

function generationJob(status: "queued" | "running", revision: number) {
  return {
    job_id: "job-1",
    project_id: "project-1",
    chapter_id: "chapter-1",
    storyboard_version_id: "storyboard-version-1",
    character_bible_version_id: "character-version-1",
    style_bible_version_id: "style-version-1",
    novelai_config_revision: 1,
    provider_model_id: "nai-diffusion-4-5-full",
    operation_kind: "panel_reroll",
    target_page_id: "page-1",
    target_page_version_id: "page-version-2",
    result_page_version_id: null,
    mapping_version: "mapping-1",
    contract_sha256: "c".repeat(64),
    credential_profile_id: "novelai",
    timeout_seconds: 30,
    plan_fingerprint: "f".repeat(64),
    status,
    user_action_id: "action-1",
    page_count: 1,
    panel_count: 1,
    max_calls: 1,
    max_cost_anlas: 10,
    estimated_cost_upper_anlas: 10,
    cost_basis: "user_confirmed_per_panel_ceiling",
    calls_started: 0,
    calls_completed: 0,
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
  };
}

function pageVersion(version: number, isCurrent: boolean): ComicPageVersion {
  return {
    page_id: "page-1",
    project_id: "project-1",
    chapter_id: "chapter-1",
    page_number: 1,
    page_revision: 3,
    page_version_id: `page-version-${version}`,
    version,
    parent_page_version_id: version === 1 ? null : `page-version-${version - 1}`,
    storyboard_version_id: "storyboard-version-1",
    document_sha256: "d".repeat(64),
    render_sha256: "r".repeat(64),
    renderer_version: "renderer-1",
    font_sha256: "a".repeat(64),
    is_current: isCurrent,
    created_at: "2026-08-09 12:00:00",
    source_job_id: version === 2 ? "job-old" : null,
    external_requests_started: 0,
    document: {
      schema_version: "1.0",
      page_id: "page-1",
      page_number: 1,
      width: 2048,
      height: 3072,
      reading_direction: "left_to_right",
      color_mode: "grayscale",
      background_color: "#ffffff",
      language: "zh-Hans",
      template_id: "grid-1",
      storyboard_version_id: "storyboard-version-1",
      panels: [
        {
          panel_id: "panel-1",
          asset_version_id: `asset-version-${version}`,
          frame: { x: 96, y: 96, width: 1856, height: 2748 },
          focal_x: 0.5,
          focal_y: 0.5,
          zoom: 1,
        },
      ],
      text_layers: [],
      show_page_number: true,
    },
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
