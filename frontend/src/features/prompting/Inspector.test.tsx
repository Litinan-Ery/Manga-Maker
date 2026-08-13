import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { PromptInspectorView } from "./Inspector";
import type { PromptInspector } from "./client";

it("keeps fixed tags read-only and marks edited structured fields pending", () => {
  const onCharacterChange = vi.fn();
  const onRelationshipChange = vi.fn();
  const { rerender } = render(
    <PromptInspectorView
      inspector={fixture()}
      draftDirty={false}
      onCharacterChange={onCharacterChange}
      onRelationshipChange={onRelationshipChange}
    />,
  );
  const region = screen.getByRole("region", { name: "Prompt Inspector" });

  expect(within(region).queryByDisplayValue("1girl, black hair")).toBeNull();
  expect(within(region).getByText("领域 ↔ 载荷一致")).toBeInTheDocument();
  expect(within(region).getByText(/1 格 · 每格候选 1 · 预计调用 1 次/)).toBeInTheDocument();

  fireEvent.change(within(region).getByLabelText("角色 1 动作"), {
    target: { value: "turns toward the window" },
  });
  expect(onCharacterChange).toHaveBeenCalledWith("panel-1", "character-1", {
    action: "turns toward the window",
  });
  fireEvent.change(within(region).getByLabelText("面板 panel-1 关系动作"), {
    target: { value: "hands over the key" },
  });
  expect(onRelationshipChange).toHaveBeenCalledWith("panel-1", "hands over the key");

  rerender(
    <PromptInspectorView
      inspector={fixture()}
      draftDirty
      onCharacterChange={onCharacterChange}
      onRelationshipChange={onRelationshipChange}
    />,
  );
  expect(screen.getByText("本地修改待保存，禁止审批")).toBeInTheDocument();
  expect(screen.getAllByText("保存后重新计算")).toHaveLength(3);
});

function fixture(): PromptInspector {
  return {
    contract_version: "1.0",
    prompt_bundle_version_id: "prompt-bundle-1",
    snapshot_sha256: "a".repeat(64),
    panels: [
      {
        panel_id: "panel-1",
        prompt_package_id: "prompt-package-1",
        prompt_package_sha256: "b".repeat(64),
        prompt_plan: {
          schema_version: "2.0",
          prompt_plan_id: "prompt-plan-1",
          version: 1,
          panel_id: "panel-1",
          base: {
            positive_tags: ["manga"],
            negative_tags: ["text"],
            relationship_action: null,
          },
          characters: [
            {
              character_id: "character-1",
              character_tag_set_version_id: "tag-set-1",
              fixed_tags: ["1girl", "black hair"],
              fixed_tags_sha256: "c".repeat(64),
              variable_positive_tags: ["alert"],
              negative_tags: ["long hair"],
              action: "looks at the door",
              order: 0,
              center: { x: 0.5, y: 0.56 },
            },
          ],
          style_tags: ["ink"],
          continuity_tags: ["rain"],
          layout_constraints: { page_layout_draft_id: "layout-1", frame_id: "frame-1" },
          content_sha256: "d".repeat(64),
        },
        prompt_plan_sha256: "d".repeat(64),
        provider_execution_spec: {
          action: "generate",
          base_positive_tags: ["manga"],
          base_negative_tags: ["text"],
          character_captions: [
            {
              character_id: "character-1",
              order: 0,
              center: { x: 0.5, y: 0.56 },
              positive_tags: ["1girl", "black hair", "alert"],
              negative_tags: ["long hair"],
            },
          ],
        },
        provider_execution_spec_sha256: "e".repeat(64),
        provider_payload_sha256: "f".repeat(64),
        provider_payload: { action: "generate", parameters: { seed: 42 } },
        mapping_version: "mapping-1",
        model_id: "nai-diffusion-4-5-full",
      },
    ],
    impact: { impacts: [], requires_reestimate: false },
    generation_summary: {
      panel_count: 1,
      candidate_count_per_panel: 1,
      estimated_calls: 1,
      estimated_cost_upper_anlas: null,
      cost_status: "requires_generation_estimate",
      cost_notice: "生成预估后确认。",
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
