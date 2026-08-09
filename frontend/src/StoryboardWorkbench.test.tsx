import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { StoryboardWorkbench } from "./StoryboardWorkbench";
import {
  type ChapterSet,
  type StoryBeatSet,
  type StoryboardDocument,
  type StoryboardVersion,
  type VaultStatus,
  clearLocalSession,
  consumeLocalSession,
} from "./api";

const chapterSet: ChapterSet = {
  source_file_id: "source-1",
  chapter_set_id: "chapter-set-1",
  chapter_set_version: 1,
  chapters: [
    {
      chapter_id: "chapter-1",
      version: 1,
      ordinal: 1,
      title: "第一章 雨夜",
      start_offset: 0,
      end_offset: 30,
      text_sha256: "chapter-hash",
    },
  ],
};

const beatSet: StoryBeatSet = {
  beat_set_id: "beat-set-1",
  beat_set_version: 1,
  chapter_id: "chapter-1",
  beats: [
    {
      beat_id: "beat-1",
      ordinal: 1,
      anchor_id: "anchor-1",
      source_summary: "林夏推开门。",
      source_excerpt: "林夏推开门。",
      start_offset: 0,
      end_offset: 7,
      excerpt_sha256: "excerpt-hash",
      resolution_status: "unresolved",
      omission_reason: null,
    },
  ],
};

const vaultStatus: VaultStatus = {
  configured: true,
  unlocked: true,
  profiles: [
    {
      profile_id: "text-model",
      provider: "openai-compatible",
      label: "文本模型",
      fingerprint: "…alue",
    },
  ],
};

const originalDocument: StoryboardDocument = {
  schema_version: "1.0",
  storyboard_id: "018f0f65-8f2f-7e65-8000-123456789abc",
  chapter_version: 1,
  beat_resolutions: [
    {
      beat_id: "beat-1",
      status: "represented",
      reason: null,
      page_numbers: [1],
    },
  ],
  scenes: [
    {
      scene_id: "018f0f65-8f2f-7e65-8000-123456789abf",
      order: 1,
      title: "进入房间",
      location: "旧屋房间",
      time_of_day: "夜晚",
      summary: "林夏推门进入房间。",
      beat_ids: ["beat-1"],
    },
  ],
  pages: [
    {
      page_id: "018f0f65-8f2f-7e65-8000-123456789abd",
      page_number: 1,
      turning_point: "主角进入房间",
      scene_ids: ["018f0f65-8f2f-7e65-8000-123456789abf"],
      panels: [
        {
          panel_id: "018f0f65-8f2f-7e65-8000-123456789abe",
          order: 1,
          purpose: "表现主角推门",
          shot: "medium shot",
          characters: ["林夏"],
          dialogue: [],
          narration: [],
          sfx: ["吱呀"],
          visual_prompt: "black and white manga, no text",
          negative_prompt: "watermark, text, logo",
          source_anchor_ids: ["anchor-1"],
        },
      ],
    },
  ],
};

afterEach(() => {
  clearLocalSession();
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
});

it("generates, revises and approves a structured storyboard through user actions", async () => {
  window.history.replaceState(null, "", "/#session=session-test&csrf=csrf-test");
  consumeLocalSession();
  let revisedDocument = originalDocument;
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    if (path.endsWith("/adaptation/text-model") && method === "GET") {
      return Promise.resolve(
        jsonResponse({
          project_id: "project-1",
          provider: "openai-compatible",
          base_url: "https://models.example.test/v1",
          endpoint_host: "models.example.test",
          model: "unit-model",
          credential_profile_id: "text-model",
          credential_fingerprint: "…alue",
          credential_status: "available",
          timeout_seconds: 60,
          temperature: 0.2,
          revision: 1,
        }),
      );
    }
    if (path.endsWith("/story-beats") && method === "GET") {
      return Promise.resolve(jsonResponse(beatSet));
    }
    if (path.includes("/storyboards/current?") && method === "GET") {
      return Promise.resolve(jsonResponse({ error: { message: "尚无分镜" } }, 404));
    }
    if (path.endsWith("/storyboards/generate") && method === "POST") {
      return Promise.resolve(jsonResponse(storyboardVersion(1, "draft", originalDocument), 201));
    }
    if (path.endsWith("/revisions") && method === "POST") {
      const body = JSON.parse(String(init?.body)) as { document: StoryboardDocument };
      revisedDocument = body.document;
      return Promise.resolve(jsonResponse(storyboardVersion(2, "draft", revisedDocument), 201));
    }
    if (path.endsWith("/approve") && method === "POST") {
      return Promise.resolve(jsonResponse(storyboardVersion(2, "approved", revisedDocument)));
    }
    return Promise.reject(new Error(`unexpected request: ${method} ${path}`));
  });
  vi.stubGlobal("fetch", fetchMock);
  const onError = vi.fn();

  render(
    <StoryboardWorkbench
      projectId="project-1"
      chapterSet={chapterSet}
      vaultStatus={vaultStatus}
      onError={onError}
      refreshKey={0}
    />,
  );

  const generateButton = screen.getByRole("button", { name: "生成结构化分镜" });
  await screen.findByText(/当前：models.example.test/);
  fireEvent.click(screen.getByRole("checkbox"));
  await waitFor(() => expect(generateButton).not.toBeDisabled());
  fireEvent.click(generateButton);

  expect(await screen.findByText("分镜待审批")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("场景摘要"), {
    target: { value: "林夏推门进入房间，并发现异常。" },
  });
  fireEvent.change(screen.getByLabelText("本页叙事功能"), {
    target: { value: "主角发现关键线索" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存为新版本" }));

  expect(await screen.findByText("分镜版本 2")).toBeInTheDocument();
  expect(revisedDocument.scenes[0].summary).toBe("林夏推门进入房间，并发现异常。");
  const approveButton = screen.getByRole("button", { name: "审批当前分镜" });
  await waitFor(() => expect(approveButton).not.toBeDisabled());
  fireEvent.click(approveButton);

  expect(await screen.findByText("分镜已审批")).toBeInTheDocument();
  expect(onError).not.toHaveBeenCalled();
  const generationCall = fetchMock.mock.calls.find(([path]) =>
    String(path).endsWith("/storyboards/generate"),
  );
  expect(generationCall).toBeDefined();
  const headers = new Headers(generationCall?.[1]?.headers);
  expect(headers.get("X-Manga-Maker-Session")).toBe("session-test");
  expect(headers.get("X-CSRF-Token")).toBe("csrf-test");
});

function storyboardVersion(
  version: number,
  status: StoryboardVersion["approval_status"],
  document: StoryboardDocument,
): StoryboardVersion {
  return {
    storyboard_id: document.storyboard_id,
    storyboard_version_id: `storyboard-version-${version}`,
    version,
    chapter_id: "chapter-1",
    chapter_version: 1,
    beat_set_id: "beat-set-1",
    page_budget: 8,
    source_fingerprint: "f".repeat(64),
    document,
    provenance: { change_type: version === 1 ? "model_generation" : "manual_edit" },
    approval_status: status,
    approval_hash: status === "approved" ? "a".repeat(64) : null,
    approved_at: status === "approved" ? "2026-08-09T00:00:00Z" : null,
    unresolved_count: 0,
    is_current: true,
    created_at: "2026-08-09T00:00:00Z",
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
