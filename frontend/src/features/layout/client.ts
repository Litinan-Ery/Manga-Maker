import { ContractApiError } from "../../generated/api/contractError";
import type { FrameSpec, PageLayoutDraft } from "../../generated/api/v03Types";

export interface StoryboardPanelSummary {
  panel_id: string;
  order: number;
  purpose: string;
  characters: string[];
}

export interface StoryboardPageSummary {
  page_id: string;
  page_number: number;
  turning_point: string;
  panels: StoryboardPanelSummary[];
}

export interface ApprovedStoryboardSummary {
  storyboard_version_id: string;
  version: number;
  chapter_id: string;
  approval_status: "draft" | "approved" | "stale";
  pages: StoryboardPageSummary[];
}

export interface StoryboardRef {
  contract_version: "1.0";
  storyboard_id: string;
  storyboard_version_id: string;
  version: number;
  content_sha256: string;
  approved: boolean;
}

export interface LayoutVersionSnapshot {
  contract_version: "1.0";
  page_layout_draft_version_id: string;
  project_id: string;
  chapter_id: string;
  revision: number;
  origin: "planned" | "imported_legacy";
  storyboard: StoryboardRef | null;
  approved_panel_ids: string[];
  legacy_page_version_id: string | null;
  layout: PageLayoutDraft;
  snapshot_sha256: string;
  created_at: string;
  external_requests_started: 0;
}

export interface DimensionCandidate {
  candidate_key: string;
  dimensions: { width: number; height: number };
  pixel_limit: number;
  cost_rank: number;
}

export interface DimensionCapabilitySet {
  contract_version: "1.0";
  capability_snapshot_id: string;
  mapping_version: string;
  candidates: DimensionCandidate[];
  content_sha256: string;
}

export interface DimensionCandidateScore {
  candidate_key: string;
  dimensions: { width: number; height: number };
  aspect_ratio_error: number;
  crop_safe_risk: number;
  expected_crop_ratio: number;
  target_pixel_delta: number;
  cost_rank: number;
  crop_safe_satisfied: boolean;
}

export interface DimensionSelection {
  contract_version: "1.0";
  status: "selected";
  dimension_selection_id: string;
  page_layout_draft_version_id: string;
  frame_id: string;
  capability_snapshot_id: string;
  capability_snapshot_sha256: string;
  rule_version: string;
  selected_candidate_key: string;
  selected: { width: number; height: number };
  target_aspect_ratio: number;
  expected_crop_ratio: number;
  ranked_candidates: DimensionCandidateScore[];
  selection_reason: string;
  content_sha256: string;
}

export interface DimensionSelectionFailure {
  contract_version: "1.0";
  status: "unsatisfied";
  page_layout_draft_version_id: string;
  frame_id: string;
  capability_snapshot_id: string;
  capability_snapshot_sha256: string;
  rule_version: string;
  target_aspect_ratio: number;
  failure_reason: "no_candidate_preserves_crop_safe_rect";
  ranked_candidates: DimensionCandidateScore[];
  content_sha256: string;
}

export type DimensionOutcome = DimensionSelection | DimensionSelectionFailure;

export interface LayoutValidationFinding {
  code: string;
  path: string;
  message: string;
  frame_ids: string[];
}

export interface LayoutValidationResult {
  contract_version: "1.0";
  rule_version: string;
  layout_content_sha256: string;
  valid: boolean;
  findings: LayoutValidationFinding[];
  external_requests_started: 0;
}

export interface LayoutApprovalValidation {
  contract_version: "1.0";
  page_layout_draft_version_id: string;
  layout: LayoutValidationResult;
  dimension_outcomes: DimensionOutcome[];
  valid: boolean;
  failure_paths: string[];
  external_requests_started: 0;
}

