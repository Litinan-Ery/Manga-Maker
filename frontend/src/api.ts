/**
 * @deprecated v0.2 compatibility seam. Do not add v0.3 feature DTOs or endpoints here.
 * Owner: MM-053. Delete only after every legacy root component has moved behind a
 * feature-local client and the full v0.2 frontend fixture suite still passes.
 */

export interface HealthResponse {
  status: "ok" | "degraded";
  version: string;
  environment: string;
  database: "ok" | "error";
  schema_version: number;
  vault_configured: boolean;
  vault_unlocked: boolean;
}

export interface Project {
  project_id: string;
  title: string;
  status: string;
  revision: number;
  workflow_version: "v03" | "legacy_v02";
  created_at: string;
  updated_at: string;
}

export interface EncodingCandidate {
  encoding: string;
  confidence: number;
  preview: string;
  cjk_ratio: number;
}

export interface SourcePreflight {
  preflight_id: string;
  filename: string;
  byte_size: number;
  sha256: string;
  candidates: EncodingCandidate[];
  recommended_encoding: string;
  requires_confirmation: boolean;
}

export interface Chapter {
  chapter_id: string;
  version: number;
  ordinal: number;
  title: string;
  start_offset: number;
  end_offset: number;
  text_sha256: string;
}

export interface ChapterSet {
  source_file_id: string;
  chapter_set_id: string;
  chapter_set_version: number;
  chapters: Chapter[];
}

export interface ChapterText {
  chapter_id: string;
  chapter_version: number;
  title: string;
  start_offset: number;
  end_offset: number;
  text: string;
}

export interface ChapterBoundaryInput {
  title: string;
  start_offset: number;
  end_offset: number;
}

export interface StoryBeat {
  beat_id: string;
  ordinal: number;
  anchor_id: string;
  source_summary: string;
  source_excerpt: string;
  start_offset: number;
  end_offset: number;
  excerpt_sha256: string;
  resolution_status: "represented" | "condensed" | "omitted" | "unresolved";
  omission_reason: string | null;
}

export interface StoryBeatSet {
  beat_set_id: string;
  beat_set_version: number;
  chapter_id: string;
  beats: StoryBeat[];
}

export interface CredentialProfile {
  profile_id: string;
  provider: string;
  label: string;
  fingerprint: string;
}

export interface VaultStatus {
  configured: boolean;
  unlocked: boolean;
  profiles: CredentialProfile[];
}

export interface TextModelConfiguration {
  project_id: string;
  text_model_profile_id: string;
  provider: "openai-compatible";
  remark_name: string | null;
  url: string;
  provider_api_url: string;
  base_url: string;
  endpoint_host: string;
  request_model: string;
  model_name: string;
  model: string;
  credential_profile_id: string;
  credential_fingerprint: string | null;
  credential_status: "available" | "locked" | "missing";
  timeout_seconds: number;
  temperature: number;
  revision: number;
}

export interface NovelAIModelCapability {
  provider_model_id: string;
  label: string;
  inpaint_model_id: string;
  recommended: boolean;
  supports_opus_zero_anlas: boolean;
  supports_precise_reference: boolean;
  supports_multi_character_prompt: boolean;
  supports_vibe_transfer: boolean;
  precise_reference_excludes_vibe_transfer: boolean;
  prompt_token_note: string;
}

export interface NovelAICapabilities {
  source_url: string;
  sha256: string;
  fetched_on: string;
  swagger_version: string;
  api_title: string;
  api_version: string;
  mapping_version: string;
  allowed_paths: Record<string, string>;
  opus_zero_anlas_profile: {
    profile_version: string;
    required_tier: number;
    max_pixels: number;
    max_steps: number;
    n_samples: number;
    requires_single_image: true;
    allows_base_or_reference_image: false;
    default_dimensions: Array<{ width: number; height: number }>;
    official_docs: string[];
  };
  models: NovelAIModelCapability[];
}

export interface NovelAIConfiguration {
  project_id: string;
  provider: "novelai";
  model_label: string;
  provider_model_id: string;
  inpaint_model_id: string;
  credential_profile_id: string;
  credential_fingerprint: string | null;
  credential_status: "available" | "locked" | "missing" | "provider_mismatch";
  timeout_seconds: number;
  contract_sha256: string;
  mapping_version: string;
  revision: number;
  last_connection_status: "ok" | "failed" | null;
  last_connection_at: string | null;
}

export interface GenerationPlanPanel {
  ordinal: number;
  page_id: string;
  page_number: number;
  panel_id: string;
  panel_order: number;
  cost_ceiling_anlas: number;
  prompt_package_id: string;
  compiled_prompt: string;
  compiled_negative_prompt: string;
  compiled_prompt_sha256: string;
}

export interface GenerationEstimate {
  project_id: string;
  chapter_id: string;
  storyboard_version_id: string;
  character_bible_version_id: string;
  style_bible_version_id: string;
  character_tag_bundle_version_id: string;
  prompt_bundle_version_id: string;
  text_model_config_revision: number;
  novelai_config_revision: number;
  provider_model_id: string;
  mapping_version: string;
  contract_sha256: string;
  page_count: number;
  panel_count: number;
  estimated_calls: number;
  estimated_verification_calls: number;
  estimated_external_requests: number;
  per_panel_cost_ceiling_anlas: number;
  estimated_cost_upper_anlas: number;
  billing_mode: "standard" | "opus_zero_anlas";
  cost_basis: "user_confirmed_per_panel_ceiling" | "opus_zero_anlas_official_limits_v1";
  cost_notice: string;
  plan_fingerprint: string;
  panels: GenerationPlanPanel[];
  external_request_created: false;
}

export type RevisionOperation = "panel_reroll" | "page_reroll" | "inpaint";

export interface RevisionTarget {
  ordinal: number;
  page_id: string;
  page_number: number;
  panel_id: string;
  panel_order: number;
  parent_asset_version_id: string;
  mask_asset_id: string | null;
  edit_prompt: string | null;
  inpaint_strength: number | null;
  cost_ceiling_anlas: number;
}

export interface RevisionEstimate {
  operation: RevisionOperation;
  project_id: string;
  chapter_id: string;
  page_id: string;
  page_version_id: string;
  page_number: number;
  provider_model_id: string;
  panel_count: number;
  estimated_calls: number;
  estimated_cost_upper_anlas: number;
  cost_basis: "user_confirmed_per_panel_ceiling";
  cost_notice: string;
  plan_fingerprint: string;
  targets: RevisionTarget[];
  external_request_created: false;
}

export interface MaskAsset {
  mask_asset_id: string;
  project_id: string;
  panel_id: string;
  parent_asset_version_id: string;
  sha256: string;
  width: number;
  height: number;
  selected_pixel_count: number;
  created_at: string;
  external_requests_started: 0;
}

export type GenerationJobStatus =
  | "draft"
  | "awaiting_approval"
  | "queued"
  | "running"
  | "paused"
  | "needs_review"
  | "failed"
  | "completed"
  | "canceled";

