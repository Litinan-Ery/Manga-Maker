import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { PromptWorkbench } from "./PromptWorkbench";
import {
  type ChapterSet,
  type PromptingWorkflow,
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

afterEach(() => {
  clearLocalSession();
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
});

it("generates, previews, and approves fixed tags and PromptPackages", async () => {
  window.history.replaceState(null, "", "/#session=session-test&csrf=csrf-test");
  consumeLocalSession();
  let stage = 0;
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    if (path.includes("/prompting?chapter_id=") && method === "GET") {
      return Promise.resolve(jsonResponse(workflow(stage)));
    }
    if (path.endsWith("/character-tags/generate")) {
      stage = 1;
      return Promise.resolve(jsonResponse(workflow(stage).character_tags, 201));
    }
    if (path.includes("/character-tags/") && path.endsWith("/approve")) {
      stage = 2;
      return Promise.resolve(jsonResponse(workflow(stage).character_tags));
    }
    if (path.endsWith("/prompt-bundles/generate")) {
      stage = 3;
      return Promise.resolve(jsonResponse(workflow(stage).prompt_bundle, 201));
    }
    if (path.includes("/prompt-bundles/") && path.endsWith("/approve")) {
      stage = 4;
      return Promise.resolve(jsonResponse(workflow(stage).prompt_bundle));
    }
    return Promise.reject(new Error(`unexpected request: ${method} ${path}`));
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    <PromptWorkbench
      projectId="project-1"
      chapterSet={chapterSet}
      refreshKey={0}
      onError={vi.fn()}
    />,
  );

  const confirmation = await screen.findByRole("checkbox");
  fireEvent.click(confirmation);
  fireEvent.click(screen.getByRole("button", { name: "生成角色固定 tags" }));
  expect(await screen.findByText(/CharacterTagSet · 版本 1/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "审批角色固定 tags" }));
  await waitFor(() => expect(stage).toBe(2));

  fireEvent.click(confirmation);
  fireEvent.click(screen.getByRole("button", { name: "生成逐格 PromptPackage" }));
  expect(await screen.findByText("最终正向 prompt")).toBeInTheDocument();
  expect(screen.getAllByText(/shoulder-length black hair/)).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "审批全部 PromptPackage" }));

  await screen.findByText("逐格 PromptPackage 已审批，可冻结到生成任务。");
  expect(workflow(stage).generation_readiness.ready).toBe(true);
  const writes = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
  expect(writes).toHaveLength(4);
  for (const [, init] of writes) {
    expect(new Headers(init?.headers).get("X-Manga-Maker-Session")).toBe("session-test");
  }
});

function workflow(stage: number): PromptingWorkflow {
  const characterTags = stage >= 1 ? tagVersion(stage >= 2) : null;
  const promptBundle = stage >= 3 ? promptVersion(stage >= 4) : null;
  return {
    project_id: "project-1",
    chapter_id: "chapter-1",
    character_tags: characterTags,
    prompt_bundle: promptBundle,
    generation_readiness: {
      ready: stage >= 4,
      blockers: stage >= 4 ? [] : ["角色固定 tags 或 PromptPackage 尚未批准。"],
      character_tag_bundle_version_id: characterTags?.version_id ?? null,
      prompt_bundle_version_id: promptBundle?.version_id ?? null,
      text_model_config_revision: promptBundle?.document.text_model_config_revision ?? null,
    },
  };
}

function tagVersion(approved: boolean): NonNullable<PromptingWorkflow["character_tags"]> {
  return {
    version_id: "018f0f65-8f2f-7e65-8000-123456789a01",
    version: 1,
    document: {
      schema_version: "1.0",
      storyboard_version_id: "018f0f65-8f2f-7e65-8000-123456789a02",
      character_bible_version_id: "018f0f65-8f2f-7e65-8000-123456789a03",
      style_bible_version_id: "018f0f65-8f2f-7e65-8000-123456789a04",
      tag_sets: [
        {
          tag_set_id: "018f0f65-8f2f-7e65-8000-123456789a05",
          character_id: "018f0f65-8f2f-7e65-8000-123456789a06",
          character_name: "林夏",
          appearance_version: "default",
          fixed_tags: ["1girl", "shoulder-length black hair"],
          negative_tags: ["long hair"],
          rationale: "稳定角色外观",
          fixed_tags_sha256: "a".repeat(64),
        },
      ],
    },
    provenance: {},
    approval_status: approved ? "approved" : "draft",
    approval_hash: approved ? "b".repeat(64) : null,
    approved_at: approved ? "2026-08-09T00:00:00Z" : null,
    is_current: true,
    created_at: "2026-08-09T00:00:00Z",
  };
}

function promptVersion(approved: boolean): NonNullable<PromptingWorkflow["prompt_bundle"]> {
  return {
    version_id: "018f0f65-8f2f-7e65-8000-123456789a07",
    version: 1,
    document: {
      schema_version: "1.0",
      storyboard_version_id: "018f0f65-8f2f-7e65-8000-123456789a02",
      character_bible_version_id: "018f0f65-8f2f-7e65-8000-123456789a03",
      style_bible_version_id: "018f0f65-8f2f-7e65-8000-123456789a04",
      character_tag_bundle_version_id: "018f0f65-8f2f-7e65-8000-123456789a01",
      text_model_profile_id: "project-1",
      text_model_config_revision: 1,
      text_model_name: "unit-model",
      prompt_template_version: "novelai-panel-prompts-1.0",
      provider_model_id: "nai-diffusion-4-5-full",
      packages: [
        {
          prompt_package_id: "018f0f65-8f2f-7e65-8000-123456789a08",
          panel_id: "018f0f65-8f2f-7e65-8000-123456789a09",
          base_visual_tags: ["black and white manga"],
          character_blocks: [
            {
              character_id: "018f0f65-8f2f-7e65-8000-123456789a06",
              tag_set_id: "018f0f65-8f2f-7e65-8000-123456789a05",
              fixed_tags: ["1girl", "shoulder-length black hair"],
              fixed_tags_sha256: "a".repeat(64),
              variable_tags: ["alert expression"],
            },
          ],
          style_tags: ["crisp ink line art"],
          negative_tags: ["bad anatomy"],
          compiled_prompt:
            "black and white manga, 1girl, shoulder-length black hair, alert expression",
          compiled_negative_prompt: "bad anatomy, text, watermark",
          compiled_prompt_sha256: "c".repeat(64),
          compiled_negative_prompt_sha256: "d".repeat(64),
        },
      ],
    },
    provenance: {},
    approval_status: approved ? "approved" : "draft",
    approval_hash: approved ? "e".repeat(64) : null,
    approved_at: approved ? "2026-08-09T00:00:00Z" : null,
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
