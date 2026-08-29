import { ContractApiError } from "../../generated/api/contractError";
import type { PageLayoutDraft } from "../../generated/api/v03Types";
import type {
  ApprovedStoryboardSummary,
  DimensionCapabilitySet,
  DimensionSelection,
  LayoutApproval,
  LayoutApprovalValidation,
  LayoutClient,
  LayoutVersionSnapshot,
} from "./client";
import { clone, leafFrames } from "./client";

export interface LayoutFixtureOptions {
  getError?: "not_found";
  saveError?: "validation" | "revision_conflict";
  initialSnapshot?: boolean;
  initialApproval?: "active" | "stale";
}

export function createLayoutFixtureClient(
  fixture: PageLayoutDraft,
  options: LayoutFixtureOptions = {},
): LayoutClient {
  let current = clone(fixture);
  let snapshot: LayoutVersionSnapshot | null = options.initialSnapshot
    ? makeSnapshot(current, storyboardFor(fixture), fixture.version)
    : null;
  let approval: LayoutApproval | null = null;
  const storyboard = storyboardFor(fixture);
  if (snapshot && options.initialApproval) {
    approval = makeApproval(snapshot, options.initialApproval);
  }

  function requireSnapshot(): LayoutVersionSnapshot {
    if (!snapshot) {
      snapshot = makeSnapshot(current, storyboard, current.version);
    }
    return clone(snapshot);
  }

  return {
    async getApprovedStoryboard() {
      return clone(storyboard);
    },
    async listCurrent() {
      return snapshot ? [clone(snapshot)] : [];
    },
    async getDraft(_projectId, layoutId) {
      if (
        options.getError === "not_found" ||
        layoutId !== current.page_layout_draft_id
      ) {
        throw apiError(404, "LAYOUT_NOT_FOUND", "没有找到版式草稿。");
      }
      return clone(current);
    },
    async createDraft(_projectId, _chapterId, _storyboardVersionId, draft) {
      current = clone(draft);
      snapshot = makeSnapshot(current, storyboard, 1);
      return clone(snapshot);
    },
    async saveDraft(
      _projectId,
      _parentVersionId,
      _storyboardVersionId,
      draft,
      expectedVersion,
    ) {
      if (options.saveError === "revision_conflict" || expectedVersion !== requireSnapshot().revision) {
        throw apiError(409, "LAYOUT_REVISION_CONFLICT", "版式已被修改，请刷新后重试。", {
          current_revision: requireSnapshot().revision + 1,
        });
      }
      if (options.saveError === "validation") {
        throw apiError(422, "LAYOUT_INVALID", "版式没有通过本地校验。");
      }
      current = { ...clone(draft), version: expectedVersion + 1 };
      snapshot = makeSnapshot(current, storyboard, current.version);
      if (approval) approval = { ...approval, state: "stale", stale_reasons: ["layout_version_superseded"] };
      return clone(snapshot);
    },
    async validate(_projectId, inputSnapshot, capabilities) {
      return validation(inputSnapshot, capabilities);
    },
    async approve(_projectId, inputSnapshot, capabilities, selections) {
      const checked = validation(inputSnapshot, capabilities);
      if (!checked.valid || selections.length !== checked.dimension_outcomes.length) {
        throw apiError(409, "LAYOUT_APPROVAL_INVALID", "版式仍有未解决问题。");
      }
      approval = makeApproval(inputSnapshot, "active", selections.map((selection) => selection.content_sha256));
      return clone(approval);
    },
    async getApproval() {
      return clone(approval);
    },
    async getImpact() {
      return {
        contract_version: "1.0",
        impacts: [
          impact("prompt_plan", "prompt-1"),
          impact("generation_spec", "spec-1"),
          impact("review_decision", "review-1"),
          impact("page_approval", "approval-1"),
        ],
        external_requests_started: 0,
      };
    },
  };
}

function storyboardFor(fixture: PageLayoutDraft): ApprovedStoryboardSummary {
  return {
    storyboard_version_id: "01900000-0000-7000-8000-000000000500",
    version: 1,
    chapter_id: "01900000-0000-7000-8000-000000000501",
    approval_status: "approved",
    pages: [
      {
        page_id: fixture.page_id,
        page_number: 1,
        page_type: leafFrames(fixture).length < 3 ? "special" : "standard",
        turning_point: "fixture turning point",
        panels: leafFrames(fixture).map((frame, index) => ({
          panel_id: frame.panel_id ?? crypto.randomUUID(),
          order: index + 1,
          purpose: `fixture panel ${index + 1}`,
          characters: [],
        })),
      },
    ],
  };
}