export interface GenerationJobItem {
  item_id: string;
  ordinal: number;
  page_id: string;
  page_number: number;
  panel_id: string;
  status: "queued" | "running" | "needs_review" | "failed" | "completed" | "canceled";
  attempt_count: number;
  cost_ceiling_anlas: number;
  recorded_cost_anlas: number;
  active_attempt_id: string | null;
  asset_version_id: string | null;
  operation_kind: "chapter_generate" | RevisionOperation;
  parent_asset_version_id: string | null;
  mask_asset_id: string | null;
  edit_prompt: string | null;
  inpaint_strength: number | null;
  last_error_code: string | null;
  prompt_plan_id: string;
  prompt_plan_version: number;
  prompt_plan_sha256: string;
  prompt_package_sha256: string;
  character_tag_set_refs: Array<Record<string, unknown>>;
  provider_execution_spec_id: string;
  provider_execution_spec_sha256: string;
  provider_payload_sha256: string;
  provider_seed: number;
  candidate_count: number;
  reference_use: Record<string, unknown> | null;
}

export interface GenerationJob {
  job_id: string;
  project_id: string;
  chapter_id: string;
  storyboard_version_id: string;
  character_bible_version_id: string;
  style_bible_version_id: string;
  character_tag_bundle_version_id: string | null;
  prompt_bundle_version_id: string | null;
  text_model_config_revision: number | null;
  novelai_config_revision: number;
  provider_model_id: string;
  operation_kind: "chapter_generate" | RevisionOperation;
  target_page_id: string | null;
  target_page_version_id: string | null;
  result_page_version_id: string | null;
  mapping_version: string;
  contract_sha256: string;
  credential_profile_id: string;
  timeout_seconds: number;
  layout_snapshot_sha256: string;
  plan_fingerprint: string;
  generation_approval_id: string | null;
  generation_approval_sha256: string;
  prompt_approval_hash: string;
  prompt_snapshot_sha256: string;
  candidate_count_per_panel: number;
  quality_rule_version: string;
  status: GenerationJobStatus;
  user_action_id: string;
  page_count: number;
  panel_count: number;
  max_calls: number;
  max_cost_anlas: number;
  estimated_cost_upper_anlas: number;
  cost_basis: string;
  calls_started: number;
  calls_completed: number;
  verification_calls_started: number;
  verification_calls_completed: number;
  max_verification_calls: number;
  max_external_requests: number;
  items_claimed: number;
  allocated_cost_anlas: number;
  recorded_cost_anlas: number;
  unverified_cost_calls: number;
  revision: number;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  paused_at: string | null;
  completed_at: string | null;
  items: GenerationJobItem[];
  external_requests_started: number;
  external_requests_completed: number;
}

export interface GenerationAsset {
  asset_version_id: string;
  project_id: string;
  panel_id: string;
  version: number;
  parent_asset_version_id: string | null;
  job_id: string;
  item_id: string;
  attempt_id: string;
  spec_id: string;
  status: "ready" | "failed";
  image_sha256: string;
  width: number;
  height: number;
  seed: number;
  is_current: boolean;
  created_at: string;
}

export interface PixelRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface PagePanelPlacement {
  panel_id: string;
  asset_version_id: string;
  frame: PixelRect;
  focal_x: number;
  focal_y: number;
  zoom: number;
}

export interface PageTextLayer {
  layer_id: string;
  panel_id: string | null;
  kind: "dialogue" | "narration" | "sfx";
  text: string;
  speaker: string | null;
  bounds: PixelRect;
  font_size: number;
  align: "left" | "center" | "right";
}

export interface PageDocument {
  schema_version: "1.0" | "2.0";
  page_id: string;
  page_number: number;
  width: number;
  height: number;
  reading_direction: "left_to_right" | "right_to_left" | "top_to_bottom";
  color_mode: "grayscale" | "color";
  background_color: string;
  language: "zh-Hans";
  template_id: string;
  storyboard_version_id: string;
  panels: PagePanelPlacement[];
  text_layers: PageTextLayer[];
  show_page_number: boolean;
}

export interface ComicPageVersion {
  page_id: string;
  project_id: string;
  chapter_id: string;
  page_number: number;
  page_revision: number;
  page_version_id: string;
  version: number;
  parent_page_version_id: string | null;
  storyboard_version_id: string;
  document_sha256: string;
  render_sha256: string;
  renderer_version: string;
  font_sha256: string;
  is_current: boolean;
  created_at: string;
  source_job_id?: string | null;
  document: PageDocument;
  external_requests_started: 0;
}

export type ExportFileKind = "engineering_package" | "png" | "pdf" | "cbz";

export interface ExportPageSelection {
  ordinal: number;
  page_id: string;
  page_number: number;
  page_version_id: string;
  version: number;
  render_sha256: string;
  width: number;
  height: number;
  reading_direction: PageDocument["reading_direction"];
  color_mode: PageDocument["color_mode"];
}

export interface ExportPreflight {
  project_id: string;
  project_title: string;
  chapter_id: string;
  chapter_title: string;
  schema_version: "1.0" | "1.1";
  page_count: number;
  pages: ExportPageSelection[];
  blockers: string[];
  warnings: string[];
  plan_fingerprint: string;
  formats: ExportFileKind[];
  external_requests_started: 0;
}

export interface ExportFile {
  export_file_id: string;
  kind: ExportFileKind;
  ordinal: number | null;
  filename: string;
  sha256: string;
  byte_size: number;
}

export interface ExportRevision {
  export_revision_id: string;
  project_id: string;
  chapter_id: string;
  chapter_title: string;
  status: "staging" | "completed" | "failed";
  schema_version: "1.0";
  pages: ExportPageSelection[];
  selection_sha256: string;
  failure_code: string | null;
  secret_scan: {
    status: "passed";
    scanned_files: number;
    scanned_bytes: number;
    credential_count: number;
    matches: 0;
  } | null;
  created_at: string;
  completed_at: string | null;
  files: ExportFile[];
  external_requests_started: 0;
}

export interface ImportPreflight {
  import_preflight_id: string;
  filename: string;
  package_sha256: string;
  source_project_id: string;
  source_title: string;
  schema_version: "1.0" | "1.1" | "1.2" | "1.3" | "1.4";
  file_count: number;
  expanded_bytes: number;
  record_counts: Record<string, number>;
  page_count: number;
  requires_confirmation: true;
  writes_performed: 0;
}

export interface RestoreResult {
  import_preflight_id: string;
  project_id: string;
  source_project_id: string;
  title: string;
  id_conflict_remapped: boolean;
  record_counts: Record<string, number>;
  file_count: number;
  external_requests_started: 0;
}

export interface RecoveryReport {
  recovery_run_id?: string;
  trigger?: "startup" | "manual";
  status: "healthy" | "needs_attention";
  message?: string;
  queue_recovery?: { needs_review: number; paused: number };
  export_recovery?: {
    interrupted_exports_failed_closed: number;
    partial_directories_preserved: number;
  };
  project_recovery?: { interrupted_workspaces_preserved: number };
  book_recovery?: {
    book_plans_paused: number;
    book_plans_needs_review: number;
  };
  integrity?: {
    database_ok: boolean;
    foreign_key_violations: number;
    missing_files: number;
    hash_mismatches: number;
    staging_items: number;
    unregistered_version_files: number;
    forbidden_project_files: number;
    invalid_audit_payloads: number;
    vault_outside_projects: boolean;
    critical_findings: number;
  };
  provider_requests_started?: 0;
  external_requests_started: 0;
}

export interface BookEstimateChapter {
  chapter_id: string;
  ordinal: number;
  title: string;
  storyboard_version_id: string;
  character_bible_version_id: string;
  style_bible_version_id: string;
  generation_plan_fingerprint: string;
  page_count: number;
  panel_count: number;
  estimated_calls: number;
  estimated_verification_calls: number;
  estimated_external_requests: number;
  estimated_cost_upper_anlas: number;
}