export interface LayoutApproval {
  contract_version: "1.0";
  approval_id: string;
  project_id: string;
  page_layout_draft_id: string;
  page_layout_draft_version_id: string;
  layout_version: number;
  layout_content_sha256: string;
  storyboard: StoryboardRef;
  dimension_selection_sha256s: string[];
  approval_sha256: string;
  state: "active" | "stale";
  stale_reasons: string[];
  created_at: string;
  external_requests_started: 0;
}

export interface ArtifactImpact {
  artifact: {
    artifact_type: string;
    artifact_id: string;
    version: number;
    content_sha256: string;
    is_stale: boolean;
  };
  path: Array<{
    artifact: { artifact_type: string; artifact_id: string; version: number };
    via_edge_type: string | null;
  }>;
  marked_stale: boolean;
}

export interface LayoutImpact {
  contract_version: "1.0";
  impacts: ArtifactImpact[];
  external_requests_started: 0;
}

export interface LayoutClient {
  getApprovedStoryboard(projectId: string, chapterId: string): Promise<ApprovedStoryboardSummary>;
  listCurrent(projectId: string, chapterId: string): Promise<LayoutVersionSnapshot[]>;
  getDraft(projectId: string, layoutId: string): Promise<PageLayoutDraft>;
  createDraft(
    projectId: string,
    chapterId: string,
    storyboardVersionId: string,
    draft: PageLayoutDraft,
    idempotencyKey: string,
  ): Promise<LayoutVersionSnapshot>;
  saveDraft(
    projectId: string,
    parentVersionId: string,
    storyboardVersionId: string,
    draft: PageLayoutDraft,
    expectedVersion: number,
    idempotencyKey: string,
  ): Promise<LayoutVersionSnapshot>;
  validate(
    projectId: string,
    snapshot: LayoutVersionSnapshot,
    capabilities: DimensionCapabilitySet,
  ): Promise<LayoutApprovalValidation>;
  approve(
    projectId: string,
    snapshot: LayoutVersionSnapshot,
    capabilities: DimensionCapabilitySet,
    selections: DimensionSelection[],
    idempotencyKey: string,
  ): Promise<LayoutApproval>;
  getApproval(projectId: string, versionId: string): Promise<LayoutApproval | null>;
  getImpact(projectId: string, snapshot: LayoutVersionSnapshot): Promise<LayoutImpact>;
}

export interface LayoutHttpSession {
  session: string;
  csrf: string;
}

