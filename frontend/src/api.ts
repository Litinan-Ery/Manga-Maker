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
  provider: "openai-compatible";
  base_url: string;
  endpoint_host: string;
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

interface ErrorPayload {
  error?: { message?: string; details?: { problem?: string; issues?: string[] } };
  detail?: string;
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

export function clearLocalSession(): void {
  localSessionToken = null;
  localCsrfToken = null;
}

export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return request<HealthResponse>("/health", { signal }, false);
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
    base_url: string;
    model: string;
    credential_profile_id: string;
    timeout_seconds: number;
    temperature: number;
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
  generated_images: 0;
  last_connection_at: string;
}> {
  return request(
    `/api/v1/projects/${projectId}/novelai/connection-test`,
    { method: "POST" },
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
      body: JSON.stringify({ storyboard_version_id: storyboardVersionId }),
    },
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
      ]
        .filter((value): value is string => Boolean(value))
        .join(" ") ||
        payload.detail ||
        "本地服务暂时无法完成该操作。",
      response.status,
    );
  }
  return (await response.json()) as T;
}