export interface BookEstimate {
  schema_version: "1.0";
  project_id: string;
  source_chapter_set_id: string;
  continuity_version_id: string;
  per_panel_cost_ceiling_anlas: number;
  chapters: BookEstimateChapter[];
  chapter_count: number;
  estimated_page_count: number;
  estimated_panel_count: number;
  estimated_calls: number;
  estimated_verification_calls: number;
  estimated_external_requests: number;
  estimated_cost_upper_anlas: number;
  billing_mode: "standard" | "opus_zero_anlas";
  cost_basis: "user_confirmed_per_panel_ceiling" | "opus_zero_anlas_official_limits_v1";
  cost_notice: string;
  plan_fingerprint: string;
  external_request_created: false;
}

export type BookPlanStatus =
  | "awaiting_approval"
  | "ready"
  | "active"
  | "paused"
  | "needs_review"
  | "completed"
  | "canceled";

export interface BookPlanChapter {
  book_chapter_plan_id: string;
  chapter_id: string;
  ordinal: number;
  title: string;
  storyboard_version_id: string;
  character_bible_version_id: string;
  style_bible_version_id: string;
  generation_plan_fingerprint: string;
  page_count: number;
  panel_count: number;
  estimated_cost_upper_anlas: number;
  max_calls: number;
  max_cost_anlas: number;
  status:
    | "awaiting_approval"
    | "approved"
    | "job_created"
    | "running"
    | "paused"
    | "needs_review"
    | "completed"
    | "canceled";
  generation_job_id: string | null;
  generation_job_status: GenerationJobStatus | null;
  calls_started: number;
  calls_completed: number;
  verification_calls_started: number;
  verification_calls_completed: number;
  allocated_cost_anlas: number;
  recorded_cost_anlas: number;
  unverified_cost_calls: number;
  external_requests_started: number;
  external_requests_completed: number;
  retry_count: number;
  approved_at: string | null;
}

export interface BookPlan {
  book_plan_id: string;
  project_id: string;
  version: number;
  source_chapter_set_id: string;
  continuity_version_id: string;
  status: BookPlanStatus;
  per_panel_cost_ceiling_anlas: number;
  estimated_page_count: number;
  estimated_panel_count: number;
  estimated_calls: number;
  estimated_cost_upper_anlas: number;
  max_calls: number;
  max_cost_anlas: number;
  plan_fingerprint: string;
  revision: number;
  is_current: boolean;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  chapters: BookPlanChapter[];
  calls_started: number;
  calls_completed: number;
  verification_calls_started: number;
  verification_calls_completed: number;
  allocated_cost_anlas: number;
  recorded_cost_anlas: number;
  unverified_cost_calls: number;
  external_requests_started: number;
  external_requests_completed: number;
  max_verification_calls: number;
  max_external_requests: number;
}

export type ContinuityKind = "character" | "outfit" | "prop" | "location" | "plot";

export interface ContinuityEntry {
  entry_id: string;
  kind: ContinuityKind;
  stable_key: string;
  name: string;
  status: string;
  attributes: Record<string, string>;
  notes: string;
  source_chapter_ids: string[];
  source_panel_ids: string[];
}

export interface ContinuityDocument {
  schema_version: "1.0";
  continuity_ledger_id: string;
  project_id: string;
  through_chapter_id: string;
  through_chapter_ordinal: number;
  entries: ContinuityEntry[];
  notes: string;
}

export interface ContinuityImpact {
  changed_entries: Array<{
    stable_key: string;
    kind: ContinuityKind;
    name: string;
    change: "added" | "changed" | "removed";
  }>;
  affected_chapters: Array<{
    chapter_id: string;
    ordinal: number;
    title: string;
    panel_count: number;
  }>;
  affected_panel_ids: string[];
  requires_future_review: boolean;
  external_requests_started: 0;
}

export interface ContinuityVersion {
  continuity_ledger_id: string;
  continuity_version_id: string;
  project_id: string;
  version: number;
  parent_version_id: string | null;
  through_chapter_id: string;
  through_chapter_ordinal: number;
  through_chapter_title: string;
  source_storyboard_version_id: string;
  source_character_bible_version_id: string;
  document_sha256: string;
  document: ContinuityDocument;
  provenance: Record<string, unknown>;
  impact: ContinuityImpact;
  approval_status: "draft" | "approved" | "stale";
  approval_hash: string | null;
  approved_at: string | null;
  is_current: boolean;
  created_at: string;
  external_requests_started: 0;
}

export interface PageTemplate {
  template_id: string;
  label: string;
  panel_count: number;
  width: number;
  height: number;
  reading_direction: PageDocument["reading_direction"];
  layout_mode: "page" | "vertical_strip";
  frames: PixelRect[];
}

export interface AssetLibraryItem {
  library_item_id: string;
  project_id: string;
  source_asset_version_id: string;
  source_panel_id: string;
  kind: "character" | "prop" | "location" | "panel";
  name: string;
  tags: string[];
  notes: string;
  status: "active" | "archived";
  revision: number;
  image_sha256: string;
  width: number;
  height: number;
  created_at: string;
  updated_at: string;
  external_requests_started: 0;
}

export interface DialogueLine {
  speaker: string;
  text: string;
}

export interface StoryboardPanel {
  panel_id: string;
  order: number;
  purpose: string;
  shot: string;
  characters: string[];
  dialogue: DialogueLine[];
  narration: string[];
  sfx: string[];
  visual_prompt: string;
  negative_prompt: string;
  source_anchor_ids: string[];
}

export interface StoryboardScene {
  scene_id: string;
  order: number;
  title: string;
  location: string;
  time_of_day: string;
  summary: string;
  beat_ids: string[];
}

export interface StoryboardPage {
  page_id: string;
  page_number: number;
  turning_point: string;
  scene_ids: string[];
  panels: StoryboardPanel[];
}

export interface BeatResolution {
  beat_id: string;
  status: "represented" | "condensed" | "omitted" | "unresolved";
  reason: string | null;
  page_numbers: number[];
}

export interface StoryboardDocument {
  schema_version: "1.0";
  storyboard_id: string;
  chapter_version: number;
  beat_resolutions: BeatResolution[];
  scenes: StoryboardScene[];
  pages: StoryboardPage[];
}

export interface StoryboardVersion {
  storyboard_id: string;
  storyboard_version_id: string;
  version: number;
  chapter_id: string;
  chapter_version: number;
  beat_set_id: string;
  page_budget: number;
  source_fingerprint: string;
  document: StoryboardDocument;
  provenance: Record<string, unknown>;
  approval_status: "draft" | "approved" | "stale";
  approval_hash: string | null;
  approved_at: string | null;
  unresolved_count: number;
  is_current: boolean;
  created_at: string;
}

export interface CharacterProfile {
  character_id: string;
  name: string;
  aliases: string[];
  narrative_role: string;
  age_range: string;
  face_shape: string;
  hair: string;
  body_type: string;
  outfit: string[];
  signature_features: string[];
  variable_features: string[];
  forbidden_changes: string[];
  props: string[];
  relationships: string[];
  expression_range: string[];
  positive_prompt_fragment: string;
  negative_prompt_fragment: string;
  reference_asset_ids: string[];
}

export interface CharacterBibleDocument {
  schema_version: "1.0";
  character_bible_id: string;
  storyboard_version_id: string;
  characters: CharacterProfile[];
  notes: string;
}