function makeApproval(
  snapshot: LayoutVersionSnapshot,
  state: "active" | "stale",
  dimensionHashes: string[] = ["1".repeat(64), "2".repeat(64)],
): LayoutApproval {
  return {
    contract_version: "1.0",
    approval_id: crypto.randomUUID(),
    project_id: snapshot.project_id,
    page_layout_draft_id: snapshot.layout.page_layout_draft_id,
    page_layout_draft_version_id: snapshot.page_layout_draft_version_id,
    layout_version: snapshot.revision,
    layout_content_sha256: snapshot.layout.content_sha256,
    storyboard: snapshot.storyboard!,
    dimension_selection_sha256s: dimensionHashes,
    approval_sha256: "a".repeat(64),
    state,
    stale_reasons: state === "stale" ? ["layout_version_superseded"] : [],
    created_at: "2026-08-13T12:00:00Z",
    external_requests_started: 0,
  };
}

function makeSnapshot(
  draft: PageLayoutDraft,
  storyboard: ApprovedStoryboardSummary,
  revision: number,
): LayoutVersionSnapshot {
  return {
    contract_version: "1.0",
    page_layout_draft_version_id: crypto.randomUUID(),
    project_id: "01900000-0000-7000-8000-000000000502",
    chapter_id: storyboard.chapter_id,
    revision,
    origin: "planned",
    storyboard: {
      contract_version: "1.0",
      storyboard_id: "01900000-0000-7000-8000-000000000503",
      storyboard_version_id: storyboard.storyboard_version_id,
      version: storyboard.version,
      content_sha256: "b".repeat(64),
      approved: true,
    },
    approved_panel_ids: storyboard.pages[0].panels.map((panel) => panel.panel_id),
    legacy_page_version_id: null,
    layout: { ...clone(draft), version: revision, content_sha256: "c".repeat(64) },
    snapshot_sha256: "d".repeat(64),
    created_at: "2026-08-13T12:00:00Z",
    external_requests_started: 0,
  };
}

function validation(
  snapshot: LayoutVersionSnapshot,
  capabilities: DimensionCapabilitySet,
): LayoutApprovalValidation {
  const outcomes: DimensionSelection[] = leafFrames(snapshot.layout).map((frame, index) => {
    const selected = { width: 1216, height: 832 };
    const candidateKey = "landscape-1216x832";
    const expectedCropRatio = index % 2 ? 0.01 : 0.02;
    return {
      contract_version: "1.0",
      status: "selected",
      dimension_selection_id: crypto.randomUUID(),
      page_layout_draft_version_id: snapshot.page_layout_draft_version_id,
      frame_id: frame.frame_id,
      capability_snapshot_id: capabilities.capability_snapshot_id,
      capability_snapshot_sha256: capabilities.content_sha256,
      rule_version: "dimension-selector-v1",
      selected_candidate_key: candidateKey,
      selected,
      target_aspect_ratio: frame.aspect_ratio,
      expected_crop_ratio: expectedCropRatio,
      ranked_candidates: [
        {
          candidate_key: candidateKey,
          dimensions: selected,
          aspect_ratio_error: 0.01,
          crop_safe_risk: expectedCropRatio,
          expected_crop_ratio: expectedCropRatio,
          target_pixel_delta: 0,
          cost_rank: 1,
          crop_safe_satisfied: true,
        },
      ],
      selection_reason: "fixture stable rank",
      content_sha256: String(index + 1).repeat(64),
    };
  });
  return {
    contract_version: "1.0",
    page_layout_draft_version_id: snapshot.page_layout_draft_version_id,
    layout: {
      contract_version: "1.0",
      rule_version: "layout-validator-v1",
      layout_content_sha256: snapshot.layout.content_sha256,
      valid: true,
      findings: [],
      external_requests_started: 0,
    },
    dimension_outcomes: outcomes,
    valid: true,
    failure_paths: [],
    external_requests_started: 0,
  };
}

function impact(artifactType: string, artifactId: string) {
  return {
    artifact: {
      artifact_type: artifactType,
      artifact_id: artifactId,
      version: 1,
      content_sha256: "e".repeat(64),
      is_stale: false,
    },
    path: [],
    marked_stale: false,
  };
}

function apiError(
  status: number,
  code: string,
  message: string,
  details?: Record<string, unknown>,
) {
  return new ContractApiError(status, { error: { code, message, details } });
}
