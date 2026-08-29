import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { PromptWorkbench } from "./PromptWorkbench";
import {
  type ChapterSet,
  type PromptingWorkflow,
  clearLocalSession,
  consumeLocalSession,
} from "./api";
import type { PromptInspectorClient } from "./features/prompting";

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
      inspectorClient={inspectorClient()}
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
  expect(await screen.findByRole("heading", { name: /Prompt Inspector/ })).toBeInTheDocument();
  const inspector = screen.getByRole("region", { name: "Prompt Inspector" });
  expect(within(inspector).queryByDisplayValue("1girl, shoulder-length black hair")).toBeNull();
  expect(screen.getByText("领域 ↔ 载荷一致")).toBeInTheDocument();
  expect(screen.getByText(/1 格 · 每格候选 1 · 预计调用 1 次/)).toBeInTheDocument();
  expect(screen.getByLabelText("角色 1 动作")).toHaveValue("looks toward the doorway");
  expect(screen.getAllByText(/shoulder-length black hair/).length).toBeGreaterThanOrEqual(2);
  const approve = screen.getByRole("button", { name: "审批全部 PromptPackage" });
  await waitFor(() => expect(approve).toBeEnabled());
  fireEvent.change(screen.getByLabelText("角色 1 动作"), {
    target: { value: "turns toward the rain" },
  });
  expect(approve).toBeDisabled();
  expect(screen.getByText("本地修改待保存，禁止审批")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("角色 1 动作"), {
    target: { value: "looks toward the doorway" },
  });
  await waitFor(() => expect(approve).toBeEnabled());
  fireEvent.click(approve);

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
    snapshot_sha256: "a".repeat(64),
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
      schema_version: "1.2",
      storyboard_version_id: "018f0f65-8f2f-7e65-8000-123456789a02",
      character_bible_version_id: "018f0f65-8f2f-7e65-8000-123456789a03",
      style_bible_version_id: "018f0f65-8f2f-7e65-8000-123456789a04",
      character_tag_bundle_version_id: "018f0f65-8f2f-7e65-8000-123456789a01",
      text_model_profile_id: "project-1",
      text_model_config_revision: 1,
      text_model_name: "unit-model",
      prompt_template_version: "novelai-panel-prompts-1.0",
      provider_model_id: "nai-diffusion-5-full",
      layout_snapshot_sha256: "1".repeat(64),
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
          structured_package: structuredPackage(),
        },
      ],
    },
    provenance: {},
    approval_status: approved ? "approved" : "draft",
    approval_hash: approved ? "e".repeat(64) : null,
    snapshot_sha256: "f".repeat(64),
    approved_at: approved ? "2026-08-09T00:00:00Z" : null,
    is_current: true,
    created_at: "2026-08-09T00:00:00Z",
  };
}

function structuredPackage() {
  const characterId = "018f0f65-8f2f-7e65-8000-123456789a06";
  const tagSetId = "018f0f65-8f2f-7e65-8000-123456789a05";
  return {
    schema_version: "2.0" as const,
    prompt_package_id: "018f0f65-8f2f-7e65-8000-123456789a08",
    version: 1,
    panel_id: "018f0f65-8f2f-7e65-8000-123456789a09",
    text_model_source: {
      text_model_profile_id: "project-1",
      profile_version: 1,
      model_name: "unit-model",
      prompt_template_version: "novelai-panel-prompts-1.0",
      text_stage_run_id: "018f0f65-8f2f-7e65-8000-123456789a10",
    },
    prompt_plan: {
      schema_version: "2.0" as const,
      prompt_plan_id: "018f0f65-8f2f-7e65-8000-123456789a11",
      version: 1,
      panel_id: "018f0f65-8f2f-7e65-8000-123456789a09",
      base: {
        positive_tags: ["black and white manga"],
        negative_tags: ["bad anatomy"],
        relationship_action: null,
      },
      characters: [
        {
          character_id: characterId,
          character_tag_set_version_id: tagSetId,
          fixed_tags: ["1girl", "shoulder-length black hair"],
          fixed_tags_sha256: "a".repeat(64),
          variable_positive_tags: ["alert expression"],
          negative_tags: ["long hair"],
          action: "looks toward the doorway",
          order: 0,
          center: { x: 0.5, y: 0.56 },
        },
      ],
      style_tags: ["crisp ink line art"],
      continuity_tags: ["rainy night"],
      layout_constraints: {
        page_layout_draft_id: "018f0f65-8f2f-7e65-8000-123456789a12",
        frame_id: "018f0f65-8f2f-7e65-8000-123456789a13",
      },
      content_sha256: "2".repeat(64),
    },
    prompt_plan_sha256: "2".repeat(64),
    content_sha256: "3".repeat(64),
    approved_content_sha256: null,
  };
}

function promptInspector() {
  const structured = structuredPackage();
  const character = structured.prompt_plan.characters[0];
  return {
    contract_version: "1.0",
    prompt_bundle_version_id: "018f0f65-8f2f-7e65-8000-123456789a07",
    snapshot_sha256: "f".repeat(64),
    panels: [
      {
        panel_id: structured.panel_id,
        prompt_package_id: structured.prompt_package_id,
        prompt_package_sha256: structured.content_sha256,
        prompt_plan: structured.prompt_plan,
        prompt_plan_sha256: structured.prompt_plan_sha256,
        provider_execution_spec: {
          action: "generate",
          base_positive_tags: structured.prompt_plan.base.positive_tags,
          base_negative_tags: structured.prompt_plan.base.negative_tags,
          character_captions: [
            {
              character_id: character.character_id,
              order: character.order,
              center: character.center,
              positive_tags: [...character.fixed_tags, ...character.variable_positive_tags],
              negative_tags: character.negative_tags,
            },
          ],
        },
        provider_execution_spec_sha256: "4".repeat(64),
        provider_payload_sha256: "5".repeat(64),
        provider_payload: {
          action: "generate",
          model: "nai-diffusion-5-full",
          parameters: { seed: 42 },
        },
        mapping_version: "unit-mapping",
        model_id: "nai-diffusion-5-full",
      },
    ],
    impact: { impacts: [], requires_reestimate: false },
    generation_summary: {
      panel_count: 1,
      candidate_count_per_panel: 1,
      estimated_calls: 1,
      estimated_cost_upper_anlas: null,
      cost_status: "requires_generation_estimate",
      cost_notice: "Prompt 审批不产生费用；保守成本上限在生成预估中确认。",
    },
    redaction: {
      credentials_included: false,
      headers_included: false,
      source_chapter_included: false,
      base64_included: false,
    },
    external_requests_started: 0,
  };
}

function inspectorClient(): PromptInspectorClient {
  return {
    inspect: vi.fn().mockResolvedValue(promptInspector()),
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