export interface StyleBibleDocument {
  schema_version: "1.0";
  style_bible_id: string;
  storyboard_version_id: string;
  summary: string;
  line_art: string;
  screentone: string;
  lighting: string;
  background_density: string;
  whitespace: string;
  camera_language: string;
  positive_prompt_fragment: string;
  negative_prompt_fragment: string;
  prohibited_elements: string[];
  reference_asset_ids: string[];
}

export interface ReferenceAsset {
  reference_asset_id: string;
  bible_kind: "character" | "style";
  character_id: string | null;
  original_filename: string;
  media_type: string;
  byte_size: number;
  width: number;
  height: number;
  sha256: string;
  source_note: string;
  rights_confirmed: boolean;
  created_at: string;
}

export interface BibleVersion<TDocument> {
  kind: "character" | "style";
  bible_id: string;
  version_id: string;
  version: number;
  storyboard_version_id: string;
  document: TDocument;
  provenance: Record<string, unknown>;
  approval_status: "draft" | "approved" | "stale";
  approval_hash: string | null;
  approved_at: string | null;
  approval_issues: string[];
  reference_assets: ReferenceAsset[];
  is_current: boolean;
  created_at: string;
}

export interface BibleBundle {
  project_id: string;
  chapter_id: string;
  character_bible: BibleVersion<CharacterBibleDocument>;
  style_bible: BibleVersion<StyleBibleDocument>;
  generation_readiness: {
    ready: boolean;
    blockers: string[];
    character_bible_version_id: string;
    style_bible_version_id: string;
  };
}

export interface CharacterTagSetDocument {
  tag_set_id: string;
  character_id: string;
  character_name: string;
  appearance_version: string;
  fixed_tags: string[];
  negative_tags: string[];
  rationale: string;
  fixed_tags_sha256: string;
}

export interface CharacterTagBundleDocument {
  schema_version: "1.0";
  storyboard_version_id: string;
  character_bible_version_id: string;
  style_bible_version_id: string;
  tag_sets: CharacterTagSetDocument[];
}

export interface PromptCharacterBlock {
  character_id: string;
  tag_set_id: string;
  fixed_tags: string[];
  fixed_tags_sha256: string;
  variable_tags: string[];
}

export interface StructuredCharacterV2 {
  character_id: string;
  character_tag_set_version_id: string;
  fixed_tags: string[];
  fixed_tags_sha256: string;
  variable_positive_tags: string[];
  negative_tags: string[];
  action: string;
  order: number;
  center: { x: number; y: number };
}

export interface StructuredPromptPackageV2 {
  schema_version: "2.0";
  prompt_package_id: string;
  version: number;
  panel_id: string;
  text_model_source: {
    text_model_profile_id: string;
    profile_version: number;
    model_name: string;
    prompt_template_version: string;
    text_stage_run_id: string;
  };
  prompt_plan: {
    schema_version: "2.0";
    prompt_plan_id: string;
    version: number;
    panel_id: string;
    base: {
      positive_tags: string[];
      negative_tags: string[];
      relationship_action: string | null;
    };
    characters: StructuredCharacterV2[];
    style_tags: string[];
    continuity_tags: string[];
    layout_constraints: Record<string, unknown>;
    content_sha256: string;
  };
  prompt_plan_sha256: string;
  content_sha256: string;
  approved_content_sha256: string | null;
}

export interface PromptPackageDocument {
  prompt_package_id: string;
  panel_id: string;
  base_visual_tags: string[];
  character_blocks: PromptCharacterBlock[];
  style_tags: string[];
  negative_tags: string[];
  compiled_prompt: string;
  compiled_negative_prompt: string;
  compiled_prompt_sha256: string;
  compiled_negative_prompt_sha256: string;
  structured_package?: StructuredPromptPackageV2 | null;
}

export interface PromptBundleDocument {
  schema_version: "1.0" | "1.1" | "1.2";
  storyboard_version_id: string;
  character_bible_version_id: string;
  style_bible_version_id: string;
  character_tag_bundle_version_id: string;
  text_model_profile_id: string;
  text_model_config_revision: number;
  text_model_name: string;
  prompt_template_version: string;
  provider_model_id: string;
  layout_snapshot_sha256?: string | null;
  packages: PromptPackageDocument[];
}

export interface PromptArtifactVersion<TDocument> {
  version_id: string;
  version: number;
  document: TDocument;
  provenance: Record<string, unknown>;
  approval_status: "draft" | "approved" | "stale";
  approval_hash: string | null;
  snapshot_sha256: string;
  approved_at: string | null;
  is_current: boolean;
  created_at: string;
}

export interface PromptingWorkflow {
  project_id: string;
  chapter_id: string;
  character_tags: PromptArtifactVersion<CharacterTagBundleDocument> | null;
  prompt_bundle: PromptArtifactVersion<PromptBundleDocument> | null;
  generation_readiness: {
    ready: boolean;
    blockers: string[];
    structured_prompt_ready?: boolean;
    character_tag_bundle_version_id: string | null;
    prompt_bundle_version_id: string | null;
    text_model_config_revision: number | null;
  };
}

interface ErrorPayload {
  error?: { message?: string; details?: { problem?: string; issues?: string[] } };
  detail?: unknown;
}

let localSessionToken: string | null = null;
let localCsrfToken: string | null = null;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function consumeLocalSession(): boolean {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const session = params.get("session");
  const csrf = params.get("csrf");
  if (session && csrf) {
    localSessionToken = session;
    localCsrfToken = csrf;
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
  }
  return localSessionToken !== null && localCsrfToken !== null;
}

export interface LocalSessionCredentials {
  session: string;
  csrf: string;
}

export function getLocalSessionCredentials(): LocalSessionCredentials | null {
  if (!localSessionToken || !localCsrfToken) return null;
  return { session: localSessionToken, csrf: localCsrfToken };
}

export function clearLocalSession(): void {
  localSessionToken = null;
  localCsrfToken = null;
}

export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return request<HealthResponse>("/health", { signal }, false);
}

export function getRecoveryReport(signal?: AbortSignal): Promise<RecoveryReport> {
  return request<RecoveryReport>("/api/v1/system/recovery", { signal }, false);
}

export function runRecoveryCheck(): Promise<RecoveryReport> {
  return request<RecoveryReport>(
    "/api/v1/system/recovery",
    { method: "POST" },
    true,
  );
}

export function getCurrentBookPlan(projectId: string): Promise<BookPlan> {
  return request<BookPlan>(
    `/api/v1/projects/${projectId}/book-production/plans/current`,
    {},
    false,
  );
}

export function estimateBookProduction(
  projectId: string,
  perPanelCostCeilingAnlas: number,
): Promise<BookEstimate> {
  return request<BookEstimate>(
    `/api/v1/projects/${projectId}/book-production/estimate`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": crypto.randomUUID(),
      },
      body: JSON.stringify({
        per_panel_cost_ceiling_anlas: perPanelCostCeilingAnlas,
      }),
    },
    true,
  );
}

export function createBookPlan(
  projectId: string,
  estimate: BookEstimate,
  maxCalls: number,
  maxCostAnlas: number,
): Promise<BookPlan> {
  return request<BookPlan>(
    `/api/v1/projects/${projectId}/book-production/plans`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": crypto.randomUUID(),
      },
      body: JSON.stringify({
        per_panel_cost_ceiling_anlas: estimate.per_panel_cost_ceiling_anlas,
        plan_fingerprint: estimate.plan_fingerprint,
        max_calls: maxCalls,
        max_cost_anlas: maxCostAnlas,
        confirmed: true,
      }),
    },
    true,
  );
}