export function createLayoutHttpClient(session: LayoutHttpSession): LayoutClient {
  return {
    async getApprovedStoryboard(projectId, chapterId) {
      const payload = await request<{
        storyboard_version_id: string;
        version: number;
        chapter_id: string;
        approval_status: ApprovedStoryboardSummary["approval_status"];
        document: { pages: StoryboardPageSummary[] };
      }>(
        `/api/v1/projects/${projectId}/adaptation/storyboards/current?chapter_id=${encodeURIComponent(chapterId)}`,
        {},
        session,
      );
      return {
        storyboard_version_id: payload.storyboard_version_id,
        version: payload.version,
        chapter_id: payload.chapter_id,
        approval_status: payload.approval_status,
        pages: payload.document.pages,
      };
    },
    async listCurrent(projectId, chapterId) {
      return request(
        `/api/v1/projects/${projectId}/layouts?chapter_id=${encodeURIComponent(chapterId)}`,
        {},
        session,
      );
    },
    async getDraft(projectId, layoutId) {
      const snapshot = await request<LayoutVersionSnapshot>(
        `/api/v1/projects/${projectId}/layouts/drafts/${layoutId}`,
        {},
        session,
      );
      return clone(snapshot.layout);
    },
    createDraft(projectId, chapterId, storyboardVersionId, draft, idempotencyKey) {
      return request(
        `/api/v1/projects/${projectId}/layouts/drafts`,
        command({ chapter_id: chapterId, storyboard_version_id: storyboardVersionId, draft }, idempotencyKey),
        session,
      );
    },
    saveDraft(
      projectId,
      parentVersionId,
      storyboardVersionId,
      draft,
      expectedVersion,
      idempotencyKey,
    ) {
      return request(
        `/api/v1/projects/${projectId}/layouts/${parentVersionId}/revisions`,
        command(
          { expected_revision: expectedVersion, storyboard_version_id: storyboardVersionId, draft },
          idempotencyKey,
        ),
        session,
      );
    },
    validate(projectId, snapshot, capabilities) {
      return request(
        `/api/v1/projects/${projectId}/layouts/${snapshot.page_layout_draft_version_id}/validate`,
        jsonPost({
          expected_revision: snapshot.revision,
          layout_content_sha256: snapshot.layout.content_sha256,
          storyboard_version_id: requiredStoryboard(snapshot).storyboard_version_id,
          dimension_capabilities: capabilities,
          target_pixels: 1_048_576,
          max_crop_safe_risk: 0.02,
        }),
        session,
      );
    },
    approve(projectId, snapshot, capabilities, selections, idempotencyKey) {
      return request(
        `/api/v1/projects/${projectId}/layouts/${snapshot.page_layout_draft_version_id}/approve`,
        command(
          {
            expected_revision: snapshot.revision,
            layout_content_sha256: snapshot.layout.content_sha256,
            storyboard_version_id: requiredStoryboard(snapshot).storyboard_version_id,
            dimension_capabilities: capabilities,
            target_pixels: 1_048_576,
            max_crop_safe_risk: 0.02,
            dimension_selections: selections,
          },
          idempotencyKey,
        ),
        session,
      );
    },
    async getApproval(projectId, versionId) {
      return request(
        `/api/v1/projects/${projectId}/layouts/${versionId}/approval`,
        {},
        session,
      );
    },
    getImpact(projectId, snapshot) {
      return request(
        `/api/v1/projects/${projectId}/layouts/${snapshot.page_layout_draft_version_id}/impact?layout_content_sha256=${snapshot.layout.content_sha256}`,
        {},
        session,
      );
    },
  };
}

function command(body: unknown, idempotencyKey: string): RequestInit {
  const init = jsonPost(body);
  const headers = new Headers(init.headers);
  headers.set("Idempotency-Key", idempotencyKey);
  return { ...init, headers };
}

function jsonPost(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

async function request<T>(path: string, init: RequestInit, session: LayoutHttpSession): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  headers.set("X-Manga-Maker-Session", session.session);
  headers.set("X-CSRF-Token", session.csrf);
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    const payload = await safePayload(response);
    const detail = payload.detail;
    const code =
      typeof detail === "object" && detail && "code" in detail
        ? String((detail as { code: unknown }).code)
        : `HTTP_${response.status}`;
    const message =
      typeof detail === "string"
        ? detail
        : typeof detail === "object" && detail && "current_revision" in detail
          ? `版式版本冲突；后端当前 revision ${(detail as { current_revision: unknown }).current_revision}。`
          : "版式操作未完成。";
    throw new ContractApiError(response.status, {
      error: {
        code,
        message,
        details: typeof detail === "object" && detail ? (detail as Record<string, unknown>) : undefined,
      },
    });
  }
  return (await response.json()) as T;
}

async function safePayload(response: Response): Promise<{ detail?: unknown }> {
  try {
    return (await response.json()) as { detail?: unknown };
  } catch {
    return {};
  }
}

function requiredStoryboard(snapshot: LayoutVersionSnapshot): StoryboardRef {
  if (!snapshot.storyboard) throw new Error("旧版导入草稿尚未绑定已批准分镜。");
  return snapshot.storyboard;
}

export function clone<T>(value: T): T {
  return structuredClone(value);
}

export function leafFrames(draft: PageLayoutDraft): FrameSpec[] {
  const parents = new Set(draft.frames.map((frame) => frame.parent_frame_id).filter(Boolean));
  return draft.frames
    .filter((frame) => !parents.has(frame.frame_id))
    .sort((first, second) => (first.order ?? 0) - (second.order ?? 0));
}