export function approveBookPlanChapter(
  projectId: string,
  plan: BookPlan,
  bookChapterPlanId: string,
): Promise<BookPlan> {
  return bookPlanPost(
    projectId,
    plan,
    `chapters/${bookChapterPlanId}/approve`,
  );
}

export function retryBookPlanChapter(
  projectId: string,
  plan: BookPlan,
  bookChapterPlanId: string,
): Promise<BookPlan> {
  return bookPlanPost(
    projectId,
    plan,
    `chapters/${bookChapterPlanId}/retry`,
  );
}

export function transitionBookPlan(
  projectId: string,
  plan: BookPlan,
  action: "start" | "advance" | "pause" | "resume" | "cancel",
): Promise<BookPlan> {
  return bookPlanPost(projectId, plan, action);
}

function bookPlanPost(
  projectId: string,
  plan: BookPlan,
  suffix: string,
): Promise<BookPlan> {
  return request<BookPlan>(
    `/api/v1/projects/${projectId}/book-production/plans/${plan.book_plan_id}/${suffix}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_revision: plan.revision }),
    },
    true,
  );
}

export function getContinuity(projectId: string): Promise<ContinuityVersion> {
  return request<ContinuityVersion>(
    `/api/v1/projects/${projectId}/continuity`,
    {},
    false,
  );
}

export function draftContinuity(
  projectId: string,
  chapterId: string,
): Promise<ContinuityVersion> {
  return request<ContinuityVersion>(
    `/api/v1/projects/${projectId}/continuity/draft`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chapter_id: chapterId }),
    },
    true,
  );
}

export function analyzeContinuityImpact(
  projectId: string,
  versionId: string,
  document: ContinuityDocument,
): Promise<ContinuityImpact> {
  return request<ContinuityImpact>(
    `/api/v1/projects/${projectId}/continuity/${versionId}/impact`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document }),
    },
    true,
  );
}

export function reviseContinuity(
  projectId: string,
  versionId: string,
  document: ContinuityDocument,
): Promise<ContinuityVersion> {
  return request<ContinuityVersion>(
    `/api/v1/projects/${projectId}/continuity/${versionId}/revisions`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document }),
    },
    true,
  );
}

export function approveContinuity(
  projectId: string,
  versionId: string,
): Promise<ContinuityVersion> {
  return request<ContinuityVersion>(
    `/api/v1/projects/${projectId}/continuity/${versionId}/approve`,
    { method: "POST" },
    true,
  );
}

export function getVaultStatus(): Promise<VaultStatus> {
  return request<VaultStatus>("/api/v1/vault", {}, false);
}

export function createVault(masterPassword: string): Promise<VaultStatus> {
  return request<VaultStatus>(
    "/api/v1/vault",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ master_password: masterPassword }),
    },
    true,
  );
}

export function unlockVault(masterPassword: string): Promise<VaultStatus> {
  return request<VaultStatus>(
    "/api/v1/vault/unlock",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ master_password: masterPassword }),
    },
    true,
  );
}

export function lockVault(): Promise<VaultStatus> {
  return request<VaultStatus>("/api/v1/vault/lock", { method: "POST" }, true);
}

export function saveCredential(
  profileId: string,
  provider: string,
  label: string,
  secret: string,
): Promise<CredentialProfile> {
  return request<CredentialProfile>(
    `/api/v1/vault/profiles/${encodeURIComponent(profileId)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, label, secret }),
    },
    true,
  );
}

export function listProjects(signal?: AbortSignal): Promise<Project[]> {
  return request<Project[]>("/api/v1/projects", { signal }, false);
}

export function createProject(title: string): Promise<Project> {
  return request<Project>(
    "/api/v1/projects",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    },
    true,
  );
}

export function preflightSource(projectId: string, file: File): Promise<SourcePreflight> {
  const body = new FormData();
  body.append("file", file);
  return request<SourcePreflight>(
    `/api/v1/projects/${projectId}/source/preflight`,
    { method: "POST", body },
    true,
  );
}

export function confirmSource(
  projectId: string,
  preflightId: string,
  encoding: string,
): Promise<ChapterSet> {
  return request<ChapterSet>(
    `/api/v1/projects/${projectId}/source/confirm`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preflight_id: preflightId, encoding }),
    },
    true,
  );
}

export function getChapters(projectId: string, signal?: AbortSignal): Promise<ChapterSet> {
  return request<ChapterSet>(`/api/v1/projects/${projectId}/source/chapters`, { signal }, false);
}

export function getChapterText(projectId: string, chapterId: string): Promise<ChapterText> {
  return request<ChapterText>(
    `/api/v1/projects/${projectId}/source/chapters/${chapterId}/text`,
    {},
    false,
  );
}

export function replaceChapters(
  projectId: string,
  sourceFileId: string,
  chapters: ChapterBoundaryInput[],
): Promise<ChapterSet> {
  return request<ChapterSet>(
    `/api/v1/projects/${projectId}/source/chapters`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_file_id: sourceFileId, chapters }),
    },
    true,
  );
}

export function getStoryBeats(projectId: string, chapterId: string): Promise<StoryBeatSet> {
  return request<StoryBeatSet>(
    `/api/v1/projects/${projectId}/source/chapters/${chapterId}/story-beats`,
    {},
    false,
  );
}

export function draftStoryBeats(projectId: string, chapterId: string): Promise<StoryBeatSet> {
  return request<StoryBeatSet>(
    `/api/v1/projects/${projectId}/source/chapters/${chapterId}/story-beats/draft`,
    { method: "POST" },
    true,
  );
}

export function getTextModelConfiguration(projectId: string): Promise<TextModelConfiguration> {
  return request<TextModelConfiguration>(
    `/api/v1/projects/${projectId}/adaptation/text-model`,
    {},
    false,
  );
}

export function saveTextModelConfiguration(
  projectId: string,
  configuration: {
    remark_name?: string | null;
    url: string;
    key_password?: string;
    request_model: string;
  },
): Promise<TextModelConfiguration> {
  return request<TextModelConfiguration>(
    `/api/v1/projects/${projectId}/adaptation/text-model`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(configuration),
    },
    true,
  );
}

export function testTextModelConfiguration(
  projectId: string,
): Promise<{ status: "ok"; endpoint_host: string; model: string; config_revision: number }> {
  return request(
    `/api/v1/projects/${projectId}/adaptation/text-model/test`,
    { method: "POST" },
    true,
  );
}

export function getNovelAICapabilities(projectId: string): Promise<NovelAICapabilities> {
  return request<NovelAICapabilities>(
    `/api/v1/projects/${projectId}/novelai/capabilities`,
    {},
    false,
  );
}

export function getNovelAIConfiguration(projectId: string): Promise<NovelAIConfiguration> {
  return request<NovelAIConfiguration>(
    `/api/v1/projects/${projectId}/novelai/config`,
    {},
    false,
  );
}

export function saveNovelAIConfiguration(
  projectId: string,
  configuration: {
    provider_model_id: string;
    credential_profile_id: string;
    timeout_seconds: number;
  },
): Promise<NovelAIConfiguration> {
  return request<NovelAIConfiguration>(
    `/api/v1/projects/${projectId}/novelai/config`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(configuration),
    },
    true,
  );
}

export function testNovelAIConnection(projectId: string): Promise<{
  status: "ok";
  provider: "novelai";
  provider_model_id: string;
  config_revision: number;
  suggestion_count: number;
  subscription: {
    profile_version: string;
    subscription_active: boolean;
    subscription_tier: number;
    is_grace_period: boolean;
    opus_active: boolean;
  };
  zero_anlas_ready: boolean;
  model_supports_zero_anlas: boolean;
  generated_images: 0;
  last_connection_at: string;
}> {
  return request(
    `/api/v1/projects/${projectId}/novelai/connection-test`,
    { method: "POST" },
    true,
  );
}

export function estimateGeneration(
  projectId: string,
  chapterId: string,
  perPanelCostCeilingAnlas: number,
): Promise<GenerationEstimate> {
  return request<GenerationEstimate>(
    `/api/v1/projects/${projectId}/generation/estimate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chapter_id: chapterId,
        per_panel_cost_ceiling_anlas: perPanelCostCeilingAnlas,
      }),
    },
    true,
  );
}

export function createGenerationJob(
  projectId: string,
  estimate: GenerationEstimate,
  maxCalls: number,
  maxCostAnlas: number,
): Promise<GenerationJob> {
  return request<GenerationJob>(
    `/api/v1/projects/${projectId}/generation/jobs`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": crypto.randomUUID(),
      },
      body: JSON.stringify({
        chapter_id: estimate.chapter_id,
        per_panel_cost_ceiling_anlas: estimate.per_panel_cost_ceiling_anlas,
        plan_fingerprint: estimate.plan_fingerprint,
        max_calls: maxCalls,
        max_cost_anlas: maxCostAnlas,
        confirmed: true,
      }),
    },
    true,
  );
}

export function listGenerationJobs(projectId: string): Promise<GenerationJob[]> {
  return request<GenerationJob[]>(
    `/api/v1/projects/${projectId}/generation/jobs`,
    {},
    false,
  );
}

export function getGenerationJob(
  projectId: string,
  jobId: string,
): Promise<GenerationJob> {
  return request<GenerationJob>(
    `/api/v1/projects/${projectId}/generation/jobs/${jobId}`,
    {},
    false,
  );
}

export function transitionGenerationJob(
  projectId: string,
  jobId: string,
  action: "start" | "pause" | "resume" | "cancel",
  expectedRevision: number,
): Promise<GenerationJob> {
  return request<GenerationJob>(
    `/api/v1/projects/${projectId}/generation/jobs/${jobId}/${action}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_revision: expectedRevision }),
    },
    true,
  );
}

export function executeGenerationJob(
  projectId: string,
  jobId: string,
  expectedRevision: number,
): Promise<{
  status: "scheduled" | "already_running";
  job_id: string;
  bounded_user_action_id: string;
}> {
  return request(
    `/api/v1/projects/${projectId}/generation/jobs/${jobId}/execute`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        expected_revision: expectedRevision,
        confirmation: "I_CONFIRM_NOVELAI_IMAGE_GENERATION",
      }),
    },
    true,
  );
}

export function listGenerationAssets(projectId: string): Promise<GenerationAsset[]> {
  return request<GenerationAsset[]>(
    `/api/v1/projects/${projectId}/generation/assets`,
    {},
    false,
  );
}

export function uploadRevisionMask(
  projectId: string,
  panelId: string,
  parentAssetVersionId: string,
  file: File,
): Promise<MaskAsset> {
  const form = new FormData();
  form.set("panel_id", panelId);
  form.set("parent_asset_version_id", parentAssetVersionId);
  form.set("mask", file);
  return request<MaskAsset>(
    `/api/v1/projects/${projectId}/generation/masks`,
    { method: "POST", body: form },
    true,
  );
}

export interface RevisionEstimateInput {
  operation: RevisionOperation;
  page_id: string;
  panel_id: string | null;
  mask_asset_id: string | null;
  edit_prompt: string | null;
  inpaint_strength: number | null;
  per_panel_cost_ceiling_anlas: number;
}

export function estimateRevision(
  projectId: string,
  input: RevisionEstimateInput,
): Promise<RevisionEstimate> {
  return request<RevisionEstimate>(
    `/api/v1/projects/${projectId}/generation/revisions/estimate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
    true,
  );
}

export function createRevisionJob(
  projectId: string,
  estimate: RevisionEstimate,
): Promise<GenerationJob> {
  const target = estimate.targets[0];
  return request<GenerationJob>(
    `/api/v1/projects/${projectId}/generation/revisions/jobs`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": crypto.randomUUID(),
      },
      body: JSON.stringify({
        operation: estimate.operation,
        page_id: estimate.page_id,
        panel_id: estimate.operation === "page_reroll" ? null : target.panel_id,
        mask_asset_id: target.mask_asset_id,
        edit_prompt: target.edit_prompt,
        inpaint_strength: target.inpaint_strength,
        per_panel_cost_ceiling_anlas: target.cost_ceiling_anlas,
        plan_fingerprint: estimate.plan_fingerprint,
        max_calls: estimate.panel_count,
        max_cost_anlas: estimate.estimated_cost_upper_anlas,
        confirmed: true,
      }),
    },
    true,
  );
}

export async function getGenerationAssetImage(
  projectId: string,
  assetVersionId: string,
): Promise<Blob> {
  if (!localSessionToken || !localCsrfToken) {
    throw new ApiError("本地会话已失效，请重新运行 Manga Maker 启动器。", 401);
  }
  const headers = new Headers({
    "X-Manga-Maker-Session": localSessionToken,
    "X-CSRF-Token": localCsrfToken,
  });
  const response = await fetch(
    `/api/v1/projects/${projectId}/generation/assets/${assetVersionId}/content`,
    { headers },
  );
  if (!response.ok) throw new ApiError("无法读取本地面板素材。", response.status);
  return response.blob();
}

export function listPageTemplates(projectId: string): Promise<PageTemplate[]> {
  return request<PageTemplate[]>(
    `/api/v1/projects/${projectId}/pages/templates`,
    {},
    false,
  );
}

export function listAssetLibrary(
  projectId: string,
  includeArchived = false,
): Promise<AssetLibraryItem[]> {
  return request<AssetLibraryItem[]>(
    `/api/v1/projects/${projectId}/asset-library?include_archived=${includeArchived}`,
    {},
    false,
  );
}

export function createAssetLibraryItem(
  projectId: string,
  input: {
    source_asset_version_id: string;
    kind: AssetLibraryItem["kind"];
    name: string;
    tags: string[];
    notes: string;
  },
): Promise<AssetLibraryItem> {
  return request<AssetLibraryItem>(
    `/api/v1/projects/${projectId}/asset-library`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
    true,
  );
}

export function updateAssetLibraryItem(
  projectId: string,
  item: AssetLibraryItem,
  input: Pick<AssetLibraryItem, "kind" | "name" | "tags" | "notes">,
): Promise<AssetLibraryItem> {
  return request<AssetLibraryItem>(
    `/api/v1/projects/${projectId}/asset-library/${item.library_item_id}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...input, expected_revision: item.revision }),
    },
    true,
  );
}

export function setAssetLibraryItemArchived(
  projectId: string,
  item: AssetLibraryItem,
  archived: boolean,
): Promise<AssetLibraryItem> {
  return request<AssetLibraryItem>(
    `/api/v1/projects/${projectId}/asset-library/${item.library_item_id}/${archived ? "archive" : "restore"}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_revision: item.revision }),
    },
    true,
  );
}

export async function getAssetLibraryImage(
  projectId: string,
  libraryItemId: string,
): Promise<Blob> {
  if (!localSessionToken || !localCsrfToken) {
    throw new ApiError("本地会话已失效，请重新运行 Manga Maker 启动器。", 401);
  }
  const response = await fetch(
    `/api/v1/projects/${projectId}/asset-library/${libraryItemId}/content`,
    {
      headers: {
        "X-Manga-Maker-Session": localSessionToken,
        "X-CSRF-Token": localCsrfToken,
      },
    },
  );
  if (!response.ok) throw new ApiError("无法读取素材库图像。", response.status);
  return response.blob();
}

export function listComicPages(
  projectId: string,
  chapterId: string,
): Promise<ComicPageVersion[]> {
  return request<ComicPageVersion[]>(
    `/api/v1/projects/${projectId}/pages?chapter_id=${encodeURIComponent(chapterId)}`,
    {},
    false,
  );
}

export function draftComicPages(
  projectId: string,
  chapterId: string,
): Promise<ComicPageVersion[]> {
  return request<ComicPageVersion[]>(
    `/api/v1/projects/${projectId}/pages/draft`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chapter_id: chapterId }),
    },
    true,
  );
}

export function saveComicPageRevision(
  projectId: string,
  page: ComicPageVersion,
  document: PageDocument,
): Promise<ComicPageVersion> {
  return request<ComicPageVersion>(
    `/api/v1/projects/${projectId}/pages/${page.page_id}/versions`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        expected_revision: page.page_revision,
        document,
      }),
    },
    true,
  );
}

export function listPageVersions(
  projectId: string,
  pageId: string,
): Promise<ComicPageVersion[]> {
  return request<ComicPageVersion[]>(
    `/api/v1/projects/${projectId}/pages/${pageId}/versions`,
    {},
    false,
  );
}

export function activatePageVersion(
  projectId: string,
  page: ComicPageVersion,
  pageVersionId: string,
): Promise<ComicPageVersion> {
  return request<ComicPageVersion>(
    `/api/v1/projects/${projectId}/pages/${page.page_id}/versions/${pageVersionId}/activate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_revision: page.page_revision }),
    },
    true,
  );
}

export async function getComicPageImage(
  projectId: string,
  pageId: string,
  pageVersionId: string,
): Promise<Blob> {
  if (!localSessionToken || !localCsrfToken) {
    throw new ApiError("本地会话已失效，请重新运行 Manga Maker 启动器。", 401);
  }
  const headers = new Headers({
    "X-Manga-Maker-Session": localSessionToken,
    "X-CSRF-Token": localCsrfToken,
  });
  const response = await fetch(
    `/api/v1/projects/${projectId}/pages/${pageId}/versions/${pageVersionId}/content`,
    { headers },
  );
  if (!response.ok) throw new ApiError("无法读取本地漫画页。", response.status);
  return response.blob();
}

export function preflightExport(
  projectId: string,
  chapterId: string,
): Promise<ExportPreflight> {
  return request<ExportPreflight>(
    `/api/v1/projects/${projectId}/exports/preflight`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chapter_id: chapterId }),
    },
    true,
  );
}

export function createExport(
  projectId: string,
  plan: ExportPreflight,
): Promise<ExportRevision> {
  return request<ExportRevision>(
    `/api/v1/projects/${projectId}/exports`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chapter_id: plan.chapter_id,
        page_version_ids: plan.pages.map((page) => page.page_version_id),
        plan_fingerprint: plan.plan_fingerprint,
        confirmed: true,
      }),
    },
    true,
  );
}

export function listExports(projectId: string): Promise<ExportRevision[]> {
  return request<ExportRevision[]>(
    `/api/v1/projects/${projectId}/exports`,
    {},
    false,
  );
}

export async function downloadExportFile(
  projectId: string,
  exportRevisionId: string,
  exportFileId: string,
): Promise<Blob> {
  if (!localSessionToken || !localCsrfToken) {
    throw new ApiError("本地会话已失效，请重新运行 Manga Maker 启动器。", 401);
  }
  const response = await fetch(
    `/api/v1/projects/${projectId}/exports/${exportRevisionId}/files/${exportFileId}`,
    {
      headers: {
        "X-Manga-Maker-Session": localSessionToken,
        "X-CSRF-Token": localCsrfToken,
      },
    },
  );
  if (!response.ok) throw new ApiError("无法读取本地导出文件。", response.status);
  return response.blob();
}

export function preflightProjectPackage(file: File): Promise<ImportPreflight> {
  const body = new FormData();
  body.set("file", file);
  return request<ImportPreflight>(
    "/api/v1/imports/preflight",
    { method: "POST", body },
    true,
  );
}

export function restoreProjectPackage(
  importPreflightId: string,
): Promise<RestoreResult> {
  return request<RestoreResult>(
    `/api/v1/imports/${importPreflightId}/restore`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirmed: true }),
    },
    true,
  );
}

export function getCurrentStoryboard(
  projectId: string,
  chapterId: string,
): Promise<StoryboardVersion> {
  return request<StoryboardVersion>(
    `/api/v1/projects/${projectId}/adaptation/storyboards/current?chapter_id=${encodeURIComponent(chapterId)}`,
    {},
    false,
  );
}

export function generateStoryboard(
  projectId: string,
  chapterId: string,
  pageBudget: number,
  adaptationPreferences: string[],
): Promise<StoryboardVersion> {
  return request<StoryboardVersion>(
    `/api/v1/projects/${projectId}/adaptation/storyboards/generate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chapter_id: chapterId,
        page_budget: pageBudget,
        adaptation_preferences: adaptationPreferences,
      }),
    },
    true,
  );
}

export function reviseStoryboard(
  projectId: string,
  storyboardVersionId: string,
  document: StoryboardDocument,
): Promise<StoryboardVersion> {
  return request<StoryboardVersion>(
    `/api/v1/projects/${projectId}/adaptation/storyboards/${storyboardVersionId}/revisions`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document }),
    },
    true,
  );
}

export function approveStoryboard(
  projectId: string,
  storyboardVersionId: string,
): Promise<StoryboardVersion> {
  return request<StoryboardVersion>(
    `/api/v1/projects/${projectId}/adaptation/storyboards/${storyboardVersionId}/approve`,
    { method: "POST" },
    true,
  );
}

export function getBibleBundle(projectId: string, chapterId: string): Promise<BibleBundle> {
  return request<BibleBundle>(
    `/api/v1/projects/${projectId}/bibles?chapter_id=${encodeURIComponent(chapterId)}`,
    {},
    false,
  );
}

export function generateBibleBundle(
  projectId: string,
  storyboardVersionId: string,
): Promise<BibleBundle> {
  return request<BibleBundle>(
    `/api/v1/projects/${projectId}/bibles/generate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        storyboard_version_id: storyboardVersionId,
        confirmed_data_send: true,
      }),
    },
    true,
  );
}

export function getPromptingWorkflow(
  projectId: string,
  chapterId: string,
): Promise<PromptingWorkflow> {
  return request<PromptingWorkflow>(
    `/api/v1/projects/${projectId}/prompting?chapter_id=${encodeURIComponent(chapterId)}`,
    {},
    false,
  );
}

export function generateCharacterTags(
  projectId: string,
  chapterId: string,
): Promise<PromptArtifactVersion<CharacterTagBundleDocument>> {
  return promptingGenerate(projectId, "character-tags", chapterId);
}

export function reviseCharacterTags(
  projectId: string,
  versionId: string,
  document: CharacterTagBundleDocument,
): Promise<PromptArtifactVersion<CharacterTagBundleDocument>> {
  return request(
    `/api/v1/projects/${projectId}/prompting/character-tags/${versionId}/revisions`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document }),
    },
    true,
  );
}

export function approveCharacterTags(
  projectId: string,
  versionId: string,
): Promise<PromptArtifactVersion<CharacterTagBundleDocument>> {
  return promptingApprove(projectId, "character-tags", versionId);
}

export function generatePromptBundle(
  projectId: string,
  chapterId: string,
): Promise<PromptArtifactVersion<PromptBundleDocument>> {
  return promptingGenerate(projectId, "prompt-bundles", chapterId);
}

export function revisePromptBundle(
  projectId: string,
  versionId: string,
  document: PromptBundleDocument,
): Promise<PromptArtifactVersion<PromptBundleDocument>> {
  const draft = {
    schema_version: "1.0",
    storyboard_version_id: document.storyboard_version_id,
    character_tag_bundle_version_id: document.character_tag_bundle_version_id,
    packages: document.packages.map((item) => ({
      prompt_package_id: item.prompt_package_id,
      panel_id: item.panel_id,
      base_visual_tags: item.base_visual_tags,
      character_blocks: item.character_blocks.map((block) => ({
        character_id: block.character_id,
        tag_set_id: block.tag_set_id,
        variable_tags: block.variable_tags,
        negative_tags:
          item.structured_package?.prompt_plan.characters.find(
            (character) => character.character_id === block.character_id,
          )?.negative_tags ?? [],
        action:
          item.structured_package?.prompt_plan.characters.find(
            (character) => character.character_id === block.character_id,
          )?.action ?? "preserve the approved panel action",
        order:
          item.structured_package?.prompt_plan.characters.find(
            (character) => character.character_id === block.character_id,
          )?.order ?? 0,
        center:
          item.structured_package?.prompt_plan.characters.find(
            (character) => character.character_id === block.character_id,
          )?.center ?? { x: 0.5, y: 0.5 },
      })),
      style_tags: item.style_tags,
      negative_tags: item.negative_tags,
      relationship_action:
        item.structured_package?.prompt_plan.base.relationship_action ?? null,
      continuity_tags:
        item.structured_package?.prompt_plan.continuity_tags ?? [],
    })),
  };
  return request(
    `/api/v1/projects/${projectId}/prompting/prompt-bundles/${versionId}/revisions`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document: draft }),
    },
    true,
  );
}

export function approvePromptBundle(
  projectId: string,
  versionId: string,
  snapshotSha256: string,
): Promise<PromptArtifactVersion<PromptBundleDocument>> {
  return request(
    `/api/v1/projects/${projectId}/prompting/prompt-bundles/${versionId}/approve`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": crypto.randomUUID(),
      },
      body: JSON.stringify({ snapshot_sha256: snapshotSha256 }),
    },
    true,
  );
}

function promptingGenerate<T>(
  projectId: string,
  kind: "character-tags" | "prompt-bundles",
  chapterId: string,
): Promise<T> {
  return request<T>(
    `/api/v1/projects/${projectId}/prompting/${kind}/generate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chapter_id: chapterId, confirmed_data_send: true }),
    },
    true,
  );
}

function promptingApprove<T>(
  projectId: string,
  kind: "character-tags" | "prompt-bundles",
  versionId: string,
): Promise<T> {
  return request<T>(
    `/api/v1/projects/${projectId}/prompting/${kind}/${versionId}/approve`,
    { method: "POST" },
    true,
  );
}

export function reviseCharacterBible(
  projectId: string,
  versionId: string,
  document: CharacterBibleDocument,
): Promise<BibleVersion<CharacterBibleDocument>> {
  return request(
    `/api/v1/projects/${projectId}/bibles/characters/${versionId}/revisions`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document }),
    },
    true,
  );
}

export function reviseStyleBible(
  projectId: string,
  versionId: string,
  document: StyleBibleDocument,
): Promise<BibleVersion<StyleBibleDocument>> {
  return request(
    `/api/v1/projects/${projectId}/bibles/styles/${versionId}/revisions`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document }),
    },
    true,
  );
}

export function approveBible<TDocument>(
  projectId: string,
  kind: "character" | "style",
  versionId: string,
): Promise<BibleVersion<TDocument>> {
  return request(
    `/api/v1/projects/${projectId}/bibles/${kind}/${versionId}/approve`,
    { method: "POST" },
    true,
  );
}

export function attachBibleReference(
  projectId: string,
  kind: "character" | "style",
  versionId: string,
  input: {
    file: File;
    sourceNote: string;
    rightsConfirmed: boolean;
    characterId?: string;
  },
): Promise<{
  bible: BibleVersion<CharacterBibleDocument> | BibleVersion<StyleBibleDocument>;
  reference_asset: ReferenceAsset;
}> {
  const body = new FormData();
  body.append("file", input.file);
  body.append("source_note", input.sourceNote);
  body.append("rights_confirmed", String(input.rightsConfirmed));
  if (input.characterId) body.append("character_id", input.characterId);
  return request(
    `/api/v1/projects/${projectId}/bibles/${kind}/${versionId}/references`,
    { method: "POST", body },
    true,
  );
}

export async function getReferenceImage(
  projectId: string,
  referenceAssetId: string,
): Promise<Blob> {
  if (!localSessionToken || !localCsrfToken) {
    throw new ApiError("本地会话已失效，请重新运行 Manga Maker 启动器。", 401);
  }
  const headers = new Headers({
    "X-Manga-Maker-Session": localSessionToken,
    "X-CSRF-Token": localCsrfToken,
  });
  const response = await fetch(
    `/api/v1/projects/${projectId}/bibles/references/${referenceAssetId}/content`,
    { headers },
  );
  if (!response.ok) throw new ApiError("无法读取本地参考图。", response.status);
  return response.blob();
}

async function request<T>(path: string, init: RequestInit, needsSession: boolean): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (needsSession) {
    if (!localSessionToken || !localCsrfToken) {
      throw new ApiError("本地会话已失效，请重新运行 Manga Maker 启动器。", 401);
    }
    headers.set("X-Manga-Maker-Session", localSessionToken);
    headers.set("X-CSRF-Token", localCsrfToken);
  }

  let response: Response;
  try {
    response = await fetch(path, { ...init, headers });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("无法连接本地 Manga Maker 服务。请确认启动器仍在运行。");
  }
  if (!response.ok) {
    let payload: ErrorPayload = {};
    try {
      payload = (await response.json()) as ErrorPayload;
    } catch {
      // Keep the safe generic message when a proxy or server returns non-JSON.
    }
    throw new ApiError(
      [
        payload.error?.message,
        payload.error?.details?.problem,
        payload.error?.details?.issues?.join(" "),
        formatErrorDetail(payload.detail),
      ]
        .filter((value): value is string => Boolean(value))
        .join(" ") ||
        "本地服务暂时无法完成该操作。",
      response.status,
    );
  }
  return (await response.json()) as T;
}

function formatErrorDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (!Array.isArray(detail)) return "";
  return detail
    .map((issue) => {
      if (typeof issue === "string") return issue;
      if (!issue || typeof issue !== "object") return "";
      const record = issue as Record<string, unknown>;
      const message = typeof record.msg === "string" ? record.msg : "";
      const location = Array.isArray(record.loc)
        ? record.loc
            .filter((part): part is string | number =>
              typeof part === "string" || typeof part === "number",
            )
            .join(".")
        : "";
      return [location, message].filter(Boolean).join(": ");
    })
    .filter(Boolean)
    .join(" ");
}
